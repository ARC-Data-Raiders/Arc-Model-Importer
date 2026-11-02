using System.Security.Cryptography;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Assets.Objects;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace ArcPak;

/// Native material-table slots and exported render sections are different indices.
/// Capture before export; bind to the actual completed UEModel only after export.
internal static class MeshMaterialSlotsExport
{
    public static JObject Capture(USkinnedAsset mesh, string canonicalPath, GameBuild.Info build)
    {
        if (mesh.SkeletalMaterials is not { Length: > 0 } materials || mesh.LODModels is null)
            throw new InvalidDataException("Native mesh material table/LODs unavailable: " + mesh.Name);
        var slots = new JArray(materials.Select((material, index) => new JObject {
            ["index"] = index,
            ["slot_name"] = material.MaterialSlotName.Text,
            ["imported_slot_name"] = material.ImportedMaterialSlotName?.Text,
            ["material"] = material.MaterialInterface?.ResolvedObject?.GetPathName() ?? "",
            // Exact MeshMaterialDto.SlotName expression; not a basename lookup.
            ["export_name"] = material.MaterialInterface?.Name ?? material.ImportedMaterialSlotName?.Text ?? material.MaterialSlotName.Text
        }));
        mesh.TryGetValue(out FStructFallback[] lodInfos, "LODInfo");
        var lods = new JArray();
        for (var lodIndex = 0; lodIndex < mesh.LODModels.Length; lodIndex++)
        {
            var lod = mesh.LODModels[lodIndex];
            if (lod is null) continue;
            int[] remap = [];
            if (lodInfos is not null && lodIndex < lodInfos.Length)
                lodInfos[lodIndex]?.TryGetValue(out remap, "LODMaterialMap");
            remap ??= [];
            var sections = new JArray();
            for (var index = 0; index < lod.Sections.Length; index++)
            {
                var section = lod.Sections[index];
                int effective = section.MaterialIndex;
                if (effective < 0 || effective >= materials.Length)
                    throw new InvalidDataException($"Native material index {effective} outside table on {mesh.Name} LOD{lodIndex}/{index}");
                bool remapped = index < remap.Length && remap[index] >= 0 && remap[index] < materials.Length;
                // CUE USkeletalMesh.Deserialize applies this precise map in-place.
                if (remapped && effective != remap[index])
                    throw new InvalidDataException("CUE effective LOD material-map mismatch");
                sections.Add(new JObject {
                    ["index"] = index,
                    ["raw_material_index"] = remapped ? JValue.CreateNull() : new JValue(effective),
                    ["raw_index_provenance"] = remapped ? "not retained after CUE LODMaterialMap application" : "unchanged; no valid authored LODMaterialMap override",
                    ["effective_material_index"] = effective,
                    ["first_index"] = section.BaseIndex,
                    ["triangles"] = section.NumTriangles,
                    ["export_name"] = slots[effective]!["export_name"]!.DeepClone(),
                    ["material"] = slots[effective]!["material"]!.DeepClone()
                });
            }
            lods.Add(new JObject { ["name"] = "LOD" + lodIndex, ["lod_material_map"] = JArray.FromObject(remap), ["sections"] = sections });
        }
        return new JObject {
            ["schema"] = "dataraiders.native-material-slots", ["version"] = 1,
            ["source"] = new JObject { ["package"] = canonicalPath, ["object_path"] = mesh.GetPathName(), ["build_id"] = build.BuildId, ["depot_manifest"] = build.DepotManifest },
            ["materials"] = slots, ["lods"] = lods,
            ["interpretation"] = "Native MaterialSlot selects table index; UEModel MATERIALS rows are render sections. Effective indices follow CUE decoded LODMaterialMap, never reverse-inferred from repeated MI paths."
        };
    }

    private static string ObjectKey(string reference)
    {
        var text = reference.Replace((char)92, '/').Trim('/');
        if (text.Contains((char)39)) text = text.Split((char)39)[1];
        foreach (var prefix in new[] { "PioneerGame/Content/", "RemappedPlugins/Content/" })
            if (text.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) text = "Game/" + text[prefix.Length..];
        return text.Trim('/').ToLowerInvariant();
    }

    public static bool SameSource(JObject snapshot, string objectPath)
        => ObjectKey((string?)snapshot["source"]?["object_path"] ?? "") == ObjectKey(objectPath);

