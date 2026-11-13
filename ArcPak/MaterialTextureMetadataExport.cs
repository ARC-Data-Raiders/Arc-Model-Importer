using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.UObject;
using System.Text.Json.Nodes;

namespace ArcPak;

internal static class MaterialTextureMetadataExport
{
    // Native enumeration only: no rendered-name heuristics, image decoder,
    // exporter session, PNG writer, mesh export or preview-cache work.
    internal static int Write(UMaterialInterface material, string logicalPackage,
        string outputRoot, string materialSidecar, GameBuild.Info build, string mappingHash)
    {
        if (material is not UMaterial && material is not UMaterialInstanceConstant)
            throw new InvalidDataException("Unsupported native texture-closure material class: " + material.ExportType);
        var referenced = new List<FPackageIndex>();
        material.AppendReferencedTextures(referenced, onlyRendered: false);
        // Index 0 is a texture parameter overridden to None (colourway skin MIs do this): no texture, not an error.
        referenced.RemoveAll(reference => reference.Index == 0);
        // Runtime virtual textures (terrain blend on props and the backpack frame) hold no texture data to export.
        referenced.RemoveAll(reference => string.Equals(reference.ResolvedObject?.Class?.Name.Text, "RuntimeVirtualTexture", StringComparison.Ordinal));
        var resolved = referenced.Select(reference => reference.Load<UUnrealMaterial>()).ToArray();
        var rejected = referenced.Zip(resolved).Where(pair => pair.Second is not UTexture)
            .Select(pair => pair.First + " -> " + (pair.Second?.ExportType ?? "unresolved")).Distinct().ToArray();
        if (rejected.Length > 0)
            throw new InvalidDataException("Native texture closure contains a non-texture resource in " + logicalPackage + ": " + string.Join("; ", rejected.Take(8)));
        var textures = resolved.OfType<UTexture>().GroupBy(item => item.GetPathName(),StringComparer.OrdinalIgnoreCase)
            .Select(group => group.First()).OrderBy(item => item.GetPathName(),StringComparer.Ordinal).ToArray();
        if (textures.Length > 16384) throw new InvalidDataException("Native texture metadata closure budget exceeded");
        var root = Path.GetFullPath(outputRoot);
        var dependencies = new List<MaterialTextureMetadataManifest.Dependency>();
        long metadataBytes = 0;
        // Textures are distinct per closure (grouped by path), so per-texture writes never share a file.
        // Results land in index order: output equals the sequential loop.
        var sizes = new long[textures.Length];
        var slots = new MaterialTextureMetadataManifest.Dependency[textures.Length];
        try { Parallel.For(0, textures.Length, new ParallelOptions { MaxDegreeOfParallelism = Program.WorkerCount }, index =>
        {
            var texture = textures[index];
            TextureMetadataExport.Write(texture, root, build.BuildId, build.DepotManifest, mappingHash);
            var path = TextureMetadataExport.MetadataPath(texture, root);
            var relative = Path.GetRelativePath(root,path).Replace('\\','/');
            if (relative.StartsWith("../",StringComparison.Ordinal) || Path.IsPathRooted(relative)) throw new InvalidDataException("Native metadata path escaped output root");
            var data = File.ReadAllBytes(path);sizes[index] = data.Length;
            var fact = JsonNode.Parse(data)!.AsObject();
            slots[index] = new MaterialTextureMetadataManifest.Dependency(fact["Package"]!.GetValue<string>(),relative,TextureMetadataDocument.Hash(data));
        }); }
        catch (AggregateException error) when (error.InnerExceptions.Count > 0)
        {
            // Same exception type and message the sequential loop raised.
            System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(error.InnerExceptions[0]).Throw();
        }
        for (var index = 0; index < slots.Length; index++)
        {
            metadataBytes += sizes[index];
            if (metadataBytes > 128L*1024*1024) throw new InvalidDataException("Native texture metadata 128MiB closure budget exceeded");
            dependencies.Add(slots[index]);
        }
        var document = MaterialTextureMetadataManifest.Capture(logicalPackage,material.GetPathName(),build.BuildId,
            build.DepotManifest,mappingHash,TextureMetadataExport.ProducerSnapshot(),dependencies);
        TextureMetadataDocument.WriteAtomic(materialSidecar + ".textures.json",document);
        return textures.Length;
    }
}
