using System.Collections.ObjectModel;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ArcPak;

public sealed record MapPackageRecordSource(string Path, string Sha256);

/// <summary>Immutable identity for one complete package record.</summary>
public sealed class MapPackageRecordKey
{
    internal static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private const int MaxKeyMetadataBytes = 256 * 1024;
    private readonly ReadOnlyCollection<MapPackageRecordSource> _sources;

    public string GameArchiveIdentitySha256 { get; }
    public string CanonicalPackage { get; }
    public IReadOnlyList<MapPackageRecordSource> SourceHashes => _sources;
    public string MappingName { get; }
    public string MappingSha256 { get; }
    public string DecoderFingerprintSha256 { get; }
    public string CaptureProducerFingerprintSha256 { get; }
    public string CaptureProfileSha256 { get; }
    public int SchemaVersion { get; }
    public string KeySha256 { get; }

    public MapPackageRecordKey(
        string gameArchiveIdentitySha256,
        string canonicalPackage,
        IEnumerable<MapPackageRecordSource> sourceHashes,
        string mappingName,
        string mappingSha256,
        string decoderFingerprintSha256,
        string captureProducerFingerprintSha256,
        string captureProfileSha256,
        int schemaVersion,
        bool sourceManifestComplete)
    {
        if (!sourceManifestComplete)
            throw new InvalidDataException("A package record key requires a complete source and dependency hash manifest");
        GameArchiveIdentitySha256 = NormalizeHash(gameArchiveIdentitySha256, nameof(gameArchiveIdentitySha256));
        CanonicalPackage = ValidatePackage(canonicalPackage);
        MappingName = ValidateFileName(mappingName, nameof(mappingName));
        MappingSha256 = NormalizeHash(mappingSha256, nameof(mappingSha256));
        DecoderFingerprintSha256 = NormalizeHash(decoderFingerprintSha256, nameof(decoderFingerprintSha256));
        CaptureProducerFingerprintSha256 = NormalizeHash(captureProducerFingerprintSha256, nameof(captureProducerFingerprintSha256));
        CaptureProfileSha256 = NormalizeHash(captureProfileSha256, nameof(captureProfileSha256));
        if (schemaVersion < 1) throw new ArgumentOutOfRangeException(nameof(schemaVersion));
        SchemaVersion = schemaVersion;

        ArgumentNullException.ThrowIfNull(sourceHashes);
        var normalized = new List<MapPackageRecordSource>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var metadataBytes = 512 + Encoding.UTF8.GetByteCount(CanonicalPackage) + Encoding.UTF8.GetByteCount(MappingName);
        if (metadataBytes > MaxKeyMetadataBytes) throw new InvalidDataException("Package record key metadata exceeds its bound");
        foreach (var source in sourceHashes)
        {
            if (source is null) throw new InvalidDataException("Source hash manifest contains a null row");
            var path = ValidateSourcePath(source.Path);
            var digest = NormalizeHash(source.Sha256, nameof(sourceHashes));
            metadataBytes = checked(metadataBytes + Encoding.UTF8.GetByteCount(path) + Encoding.UTF8.GetByteCount(digest) + 64);
            if (metadataBytes > MaxKeyMetadataBytes)
                throw new InvalidDataException("Package record key metadata exceeds its bound");
            if (!seen.Add(path)) throw new InvalidDataException("Duplicate source path in hash manifest: " + path);
            normalized.Add(new MapPackageRecordSource(path, digest));
            if (normalized.Count > 32768) throw new InvalidDataException("Source hash manifest exceeds the entry bound");
        }
        if (normalized.Count == 0) throw new InvalidDataException("Source hash manifest is empty");
        normalized.Sort((left, right) =>
        {
            var folded = StringComparer.OrdinalIgnoreCase.Compare(left.Path, right.Path);
            return folded != 0 ? folded : StringComparer.Ordinal.Compare(left.Path, right.Path);
        });
        _sources = Array.AsReadOnly(normalized.ToArray());
        var canonicalBytes = CanonicalBytes();
        if (canonicalBytes.Length > 2 * 1024 * 1024)
            throw new InvalidDataException("Package record key exceeds the receipt metadata bound");
        KeySha256 = Convert.ToHexString(SHA256.HashData(canonicalBytes)).ToLowerInvariant();
    }

