using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapCatalogExport
{
    internal static JObject Document(IEnumerable<string> paths)
    {
        var packages = paths.Where(path => path.EndsWith(".umap", StringComparison.OrdinalIgnoreCase))
            .Select(MapBundleDocument.CanonicalPackage).Distinct(StringComparer.OrdinalIgnoreCase)
            .Order(StringComparer.Ordinal);
        return new JObject
        {
            ["schemaVersion"] = 1,
            ["maps"] = new JArray(packages.Select(package => new JObject
            {
                ["package"] = package, ["name"] = package.Split('/').Last(), ["kind"] = "map"
            }))
        };
    }

    internal static int Run(string[] args)
    {
        var options = ArcVerbCommon.Parse(args, ["game-dir", "out", "usmap", "index"], []);
        var gameDir = Path.GetFullPath(ArcVerbCommon.Required(options, "game-dir"));
        var output = Path.GetFullPath(ArcVerbCommon.Required(options, "out"));
        if (!Directory.Exists(gameDir)) throw new ArgumentException("Missing or absent --game-dir");
        if (options.ContainsKey("index"))
        {
            _ = ArcVerbCommon.Required(options, "index");
            Console.WriteLine("map catalog uses the full-container mount fallback rather than a mesh package index");
        }
        using var provider = Program.CreateShell(gameDir);
        provider.ReadShaderMaps = false;
        Program.PrepareCompression(gameDir);
        if (options.ContainsKey("usmap"))
            provider.MappingsContainer = new MapBundleMappingsProvider(ArcVerbCommon.Required(options, "usmap"));
        Program.MountEverything(provider);
        if (provider.MountedVfs.Count == 0) throw new InvalidDataException("No game containers mounted");
        var document = Document(provider.Files.Values.Select(file => file.Path));
        MapBundleDocument.WriteAtomic(Path.GetDirectoryName(output)!, Path.GetFileName(output), MapBundleDocument.Bytes(document));
        Console.WriteLine("map catalog " + output + ", maps=" + document["maps"]!.Count());
        return 0;
    }
}
