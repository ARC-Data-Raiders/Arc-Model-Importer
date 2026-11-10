using System.Security.Cryptography;
using ArcPak;

namespace ArcPak.Tests;

internal static class MapArchiveSourceIdentityChecks
{
    private const int BufferBytes = 4096;
    private static int _checks;

    public static int Run(string evidence)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(evidence);
        _checks = 0;
        var root = Path.Combine(Path.GetFullPath(evidence), "source-identity-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var inputs = WriteInputs(root);
            CheckStableManifest(inputs);
            CheckEveryInputInvalidates(inputs, root);
            CheckInvalidDeclarations(inputs);
            CheckLeasesAndSetChanges(inputs, root);
            CheckCancellationAndFailureCleanup(inputs, root);
            CheckBoundedStreamHash();
            return _checks;
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void CheckStableManifest(MapArchiveSourceInput[] inputs)
    {
        string expected;
        using (var first = MapArchiveSourceIdentity.Acquire(inputs, RequiredRoles(), BufferBytes))
        {
            expected = first.IdentitySha256;
            Check(first.Manifest.Count == inputs.Length, "identity contains every listed source file");
            Check(first.Manifest.Select(row => row.Role).SequenceEqual(first.Manifest.Select(row => row.Role).Order(StringComparer.Ordinal)),
                "manifest order is canonical by role and logical name");
            Check(first.IdentitySha256.Length == 64 && first.IdentitySha256.All(Uri.IsHexDigit), "identity is SHA-256");
            Check(first.BufferBytes == BufferBytes, "hash buffer size is fixed and exposed");
            Check(first.Manifest is System.Collections.ObjectModel.ReadOnlyCollection<MapArchiveSourceManifestEntry>,
                "source manifest cannot be mutated by callers");
            first.EnsureDiscoveredFileSet(inputs.Select(input => input.Path));
            first.EnsureInputBindings(inputs);
            Check(true, "role/logical names remain bound to the physical files hashed for them");
            var swappedPaths = inputs.ToArray();
            swappedPaths[0] = swappedPaths[0] with { Path = inputs[1].Path };
            swappedPaths[1] = swappedPaths[1] with { Path = inputs[0].Path };
            Throws<InvalidDataException>(() => first.EnsureInputBindings(swappedPaths),
                "same physical set with swapped logical-path bindings is rejected");
        }
        using (var reversed = MapArchiveSourceIdentity.Acquire(inputs.Reverse(), RequiredRoles().Reverse(), BufferBytes))
            Check(reversed.IdentitySha256 == expected, "input enumeration order does not change identity");

        var renamed = inputs.ToArray();
        renamed[0] = renamed[0] with { LogicalName = renamed[0].LogicalName + ".alias" };
        using var changedName = MapArchiveSourceIdentity.Acquire(renamed, RequiredRoles(), BufferBytes);
        Check(changedName.IdentitySha256 != expected, "logical source name changes identity");

        var rerolled = inputs.ToArray();
        rerolled[0] = rerolled[0] with { Role = rerolled[0].Role + "-other" };
        using var changedRole = MapArchiveSourceIdentity.Acquire(rerolled, RequiredRoles().Where(role => role != inputs[0].Role).Append(rerolled[0].Role), BufferBytes);
        Check(changedRole.IdentitySha256 != expected, "source role changes identity");
    }

    private static void CheckEveryInputInvalidates(MapArchiveSourceInput[] inputs, string root)
    {
        using var baseline = MapArchiveSourceIdentity.Acquire(inputs, RequiredRoles(), BufferBytes);
        var originalIdentity = baseline.IdentitySha256;
        baseline.Dispose();
        foreach (var input in inputs)
        {
            var original = File.ReadAllBytes(input.Path);
            try
            {
                var changed = original.ToArray();
                changed[0] ^= 0x5a;
                File.WriteAllBytes(input.Path, changed);
                using var identity = MapArchiveSourceIdentity.Acquire(inputs, RequiredRoles(), BufferBytes);
                Check(identity.IdentitySha256 != originalIdentity, "content change invalidates " + input.Role + ":" + input.LogicalName);
            }
            finally
            {
                File.WriteAllBytes(input.Path, original);
            }
        }
    }

    private static void CheckInvalidDeclarations(MapArchiveSourceInput[] inputs)
    {
        Throws<InvalidDataException>(() => MapArchiveSourceIdentity.Acquire(inputs, RequiredRoles().Append("missing-role"), BufferBytes),
            "missing required role is rejected");
        Throws<InvalidDataException>(() => MapArchiveSourceIdentity.Acquire(inputs.Append(inputs[0]), RequiredRoles(), BufferBytes),
            "duplicate physical source path is rejected");
        var duplicateLogical = inputs.ToArray();
        duplicateLogical[1] = duplicateLogical[1] with { LogicalName = duplicateLogical[0].LogicalName, Role = duplicateLogical[0].Role };
        Throws<InvalidDataException>(() => MapArchiveSourceIdentity.Acquire(duplicateLogical, RequiredRoles(), BufferBytes),
            "duplicate role and logical name is rejected");
        Throws<ArgumentOutOfRangeException>(() => MapArchiveSourceIdentity.Acquire(inputs, RequiredRoles(), BufferBytes / 2),
            "undersized hash buffer is rejected");
    }

    private static void CheckLeasesAndSetChanges(MapArchiveSourceInput[] inputs, string root)
    {
        var source = inputs[0];
        var original = File.ReadAllBytes(source.Path);
        var replacement = Path.Combine(root, "replacement.tmp");
        File.WriteAllBytes(replacement, [90, 91, 92]);
        var identity = MapArchiveSourceIdentity.Acquire(inputs, RequiredRoles(), BufferBytes);
        try
        {
            Denied(() => File.WriteAllBytes(source.Path, [99]), "write denied while identity lease is active");
            Denied(() => File.Delete(source.Path), "delete denied while identity lease is active");
            Denied(() => File.Move(source.Path, source.Path + ".moved"), "rename denied while identity lease is active");
            Denied(() => File.Replace(replacement, source.Path, null), "replace denied while identity lease is active");

            var added = Path.Combine(root, "new-sidecar.sig");
            File.WriteAllBytes(added, [7, 8]);
            Throws<InvalidDataException>(() => identity.EnsureDiscoveredFileSet(inputs.Select(input => input.Path).Append(added)),
                "newly discovered source file changes the manifest set");
            Throws<InvalidDataException>(() => identity.EnsureDiscoveredFileSet(inputs.Skip(1).Select(input => input.Path)),
                "missing discovered source file changes the manifest set");
            File.Delete(added);
        }
        finally
        {
            identity.Dispose();
        }
        Check(!identity.IsActive, "dispose marks identity inactive");
        Throws<ObjectDisposedException>(() => _ = identity.IdentitySha256, "identity cannot be read after lease disposal");
        Throws<ObjectDisposedException>(() => identity.EnsureDiscoveredFileSet(inputs.Select(input => input.Path)),
            "file-set check requires an active lease");
        File.WriteAllBytes(source.Path, original);
        Check(File.ReadAllBytes(source.Path).SequenceEqual(original), "source mutation is allowed after lease disposal");
        File.Delete(replacement);
    }

    private static void CheckCancellationAndFailureCleanup(MapArchiveSourceInput[] inputs, string root)
    {
        using (var cancellation = new CancellationTokenSource())
        {
            IEnumerable<MapArchiveSourceInput> CancelAfterEnumeration()
            {
                yield return inputs[0];
                cancellation.Cancel();
            }
            Throws<OperationCanceledException>(() => MapArchiveSourceIdentity.Acquire(CancelAfterEnumeration(),
                [inputs[0].Role], BufferBytes, cancellation.Token), "cancellation after enumeration releases acquired leases");
            File.WriteAllBytes(inputs[0].Path, File.ReadAllBytes(inputs[0].Path));
            Check(true, "cancelled acquisition leaves source writable");
        }

        var firstPath = Path.Combine(root, "a-partial.pak");
        var blockedPath = Path.Combine(root, "z-blocked.ucas");
        File.WriteAllBytes(firstPath, [1]);
        File.WriteAllBytes(blockedPath, [2]);
        using (var blocker = new FileStream(blockedPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        {
            Throws<IOException>(() => MapArchiveSourceIdentity.Acquire(
                [new("container-pak", "a-partial.pak", firstPath), new("container-ucas", "z-blocked.ucas", blockedPath)],
                ["container-pak", "container-ucas"], BufferBytes), "partial lease acquisition failure is surfaced");
            File.WriteAllBytes(firstPath, [3]);
            Check(File.ReadAllBytes(firstPath).SequenceEqual(new byte[] { 3 }), "partial acquisition failure releases prior handles");
        }
    }

    private static void CheckBoundedStreamHash()
    {
        var bytes = Enumerable.Range(0, BufferBytes * 5 + 17).Select(index => (byte)(index % 251)).ToArray();
        using var cancel = new CancellationTokenSource();
        using var stream = new TrackingStream(bytes, BufferBytes / 2, cancel);
        Throws<OperationCanceledException>(() => MapArchiveSourceIdentity.HashStream(stream, BufferBytes, cancel.Token),
            "cancellation during chunked hashing is observed");
        Check(stream.MaxRequestedBytes <= BufferBytes, "hash loop never requests more than the configured buffer");

        using var complete = new TrackingStream(bytes, BufferBytes / 2, cancellationTokenSource: null);
        var hashed = MapArchiveSourceIdentity.HashStream(complete, BufferBytes);
        var expected = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        Check(hashed.Length == bytes.Length && hashed.Sha256 == expected, "chunked hash matches SHA-256 of complete bytes");
        Check(hashed.MaxReadRequestBytes == BufferBytes && complete.MaxRequestedBytes <= BufferBytes,
            "measured hash reads respect the fixed buffer bound");
    }

    private static MapArchiveSourceInput[] WriteInputs(string root)
    {
        var definitions = new (string Role, string Name, byte[] Bytes)[]
        {
            ("container-pak", "Paks/pakchunk0.pak", [1, 3, 5, 7]),
            ("container-utoc", "Paks/global.utoc", [2, 4, 6, 8]),
            ("container-ucas", "Paks/global.ucas", [9, 11, 13]),
            ("container-ucas", "Paks/global_s1.ucas", [10, 12, 14]),
            ("sidecar", "Paks/pakchunk0.sig", [15, 17]),
            ("mapping", "mappings/ArcRaiders.usmap", [16, 18, 20]),
            ("build-metadata", "appmanifest_123.acf", [21, 22]),
            ("producer", "runtime/ArcPak.Export.exe", [23]),
            ("parser", "runtime/CUE4Parse.Arc.dll", [24]),
            ("converter", "runtime/CUE4Parse-Conversion.Arc.dll", [25]),
            ("compression", "runtime/oodle-data-shared.dll", [26, 27]),
        };
        return definitions.Select((definition, index) =>
        {
            var path = Path.Combine(root, "input-" + index.ToString("D2") + ".bin");
            File.WriteAllBytes(path, definition.Bytes);
            return new MapArchiveSourceInput(definition.Role, definition.Name, path);
        }).ToArray();
    }

    private static string[] RequiredRoles() =>
    ["container-pak", "container-utoc", "container-ucas", "sidecar", "mapping", "build-metadata", "producer", "parser", "converter", "compression"];

    private static void Check(bool condition, string name)
    {
        if (!condition) throw new InvalidOperationException("FAIL " + name);
        _checks++;
        Console.WriteLine("PASS " + name);
    }

    private static void Throws<TException>(Action operation, string name) where TException : Exception
    {
        try { operation(); }
        catch (TException) { Check(true, name); return; }
        throw new InvalidOperationException("FAIL " + name + " was accepted");
    }

    private static void Denied(Action operation, string name)
    {
        try { operation(); }
        catch (IOException) { Check(true, name); return; }
        catch (UnauthorizedAccessException) { Check(true, name); return; }
        throw new InvalidOperationException("FAIL " + name + " was allowed");
    }

    private sealed class TrackingStream : Stream
    {
        private readonly MemoryStream _inner;
        private readonly int _maximumReturnedPerRead;
        private readonly CancellationTokenSource? _cancelAfterFirstRead;
        private bool _cancelled;

        public int MaxRequestedBytes { get; private set; }
        public override bool CanRead => true;
        public override bool CanSeek => true;
        public override bool CanWrite => false;
        public override long Length => _inner.Length;
        public override long Position { get => _inner.Position; set => _inner.Position = value; }

        public TrackingStream(byte[] bytes, int maximumReturnedPerRead, CancellationTokenSource? cancellationTokenSource)
        {
            _inner = new MemoryStream(bytes, writable: false);
            _maximumReturnedPerRead = maximumReturnedPerRead;
            _cancelAfterFirstRead = cancellationTokenSource;
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            MaxRequestedBytes = Math.Max(MaxRequestedBytes, count);
            var read = _inner.Read(buffer, offset, Math.Min(count, _maximumReturnedPerRead));
            if (!_cancelled && _cancelAfterFirstRead is not null)
            {
                _cancelled = true;
                _cancelAfterFirstRead.Cancel();
            }
            return read;
        }

        public override int Read(Span<byte> buffer)
        {
            var temporary = new byte[Math.Min(buffer.Length, _maximumReturnedPerRead)];
            var read = Read(temporary, 0, temporary.Length);
            temporary.AsSpan(0, read).CopyTo(buffer);
            return read;
        }

        public override long Seek(long offset, SeekOrigin origin) => _inner.Seek(offset, origin);
        public override void Flush() => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        protected override void Dispose(bool disposing)
        {
            if (disposing) _inner.Dispose();
            base.Dispose(disposing);
        }
    }
}