    private static bool HasExactHeader(string path, JObject snapshot)
    {
        using var stream = File.OpenRead(path); using var reader = new BinaryReader(stream);
        string FString()
        {
            int size = reader.ReadInt32();
            if (size < 0 || size > 1024 * 1024) throw new InvalidDataException("Invalid UEModel header string");
            var bytes = reader.ReadBytes(size);
            if (bytes.Length != size) throw new EndOfStreamException();
            return System.Text.Encoding.UTF8.GetString(bytes).TrimEnd((char)0);
        }
        if (System.Text.Encoding.ASCII.GetString(reader.ReadBytes(8)) != "UEFORMAT" || FString() != "UEMODEL" || reader.ReadByte() < 10) return false;
        var name = FString(); var reference = FString();
        var native = (string?)snapshot["source"]?["object_path"] ?? "";
        return ObjectKey(reference) == ObjectKey(native) && name.Equals(native.Split('.').Last(), StringComparison.OrdinalIgnoreCase);
    }

    private static string ComparablePath(string path)
    {
        var text = Path.GetFullPath(path);
        var slash = ((char)92).ToString();
        var prefix = slash + slash + "?" + slash;
        if (text.StartsWith(prefix + "UNC" + slash, StringComparison.OrdinalIgnoreCase))
            return slash + slash + text[(prefix.Length + 4)..];
        return text.StartsWith(prefix, StringComparison.Ordinal) ? text[prefix.Length..] : text;
    }

    private static string ExactModel(JObject snapshot, string outputRoot, string? exportedFile)
    {
        if (!string.IsNullOrEmpty(exportedFile))
        {
            var file = Path.GetFullPath(exportedFile); var root = ComparablePath(outputRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!ComparablePath(file).StartsWith(root, StringComparison.OrdinalIgnoreCase) || !file.EndsWith(".uemodel", StringComparison.OrdinalIgnoreCase) || !File.Exists(file) || !HasExactHeader(file, snapshot))
                throw new InvalidDataException("Export result UEModel path/header does not match exact native source");
            return file;
        }
        var package = (string?)snapshot["source"]?["package"] ?? throw new InvalidDataException("Missing source package");
        var packages = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { package };
        const string game = "PioneerGame/Content/", remapped = "RemappedPlugins/Content/";
        if (package.StartsWith(game, StringComparison.OrdinalIgnoreCase)) packages.Add(remapped + package[game.Length..]);
        if (package.StartsWith(remapped, StringComparison.OrdinalIgnoreCase)) packages.Add(game + package[remapped.Length..]);
        var matches = packages.Select(p => Path.Combine(outputRoot, p.Replace('/', Path.DirectorySeparatorChar) + ".uemodel"))
            .Where(File.Exists).Where(p => HasExactHeader(p, snapshot)).ToArray();
        if (matches.Length != 1) throw new InvalidDataException($"Expected one exact full-mount UEModel/header; found {matches.Length}. Use an explicit exporter result path for ambiguous aliases.");
        return matches[0];
    }

    public static bool Write(JObject snapshot, string outputRoot, string? exportedFile = null)
    {
        try
        {
            var package = (string?)snapshot["source"]?["package"] ?? throw new InvalidDataException("Missing exact source package");
            if (package.Contains("..") || Path.IsPathRooted(package)) throw new InvalidDataException("Unsafe source package");
            var model = ExactModel(snapshot, outputRoot, exportedFile);
            if (!File.Exists(model)) throw new FileNotFoundException("Exact exported UEModel required for material-slot metadata", model);
            using var stream = File.OpenRead(model);
            snapshot["uemodel"] = new JObject { ["relative_path"] = Path.GetRelativePath(ComparablePath(outputRoot), ComparablePath(model)).Replace('\\', '/'), ["size"] = stream.Length, ["sha256"] = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant() };
            var target = Path.ChangeExtension(model, ".arc-material-slots.json");
            var temp = target + ".tmp-" + Guid.NewGuid().ToString("N");
            try
            {
                File.WriteAllText(temp, snapshot.ToString(Formatting.Indented));
                for (int attempt = 0; ; attempt++)
                {
                    try { File.Move(temp, target, true); break; }
                    catch (Exception error) when (attempt < 6 && (error is IOException or UnauthorizedAccessException) && ((error.HResult & 0xffff) is 5 or 32 or 33))
                    { Thread.Sleep(10 << attempt); }
                }
            }
            finally { try { if (File.Exists(temp)) File.Delete(temp); } catch (Exception cleanup) when (cleanup is IOException or UnauthorizedAccessException) { /* preserve original exception; retain unique temp for later cleanup */ } }
            Log.Information("native material slots {Mesh} table={Count}", Path.GetFileName(model), snapshot["materials"]!.Count());
            return true;
        }
        catch (Exception error) { Log.Error(error, "Native material-slot metadata failed"); return false; }
    }
}




