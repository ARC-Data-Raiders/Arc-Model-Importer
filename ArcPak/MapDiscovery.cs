using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapDiscovery
{
    internal static JObject Document(string map, IEnumerable<string> mountedPaths, IEnumerable<string> worlds, IEnumerable<string> objectPackages)
    {
        var visited = worlds.Select(MapBundleDocument.CanonicalPackage).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var captured = objectPackages.Select(MapBundleDocument.CanonicalPackage).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var generated = new SortedSet<string>(StringComparer.Ordinal);
        var external = new SortedSet<string>(StringComparer.Ordinal);
        foreach (var path in mountedPaths.Where(path => path.EndsWith(".umap", StringComparison.OrdinalIgnoreCase) ||
            path.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)))
        {
            var package = MapBundleDocument.CanonicalPackage(path);
            if (package.StartsWith(map + "/_Generated_/", StringComparison.OrdinalIgnoreCase)) generated.Add(package);
            var segments = package.Split('/');
            if (!segments.Any(segment => segment is "__ExternalActors__" or "__ExternalObjects__")) continue;
            var withoutMarker = string.Join('/', segments.Where(segment => segment is not "__ExternalActors__" and not "__ExternalObjects__"));
            if (withoutMarker.StartsWith(map + "/", StringComparison.OrdinalIgnoreCase)) external.Add(package);
        }
        var sourcePackages = generated.Select(package => new JObject
            {
                ["package"] = package,
                ["kind"] = "generated-world",
                ["disposition"] = visited.Contains(package) ? "visited" : "unvisited"
            })
            .Concat(external.Select(package => new JObject
            {
                ["package"] = package,
                ["kind"] = "external-package",
                ["disposition"] = captured.Contains(package) ? "captured" : "uncaptured"
            }))
            .OrderBy(record => record.Value<string>("package"), StringComparer.Ordinal)
            .ThenBy(record => record.Value<string>("kind"), StringComparer.Ordinal)
            .ToArray();
        return new JObject
        {
            ["basis"] = "mounted-package-path-census-not-independent-authored-world-proof",
            ["sourcePackages"] = new JArray(sourcePackages),
            ["generatedWorldCandidates"] = new JArray(generated),
            ["unvisitedGeneratedWorlds"] = new JArray(generated.Where(package => !visited.Contains(package))),
            ["externalPackageCandidates"] = new JArray(external),
            ["uncapturedExternalPackages"] = new JArray(external.Where(package => !captured.Contains(package))),
            ["independentlyComplete"] = false
        };
    }
}

