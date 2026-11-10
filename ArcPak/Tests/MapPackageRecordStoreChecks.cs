using System.Diagnostics;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using ArcPak;

namespace ArcPak.Tests;

public static class MapPackageRecordStoreChecks
{
    private const int BufferBytes = 8192;
    private const int LargeLength = 5 * 1024 * 1024 + 123;
    private static int _checks;

    public static int Run(string evidenceRoot)
    {
        _checks = 0;
        Directory.CreateDirectory(evidenceRoot);
        CheckKeyBounds();
        CheckExactBoundedReplay(Path.Combine(evidenceRoot, "large"));
        CheckEveryKeyFieldInvalidates(Path.Combine(evidenceRoot, "keys"));
        CheckIncompleteAndFailureCleanup(Path.Combine(evidenceRoot, "failures"));
        CheckCorruptionAndTruncation(Path.Combine(evidenceRoot, "corruption"));
        CheckDiskBudgets(Path.Combine(evidenceRoot, "budget"));
        CheckWriterLease(Path.Combine(evidenceRoot, "writer-lock"));
        CheckCrossProcessReopen(Path.Combine(evidenceRoot, "cross-process"));
        return _checks;
    }

    public static int RunChild(string mode, string root, string outputPath = "")
    {
        var store = new MapPackageRecordStore(root, Limits());
        if (mode == "--child-reopen")
        {
            var decoderCalls = 0;
            if (!store.TryOpenRead(BaseKey(), out var record))
            {
                decoderCalls++;
                Console.Error.WriteLine("CHILD_MISS");
                return 2;
            }
            using (record)
            using (var output = new FileStream(outputPath, FileMode.Create, FileAccess.Write, FileShare.None))
                CopyBounded(record!, output, BufferBytes);
            Console.WriteLine($"CHILD_HIT=true DECODER_CALLS={decoderCalls}");
            return decoderCalls == 0 ? 0 : 3;
        }
        if (mode == "--child-lock")
        {
            try
            {
                using var session = store.BeginWriteSession();
                Console.WriteLine("CHILD_WRITER_ACQUIRED=true");
                return 0;
            }
            catch (IOException)
            {
                Console.WriteLine("CHILD_WRITER_REJECTED=true");
                return 0;
            }
        }
        Console.Error.WriteLine("Unknown child mode");
        return 5;
    }

    private static void CheckKeyBounds()
    {
        Throws<InvalidDataException>(() => Key(sourceManifestComplete: false), "unknown source manifest cannot form a reusable key");
        Throws<InvalidDataException>(() => Key(sources: []), "empty dependency manifest is rejected");
        Throws<ArgumentException>(() => Key(sources: [new MapPackageRecordSource("Package.uasset", "bad")]), "failed source hash is rejected");
        Throws<ArgumentException>(() => Key(sources: [new MapPackageRecordSource(new string('a', 5000), Sha("x"))]), "overlong source path is rejected before normalization");

        var visited = 0;
        IEnumerable<MapPackageRecordSource> OversizedManifest()
        {
            while (visited < 10000)
            {
                var sequence = visited++;
                yield return new MapPackageRecordSource("Packages/" + sequence.ToString("D5") + "/" + new string('p', 3000) + ".uasset", Sha("bulk"));
            }
        }
        Throws<InvalidDataException>(() => Key(sources: OversizedManifest()), "aggregate key metadata is rejected during manifest enumeration");
        Check(visited < 10000, "oversized manifest enumeration stops at metadata budget");
        Check(MapPackageRecordStore.FitsStoreBudget(long.MaxValue - 4, 3, 1, long.MaxValue), "quota arithmetic accepts exact long boundary without overflow");
        Check(!MapPackageRecordStore.FitsStoreBudget(long.MaxValue - 4, 3, 2, long.MaxValue), "quota arithmetic rejects overflow-sized reservation");
    }

