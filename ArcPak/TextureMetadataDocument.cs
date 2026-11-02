using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ArcPak;

// Native fact projection and atomic storage are separate from CUE4Parse and PNG
// conversion so their invariants can be tested without mounting game packages.
internal static class TextureMetadataDocument
{
    internal const string Projection = "native-texture-sidecar-v1";
    private static readonly HashSet<string> NativeKinds = new(StringComparer.Ordinal) { "Texture", "Texture2D", "Texture2DArray", "TextureCube", "TextureCubeArray", "VolumeTexture" };
    private static readonly HashSet<string> NativeProperties = new(StringComparer.Ordinal) {
        "SRGB", "Filter", "AddressX", "AddressY", "AddressZ", "CompressionSettings",
        "LODGroup", "NeverStream", "VirtualTextureStreaming", "SourceTextures"
    };
    internal static bool IsPropertyAllowed(string name) => NativeProperties.Contains(name.Split('[')[0]);
    internal readonly record struct Mip(int SizeX, int SizeY, int SizeZ);
    internal sealed record Facts(string Type, string Name, string Package,
        int SizeX, int SizeY, uint PackedData, string PixelFormat,
        int FirstMipToSerialize, IReadOnlyList<Mip> Mips, JsonObject Properties, string? NativeTextureKind = null);
    internal sealed record Source(string Build, string DepotManifest,
        string MappingSha256, string ObjectPath, string PhysicalPackagePath, JsonObject? ProducerAssemblies = null);

    internal static JsonObject Capture(Facts facts, Source source)
    {
        // The typed native adapter supplies the runtime UTexture inheritance
        // kind. Pure/legacy fixtures may omit it only for exact known base kinds;
        // arbitrary concrete strings never become textures through a prefix.
        var nativeKind = facts.NativeTextureKind ?? (NativeKinds.Contains(facts.Type) ? facts.Type : null);
        if (string.IsNullOrWhiteSpace(facts.Type) || string.IsNullOrWhiteSpace(facts.Name) ||
            nativeKind is null || !NativeKinds.Contains(nativeKind) ||
            !facts.Package.StartsWith('/') || facts.Package.Split('/').Any(x => x is "." or ".."))
            throw new ArgumentException("Exact native texture identity and runtime UTexture kind are required");
        var properties = new JsonObject();
        foreach (var pair in facts.Properties.Where(x => IsPropertyAllowed(x.Key)).OrderBy(x => x.Key, StringComparer.Ordinal))
            properties[pair.Key] = pair.Value?.DeepClone();
        var mips = new JsonArray();
        foreach (var mip in facts.Mips)
        {
            if (mip.SizeX < 0 || mip.SizeY < 0 || mip.SizeZ < 0) throw new ArgumentException("Negative native mip dimension");
            mips.Add(new JsonObject { ["SizeX"] = mip.SizeX, ["SizeY"] = mip.SizeY, ["SizeZ"] = mip.SizeZ });
        }
        var result = new JsonObject {
            ["Type"] = facts.Type, ["NativeTextureKind"] = nativeKind, ["Name"] = facts.Name, ["Package"] = facts.Package,
            ["Properties"] = properties, ["SizeX"] = facts.SizeX, ["SizeY"] = facts.SizeY,
            ["PackedData"] = facts.PackedData, ["PixelFormat"] = facts.PixelFormat,
            ["FirstMipToSerialize"] = facts.FirstMipToSerialize, ["Mips"] = mips
        };
        if (nativeKind == "Texture2DArray")
        {
            var depths = facts.Mips.Select(x => x.SizeZ).Distinct().ToArray();
            if (depths.Length == 1 && depths[0] > 0) result["ArrayLayerCount"] = depths[0];
            else result["MetadataDiagnostics"] = new JsonArray("native array depth unavailable or inconsistent across platform mips");
        }
        // This hashes projected native FACTS, not a claim about original pak bytes.
        var factsHash = Hash(Encoding.UTF8.GetBytes(result.ToJsonString()));
        result["DataRaidersSource"] = new JsonObject {
            ["projection"] = Projection, ["schema_version"] = 1,
            ["build"] = source.Build, ["depot_manifest"] = source.DepotManifest,
            ["mapping_sha256"] = source.MappingSha256, ["object_path"] = source.ObjectPath,
            ["physical_package_path"] = source.PhysicalPackagePath, ["facts_sha256"] = factsHash,
            ["facts_hash_algorithm"] = "sha256 compact System.Text.Json projection before DataRaidersSource",
            ["producer_assemblies"] = source.ProducerAssemblies?.DeepClone()
        };
        return result;
    }

    internal static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    internal static string MappingHash(string? path) => string.IsNullOrWhiteSpace(path) ? "" : Hash(File.ReadAllBytes(path));
    internal static string SidecarPath(string outputRoot, string nativeSavePath)
    {
        if (Path.IsPathRooted(nativeSavePath)) throw new ArgumentException("Texture save path must be relative");
        var root = Path.GetFullPath(outputRoot);
        var file = Path.GetFullPath(Path.Combine(root, nativeSavePath.Replace('/', Path.DirectorySeparatorChar) + ".arc-texture.json"));
        var relative = Path.GetRelativePath(root, file);
        if (relative == ".." || relative.StartsWith(".." + Path.DirectorySeparatorChar) || Path.IsPathRooted(relative))
            throw new ArgumentException("Native texture path escaped output root");
        return file;
    }

    private static readonly object[] WriteLocks = Enumerable.Range(0, 64).Select(_ => new object()).ToArray();
    internal static bool WriteAtomic(string destination, JsonObject document)
    {
        destination = Path.GetFullPath(destination);
        var gate = WriteLocks[(StringComparer.OrdinalIgnoreCase.GetHashCode(destination) & int.MaxValue) % WriteLocks.Length];
        lock (gate) return WriteAtomicLocked(destination, document);
    }

    private static bool WriteAtomicLocked(string destination, JsonObject document)
    {
        var bytes = Encoding.UTF8.GetBytes(document.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n");
        if (File.Exists(destination) && File.ReadAllBytes(destination).AsSpan().SequenceEqual(bytes)) return false;
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        var temporary = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            { stream.Write(bytes); stream.Flush(flushToDisk: true); }
            File.Move(temporary, destination, overwrite: true);
            return true;
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
