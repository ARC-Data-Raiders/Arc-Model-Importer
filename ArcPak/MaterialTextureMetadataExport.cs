using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.Texture;
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
        var referenced = new List<UUnrealMaterial>();
        material.AppendReferencedTextures(referenced, onlyRendered: false);
        if (referenced.Any(item => item is not UTexture)) throw new InvalidDataException("Native texture closure contains a non-texture resource");
        var textures = referenced.OfType<UTexture>().GroupBy(item => item.GetPathName(),StringComparer.OrdinalIgnoreCase)
            .Select(group => group.First()).OrderBy(item => item.GetPathName(),StringComparer.Ordinal).ToArray();
        if (textures.Length > 16384) throw new InvalidDataException("Native texture metadata closure budget exceeded");
        var root = Path.GetFullPath(outputRoot);
        var dependencies = new List<MaterialTextureMetadataManifest.Dependency>();
        long metadataBytes = 0;
        foreach (var texture in textures)
        {
            TextureMetadataExport.Write(texture, root, build.BuildId, build.DepotManifest, mappingHash);
            var path = TextureMetadataExport.MetadataPath(texture, root);
            var relative = Path.GetRelativePath(root,path).Replace('\\','/');
            if (relative.StartsWith("../",StringComparison.Ordinal) || Path.IsPathRooted(relative)) throw new InvalidDataException("Native metadata path escaped output root");
            var data = File.ReadAllBytes(path);metadataBytes += data.Length;
            if (metadataBytes > 128L*1024*1024) throw new InvalidDataException("Native texture metadata 128MiB closure budget exceeded");
            var fact = JsonNode.Parse(data)!.AsObject();
            dependencies.Add(new MaterialTextureMetadataManifest.Dependency(fact["Package"]!.GetValue<string>(),relative,TextureMetadataDocument.Hash(data)));
        }
        var document = MaterialTextureMetadataManifest.Capture(logicalPackage,material.GetPathName(),build.BuildId,
            build.DepotManifest,mappingHash,TextureMetadataExport.ProducerSnapshot(),dependencies);
        TextureMetadataDocument.WriteAtomic(materialSidecar + ".textures.json",document);
        return textures.Length;
    }
}
