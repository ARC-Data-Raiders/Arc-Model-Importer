using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapDependencyCache
{
    private static string Receipt(string package) => "dependency-receipts/" +
        MapBundleDocument.Hash(System.Text.Encoding.UTF8.GetBytes(package)) + ".json";

    internal static bool TryReuse(string output, string package, string revision,
        Func<string, string?> sourceHash, out JObject asset)
    {
        asset = null!;
        try
        {
            var path = MapBundleDocument.Inside(output, Receipt(package));
            if (!File.Exists(path)) return false;
            var receipt = JObject.Parse(File.ReadAllText(path));
            if (receipt.Value<string>("package") != package || receipt.Value<string>("sourceRevision") != revision ||
                receipt["dependencies"] is not JObject dependencies || !dependencies.HasValues ||
                receipt["asset"] is not JObject candidate || receipt["hashes"] is not JObject hashes || !hashes.HasValues)
                return false;
            var sealedData = new JObject { ["asset"] = candidate.DeepClone(), ["dependencies"] = dependencies.DeepClone(), ["hashes"] = hashes.DeepClone() };
            if (receipt.Value<string>("digest") != MapBundleDocument.Hash(MapBundleDocument.Bytes(sealedData))) return false;
            foreach (var dependency in dependencies.Properties())
                if (sourceHash(dependency.Name) != dependency.Value.Value<string>()) return false;
            if (candidate["files"] is not JArray files || files.Count != hashes.Count ||
                files.Values<string>().Any(file => file == null || !hashes.ContainsKey(file))) return false;
            var fingerprint = candidate.Value<string>("fingerprint");
            if (fingerprint?.Length != 64 || files.Values<string>().Any(file =>
                !file!.StartsWith("assets/" + fingerprint + "/", StringComparison.Ordinal))) return false;
            foreach (var field in new[] { "mesh", "material" })
                if (candidate[field] is { } value && !files.Values<string>().Contains(value.Value<string>())) return false;
            foreach (var hash in hashes.Properties())
            {
                var file = MapBundleDocument.Inside(output, hash.Name);
                if (!File.Exists(file) || MapBundleDocument.HashFile(file) != hash.Value.Value<string>()) return false;
            }
            asset = (JObject)candidate.DeepClone();
            return true;
        }
        catch (Exception exception) when (exception is IOException or JsonException or InvalidDataException or ArgumentException)
        {
            return false;
        }
    }

    internal static void Save(string output, string package, string revision, JObject dependencies, JObject asset)
    {
        if (!dependencies.HasValues) return;
        var hashes = new JObject(asset["files"]!.Values<string>().Select(file => new JProperty(file!,
            MapBundleDocument.HashFile(MapBundleDocument.Inside(output, file!)))));
        var sealedData = new JObject { ["asset"] = asset.DeepClone(), ["dependencies"] = dependencies.DeepClone(), ["hashes"] = hashes.DeepClone() };
        MapBundleDocument.WriteAtomic(output, Receipt(package), MapBundleDocument.Bytes(new JObject
        {
            ["package"] = package, ["sourceRevision"] = revision, ["asset"] = asset.DeepClone(),
            ["dependencies"] = dependencies.DeepClone(), ["hashes"] = hashes,
            ["digest"] = MapBundleDocument.Hash(MapBundleDocument.Bytes(sealedData))
        }));
    }

    internal static JObject Capture(AbstractVfsFileProvider provider, IPackage root)
    {
        var hashes = new JObject();
        var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var pending = new Queue<IPackage>();
        pending.Enqueue(root);
        while (pending.Count > 0)
        {
            var package = pending.Dequeue();
            var canonical = MapBundleDocument.CanonicalPackage(package.Name);
            if (!visited.Add(canonical)) continue;
            if (visited.Count > 512) throw new InvalidDataException("Dependency receipt closure exceeds bounded package limit");
            var parts = canonical.TrimStart('/').Split('/', 2);
            var remapped = parts[0] == "Game" ? "RemappedPlugins/Content/" + parts[1] :
                "RemappedPlugins/" + parts[0] + "/Content/" + parts[1];
            if ((!provider.TryGetGameFile(package.Name, out var source) && !provider.TryGetGameFile(canonical, out source) &&
                !provider.TryGetGameFile(remapped, out source)) || !source.IsUePackage)
                throw new InvalidDataException("Dependency source unavailable: " + canonical);
            provider.Files.FindPayloads(source, out var uexp, out var ubulks, out var uptnls);
            foreach (var file in new[] { source, uexp }.Where(file => file != null).Concat(ubulks).Concat(uptnls))
                hashes[file!.Path] = MapBundleDocument.Hash(file.Read());
            for (var index = 1; index <= package.ImportMapLength; index++)
            {
                if (package is IoPackage cooked && (cooked.ImportMap[index - 1].IsNull || cooked.ImportMap[index - 1].IsScriptImport))
                    continue;
                var reference = package.ResolvePackageIndex(new FPackageIndex(package, -index));
                if (reference == null) throw new InvalidDataException("Dependency import unresolved in " + canonical);
                var path = reference.GetPathName();
                if (path.StartsWith("/Script/", StringComparison.Ordinal) || path.StartsWith("/Engine/Transient", StringComparison.Ordinal)) continue;
                var dependency = MapBundleDocument.CanonicalPackage(path);
                if (package is IoPackage io && io.ImportMap[index - 1].IsPackageImport && io.ImportedPublicExportHashes != null)
                {
                    var importedIndex = io.ImportMap[index - 1].AsPackageImportRef.ImportedPackageIndex;
                    var imported = io.ImportedPackages.Value;
                    if (importedIndex >= imported.Length || !ReferenceEquals(reference.Package, imported[importedIndex]))
                        throw new InvalidDataException("Historical dependency version cannot be source-fingerprinted safely: " + dependency);
                }
                if (!visited.Contains(dependency))
                    pending.Enqueue(package is IoPackage ? reference.Package : Program.FindPackage(provider, dependency) ??
                        throw new InvalidDataException("Dependency package unavailable: " + dependency));
            }
        }
        return hashes;
    }
}