    private static void CheckExactBoundedReplay(string root)
    {
        var limits = Limits();
        var store = new MapPackageRecordStore(root, limits);
        var key = BaseKey();
        string expectedHash;
        var source = new PatternStream(LargeLength);
        expectedHash = HashPattern(LargeLength);
        MapPackageRecordDescriptor descriptor;
        using (var session = store.BeginWriteSession())
            descriptor = session.Write(key, source, captureComplete: true);
        Check(descriptor.Length == LargeLength && descriptor.PayloadSha256 == expectedHash, "large opaque payload is streamed with exact length and SHA-256");
        Check(source.MaxRequestedRead <= limits.BufferBytes, "payload writer requests no more than the configured buffer");

        var outputPath = Path.Combine(Directory.GetParent(root)!.FullName, "roundtrip.bin");
        using (var read = store.TryOpenRead(key, out var record) ? record! : throw new InvalidOperationException("record missing"))
        using (var output = new FileStream(outputPath, FileMode.Create, FileAccess.Write, FileShare.None))
            CopyBounded(read, output, limits.BufferBytes);
        Check(HashFile(outputPath) == expectedHash && new FileInfo(outputPath).Length == LargeLength, "bounded replay preserves exact payload bytes");
        using (var reopened = store.TryOpenRead(key, out var record) ? record! : throw new InvalidOperationException("record missing after reopen"))
        {
            var oversizedRequest = new byte[limits.BufferBytes * 8];
            var read = reopened.Read(oversizedRequest, 0, oversizedRequest.Length);
            Check(read <= limits.BufferBytes, "public record stream caps caller read requests to the configured buffer");
            var writeDenied = false;
            try { using var writer = new FileStream(PayloadPath(root, descriptor), FileMode.Open, FileAccess.Write, FileShare.None); }
            catch (IOException) { writeDenied = true; }
            Check(writeDenied, "validated payload handle denies concurrent write and delete sharing");
        }
        Check(new MapPackageRecordStore(root, limits).TryOpenRead(key, out var independent) && independent is not null,
            "fresh store instance opens the committed record");
        independent!.Dispose();
    }

    private static void CheckEveryKeyFieldInvalidates(string root)
    {
        var store = new MapPackageRecordStore(root, Limits());
        var key = BaseKey();
        using (var session = store.BeginWriteSession())
        using (var payload = new MemoryStream(Encoding.UTF8.GetBytes("one package decoded record")))
            session.Write(key, payload, captureComplete: true);

        var variants = new (string Name, MapPackageRecordKey Key)[]
        {
            ("archive identity", Key(archive: Sha("other archive"))),
            ("canonical package", Key(package: "/Game/Other/Package")),
            ("root or sidecar source hash", Key(sources: [new("PioneerGame/Content/Test/Package.uasset", Sha("root changed")), new("PioneerGame/Content/Test/Package.uexp", Sha("exports")), new("PioneerGame/Content/Test/Package.ubulk", Sha("bulk"))])),
            ("mapping name", Key(mappingName: "renamed.usmap")),
            ("mapping content", Key(mappingHash: Sha("mapping changed"))),
            ("decoder fingerprint", Key(decoder: Sha("decoder changed"))),
            ("capture producer fingerprint", Key(capture: Sha("capture changed"))),
            ("capture profile", Key(profile: Sha("raw-only capture profile"))),
            ("record schema", Key(schema: 2)),
        };
        foreach (var variant in variants)
            Check(!store.TryOpenRead(variant.Key, out _), $"{variant.Name} change invalidates package record");
        Check(store.TryOpenRead(key, out var original) && original is not null, "base key remains reusable after variant lookups");
        original!.Dispose();
    }

