using ArcPak;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Objects.Engine;
using Newtonsoft.Json.Linq;

internal static class RealMapProbe
{
    internal static void Run(string[] args)
    {
        var output = Path.GetFullPath(args[0]);
        using var provider = ArcPak.Program.CreateShell(args[1]);
        provider.ReadShaderMaps = false;
        ArcPak.Program.PrepareCompression(args[1]);
        provider.MappingsContainer = new MapBundleMappingsProvider(args[2]);
        ArcPak.Program.MountEverything(provider);
        var rows = new JArray();
        var selectors = args.Skip(3).SelectMany(selector => selector.EndsWith("/*", StringComparison.Ordinal)
            ? provider.Files.Values.Where(file => file.Path.EndsWith(".umap", StringComparison.OrdinalIgnoreCase) &&
                MapBundleDocument.CanonicalPackage(file.Path).StartsWith(selector[..^1], StringComparison.OrdinalIgnoreCase))
                .DistinctBy(file => file.Path, StringComparer.OrdinalIgnoreCase).OrderByDescending(file => file.Size).Take(6)
                .Select(file => MapBundleDocument.CanonicalPackage(file.Path))
            : new[] { selector });
        foreach (var selector in selectors)
        {
            var files = provider.Files.Values.Where(file => file.IsUePackage &&
                MapBundleDocument.CanonicalPackage(file.Path).Equals(selector, StringComparison.OrdinalIgnoreCase))
                .DistinctBy(file => file.Path, StringComparer.OrdinalIgnoreCase).OrderBy(file => file.Path, StringComparer.Ordinal).ToArray();
            var row = new JObject { ["selector"] = selector, ["files"] = new JArray(files.Select(file => file.Path)) };
            rows.Add(row);
            foreach (var file in files)
            {
                try
                {
                    var exports = provider.LoadPackage(file).GetExports().ToArray();
                    row["types"] = new JObject(exports.GroupBy(obj => obj.ExportType).OrderBy(group => group.Key)
                        .Select(group => new JProperty(group.Key, group.Count())));
                    row["resourceObjects"] = new JArray(exports.Where(obj =>
                        obj.ExportType.Contains("Landscape", StringComparison.OrdinalIgnoreCase) ||
                        obj.ExportType.Contains("Spline", StringComparison.OrdinalIgnoreCase) ||
                        obj.ExportType.Contains("FastGeo", StringComparison.OrdinalIgnoreCase)).Select(obj => obj.GetPathName()));
                    row["worlds"] = new JArray(exports.OfType<UWorld>().Select(world => new JObject
                    {
                        ["path"] = world.GetPathName(), ["streamingLevels"] = world.StreamingLevels?.Length,
                        ["actors"] = world.TryLoadPersistentLevel(out var level, out _) ? level?.Actors?.Length : null
                    }));
                    var streamingWorlds = exports.OfType<ULevelStreaming>().Select(streaming => streaming.WorldAsset?.AssetPathName.Text)
                        .OfType<string>().Where(path => path.Length > 0 && path != "None").Select(MapBundleDocument.CanonicalPackage)
                        .Distinct(StringComparer.OrdinalIgnoreCase).Order(StringComparer.Ordinal).ToArray();
                    var mountedWorlds = provider.Files.Values.Where(candidate => candidate.Path.EndsWith(".umap", StringComparison.OrdinalIgnoreCase))
                        .Select(candidate => MapBundleDocument.CanonicalPackage(candidate.Path))
                        .Where(path => path.StartsWith(selector + "/_Generated_/", StringComparison.OrdinalIgnoreCase))
                        .Distinct(StringComparer.OrdinalIgnoreCase).Order(StringComparer.Ordinal).ToArray();
                    row["streamingWorldReferences"] = new JArray(streamingWorlds);
                    row["mountedGeneratedWorlds"] = new JArray(mountedWorlds);
                    row["unreferencedMountedWorlds"] = new JArray(mountedWorlds.Except(streamingWorlds, StringComparer.OrdinalIgnoreCase));
                }
                catch (Exception exception) { row["error"] = exception.GetType().Name + ": " + exception.Message; }
            }
            MapBundleDocument.WriteAtomic(output, "probe.json", MapBundleDocument.Bytes(rows));
            Console.WriteLine(selector + ": files=" + files.Length + ", resourceObjects=" + row["resourceObjects"]?.Count() +
                ", types=" + new JObject(((JObject?)row["types"] ?? new JObject()).Properties().Where(property =>
                    property.Name.Contains("Spline", StringComparison.OrdinalIgnoreCase) || property.Name.Contains("FastGeo", StringComparison.OrdinalIgnoreCase) ||
                    property.Name.Contains("Landscape", StringComparison.OrdinalIgnoreCase))).ToString(Newtonsoft.Json.Formatting.None));
        }
    }
}