    internal byte[] CanonicalBytes() => JsonSerializer.SerializeToUtf8Bytes(new KeyDocument(
        GameArchiveIdentitySha256, CanonicalPackage, _sources, MappingName, MappingSha256,
        DecoderFingerprintSha256, CaptureProducerFingerprintSha256, CaptureProfileSha256, SchemaVersion), JsonOptions);

    internal static string NormalizeHash(string value, string parameterName)
    {
        ArgumentNullException.ThrowIfNull(value, parameterName);
        if (value.Length != 64 || value.Any(character => !Uri.IsHexDigit(character)))
            throw new ArgumentException("SHA-256 values must contain exactly 64 hexadecimal characters", parameterName);
        return value.ToLowerInvariant();
    }

    private static string ValidatePackage(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        if (value.Length > 4096) throw new ArgumentException("Package identity exceeds the key bound", nameof(value));
        if (!value.StartsWith('/') || value.Contains('\\') || value.Contains('\0') || value.Contains(':') ||
            value.Split('/').Skip(1).Any(segment => segment.Length == 0 || segment is "." or ".."))
            throw new ArgumentException("Package identity must be a canonical rooted package path", nameof(value));
        return value;
    }

    private static string ValidateFileName(string value, string parameterName)
    {
        ArgumentNullException.ThrowIfNull(value, parameterName);
        if (value.Length == 0 || value.Length > 1024 || value is "." or ".." || value.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
            value.Contains('/') || value.Contains('\\') || value.Contains('\0'))
            throw new ArgumentException("Mapping name must be a file name", parameterName);
        return value;
    }

    private static string ValidateSourcePath(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        if (value.Length > 4096) throw new ArgumentException("Source path exceeds the key bound", nameof(value));
        var normalized = value.Replace('\\', '/');
        if (normalized.Length == 0 || normalized.StartsWith('/') || normalized.Contains(':') || normalized.Contains('\0') ||
            normalized.Split('/').Any(segment => segment.Length == 0 || segment is "." or ".."))
            throw new ArgumentException("Source paths must be normalized relative paths", nameof(value));
        return normalized;
    }

    private sealed record KeyDocument(
        string GameArchiveIdentitySha256,
        string CanonicalPackage,
        IReadOnlyList<MapPackageRecordSource> SourceHashes,
        string MappingName,
        string MappingSha256,
        string DecoderFingerprintSha256,
        string CaptureProducerFingerprintSha256,
        string CaptureProfileSha256,
        int SchemaVersion);
}

public sealed record MapPackageRecordStoreLimits(
    long MaxEntryBytes = 8L * 1024 * 1024 * 1024,
    long MaxStoreBytes = 256L * 1024 * 1024 * 1024,
    int BufferBytes = 64 * 1024,
    int MaxReceiptBytes = 2 * 1024 * 1024)
{
    internal void Validate()
    {
        if (MaxEntryBytes < 1) throw new ArgumentOutOfRangeException(nameof(MaxEntryBytes));
        if (MaxStoreBytes < 1 || MaxEntryBytes > MaxStoreBytes) throw new ArgumentOutOfRangeException(nameof(MaxStoreBytes));
        if (BufferBytes is < 4096 or > 1024 * 1024) throw new ArgumentOutOfRangeException(nameof(BufferBytes));
        if (MaxReceiptBytes is < 1024 or > 16 * 1024 * 1024) throw new ArgumentOutOfRangeException(nameof(MaxReceiptBytes));
    }
}

public sealed record MapPackageRecordDescriptor(string KeySha256, long Length, string PayloadSha256);

/// <summary>
/// Append-only opaque package record store. Payloads are content-addressed and a
/// complete receipt is atomically published last. Readers hold a non-write-shared
/// payload handle until they dispose the returned stream.
/// </summary>
public sealed class MapPackageRecordStore
{
    private const int ReceiptFormatVersion = 1;
    private readonly string _root;
    private readonly string _entries;
    private readonly string _writerLockPath;
    private readonly MapPackageRecordStoreLimits _limits;

    public string Root => _root;
    public MapPackageRecordStoreLimits Limits => _limits;

    public MapPackageRecordStore(string root, MapPackageRecordStoreLimits? limits = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        _root = Path.GetFullPath(root);
        _entries = Path.Combine(_root, "entries");
        _writerLockPath = Path.Combine(_root, ".writer.lock");
        _limits = limits ?? new MapPackageRecordStoreLimits();
        _limits.Validate();
        Directory.CreateDirectory(_root);
        RejectReparsePoint(_root);
        Directory.CreateDirectory(_entries);
        RejectReparsePoint(_entries);
    }

