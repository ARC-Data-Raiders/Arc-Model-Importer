using CUE4Parse.UE4.Assets.Exports.Texture;

namespace ArcPak;

/// <summary>
/// CUE4Parse decodes a texture from its first mip whose bulk data loads. When the top
/// mips do not resolve, the PNG is written at a lower mip and nothing else reports it.
/// That happened with ArcPak builds made before CUE4Parse f059f95b: they read the owner
/// package's bulk data map for exports in optional segments, so the 2048 to 128 mips of
/// some outfit textures failed to load and the cache got 64 px PNGs.
/// </summary>
internal static class TextureMipGuard
{
    /// <returns>null when mip 0 loads, else a reason to log as an export failure.</returns>
    internal static string? TopMipMissing(UTexture texture)
    {
        if (texture.PlatformData is not { Mips.Length: > 0 } data)
            return null;
        if (data.VTData is { } vt && vt.IsInitialized())
            return null;
        var first = texture.GetFirstMipIndex();
        if (first == 0)
            return null;
        var top = data.Mips[0];
        if (first < 0)
            return $"no mip has bulk data (mip 0 is {top.SizeX}x{top.SizeY})";
        var loaded = data.Mips[first];
        return $"mip 0 ({top.SizeX}x{top.SizeY}) has no bulk data; the PNG would come from mip {first} ({loaded.SizeX}x{loaded.SizeY})";
    }
}
