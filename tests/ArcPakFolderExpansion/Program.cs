using System.Collections;
using System.Reflection;
using System.Runtime.Loader;
using System.Text;
using System.Text.Json;

// Run against the freshly rebuilt helper, never the unmodified shipping binary:
// dotnet <test.dll> <ArcPak.Export.dll> <current-index.bin> <scratch/result.json> [published-dependency-dir]
internal static class FolderExpansionRegression
{
    private const BindingFlags Methods = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
    private const string Folder = "PioneerGame/Content/Pioneer/Items/Characters/Skins/Outfit";
    private static Type IndexType = null!;
    private static MethodInfo Load = null!;
    private static MethodInfo Prepare = null!;

    private static string Identity(string path)
    {
        var text = path.Replace('\\', '/').Trim('/').ToLowerInvariant();
        foreach (var alias in new[] { "game/", "remappedplugins/content/" })
            if (text.StartsWith(alias)) return "pioneergame/content/" + text[alias.Length..];
        return text;
    }
    private static bool Listed(string path)
    {
        var leaf = path.Split('/')[^1].ToLowerInvariant();
        if (leaf.StartsWith("sk_") || leaf.StartsWith("sm_"))
            return !leaf.EndsWith("_physics") && !leaf.EndsWith("_skeleton");
        return leaf.StartsWith("da_oi_") && !leaf.Contains("persistence");
    }
    private static List<string> Prepared(string indexPath, string[] requested)
    {
        var index = Load.Invoke(null, new object[] { indexPath }) ?? throw new Exception("Index failed to load");
        var arguments = new object?[] { requested, null, null };
        if (!(bool)Prepare.Invoke(index, arguments)!) throw new Exception("TryPrepare returned false");
        var packages = ((IEnumerable)arguments[1]!).Cast<string>().ToList();
        var hits = ((IEnumerable)arguments[2]!).Cast<object>().Count();
        if (hits != packages.Count) throw new Exception("Package/hit count mismatch");
        if (packages.Select(Identity).Distinct().Count() != packages.Count) throw new Exception("Duplicate mount alias survived");
        return packages;
    }
    private static void Name(BinaryWriter writer, string name)
    {
        var bytes = Encoding.UTF8.GetBytes(name);
        writer.Write((ushort)bytes.Length); writer.Write(bytes);
    }
    private static void Synthetic(string path, int count)
    {
        using var writer = new BinaryWriter(File.Create(path));
        writer.Write(Encoding.ASCII.GetBytes("ARCPAKIX")); writer.Write((uint)6);
        Name(writer, "synthetic"); Name(writer, "fixture");
        writer.Write(1); Name(writer, "fixture.utoc"); writer.Write((long)1); writer.Write((ulong)0);
        writer.Write(0); writer.Write(0); // No exact/package dictionaries needed for folder expansion.
        var paths = Enumerable.Range(0, count).SelectMany(i => new[] {
            Folder + $"/Asset{i}/DA_OI_Outfit_{i}",
            Folder.Replace("PioneerGame/Content/", "RemappedPlugins/Content/") + $"/Asset{i}/DA_OI_Outfit_{i}"
        }).ToList();
        paths.AddRange(new[] { Folder + "/DA_OI_Test_Persistence", Folder + "/SK_Test_Skeleton",
                               Folder + "/T_UI_Icon_Test", Folder + "Elsewhere/DA_OI_Outside" });
        writer.Write(paths.Count);
        for (var i = 0; i < paths.Count; i++)
        {
            Name(writer, paths[i]); writer.Write((ushort)0); writer.Write((ulong)(1 + i / 2));
        }
    }
    private static List<string> ReadStored(string path)
    {
        using var reader = new BinaryReader(File.OpenRead(path));
        if (Encoding.ASCII.GetString(reader.ReadBytes(8)) != "ARCPAKIX") throw new Exception("Bad index signature");
        var version = reader.ReadUInt32();
        string NameRead() => Encoding.UTF8.GetString(reader.ReadBytes(reader.ReadUInt16()));
        if (version >= 2) { NameRead(); NameRead(); }
        var containers = reader.ReadInt32();
        for (var i = 0; i < containers; i++) { NameRead(); reader.ReadInt64(); reader.ReadUInt64(); }
        var packageCount = reader.ReadInt32(); reader.BaseStream.Position += packageCount * 10L;
        var hashCount = reader.ReadInt32(); reader.BaseStream.Position += hashCount * 18L;
        var count = reader.ReadInt32(); var paths = new List<string>();
        for (var i = 0; i < count; i++)
        {
            var value = NameRead(); var container = reader.ReadUInt16(); reader.ReadUInt64();
            if (container < containers) paths.Add(value);
        }
        return paths;
    }
    public static int Main(string[] args)
    {
        if (args.Length < 3) { Console.Error.WriteLine("Expected helper.dll current-index.bin output.json [dependency-dir]"); return 2; }
        var output = Path.GetFullPath(args[2]); Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var scratch = Path.Combine(Path.GetDirectoryName(output)!, "folder-fixture-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(scratch);
        try
        {
            var dependencyDir = args.Length > 3 ? Path.GetFullPath(args[3]) : Path.GetDirectoryName(Path.GetFullPath(args[0]))!;
            AssemblyLoadContext.Default.Resolving += (_, name) => {
                var path = Path.Combine(dependencyDir, name.Name + ".dll");
                return File.Exists(path) ? AssemblyLoadContext.Default.LoadFromAssemblyPath(path) : null;
            };
            var assembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.GetFullPath(args[0]));
            IndexType = assembly.GetType("ArcPak.PakIndex", throwOnError: true)!;
            Load = IndexType.GetMethod("TryLoad", Methods)!; Prepare = IndexType.GetMethod("TryPrepare", Methods)!;
            var results = new List<object>();
            foreach (var count in new[] { 127, 128, 129, 513 })
            {
                var index = Path.Combine(scratch, $"index-{count}.bin"); Synthetic(index, count);
                var prepared = Prepared(index, new[] { Folder, Folder, Folder.Replace("PioneerGame/Content/", "Game/") });
                if (prepared.Count != count) throw new Exception($"Boundary{count}: got {prepared.Count}");
                results.Add(new { expected = count, actual = prepared.Count, aliasesDeduplicated = true });
            }
            var expected = ReadStored(args[1]).Where(Listed).Select(Identity)
                .Where(path => path == Identity(Folder) || path.StartsWith(Identity(Folder) + "/"))
                .Distinct().ToHashSet();
            var actual = Prepared(args[1], new[] { Folder });
            if (!expected.SetEquals(actual.Select(Identity))) throw new Exception($"Current index mismatch expected{expected.Count} actual{actual.Count}");
            var report = new { passed = true, synthetic = results, currentIndex = Path.GetFullPath(args[1]),
                expected = expected.Count, actual = actual.Count,
                dataAssets = actual.Count(path => path.Split('/')[^1].StartsWith("DA_OI_", StringComparison.OrdinalIgnoreCase)),
                helperAssembly = assembly.Location };
            File.WriteAllText(output, JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
            Console.WriteLine(JsonSerializer.Serialize(report)); return 0;
        }
        catch (Exception exception) { Console.Error.WriteLine(exception); return 1; }
        finally { Directory.Delete(scratch, recursive: true); }
    }
}
