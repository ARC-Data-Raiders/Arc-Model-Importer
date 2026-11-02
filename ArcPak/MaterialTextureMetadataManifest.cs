using System.Text.Json.Nodes;

namespace ArcPak;

internal static class MaterialTextureMetadataManifest
{
    internal const string Schema = "dataraiders.native-material-texture-closure";
    internal const int Version = 1;
    internal const string Recipe = "inherited-native-texture-metadata-v1";
    internal sealed record Dependency(string Package, string RelativePath, string Sha256);
    internal static JsonObject Capture(string package, string objectPath, string build,
        string depot, string mappingHash, JsonObject producer, IReadOnlyList<Dependency> dependencies)
    {
        if (!package.StartsWith('/') || dependencies.Count > 16384) throw new ArgumentException("Invalid native texture closure identity/budget");
        var items = new JsonArray();
        foreach (var item in dependencies.OrderBy(x => x.Package,StringComparer.Ordinal).ThenBy(x => x.RelativePath,StringComparer.Ordinal))
        {
            if (item.RelativePath.StartsWith('/') || item.RelativePath.Split('/').Any(part => part is ".." or ".") ||
                item.Sha256.Length != 64 || !item.Sha256.All(Uri.IsHexDigit)) throw new ArgumentException("Invalid native texture dependency");
            items.Add(new JsonObject { ["package"] = item.Package, ["relative_path"] = item.RelativePath, ["sha256"] = item.Sha256 });
        }
        return new JsonObject { ["schema"] = Schema, ["version"] = Version, ["recipe"] = Recipe,
            ["material_package"] = package, ["material_object_path"] = objectPath,
            ["build_id"] = build, ["depot_manifest"] = depot, ["mapping_sha256"] = mappingHash,
            ["texture_projection"] = TextureMetadataDocument.Projection, ["texture_schema_version"] = 1,
            ["producer_assemblies"] = producer.DeepClone(), ["dependencies"] = items };
    }
}
