using System.Buffers.Binary;
using System.IO.Compression;
using System.Numerics;

namespace CUE4Parse_Conversion.Textures;

// fpng-style fast PNG writer for 8-bit Gray/RGBA: one Up filter per row,
// zlib Fastest deflate, single IDAT. Decoded pixels equal the Skia encoder output.
public static class FastPng
{
    // ARCPAK_FAST_PNG=0 restores the Skia encoder.
    public static readonly bool Enabled = Environment.GetEnvironmentVariable("ARCPAK_FAST_PNG") != "0";

    public static byte[] Encode(ReadOnlySpan<byte> pixels, int width, int height, int channels, bool bgra)
    {
        if (channels is not (1 or 4)) throw new ArgumentOutOfRangeException(nameof(channels));
        var stride = width * channels;
        if (pixels.Length < (long)stride * height) throw new ArgumentException("pixel buffer too small");
        var raw = new byte[(stride + 1) * height];
        var rowA = new byte[stride];
        var rowB = new byte[stride];
        var prev = rowA; var cur = rowB; byte[]? scratch = null;
        for (var y = 0; y < height; y++)
        {
            var src = pixels.Slice(y * stride, stride);
            if (bgra) Swizzle(src, cur); else src.CopyTo(cur);
            var dst = raw.AsSpan(y * (stride + 1) + 1, stride);
            if (y == 0) { raw[y * (stride + 1)] = 1; SubFilter(cur, dst, channels); }
            else
            {
                // Pick Sub or Up per row by smallest sum of signed residues (libpng heuristic, two filters like Skia level 3).
                Subtract(cur, prev, dst);
                if (Adaptive)
                {
                    var up = Cost(dst);
                    SubFilter(cur, scratch ??= new byte[stride], channels);
                    if (Cost(scratch) < up) { scratch.CopyTo(dst); raw[y * (stride + 1)] = 1; }
                    else raw[y * (stride + 1)] = 2;
                }
                else raw[y * (stride + 1)] = 2;
            }
            (prev, cur) = (cur, prev);
        }

        using var output = new MemoryStream(raw.Length / 2 + 128);
        output.Write([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
        Span<byte> ihdr = stackalloc byte[13];
        BinaryPrimitives.WriteInt32BigEndian(ihdr, width);
        BinaryPrimitives.WriteInt32BigEndian(ihdr[4..], height);
        ihdr[8] = 8; ihdr[9] = (byte)(channels == 1 ? 0 : 6); ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
        WriteChunk(output, "IHDR"u8, ihdr);
        using (var compressed = new MemoryStream(raw.Length / 2 + 64))
        {
            using (var zlib = new ZLibStream(compressed, new ZLibCompressionOptions { CompressionLevel = Level, CompressionStrategy = ZLibCompressionStrategy.Default }, leaveOpen: true))
                zlib.Write(raw);
            WriteChunk(output, "IDAT"u8, compressed.GetBuffer().AsSpan(0, (int)compressed.Length));
        }
        WriteChunk(output, "IEND"u8, ReadOnlySpan<byte>.Empty);
        return output.ToArray();
    }

    public static bool Adaptive = false;
    public static int Level = 2;

    private static void SubFilter(ReadOnlySpan<byte> cur, Span<byte> dst, int bpp)
    {
        cur[..bpp].CopyTo(dst);
        var i = bpp;
        var n = Vector<byte>.Count;
        for (; i <= cur.Length - n; i += n)
            (new Vector<byte>(cur[i..]) - new Vector<byte>(cur[(i - bpp)..])).CopyTo(dst[i..]);
        for (; i < cur.Length; i++) dst[i] = (byte)(cur[i] - cur[i - bpp]);
    }

    private static long Cost(ReadOnlySpan<byte> row)
    {
        long sum = 0;
        foreach (var b in row) sum += b < 128 ? b : 256 - b;
        return sum;
    }

    private static void Swizzle(ReadOnlySpan<byte> src, Span<byte> dst)
    {
        for (var i = 0; i < src.Length; i += 4)
        {
            dst[i] = src[i + 2]; dst[i + 1] = src[i + 1]; dst[i + 2] = src[i]; dst[i + 3] = src[i + 3];
        }
    }

    private static void Subtract(ReadOnlySpan<byte> cur, ReadOnlySpan<byte> prev, Span<byte> dst)
    {
        var i = 0;
        var n = Vector<byte>.Count;
        for (; i <= cur.Length - n; i += n)
            (new Vector<byte>(cur[i..]) - new Vector<byte>(prev[i..])).CopyTo(dst[i..]);
        for (; i < cur.Length; i++) dst[i] = (byte)(cur[i] - prev[i]);
    }

    private static void WriteChunk(Stream stream, ReadOnlySpan<byte> type, ReadOnlySpan<byte> data)
    {
        Span<byte> len = stackalloc byte[4];
        BinaryPrimitives.WriteInt32BigEndian(len, data.Length);
        stream.Write(len);
        stream.Write(type);
        stream.Write(data);
        var crc = Crc32(Crc32(0xFFFFFFFFu, type), data) ^ 0xFFFFFFFFu;
        BinaryPrimitives.WriteUInt32BigEndian(len, crc);
        stream.Write(len);
    }

    private static readonly uint[] Table = BuildTable();
    private static uint[] BuildTable()
    {
        var table = new uint[256];
        for (uint n = 0; n < 256; n++)
        {
            var c = n;
            for (var k = 0; k < 8; k++) c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[n] = c;
        }
        return table;
    }

    private static uint Crc32(uint crc, ReadOnlySpan<byte> data)
    {
        foreach (var b in data) crc = Table[(crc ^ b) & 0xFF] ^ (crc >> 8);
        return crc;
    }
}
