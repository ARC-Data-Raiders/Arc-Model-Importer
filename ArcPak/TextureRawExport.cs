using System.Text;
using CUE4Parse.UE4.Assets.Exports.Texture;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace ArcPak;

internal static class TextureRawExport
{
    internal static int Run(string[] args)
    {
        try
        {
            var o = ArcVerbCommon.Parse(args, new[] { "game-dir", "usmap", "out", "texture", "texture-file" }, Array.Empty<string>());
            var output = ArcVerbCommon.Required(o, "out");
            using var provider = ArcVerbCommon.Open(ArcVerbCommon.Required(o, "game-dir"), ArcVerbCommon.Required(o, "usmap"), false);
            var paths = o.TryGetValue("texture", out var direct) ? new List<string>(direct) : new List<string>();
            if (o.TryGetValue("texture-file", out var files)) foreach (var file in files) paths.AddRange(ArcVerbCommon.ReadList(file));
            if (paths.Count == 0) throw new ArgumentException("At least one texture is required");
            var manifest = new JArray();
            foreach (var path in paths.OrderBy(p => p, StringComparer.Ordinal))
            {
                var pkg = Program.FindPackage(provider, path);
                var textures = pkg?.GetExports().OfType<UTexture>().Where(t => t.Name.Equals(path.Split('/').Last(), StringComparison.OrdinalIgnoreCase)).ToArray();
                var reason = textures is not { Length: 1 } ? "identity" : Export(textures[0], path, output);
                if (reason != null) WriteJson(Path.Combine(output, path.TrimStart('/') + ".unresolved.json"), new JObject { ["object_path"] = path, ["reason"] = reason });
                manifest.Add(new JObject { ["object_path"] = path, ["status"] = reason == null ? "exported" : "unresolved", ["reason"] = reason });
            }
            WriteJson(Path.Combine(output, "texture-raw.manifest.json"), manifest);
            return 0;
        }
        catch (ArgumentException e)
        {
            Log.Error(e.Message);
            return 1;
        }
    }