    /// <summary>Starts one cache-wide writer session and scans disk usage once.</summary>
    public MapPackageRecordWriteSession BeginWriteSession()
    {
        var lease = AcquireWriterLease();
        try
        {
            var used = MeasureStoreBytes();
            if (used > _limits.MaxStoreBytes)
                throw new IOException("Existing package record store files exceed the configured total disk budget");
            return new MapPackageRecordWriteSession(this, lease, used);
        }
        catch
        {
            lease.Dispose();
            throw;
        }
    }

    internal MapPackageRecordDescriptor Write(MapPackageRecordWriteSession session, MapPackageRecordKey key, Stream source, bool captureComplete)
    {
        ArgumentNullException.ThrowIfNull(session);
        ArgumentNullException.ThrowIfNull(key);
        ArgumentNullException.ThrowIfNull(source);
        session.EnsureActive(this);
        if (!captureComplete) throw new InvalidDataException("Incomplete package captures cannot be published as reusable records");
        if (!source.CanRead) throw new ArgumentException("Payload source must be readable", nameof(source));

        if (TryOpenRead(key, out var existing))
        {
            using (existing!) return existing!.Descriptor;
        }

        var keyBytes = key.CanonicalBytes();
        var keyText = Encoding.UTF8.GetString(keyBytes);
        var keyHash = Hash(keyBytes);
        var entryDirectory = EntryDirectory(keyHash);
        var shardDirectory = Path.GetDirectoryName(entryDirectory)!;
        Directory.CreateDirectory(shardDirectory);
        RejectReparsePoint(shardDirectory);
        Directory.CreateDirectory(entryDirectory);
        RejectReparsePoint(entryDirectory);
        var receiptPath = Path.Combine(entryDirectory, "receipt.json");
        var existingReceiptIsIncomplete = ReadExistingReceiptState(receiptPath, keyHash, keyText);
        var receiptReserve = JsonSerializer.SerializeToUtf8Bytes(new ReceiptDocument(ReceiptFormatVersion, true,
            keyHash, keyText, long.MaxValue, new string('f', 64), "payload-" + new string('f', 64) + ".bin"), MapPackageRecordKey.JsonOptions).Length;
        if (receiptReserve > _limits.MaxReceiptBytes)
            throw new IOException("Package record key metadata cannot fit within the configured receipt bound");
        var writerId = Guid.NewGuid().ToString("N");
        var payloadTemporary = Path.Combine(entryDirectory, ".payload." + writerId + ".tmp");
        var receiptTemporary = Path.Combine(entryDirectory, ".receipt." + writerId + ".tmp");
        string? ownedFinalPayload = null;
        long ownedFinalPayloadBytes = 0;
        var receiptTemporaryBytes = 0L;
        var receiptPublished = false;
        try
        {
            long length = 0;
            string digest;
            using (var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256))
            using (var output = new FileStream(payloadTemporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                       _limits.BufferBytes, FileOptions.WriteThrough | FileOptions.SequentialScan))
            {
                var buffer = new byte[_limits.BufferBytes];
                while (true)
                {
                    var read = source.Read(buffer, 0, buffer.Length);
                    if (read == 0) break;
                    length = checked(length + read);
                    if (length > _limits.MaxEntryBytes)
                        throw new IOException("Package record exceeds the configured per-entry disk budget");
                    if (!FitsStoreBudget(session.UsedBytes, length, receiptReserve, _limits.MaxStoreBytes))
                        throw new IOException("Package record store total disk budget would be exceeded");
                    hash.AppendData(buffer, 0, read);
                    output.Write(buffer, 0, read);
                }
                output.Flush(flushToDisk: true);
                digest = Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
            }

            var payloadName = "payload-" + digest + ".bin";
            var payloadPath = Path.Combine(entryDirectory, payloadName);
            if (File.Exists(payloadPath))
            {
                using var existingPayload = OpenAndVerifyPayload(payloadPath, length, digest);
                File.Delete(payloadTemporary);
            }
            else
            {
                File.Move(payloadTemporary, payloadPath, overwrite: false);
                ownedFinalPayload = payloadPath;
                ownedFinalPayloadBytes = length;
                session.AddBytes(length);
            }

            var receipt = new ReceiptDocument(ReceiptFormatVersion, true, keyHash, keyText, length, digest, payloadName);
            var receiptBytes = JsonSerializer.SerializeToUtf8Bytes(receipt, MapPackageRecordKey.JsonOptions);
            if (receiptBytes.Length > _limits.MaxReceiptBytes)
                throw new IOException("Package record receipt exceeds the configured metadata bound");
            if (!FitsStoreBudget(session.UsedBytes, receiptBytes.Length, 0, _limits.MaxStoreBytes))
                throw new IOException("Package record store disk budget cannot accommodate the complete receipt");
            using (var output = new FileStream(receiptTemporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                       _limits.BufferBytes, FileOptions.WriteThrough))
            {
                output.Write(receiptBytes);
                output.Flush(flushToDisk: true);
            }
            receiptTemporaryBytes = receiptBytes.Length;
            session.AddBytes(receiptTemporaryBytes);

            var oldReceiptLength = existingReceiptIsIncomplete ? new FileInfo(receiptPath).Length : 0;
            // Receipt publication is the commit point. Valid immutable records are never overwritten.
            File.Move(receiptTemporary, receiptPath, overwrite: existingReceiptIsIncomplete);
            session.AddBytes(-oldReceiptLength);
            receiptTemporaryBytes = 0;
            receiptPublished = true;
            ownedFinalPayload = null;
            ownedFinalPayloadBytes = 0;
            return new MapPackageRecordDescriptor(keyHash, length, digest);
        }
        finally
        {
            if (File.Exists(payloadTemporary)) File.Delete(payloadTemporary);
            if (File.Exists(receiptTemporary))
            {
                var length = new FileInfo(receiptTemporary).Length;
                File.Delete(receiptTemporary);
                if (receiptTemporaryBytes > 0) session.AddBytes(-Math.Min(length, receiptTemporaryBytes));
            }
            if (!receiptPublished && ownedFinalPayload is not null && File.Exists(ownedFinalPayload))
            {
                File.Delete(ownedFinalPayload);
                session.AddBytes(-ownedFinalPayloadBytes);
            }
        }
    }

    /// <summary>
    /// Opens and validates one record using a bounded hash pass. The returned stream
    /// remains backed by a file handle opened without write or delete sharing.
    /// </summary>
    public bool TryOpenRead(MapPackageRecordKey key, out MapPackageRecordReadStream? record)
    {
        ArgumentNullException.ThrowIfNull(key);
        record = null;
        var keyBytes = key.CanonicalBytes();
        var keyText = Encoding.UTF8.GetString(keyBytes);
        var keyHash = Hash(keyBytes);
        var shardDirectory = Path.Combine(_entries, keyHash[..2]);
        if (!Directory.Exists(shardDirectory)) return false;
        RejectReparsePoint(shardDirectory);
        var entryDirectory = EntryDirectory(keyHash);
        if (!Directory.Exists(entryDirectory)) return false;
        RejectReparsePoint(entryDirectory);
        var receiptPath = Path.Combine(entryDirectory, "receipt.json");
        if (!File.Exists(receiptPath)) return false;
        RejectReparsePoint(receiptPath);

        ReceiptDocument receipt;
        using (var receiptStream = new FileStream(receiptPath, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            if (receiptStream.Length < 2 || receiptStream.Length > _limits.MaxReceiptBytes)
                throw new InvalidDataException("Package record receipt length is invalid");
            using var document = JsonDocument.Parse(receiptStream, new JsonDocumentOptions { MaxDepth = 8 });
            var root = document.RootElement;
            receipt = new ReceiptDocument(
                root.GetProperty("formatVersion").GetInt32(), root.GetProperty("complete").GetBoolean(),
                root.GetProperty("keySha256").GetString() ?? "", root.GetProperty("keyCanonical").GetString() ?? "",
                root.GetProperty("payloadLength").GetInt64(), root.GetProperty("payloadSha256").GetString() ?? "",
                root.GetProperty("payloadName").GetString() ?? "");
        }
        if (receipt.FormatVersion != ReceiptFormatVersion) throw new InvalidDataException("Package record receipt version is unsupported");
        if (receipt.KeySha256 != keyHash || receipt.KeyCanonical != keyText)
            throw new InvalidDataException("Package record receipt belongs to a different source key");
        if (!receipt.Complete) return false;
        if (receipt.PayloadLength < 0 || receipt.PayloadLength > _limits.MaxEntryBytes ||
            receipt.PayloadSha256.Length != 64 || receipt.PayloadSha256.Any(character => !Uri.IsHexDigit(character)) ||
            receipt.PayloadName != "payload-" + receipt.PayloadSha256.ToLowerInvariant() + ".bin")
            throw new InvalidDataException("Package record receipt payload descriptor is invalid");

        var payloadPath = Path.Combine(entryDirectory, receipt.PayloadName);
        RejectReparsePoint(payloadPath);
        var payload = new FileStream(payloadPath, FileMode.Open, FileAccess.Read, FileShare.Read,
            _limits.BufferBytes, FileOptions.SequentialScan);
        try
        {
            if (payload.Length != receipt.PayloadLength)
                throw new InvalidDataException("Package record payload length does not match its receipt");
            var digest = HashStream(payload, _limits.BufferBytes, _limits.MaxEntryBytes);
            if (digest != receipt.PayloadSha256.ToLowerInvariant())
                throw new InvalidDataException("Package record payload checksum does not match its receipt");
            payload.Position = 0;
            record = new MapPackageRecordReadStream(payload, _limits.BufferBytes,
                new MapPackageRecordDescriptor(keyHash, receipt.PayloadLength, digest));
            payload = null!;
            return true;
        }
        finally
        {
            payload?.Dispose();
        }
    }

    private FileStream OpenAndVerifyPayload(string path, long expectedLength, string expectedHash)
    {
        var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read,
            _limits.BufferBytes, FileOptions.SequentialScan);
        try
        {
            if (stream.Length != expectedLength || HashStream(stream, _limits.BufferBytes, _limits.MaxEntryBytes) != expectedHash)
                throw new InvalidDataException("Content-addressed package payload collides with different bytes");
            stream.Position = 0;
            return stream;
        }
        catch
        {
            stream.Dispose();
            throw;
        }
    }

    private bool ReadExistingReceiptState(string receiptPath, string keyHash, string keyText)
    {
        if (!File.Exists(receiptPath)) return false;
        RejectReparsePoint(receiptPath);
        using var stream = new FileStream(receiptPath, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (stream.Length < 2 || stream.Length > _limits.MaxReceiptBytes)
            throw new InvalidDataException("Existing package record receipt is corrupt; it was left untouched");
        using var document = JsonDocument.Parse(stream, new JsonDocumentOptions { MaxDepth = 8 });
        var root = document.RootElement;
        var version = root.GetProperty("formatVersion").GetInt32();
        var complete = root.GetProperty("complete").GetBoolean();
        var existingKeyHash = root.GetProperty("keySha256").GetString();
        var existingKey = root.GetProperty("keyCanonical").GetString();
        if (version != ReceiptFormatVersion || existingKeyHash != keyHash || existingKey != keyText)
            throw new InvalidDataException("Existing package receipt has the wrong format or source key; it was left untouched");
        return !complete;
    }

    private FileStream AcquireWriterLease()
    {
        try
        {
            return new FileStream(_writerLockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.WriteThrough);
        }
        catch (IOException exception)
        {
            throw new IOException("Another writer currently owns this package record store", exception);
        }
    }

    private long MeasureStoreBytes()
    {
        // One metadata-only recursive scan per write session, not per package record.
        // The scan counts payloads, receipts, temporary files, and crash orphans without reading payload bytes.
        long total = 0;
        var pending = new Stack<string>();
        pending.Push(_root);
        while (pending.Count > 0)
        {
            var directory = pending.Pop();
            RejectReparsePoint(directory);
            foreach (var path in Directory.EnumerateFileSystemEntries(directory))
            {
                var attributes = File.GetAttributes(path);
                if ((attributes & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("Package record store refuses reparse points");
                if ((attributes & FileAttributes.Directory) != 0) pending.Push(path);
                else
                {
                    total = checked(total + new FileInfo(path).Length);
                    if (total > _limits.MaxStoreBytes)
                        throw new IOException("Existing package record store files exceed the configured total disk budget");
                }
            }
        }
        return total;
    }

    private string EntryDirectory(string keyHash)
    {
        var path = Path.GetFullPath(Path.Combine(_entries, keyHash[..2], keyHash));
        var prefix = _entries.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!path.StartsWith(prefix, OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
            throw new InvalidDataException("Package record path escaped the configured store root");
        return path;
    }

    internal static bool FitsStoreBudget(long usedBytes, long additionalBytes, long reservedBytes, long maximumBytes)
    {
        if (usedBytes < 0 || additionalBytes < 0 || reservedBytes < 0 || maximumBytes < 0 || usedBytes > maximumBytes)
            return false;
        var remaining = maximumBytes - usedBytes;
        if (additionalBytes > remaining) return false;
        return reservedBytes <= remaining - additionalBytes;
    }

    private static string Hash(ReadOnlySpan<byte> bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static string HashStream(Stream stream, int bufferBytes, long maxBytes)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[bufferBytes];
        long total = 0;
        int read;
        while ((read = stream.Read(buffer, 0, buffer.Length)) != 0)
        {
            total = checked(total + read);
            if (total > maxBytes) throw new InvalidDataException("Package record exceeds the configured read budget");
            hash.AppendData(buffer, 0, read);
        }
        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    private static void RejectReparsePoint(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Package record store refuses reparse points");
    }

    private static void TryDeleteOwned(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private sealed record ReceiptDocument(
        int FormatVersion, bool Complete, string KeySha256, string KeyCanonical,
        long PayloadLength, string PayloadSha256, string PayloadName);
}

public sealed class MapPackageRecordWriteSession : IDisposable
{
    private readonly MapPackageRecordStore _store;
    private readonly object _gate = new();
    private FileStream? _lease;
    internal long UsedBytes { get; private set; }
    public bool IsDisposed => _lease is null;

    internal MapPackageRecordWriteSession(MapPackageRecordStore store, FileStream lease, long usedBytes)
    {
        _store = store;
        _lease = lease;
        UsedBytes = usedBytes;
    }

    public MapPackageRecordDescriptor Write(MapPackageRecordKey key, Stream source, bool captureComplete)
    {
        lock (_gate)
        {
            EnsureActive(_store);
            return _store.Write(this, key, source, captureComplete);
        }
    }

    internal void EnsureActive(MapPackageRecordStore store)
    {
        ObjectDisposedException.ThrowIf(_lease is null, this);
        if (!ReferenceEquals(_store, store)) throw new InvalidOperationException("Write session belongs to another record store");
    }

    internal void AddBytes(long delta)
    {
        UsedBytes = checked(UsedBytes + delta);
        if (UsedBytes < 0) throw new InvalidOperationException("Package store disk accounting underflow");
    }

    public void Dispose()
    {
        lock (_gate)
        {
            _lease?.Dispose();
            _lease = null;
        }
    }
}

public sealed class MapPackageRecordReadStream : Stream
{
    private readonly FileStream _payload;
    private readonly int _maxReadBytes;
    private bool _disposed;

    internal MapPackageRecordReadStream(FileStream payload, int maxReadBytes, MapPackageRecordDescriptor descriptor)
    {
        _payload = payload;
        _maxReadBytes = maxReadBytes;
        Descriptor = descriptor;
    }

    public MapPackageRecordDescriptor Descriptor { get; }
    public override bool CanRead => !_disposed;
    public override bool CanSeek => false;
    public override bool CanWrite => false;
    public override long Length => _payload.Length;
    public override long Position { get => _payload.Position; set => throw new NotSupportedException(); }
    public override void Flush() => throw new NotSupportedException();
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    public override int Read(byte[] buffer, int offset, int count)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentNullException.ThrowIfNull(buffer);
        ArgumentOutOfRangeException.ThrowIfNegative(offset);
        ArgumentOutOfRangeException.ThrowIfNegative(count);
        if (offset > buffer.Length - count) throw new ArgumentException("Invalid read buffer range");
        return _payload.Read(buffer, offset, Math.Min(count, _maxReadBytes));
    }

    public override int Read(Span<byte> buffer)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        return _payload.Read(buffer[..Math.Min(buffer.Length, _maxReadBytes)]);
    }

    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        return _payload.ReadAsync(buffer[..Math.Min(buffer.Length, _maxReadBytes)], cancellationToken);
    }

    protected override void Dispose(bool disposing)
    {
        if (!_disposed)
        {
            _disposed = true;
            if (disposing) _payload.Dispose();
        }
        base.Dispose(disposing);
    }
}

