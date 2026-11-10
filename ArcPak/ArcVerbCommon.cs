using System.Security.Cryptography;
using CUE4Parse.GameTypes.Theia.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;

namespace ArcPak;

internal static class ArcVerbCommon
{
    internal static Dictionary<string, List<string>> Parse(string[] args, string[] valued, string[] flags)
    {
        var o = new Dictionary<string, List<string>>(StringComparer.Ordinal);
        for (var i = 0; i < args.Length; i++)
        {
            var name = args[i];
            if (!name.StartsWith("--", StringComparison.Ordinal)) throw new ArgumentException($"Unknown token {name}");
            name = name[2..];
            if (valued.Contains(name))
            {
                if (i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal)) throw new ArgumentException($"Missing value after --{name}");
                if (!o.TryGetValue(name, out var values)) o[name] = values = new List<string>();
                values.Add(args[++i]);
            }
            else if (flags.Contains(name)) o[name] = new List<string>();
            else throw new ArgumentException($"Unknown token --{name}");
        }
        return o;
    }

    internal static string Required(Dictionary<string, List<string>> o, string name)
    {
        if (!o.TryGetValue(name, out var values) || values.Count != 1) throw new ArgumentException($"Expected one --{name}");
        return values[0];
    }

    internal static TheiaFileProvider Open(string gameDir, string usmap, bool readShaderMaps)
    {
        Program.PrepareCompression(gameDir);
        var p = Program.CreateShell(gameDir);
        p.ReadShaderMaps = readShaderMaps;
        p.MappingsContainer = Program.Mappings(usmap);
        Program.MountEverything(p);
        return p;
    }

    internal static string Sha256(byte[] data) => Convert.ToHexString(SHA256.HashData(data));

    internal static void WriteAtomic(string path, byte[] data)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        var tmp = path + ".tmp";
        File.WriteAllBytes(tmp, data);
        File.Move(tmp, path, true);
    }

    internal static List<string> ReadList(string file) => File.ReadLines(file).Select(s => s.Trim()).Where(s => s.Length > 0 && !s.StartsWith("#", StringComparison.Ordinal)).ToList();
}