    private static void CheckIncompleteAndFailureCleanup(string root)
    {
        var store = new MapPackageRecordStore(root, Limits());
        var oldKey = BaseKey();
        using (var session = store.BeginWriteSession())
        using (var original = new MemoryStream(Encoding.UTF8.GetBytes("old valid payload")))
            session.Write(oldKey, original, captureComplete: true);

        var newKey = Key(decoder: Sha("new decoder"));
        using (var session = store.BeginWriteSession())
        using (var incomplete = new MemoryStream(Encoding.UTF8.GetBytes("partial")))
            Throws<InvalidDataException>(() => session.Write(newKey, incomplete, captureComplete: false), "incomplete capture cannot publish a reusable receipt");
        Check(!store.TryOpenRead(newKey, out _), "incomplete capture has no reusable record");

        using (var session = store.BeginWriteSession())
        using (var throwing = new ThrowAfterStream(3000))
            Throws<IOException>(() => session.Write(newKey, throwing, captureComplete: true), "failed payload write is surfaced");
        Check(!store.TryOpenRead(newKey, out _), "failed publication has no complete receipt");
        Check(!Directory.EnumerateFiles(root, "*.tmp", SearchOption.AllDirectories).Any(), "failed write removes only its owned temporary files");
        Check(store.TryOpenRead(oldKey, out var oldRecord) && oldRecord is not null, "failed replacement leaves prior valid key readable");
        oldRecord!.Dispose();

        var descriptor = GetDescriptor(root, oldKey);
        var keyHash = descriptor.KeySha256;
        var payload = Path.Combine(root, "entries", keyHash[..2], keyHash, "payload-" + descriptor.PayloadSha256 + ".bin");
        var orphanRoot = Path.Combine(root, "interrupted");
        var orphanStore = new MapPackageRecordStore(orphanRoot, Limits());
        var orphanKey = Key(package: "/Game/Interrupted/Package");
        var orphanDirectory = Path.Combine(orphanRoot, "entries", orphanKey.KeySha256[..2], orphanKey.KeySha256);
        Directory.CreateDirectory(orphanDirectory);
        File.WriteAllText(Path.Combine(orphanDirectory, ".payload.crash.tmp"), "partial payload without receipt");
        Check(!orphanStore.TryOpenRead(orphanKey, out _), "interrupted payload without a receipt is never reusable");
        Check(File.Exists(Path.Combine(orphanDirectory, ".payload.crash.tmp")), "store leaves unowned crash orphan for quota accounting");
    }

