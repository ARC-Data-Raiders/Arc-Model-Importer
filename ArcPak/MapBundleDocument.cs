using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.IO.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using FModel.Views.Snooper.MapPlacement;
using FModel.Exporters.MapPlacement;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapBundleDocument
{
    internal const string ProducerVersion = "arcpak-map-bundle-v1";

    internal static string CanonicalPackage(string value)
    {
        var path = value.Trim().Replace('\\', '/');
        var quote = path.IndexOf('\'');
        if (quote >= 0) path = path[(quote + 1)..].TrimEnd('\'');
        if (path.Split('/').Any(segment => segment is "." or ".."))
            throw new InvalidDataException("Invalid package path: " + value);
        var spline = path.IndexOf("#spline:", StringComparison.OrdinalIgnoreCase);
        if (spline >= 0) path = path[..spline];
        var dot = path.IndexOf('.');
        if (dot >= 0 && !path[dot..].StartsWith("..", StringComparison.Ordinal)) path = path[..dot];
        path = path.Trim('/');
        var parts = path.Split('/');
        if (parts.Length < 2 || parts.Any(part => part.Length == 0 || part is "." or ".." || part.IndexOfAny([':', '\0']) >= 0))
            throw new InvalidDataException("Invalid package path: " + value);
        foreach (var prefix in new[] { "PioneerGame/Content/", "RemappedPlugins/Content/", "Game/" })
            if (path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return "/Game/" + path[prefix.Length..];
        if (path.StartsWith("Engine/Content/", StringComparison.OrdinalIgnoreCase)) return "/Engine/" + path["Engine/Content/".Length..];
        if (parts.Length > 3 && parts[0].Equals("RemappedPlugins", StringComparison.OrdinalIgnoreCase) && parts[2].Equals("Content", StringComparison.OrdinalIgnoreCase))
            return "/" + parts[1] + "/" + string.Join('/', parts.Skip(3));
        return "/" + path;
    }

    internal static string CanonicalObject(string value)
    {
        var exact = ExactObjectPath(value);
        var package = CanonicalPackage(exact);
        return exact.Equals(package + "." + package.Split('/').Last(), StringComparison.OrdinalIgnoreCase) ? package : exact;
    }

    internal static string ExactObjectPath(string value)
    {
        var path = value.Trim().Replace('\\', '/');
        var quote = path.IndexOf('\'');
        if (quote >= 0) path = path[(quote + 1)..].TrimEnd('\'');
        var package = CanonicalPackage(path);
        var dot = path.IndexOf('.');
        var suffix = dot < 0 ? "" : path[(dot + 1)..];
        if (suffix.Equals("uasset", StringComparison.OrdinalIgnoreCase) || suffix.Equals("umap", StringComparison.OrdinalIgnoreCase)) suffix = "";
        if (suffix.Length == 0) suffix = package.Split('/').Last();
        if (suffix.Split(['.', ':']).Any(part => part.Length == 0 || part.IndexOfAny(['/', '\0', '\'', '#']) >= 0))
            throw new InvalidDataException("Invalid object path: " + value);
        return package + "." + suffix;
    }

    internal static string Inside(string root, string relative)
    {
        if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative) || relative.Contains(':'))
            throw new InvalidDataException("Output path must be relative: " + relative);
        var segments = relative.Replace('\\', '/').Split('/');
        if (segments.Any(segment => segment is ".." or "." || segment.Length == 0))
            throw new InvalidDataException("Output path contains traversal: " + relative);
        var absoluteRoot = Path.GetFullPath(root);
        var absolute = Path.GetFullPath(Path.Combine(absoluteRoot, relative));
        if (!absolute.StartsWith(Path.TrimEndingDirectorySeparator(absoluteRoot) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Output path escapes root: " + relative);
        for (var directory = new DirectoryInfo(Path.GetDirectoryName(absolute)!); directory != null; directory = directory.Parent)
        {
            if (directory.Exists && directory.Attributes.HasFlag(FileAttributes.ReparsePoint))
                throw new InvalidDataException("Output path crosses a reparse point: " + relative);
            if (directory.FullName.Equals(absoluteRoot, StringComparison.OrdinalIgnoreCase)) break;
        }
        if (File.Exists(absolute) && File.GetAttributes(absolute).HasFlag(FileAttributes.ReparsePoint))
            throw new InvalidDataException("Output file is a reparse point: " + relative);
        return absolute;
    }

    internal static JArray Matrix(float positionX, float positionY, float positionZ, float pitch, float yaw, float roll, float scaleX, float scaleY, float scaleZ)
        => Matrix(new FTransform(new FRotator(pitch, yaw, roll), new FVector(positionX, positionY, positionZ), new FVector(scaleX, scaleY, scaleZ)));

    internal static JArray Matrix(FTransform transform)
    {
        var matrix = transform.ToMatrixWithScale();
        float[] values = [matrix.M00, matrix.M10, matrix.M20, matrix.M30,
            matrix.M01, matrix.M11, matrix.M21, matrix.M31,
            matrix.M02, matrix.M12, matrix.M22, matrix.M32,
            matrix.M03, matrix.M13, matrix.M23, matrix.M33];
        if (values.Any(value => !float.IsFinite(value))) throw new InvalidDataException("Non-finite placement transform");
        return new JArray(values);
    }

    internal static JObject Placement(MapPlacementRecord record, JObject raw)
    {
        if (string.IsNullOrWhiteSpace(record.PlacementId)) throw new InvalidDataException("Placement has no stable component identity");
        return new JObject
        {
            ["id"] = record.PlacementId,
            ["asset"] = string.IsNullOrEmpty(record.AssetPath) ? "" : CanonicalPackage(record.AssetPath),
            ["kind"] = record.AssetKind,
            ["matrix"] = Matrix(record.X, record.Y, record.Z, record.Pitch, record.Yaw, record.Roll, record.ScaleX, record.ScaleY, record.ScaleZ),
            ["materials"] = new JArray(record.MaterialOverrides.Select(path => string.IsNullOrEmpty(path) ? "" : CanonicalObject(path))),
            ["materialObjects"] = new JArray(record.MaterialOverrides.Select(path => string.IsNullOrEmpty(path) ? "" : CanonicalObject(path))),
            ["materialPackages"] = new JArray(record.MaterialOverrides.Select(path => string.IsNullOrEmpty(path) ? "" : CanonicalPackage(path))),
            ["properties"] = new JObject
            {
                ["actorName"] = record.ActorName, ["componentPath"] = record.ComponentPath,
                ["sourceAssetKey"] = record.AssetPath,
                ["sourceFile"] = record.SourceFile, ["worldInstancePath"] = record.WorldInstancePath,
                ["instanceIndex"] = record.InstanceIndex, ["notes"] = record.Notes,
                ["raw"] = raw.DeepClone()
            }
        };
    }

    internal static JObject Actor(MapTraversalObject item, JObject raw) => new()
    {
        ["id"] = item.Id, ["kind"] = item.Source?.ExportType ?? "UnresolvedActor",
        ["matrix"] = Matrix(item.TransformCm ?? FTransform.Identity),
        ["parentId"] = item.ParentId, ["actorId"] = item.ActorId,
        ["properties"] = new JObject
        {
            ["sourcePath"] = item.SourcePath, ["sourceFile"] = item.WorldPath,
            ["worldInstancePath"] = item.WorldInstancePath, ["parentPath"] = item.ParentPath,
            ["actorPath"] = item.ActorPath, ["isActor"] = item.SourcePath == item.ActorPath,
            ["transformKnown"] = item.TransformCm != null, ["transformIssue"] = item.TransformIssue,
            ["standin"] = true, ["raw"] = raw.DeepClone()
        }
    };

    internal static JToken Ordered(JToken token) => token switch
    {
        JObject obj => new JObject(obj.Properties().OrderBy(property => property.Name, StringComparer.Ordinal)
            .Select(property => new JProperty(property.Name, Ordered(property.Value)))),
        JArray array => new JArray(array.Select(Ordered)),
        _ => token.DeepClone()
    };

    internal static byte[] Bytes(JToken token) => Encoding.UTF8.GetBytes(Ordered(token).ToString(Formatting.Indented) + "\n");
    internal static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    internal static string HashSource(GameFile file)
    {
        if (file is FIoStoreEntry entry)
        {
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            entry.IoStoreReader.ReadToSink(entry, bytes => hash.AppendData(bytes));
            return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
        }

        return Hash(file.Read());
    }
    internal static string HashFile(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    internal static void WriteAtomic(string root, string relative, byte[] bytes)
    {
        var destination = Inside(root, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        var temporary = destination + "." + Guid.NewGuid().ToString("N", CultureInfo.InvariantCulture) + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                stream.Write(bytes);
                stream.Flush(true);
            }
            File.Move(temporary, destination, true);
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }

    internal static JObject PublishAsset(string stagingRoot, string output, string package, string sourceRevision, string[] materials,
        string? objectPath = null, string[]? materialObjects = null, string? materialFile = null,
        JObject? derivedGeometry = null, JArray? materialSlots = null, JArray? textureDependencies = null)
    {
        var sourceFiles = Directory.EnumerateFiles(stagingRoot, "*", SearchOption.AllDirectories)
            .Select(path => Path.GetRelativePath(stagingRoot, path).Replace('\\', '/')).Order(StringComparer.Ordinal).ToArray();
        if (sourceFiles.Length == 0) throw new InvalidDataException("Asset export wrote no files: " + package);
        var hashes = new JObject(sourceFiles.Select(relative => new JProperty(relative, HashFile(Inside(stagingRoot, relative)))));
        var fingerprint = Hash(Bytes(new JObject
        {
            ["producer"] = ProducerVersion, ["package"] = package, ["sourceRevision"] = sourceRevision,
            ["materials"] = new JArray(materials), ["files"] = hashes,
            ["objectPath"] = objectPath, ["materialObjects"] = new JArray(materialObjects ?? []), ["materialFile"] = materialFile,
            ["derivedGeometry"] = derivedGeometry, ["materialSlots"] = materialSlots, ["textureDependencies"] = textureDependencies
        }));
        var prefix = "assets/" + fingerprint + "/";
        var files = sourceFiles.Select(relative => prefix + relative).ToArray();
        var meshes = files.Where(path => path.EndsWith(".uemodel", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".pskx", StringComparison.OrdinalIgnoreCase)).ToArray();
        var result = new JObject
        {
            ["fingerprint"] = fingerprint,
            ["materials"] = new JArray((materialObjects ?? materials).Where(path => path.Length > 0).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal)),
            ["materialPackages"] = new JArray(materials), ["files"] = new JArray(files),
            ["meshFiles"] = new JArray(meshes),
            ["materialObjects"] = new JArray(materialObjects ?? [])
        };
        if (objectPath != null) result["objectPath"] = objectPath;
        if (materialSlots != null) result["materialSlots"] = materialSlots.DeepClone();
        if (textureDependencies != null) result["textureDependencies"] = textureDependencies.DeepClone();
        if (derivedGeometry != null)
        {
            var binding = (JObject)derivedGeometry.DeepClone();
            foreach (var physical in binding["outputs"]!.OfType<JObject>())
            {
                var relative = physical.Value<string>("file")!;
                if (!sourceFiles.Contains(relative, StringComparer.Ordinal) || physical.Value<string>("sha256") != hashes.Value<string>(relative))
                    throw new InvalidDataException("Derived output is not a hash-verified staged file");
                physical["file"] = prefix + relative;
            }
            result["derivedGeometry"] = binding;
        }
        if (materialFile != null)
        {
            if (!sourceFiles.Contains(materialFile, StringComparer.Ordinal)) throw new InvalidDataException("Exact material JSON is absent: " + materialFile);
            result["material"] = prefix + materialFile;
        }
        if (meshes.Length > 0) result["mesh"] = meshes[0];
        var receiptPath = Inside(output, "receipts/" + fingerprint + ".json");
        if (File.Exists(receiptPath))
        {
            try
            {
                var receipt = JObject.Parse(File.ReadAllText(receiptPath));
                if ((string?)receipt["package"] == package && (string?)receipt["sourceRevision"] == sourceRevision &&
                    JToken.DeepEquals(receipt["hashes"], hashes) && receipt["asset"] is JObject cached && JToken.DeepEquals(cached, result) &&
                    sourceFiles.All(relative => File.Exists(Inside(output, prefix + relative)) &&
                        HashFile(Inside(output, prefix + relative)) == (string?)hashes[relative]))
                {
                    return (JObject)cached.DeepClone();
                }
            }
            catch (Exception exception) when (exception is JsonException or IOException or InvalidDataException)
            {
            }
        }
        foreach (var relative in sourceFiles)
        {
            var destination = Inside(output, prefix + relative);
            if (File.Exists(destination) && HashFile(destination) == (string?)hashes[relative]) continue;
            WriteAtomic(output, prefix + relative, File.ReadAllBytes(Inside(stagingRoot, relative)));
        }
        WriteAtomic(output, "receipts/" + fingerprint + ".json", Bytes(new JObject
        {
            ["package"] = package, ["sourceRevision"] = sourceRevision, ["asset"] = result.DeepClone(), ["hashes"] = hashes
        }));
        return result;
    }
}


