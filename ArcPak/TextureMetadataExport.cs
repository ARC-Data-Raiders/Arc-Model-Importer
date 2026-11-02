using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse_Conversion.Exporters;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System.Text.Json.Nodes;

namespace ArcPak;

internal static class TextureMetadataExport
{
    private static readonly Lazy<JsonObject> ProducerIdentity = new(() => new JsonObject {
        ["native_parser"] = AssemblyIdentity(typeof(UTexture).Assembly),
        ["texture_conversion"] = AssemblyIdentity(typeof(TextureExporter).Assembly),
        ["metadata_cli"] = AssemblyIdentity(typeof(TextureMetadataExport).Assembly)
    });

    private static JsonObject AssemblyIdentity(System.Reflection.Assembly assembly)
    {
        if (string.IsNullOrWhiteSpace(assembly.Location) || !File.Exists(assembly.Location))
            throw new InvalidOperationException("Cannot prove loaded producer assembly bytes: " + assembly.FullName);
        return new JsonObject { ["assembly_name"] = assembly.GetName().Name,
            ["sha256"] = TextureMetadataDocument.Hash(File.ReadAllBytes(assembly.Location)) };
    }

    internal static JsonObject ProducerSnapshot() => (JsonObject)ProducerIdentity.Value.DeepClone();
    internal static string MetadataPath(UTexture texture,string outputRoot) =>
        TextureMetadataDocument.SidecarPath(outputRoot,new TextureExporter(texture).SavePath);

    internal static string NativeKind(UTexture texture) => texture switch {
        UTexture2DArray => "Texture2DArray",
        UTextureCubeArray => "TextureCubeArray",
        UTextureCube => "TextureCube",
        UVolumeTexture => "VolumeTexture",
        UTexture2D => "Texture2D",
        _ => "Texture" // The parameter is an actual UTexture, never a raw Type string.
    };

    internal static bool Write(UTexture texture, string outputRoot, string build,
        string depotManifest, string mappingSha256)
    {
        // Serialize only selected ACTUAL property tags. UTexture.SRGB/Filter and
        // address accessors apply library defaults when the native tag is absent;
        // those fallback values must not be published as serialized game facts.
        var properties = new JsonObject();
        foreach (var tag in texture.Properties)
        {
            var name = tag.Name.Text;
            if (!TextureMetadataDocument.IsPropertyAllowed(name)) continue;
            var key = tag.ArrayIndex > 0 ? name + "[" + tag.ArrayIndex + "]" : name;
            properties[key] = tag.Tag is null ? null : JsonNode.Parse(JToken.FromObject(tag.Tag).ToString(Formatting.None));
        }
        var platform = texture.PlatformData;
        var mips = platform.Mips.Select(mip => new TextureMetadataDocument.Mip(mip.SizeX, mip.SizeY, mip.SizeZ)).ToArray();
        // Exporter SavePath is the actual same-package PNG location, including
        // RemappedPlugins and mounted-plugin physical prefixes and subobjects.
        var exporter = new TextureExporter(texture);
        var physical = exporter.PackagePath;
        var logical = LogicalPackage(physical);
        var facts = new TextureMetadataDocument.Facts(texture.ExportType, texture.Name, logical,
            platform.SizeX, platform.SizeY, platform.PackedData, platform.PixelFormat,
            platform.FirstMipToSerialize, mips, properties, NativeKind(texture));
        var source = new TextureMetadataDocument.Source(build, depotManifest, mappingSha256,
            texture.GetPathName(), physical, ProducerIdentity.Value);
        var document = TextureMetadataDocument.Capture(facts, source);
        return TextureMetadataDocument.WriteAtomic(TextureMetadataDocument.SidecarPath(outputRoot, exporter.SavePath), document);
    }

    internal static string LogicalPackage(string physical)
    {
        var path = physical.Replace('\\', '/').Trim('/');
        foreach (var prefix in new[] { "PioneerGame/Content/", "RemappedPlugins/Content/", "Game/" })
            if (path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return "/Game/" + path[prefix.Length..];
        if (path.StartsWith("Engine/Content/", StringComparison.OrdinalIgnoreCase)) return "/Engine/" + path["Engine/Content/".Length..];
        if (path.StartsWith("RemappedPlugins/", StringComparison.OrdinalIgnoreCase))
        {
            var parts = path.Split('/');
            if (parts.Length > 3 && parts[2].Equals("Content", StringComparison.OrdinalIgnoreCase))
                return "/" + parts[1] + "/" + string.Join('/', parts.Skip(3));
        }
        // A mounted plugin's exporter path is already its logical namespace, with or without the leading slash
        // (EmbarkScript/MaterialLibrary/Character/Templates/Textures/TA_Character_Colors).
        if (!path.Contains("/Content/", StringComparison.OrdinalIgnoreCase) && path.Contains('/')) return "/" + path;
        throw new ArgumentException("Unproven native texture mount " + physical);
    }
}
