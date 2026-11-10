using System.Security.Cryptography;
using CUE4Parse.Compression;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.IO;
using CUE4Parse.UE4.IO.Objects;
using CUE4Parse.UE4.Versions;

namespace ArcPak.Tests;

internal static class DependencyHashChecks
{
    internal static int Run(string evidence, string fixtureRoot)
    {
        Directory.CreateDirectory(evidence);
        var variant = Path.Combine(fixtureRoot, "IoStore", "Tagged");
        var container = Path.Combine(variant, "OodlePartitioned");
        var provider = new DefaultFileProvider(container, SearchOption.TopDirectoryOnly,
            new VersionContainer(EGame.GAME_UE5_8), StringComparer.OrdinalIgnoreCase);
        using (provider)
        {
            provider.Initialize();
            provider.RegisterVfs(Path.Combine(variant, "global.utoc"));
            var mainToc = Path.Combine(container, "CUE4ParseFixtures-Minimal-OodlePartitioned-Windows.utoc");
            if (provider.Mount() != 1) throw new InvalidDataException("Synthetic IoStore fixture did not mount");
            var reader = provider.MountedVfs.OfType<IoStoreReader>().Single(candidate =>
                Path.GetFileName(candidate.Name).Equals(Path.GetFileName(mainToc), StringComparison.OrdinalIgnoreCase));
            var blockSize = (int)reader.TocResource.Header.CompressionBlockSize;
            var chunkIndex = Array.FindIndex(reader.TocResource.ChunkOffsetLengths,
                candidate => candidate.Length > (ulong)blockSize);
            if (chunkIndex < 0) throw new InvalidDataException("Fixture lacks a multi-block IoStore entry");
            var expectedEntry = new FIoStoreEntry(reader, (uint)chunkIndex);
            var expectedBytes = expectedEntry.Read();
            var expectedHash = MapBundleDocument.Hash(expectedBytes);
            var guardedEntry = new ReadForbiddenIoStoreEntry(reader, expectedEntry.Path, (uint)chunkIndex);
            var streamedHash = MapBundleDocument.HashSource(guardedEntry);
            if (!string.Equals(expectedHash, streamedHash, StringComparison.Ordinal))
                throw new InvalidDataException("IoStore dependency hash differs from exact entry.Read() digest");
            Console.WriteLine($"PASS IoStore digest parity; bytes={expectedBytes.LongLength}; sha256={streamedHash}; chunkIndex={chunkIndex}; offset={expectedEntry.Offset}; blockSize={blockSize}; readFallback=forbidden");
        }

        var fallbackPath = Path.Combine(evidence, "loose-fallback.bin");
        byte[] fallbackBytes = [0, 1, 7, 31, 127, 128, 254, 255];
        File.WriteAllBytes(fallbackPath, fallbackBytes);
        var loose = new OsGameFile(new FileInfo(fallbackPath), new VersionContainer(EGame.GAME_UE5_8));
        var expectedLooseHash = MapBundleDocument.Hash(fallbackBytes);
        var actualLooseHash = MapBundleDocument.HashSource(loose);
        if (!string.Equals(expectedLooseHash, actualLooseHash, StringComparison.Ordinal))
            throw new InvalidDataException("Non-IoStore fallback digest changed");
        Console.WriteLine($"PASS loose-file fallback parity; bytes={fallbackBytes.Length}; sha256={actualLooseHash}");
        return 2;
    }

    private sealed class ReadForbiddenIoStoreEntry(IoStoreReader reader, string path, uint chunkIndex)
        : FIoStoreEntry(reader, path, chunkIndex)
    {
        public override byte[] Read(FByteBulkDataHeader? header = null)
            => throw new InvalidOperationException("IoStore hash path called entry.Read() fallback");
    }
}