    private static void CheckCorruptionAndTruncation(string root)
    {
        var tamperRoot = Path.Combine(root, "tamper");
        var tamperKey = Key(package: "/Game/Tamper/Package");
        var tamperDescriptor = GetDescriptor(tamperRoot, tamperKey);
        var tamperPath = PayloadPath(tamperRoot, tamperDescriptor);
        File.WriteAllText(tamperPath, "tampered bytes");
        Throws<InvalidDataException>(() => new MapPackageRecordStore(tamperRoot, Limits()).TryOpenRead(tamperKey, out _), "checksum mismatch rejects tampered payload");

        var truncateRoot = Path.Combine(root, "truncate");
        var truncateKey = Key(package: "/Game/Truncate/Package");
        var truncateDescriptor = GetDescriptor(truncateRoot, truncateKey);
        using (var payload = new FileStream(PayloadPath(truncateRoot, truncateDescriptor), FileMode.Open, FileAccess.Write, FileShare.None))
            payload.SetLength(2);
        Throws<InvalidDataException>(() => new MapPackageRecordStore(truncateRoot, Limits()).TryOpenRead(truncateKey, out _), "receipt length rejects truncated payload");

        var receiptRoot = Path.Combine(root, "incomplete-receipt");
        var receiptKey = Key(package: "/Game/Incomplete/Package");
        var receiptDescriptor = GetDescriptor(receiptRoot, receiptKey);
        var receiptPath = Path.Combine(receiptRoot, "entries", receiptDescriptor.KeySha256[..2], receiptDescriptor.KeySha256, "receipt.json");
        var json = System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(receiptPath))!;
        json["complete"] = false;
        File.WriteAllText(receiptPath, json.ToJsonString());
        Check(!new MapPackageRecordStore(receiptRoot, Limits()).TryOpenRead(receiptKey, out _), "incomplete receipt is never treated as reusable");
    }

    private static void CheckDiskBudgets(string root)
    {
        var entryRoot = Path.Combine(root, "entry-budget");
        var entryLimits = new MapPackageRecordStoreLimits(MaxEntryBytes: 4096, MaxStoreBytes: 64 * 1024, BufferBytes: 4096);
        var entryStore = new MapPackageRecordStore(entryRoot, entryLimits);
        var key = Key(package: "/Game/EntryBudget/Package");
        using (var session = entryStore.BeginWriteSession())
        using (var payload = new PatternStream(8192))
            Throws<IOException>(() => session.Write(key, payload, captureComplete: true), "per-entry disk budget fails explicitly without truncation");
        Check(!entryStore.TryOpenRead(key, out _), "over-budget payload is not published");

        var totalRoot = Path.Combine(root, "total-budget");
        var totalLimits = new MapPackageRecordStoreLimits(MaxEntryBytes: 12 * 1024, MaxStoreBytes: 12 * 1024, BufferBytes: 4096);
        var totalStore = new MapPackageRecordStore(totalRoot, totalLimits);
        var smallKey = Key(package: "/Game/TotalBudget/Package");
        using (var session = totalStore.BeginWriteSession())
        using (var payload = new PatternStream(12 * 1024))
            Throws<IOException>(() => session.Write(smallKey, payload, captureComplete: true), "total store disk budget fails explicitly");
        Check(!totalStore.TryOpenRead(smallKey, out _), "total-budget failure publishes no receipt");
    }

    private static void CheckWriterLease(string root)
    {
        var storeA = new MapPackageRecordStore(root, Limits());
        var storeB = new MapPackageRecordStore(root, Limits());
        using (var sessionA = storeA.BeginWriteSession())
        {
            Throws<IOException>(() => storeB.BeginWriteSession(), "second in-process writer is rejected while the cache lease is held");
            Check(!sessionA.IsDisposed, "first writer retains its lease during the conflict");
            var busy = RunChildProcess("--child-lock", root, "");
            Check(busy.ExitCode == 0 && busy.Output.Contains("CHILD_WRITER_REJECTED=true", StringComparison.Ordinal),
                "cross-process writer is rejected while the cache lease is held");
        }
        var released = RunChildProcess("--child-lock", root, "");
        Check(released.ExitCode == 0 && released.Output.Contains("CHILD_WRITER_ACQUIRED=true", StringComparison.Ordinal),
            "cross-process writer succeeds after the cache lease closes");
    }

    private static void CheckCrossProcessReopen(string root)
    {
        var store = new MapPackageRecordStore(root, Limits());
        var key = BaseKey();
        using (var session = store.BeginWriteSession())
        using (var source = new PatternStream(LargeLength))
            session.Write(key, source, captureComplete: true);
        var output = Path.Combine(Directory.GetParent(root)!.FullName, "child-replayed.bin");
        var result = RunChildProcess("--child-reopen", root, output);
        Check(result.ExitCode == 0 && result.Output.Contains("CHILD_HIT=true DECODER_CALLS=0", StringComparison.Ordinal),
            "separate process reopens the record without invoking the decoder: " + result.Error.Trim());
        Check(HashFile(output) == HashPattern(LargeLength), "separate process replays exact stored bytes");
    }

    private static (int ExitCode, string Output, string Error) RunChildProcess(string mode, string root, string argument)
    {
        var processPath = Environment.ProcessPath ?? throw new InvalidOperationException("Cannot resolve current test process");
        var start = new ProcessStartInfo(processPath) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true };
        if (Path.GetFileNameWithoutExtension(processPath).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            start.ArgumentList.Add(Assembly.GetEntryAssembly()!.Location);
        start.ArgumentList.Add(mode);
        start.ArgumentList.Add(root);
        if (!string.IsNullOrEmpty(argument)) start.ArgumentList.Add(argument);
        using var child = Process.Start(start) ?? throw new InvalidOperationException("Could not start package record child");
        if (!child.WaitForExit(30000))
        {
            child.Kill(entireProcessTree: true);
            throw new TimeoutException("Package record child process timed out");
        }
        return (child.ExitCode, child.StandardOutput.ReadToEnd(), child.StandardError.ReadToEnd());
    }

    private static MapPackageRecordDescriptor GetDescriptor(string root, MapPackageRecordKey key)
    {
        var store = new MapPackageRecordStore(root, Limits());
        using var session = store.BeginWriteSession();
        using var source = new MemoryStream(Encoding.UTF8.GetBytes("immutable test record"));
        return session.Write(key, source, captureComplete: true);
    }

    private static string PayloadPath(string root, MapPackageRecordDescriptor descriptor)
        => Path.Combine(root, "entries", descriptor.KeySha256[..2], descriptor.KeySha256, "payload-" + descriptor.PayloadSha256 + ".bin");

    private static MapPackageRecordStoreLimits Limits() => new(MaxEntryBytes: 16 * 1024 * 1024, MaxStoreBytes: 64 * 1024 * 1024, BufferBytes: BufferBytes, MaxReceiptBytes: 2 * 1024 * 1024);

    private static MapPackageRecordKey BaseKey() => Key();

    private static MapPackageRecordKey Key(
        string? archive = null,
        string? package = null,
        IEnumerable<MapPackageRecordSource>? sources = null,
        string? mappingName = null,
        string? mappingHash = null,
        string? decoder = null,
        string? capture = null,
        string? profile = null,
        int schema = 1,
        bool sourceManifestComplete = true)
        => new(archive ?? Sha("archive-set"), package ?? "/Game/Test/Package",
            sources ?? [
                new MapPackageRecordSource("PioneerGame/Content/Test/Package.umap", Sha("root package")),
                new MapPackageRecordSource("PioneerGame/Content/Test/Package.uexp", Sha("exports")),
                new MapPackageRecordSource("PioneerGame/Content/Test/Package.ubulk", Sha("bulk sidecar"))],
            mappingName ?? "pioneer.usmap", mappingHash ?? Sha("mapping bytes"),
            decoder ?? Sha("decoder binary"), capture ?? Sha("capture producer"), profile ?? Sha("capture profile"), schema, sourceManifestComplete);

    private static string Sha(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static string HashPattern(int length)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[BufferBytes];
        long position = 0;
        while (position < length)
        {
            var count = (int)Math.Min(buffer.Length, length - position);
            for (var index = 0; index < count; index++) buffer[index] = PatternByte(position + index);
            hash.AppendData(buffer, 0, count);
            position += count;
        }
        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    private static byte PatternByte(long position) => (byte)((position * 131 + 17) & 0xff);

    private static string HashFile(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static void CopyBounded(Stream source, Stream destination, int bufferBytes)
    {
        var buffer = new byte[bufferBytes];
        int read;
        while ((read = source.Read(buffer, 0, buffer.Length)) != 0) destination.Write(buffer, 0, read);
    }

    private static void Check(bool condition, string name)
    {
        if (!condition) throw new InvalidOperationException("FAIL " + name);
        _checks++;
        Console.WriteLine("PASS " + name);
    }

    private static void Throws<T>(Action action, string name) where T : Exception
    {
        try { action(); }
        catch (T) { Check(true, name); return; }
        throw new InvalidOperationException("FAIL " + name);
    }

    private sealed class PatternStream : Stream
    {
        private readonly long _length;
        private long _position;
        public int MaxRequestedRead { get; private set; }
        public PatternStream(long length) => _length = length;
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => _length;
        public override long Position { get => _position; set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count)
        {
            MaxRequestedRead = Math.Max(MaxRequestedRead, count);
            var available = (int)Math.Min(count, _length - _position);
            for (var index = 0; index < available; index++) buffer[offset + index] = PatternByte(_position + index);
            _position += available;
            return available;
        }
        public override int Read(Span<byte> buffer)
        {
            MaxRequestedRead = Math.Max(MaxRequestedRead, buffer.Length);
            var available = (int)Math.Min(buffer.Length, _length - _position);
            for (var index = 0; index < available; index++) buffer[index] = PatternByte(_position + index);
            _position += available;
            return available;
        }
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    private sealed class ThrowAfterStream : Stream
    {
        private readonly int _limit;
        private int _written;
        public ThrowAfterStream(int limit) => _limit = limit;
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => _written; set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count)
        {
            if (_written >= _limit) throw new IOException("injected source read failure");
            var size = Math.Min(count, _limit - _written);
            Array.Fill(buffer, (byte)0x5a, offset, size);
            _written += size;
            return size;
        }
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }
}

