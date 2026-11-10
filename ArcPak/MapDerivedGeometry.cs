using System.Text;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Actor;
using CUE4Parse.UE4.Assets.Exports.Component.Landscape;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;
using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapDerivedGeometry
{
    internal static (string Name, string Path) Header(string file)
    {
        using var input = new BinaryReader(File.OpenRead(file), Encoding.UTF8);
        if (Encoding.ASCII.GetString(input.ReadBytes(8)) != "UEFORMAT" || ReadString(input) != "UEMODEL")
            throw new InvalidDataException("Derived output is not UEModel");
        input.ReadByte();
        return (ReadString(input), ReadString(input));
    }

    private static string ReadString(BinaryReader input)
    {
        var size = input.ReadInt32();
        if (size == 0 || size < -1048576 || size > 1048576) throw new InvalidDataException("Invalid UEModel header string");
        var bytes = input.ReadBytes(size < 0 ? -size * 2 : size);
        return (size < 0 ? Encoding.Unicode : Encoding.UTF8).GetString(bytes).TrimEnd((char)0);
    }

    internal static JObject Source(AbstractVfsFileProvider provider, UObject source, string recordId)
    {
        var canonical = MapBundleDocument.CanonicalPackage(source.Owner?.Name ?? source.GetPathName());
        var files = provider.Files.Values.Where(file => file.IsUePackage &&
            MapBundleDocument.CanonicalPackage(file.Path).Equals(canonical, StringComparison.OrdinalIgnoreCase)).DistinctBy(file => file.Path, StringComparer.OrdinalIgnoreCase).ToArray();
        if (files.Length != 1) throw new InvalidDataException("Derived source has no unique exact mounted package: " + canonical);
        provider.Files.FindPayloads(files[0], out var uexp, out var ubulks, out var uptnls);
        var hashes = new JObject();
        foreach (var file in new[] { files[0], uexp }.Where(file => file != null).Concat(ubulks).Concat(uptnls))
            hashes[file!.Path] = MapBundleDocument.HashSource(file!);
        return new JObject { ["recordId"] = recordId, ["objectPath"] = source.GetPathName(), ["package"] = canonical, ["packageFiles"] = hashes };
    }

    internal static JObject Binding(AbstractVfsFileProvider provider, UObject source, string recordId, string kind, string resourceId, string scratch)
    {
        var outputs = new JArray();
        foreach (var file in Directory.EnumerateFiles(scratch, "*.uemodel", SearchOption.AllDirectories).Order(StringComparer.Ordinal))
        {
            var header = Header(file);
            outputs.Add(new JObject { ["file"] = Path.GetRelativePath(scratch, file).Replace((char)92, (char)47),
                ["objectName"] = header.Name, ["objectPath"] = header.Path, ["sha256"] = MapBundleDocument.HashFile(file) });
        }
        if (outputs.Count == 0) throw new InvalidDataException("Derived geometry has no physical UEModel");
        return new JObject { ["schema"] = "arcpak-derived-geometry-v1", ["kind"] = kind, ["resourceId"] = resourceId,
            ["source"] = Source(provider, source, recordId), ["outputs"] = outputs };
    }

    internal static JArray Slots(StaticMeshDto dto)
    {
        var slots = new JArray();
        for (var index = 0; index < dto.Materials.Length; index++)
        {
            var reference = dto.Materials[index].Material;
            var identity = reference == null || reference.IsNull ? "" : reference.Load<UMaterialInterface>()?.GetPathName() ??
                throw new InvalidDataException("Authored derived material could not be loaded");
            var sections = new JArray();
            for (var lodIndex = 0; lodIndex < dto.LODs.Count; lodIndex++)
                for (var sectionIndex = 0; sectionIndex < dto.LODs[lodIndex].Sections.Length; sectionIndex++)
                {
                    var section = dto.LODs[lodIndex].Sections[sectionIndex];
                    if (section.MaterialIndex == index) sections.Add(new JObject { ["lod"] = lodIndex, ["section"] = sectionIndex,
                        ["firstIndex"] = dto.LODs[lodIndex].Sections.Take(sectionIndex).Sum(previous => previous.NumFaces * 3),
                        ["sourceFirstIndex"] = section.FirstIndex, ["numFaces"] = section.NumFaces });
                }
            slots.Add(new JObject { ["slotIndex"] = index, ["slotName"] = dto.Materials[index].SlotName,
                ["materialObject"] = identity.Length == 0 ? "" : MapBundleDocument.CanonicalObject(identity), ["sections"] = sections });
        }
        return slots;
    }

    internal static void Materials(AbstractVfsFileProvider provider, JObject bundle, JArray slots, string output, string gameDir, string usmap,
        SortedSet<string> diagnostics, ExportArtifactReuse? artifactReuse = null)
    {
        var assets = (JObject)bundle["assets"]!;
        var required = (JArray)bundle["requiredAssets"]!;
        foreach (var identity in slots.OfType<JObject>().Select(slot => slot.Value<string>("materialObject")!).Where(value => value.Length > 0).Distinct(StringComparer.Ordinal))
        {
            if (!required.Values<string>().Contains(identity)) required.Add(identity);
            if (assets.ContainsKey(identity)) continue;
            try { assets[identity] = MapBundleExport.ExportAsset(provider, identity, output, gameDir, usmap,
                bundle.Value<string>("sourceRevision")!, diagnostics, artifactReuse); }
            catch (Exception exception) { diagnostics.Add("Derived material export failed: " + identity + ": " + exception.Message); }
        }
    }
}