    private static string? Export(UTexture tex, string path, string output)
    {
        string kind;
        if (tex is UTexture2DArray) kind = "array";
        else if (tex is UTextureCubeArray) return "cube_array";
        else if (tex is UTextureCube) kind = "cube";
        else if (tex is UVolumeTexture) kind = "volume";
        else if (tex is UTexture2D) kind = "2d";
        else return "kind:" + tex.ExportType;
        var pd = tex.PlatformData;
        if (pd.VTData is { } vt && vt.IsInitialized()) return "virtual_texture";
        var formats = new Dictionary<string, (int Dxgi, int Bytes, bool Block)>
        {
            ["PF_DXT1"] = (71, 8, true), ["PF_DXT3"] = (74, 16, true), ["PF_DXT5"] = (77, 16, true), ["PF_BC4"] = (80, 8, true), ["PF_BC5"] = (83, 16, true), ["PF_BC6H"] = (95, 16, true), ["PF_BC7"] = (98, 16, true),
            ["PF_B8G8R8A8"] = (87, 4, false), ["PF_R8G8B8A8"] = (28, 4, false), ["PF_G8"] = (61, 1, false), ["PF_G16"] = (56, 2, false), ["PF_R8G8"] = (49, 2, false), ["PF_R16F"] = (54, 2, false), ["PF_R32_FLOAT"] = (41, 4, false), ["PF_FloatRGBA"] = (10, 8, false), ["PF_A16B16G16R16"] = (11, 8, false), ["PF_A32B32G32R32F"] = (2, 16, false)
        };
        if (!formats.TryGetValue(pd.PixelFormat, out var fmt)) return "unmapped_pixel_format:" + pd.PixelFormat;
        var first = tex.GetFirstMipIndex();
        if (first < 0) return "no_valid_mips";
        var mips = new List<(FTexture2DMipMap Mip, byte[] Data)>();
        var metadata = new JArray();
        for (var i = first; i < pd.Mips.Length; i++)
        {
            var mip = tex.GetMip(i);
            var data = mip?.BulkData?.Data;
            if (data == null) return "mip_unavailable:" + i;
            var mipLayers = kind == "cube" ? 6 : kind is "array" or "volume" ? mip!.SizeZ : 1;
            var expected = fmt.Block ? Math.Max(1, (mip!.SizeX + 3) / 4) * Math.Max(1, (mip.SizeY + 3) / 4) * fmt.Bytes * mipLayers : mip!.SizeX * mip.SizeY * fmt.Bytes * mipLayers;
            if (data.Length != expected) return $"mip_size_mismatch:{i}:{data.Length}:{expected}";
            mips.Add((mip, data));
            metadata.Add(new JObject { ["index"] = i, ["size_x"] = mip.SizeX, ["size_y"] = mip.SizeY, ["size_z"] = mip.SizeZ, ["bytes"] = data.Length, ["sha256"] = ArcVerbCommon.Sha256(data) });
        }
        var top = mips[0].Mip;
        var layers = kind == "cube" ? 6 : kind is "array" or "volume" ? top.SizeZ : 1;
        using var stream = new MemoryStream();
        using (var w = new BinaryWriter(stream, Encoding.UTF8, true))
        {
            w.Write(Encoding.ASCII.GetBytes("DDS "));
            var flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | (fmt.Block ? 0x80000 : 0x8) | (kind == "volume" ? 0x800000 : 0);
            foreach (var v in new[] { 124, flags, top.SizeY, top.SizeX, fmt.Block ? mips[0].Data.Length / layers : top.SizeX * fmt.Bytes, kind == "volume" ? layers : 0, mips.Count }) w.Write(v);
            for (var i = 0; i < 11; i++) w.Write(0);
            foreach (var v in new[] { 32, 0x4, 0x30315844, 0, 0, 0, 0, 0 }) w.Write(v);
            foreach (var v in new[] { 0x1000 | (mips.Count > 1 ? 0x400008 : 0) | (kind is "cube" or "volume" or "array" ? 0x8 : 0), kind == "cube" ? 0xFE00 : kind == "volume" ? 0x200000 : 0, 0, 0, 0, fmt.Dxgi, kind == "volume" ? 4 : 3, kind == "cube" ? 0x4 : 0, kind == "array" ? layers : 1, 0 }) w.Write(v);
            if (kind == "volume") foreach (var mip in mips) w.Write(mip.Data);
            else for (var e = 0; e < layers; e++) foreach (var mip in mips) w.Write(mip.Data, e * (mip.Data.Length / layers), mip.Data.Length / layers);
        }
        var dds = stream.ToArray();
        var relative = path.TrimStart('/') + ".dds";
        ArcVerbCommon.WriteAtomic(Path.Combine(output, relative), dds);
        var tags = new JObject();
        foreach (var name in new[] { "SRGB", "CompressionSettings", "LODGroup", "Filter", "AddressX", "AddressY", "AddressZ", "LODBias", "MipGenSettings", "VirtualTextureStreaming", "NeverStream" })
        {
            var tag = tex.Properties.FirstOrDefault(t => t.Name.Text == name);
            tags[name] = tag?.Tag != null ? JToken.FromObject(tag.Tag) : JValue.CreateNull();
        }
        WriteJson(Path.Combine(output, path.TrimStart('/') + ".raw.json"), new JObject { ["schema"] = "dr.texture-raw/1", ["object_path"] = path, ["class"] = tex.ExportType, ["kind"] = kind, ["pixel_format"] = pd.PixelFormat, ["dxgi_format"] = fmt.Dxgi, ["tags"] = tags, ["size_x"] = top.SizeX, ["size_y"] = top.SizeY, ["layers"] = layers, ["first_mip_index"] = first, ["mip_count"] = mips.Count, ["mips"] = metadata, ["dds_file"] = relative, ["dds_sha256"] = ArcVerbCommon.Sha256(dds) });
        return null;
    }

    private static void WriteJson(string path, JToken value) => ArcVerbCommon.WriteAtomic(path, Encoding.UTF8.GetBytes(value.ToString(Formatting.Indented)));
}
