using System.Security.Cryptography;
using System.Text;
using ArcPak;
using CUE4Parse.UE4.Assets.Exports;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace ArcPak;

public sealed record MapRawPackageRecordLimits(
    int MaxRows = 100_000,
    int MaxRowBytes = 8 * 1024 * 1024,
    int MaxMetadataBytes = 2 * 1024 * 1024,
    int MaxDepth = 96,
    long MaxEntryBytes = 8L * 1024 * 1024 * 1024,
    int MaxDiagnostics = 10_000,
    int BufferBytes = 64 * 1024)
{
    internal void Validate()
    {
        if (MaxRows is < 1 or > 10_000_000) throw new ArgumentOutOfRangeException(nameof(MaxRows));
        if (MaxRowBytes is < 128 or > 512 * 1024 * 1024) throw new ArgumentOutOfRangeException(nameof(MaxRowBytes));
        if (MaxMetadataBytes is < 256 or > 16 * 1024 * 1024) throw new ArgumentOutOfRangeException(nameof(MaxMetadataBytes));
        if (MaxDepth is < 4 or > 512) throw new ArgumentOutOfRangeException(nameof(MaxDepth));
        if (MaxEntryBytes < 1 || MaxEntryBytes > 8L * 1024 * 1024 * 1024) throw new ArgumentOutOfRangeException(nameof(MaxEntryBytes));
        if (MaxDiagnostics is < 0 or > 1_000_000) throw new ArgumentOutOfRangeException(nameof(MaxDiagnostics));
        if (BufferBytes is < 4096 or > 1024 * 1024) throw new ArgumentOutOfRangeException(nameof(BufferBytes));
    }
}

/// <summary>Scalar descriptor for a complete raw-metadata transaction. It retains no UObject or provider.</summary>
public sealed record MapRawPackageRecordDescriptor(
    MapPackageRecordDescriptor StoreRecord,
    string CanonicalPackage,
    string CaptureProfileSha256,
    int RowCount,
    bool CaptureTransactionComplete,
    bool DeclaredSourceCoverageComplete,
    bool EffectiveSourceCoverageComplete,
    bool CacheHit);

public sealed record MapRawPackageRecordHeader(
    int SchemaVersion,
    string Scope,
    string CanonicalPackage,
    string CaptureProfileSha256,
    int RowCount,
    bool CaptureTransactionComplete,
    bool DeclaredSourceCoverageComplete,
    bool EffectiveSourceCoverageComplete,
    IReadOnlyList<string> Diagnostics);

/// <summary>One bounded replay row. The JObject contains raw metadata only, never a live UObject.</summary>
public sealed record MapRawPackageRecordRow(
    string SourcePath,
    string? OwnerPackageName,
    string? SourceExportType,
    JObject Raw,
    IReadOnlyList<string> Diagnostics);

/// <summary>
/// Streams the existing Raw serializer output into the opaque package-record store.
/// The profile covers decoded raw metadata only; it does not certify map completeness.
/// Raw itself materializes one JObject per export before the row byte bound is checked.
/// Spool and replay memory are bounded; this codec is not a per-UObject or provider memory ceiling.
/// </summary>
public static class MapRawPackageRecord
{
    public const int SchemaVersion = 1;
    private const string Scope = "raw-package-metadata";
    private const string ProfilePrefix = "arc-pak/raw-package-metadata/v1;declared-source-coverage=";
    private static readonly UTF8Encoding Utf8Strict = new(false, true);
    private static readonly UTF8Encoding Utf8NoBom = new(false, true);

    /// <summary>Build the profile identity from source-coverage evidence known before decoding.</summary>
    public static string CaptureProfileSha256(bool declaredSourceCoverageComplete)
        => HashText(ProfileText(declaredSourceCoverageComplete));

    /// <summary>
    /// Reuses a verified stored record before invoking openExports. The predecode declared
    /// coverage argument applies only to a miss; a hit returns the stored declaration/profile.
    /// </summary>
    public static MapRawPackageRecordDescriptor CaptureOrReuse(
        MapPackageRecordStore store,
        MapPackageRecordKey key,
        string canonicalPackage,
        Func<IEnumerable<UObject>>? openExports,
        bool declaredSourceCoverageComplete,
        string temporaryRoot,
        MapRawPackageRecordLimits? limits = null,
        MapPackageRecordWriteSession? writeSession = null)
    {
        ArgumentNullException.ThrowIfNull(store);
        ArgumentNullException.ThrowIfNull(key);
        ValidateCanonicalPackage(canonicalPackage, key);
        if (writeSession is not null) writeSession.EnsureActive(store);
        limits ??= new MapRawPackageRecordLimits();
        limits.Validate();

        var existing = ReadExisting(store, key, limits);
        if (existing is not null) return existing;

        if (openExports is null) throw new ArgumentNullException(nameof(openExports), "A cache miss requires an export iterator");
        var expectedProfile = CaptureProfileSha256(declaredSourceCoverageComplete);
        if (!string.Equals(key.CaptureProfileSha256, expectedProfile, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Package record key capture profile does not match the requested raw coverage state");
        if (key.SchemaVersion != SchemaVersion)
            throw new InvalidDataException("Package record key schema does not match the raw package record schema");

        var tempRoot = Path.GetFullPath(temporaryRoot);
        if (!string.Equals(Path.GetPathRoot(tempRoot), Path.GetPathRoot(store.Root), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Raw package spool and record store must use the same volume");
        Directory.CreateDirectory(tempRoot);
        RejectReparsePoint(tempRoot);
        var spoolPath = Path.Combine(tempRoot, "raw-package-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            var captured = CaptureRows(openExports(), spoolPath, declaredSourceCoverageComplete, limits,
                Math.Min(limits.MaxEntryBytes, store.Limits.MaxEntryBytes));
            using var header = BuildHeader(canonicalPackage, key.CaptureProfileSha256, captured,
                declaredSourceCoverageComplete, limits);
            using var rows = new FileStream(spoolPath, FileMode.Open, FileAccess.Read, FileShare.Read,
                limits.BufferBytes, FileOptions.SequentialScan);
            var totalLength = checked(header.Length + rows.Length);
            if (totalLength > Math.Min(limits.MaxEntryBytes, store.Limits.MaxEntryBytes))
                throw new IOException("Raw package record exceeds the configured complete-entry budget");
            header.Position = 0;
            using var payload = new ConcatenatedReadStream(header, rows);

            if (writeSession is not null)
            {
                writeSession.EnsureActive(store);
                existing = ReadExisting(store, key, limits);
                if (existing is not null) return existing;
                var descriptor = writeSession.Write(key, payload, captureComplete: true);
                return CreateDescriptor(descriptor, canonicalPackage, key.CaptureProfileSha256,
                    captured, declaredSourceCoverageComplete, cacheHit: false);
            }

            using (var ownedSession = store.BeginWriteSession())
            {
                existing = ReadExisting(store, key, limits);
                if (existing is not null) return existing;
                var descriptor = ownedSession.Write(key, payload, captureComplete: true);
                return CreateDescriptor(descriptor, canonicalPackage, key.CaptureProfileSha256,
                    captured, declaredSourceCoverageComplete, cacheHit: false);
            }
        }
        finally
        {
            if (File.Exists(spoolPath)) File.Delete(spoolPath);
        }
    }

    /// <summary>Opens a verified record and replays one bounded raw row per call.</summary>
    public static MapRawPackageRecordReader OpenRead(
        MapPackageRecordStore store,
        MapPackageRecordKey key,
        MapRawPackageRecordLimits? limits = null)
    {
        ArgumentNullException.ThrowIfNull(store);
        ArgumentNullException.ThrowIfNull(key);
        limits ??= new MapRawPackageRecordLimits();
        limits.Validate();
        if (!store.TryOpenRead(key, out var record) || record is null)
            throw new FileNotFoundException("Raw package record is not available in the record store");
        try
        {
            return new MapRawPackageRecordReader(record, key, limits);
        }
        catch
        {
            record.Dispose();
            throw;
        }
    }

    internal static bool HasRawOmission(IEnumerable<string> diagnostics)
        => diagnostics.Any(message => message.StartsWith("Raw property omitted:", StringComparison.Ordinal) ||
            message.StartsWith("Decoded export omitted:", StringComparison.Ordinal));

    internal static bool IsEffectiveSourceCoverageComplete(bool declaredSourceCoverageComplete, IEnumerable<string> diagnostics)
        => declaredSourceCoverageComplete && !HasRawOmission(diagnostics);

    private static CapturedRows CaptureRows(
        IEnumerable<UObject> exports,
        string spoolPath,
        bool declaredSourceCoverageComplete,
        MapRawPackageRecordLimits limits,
        long maxEntryBytes)
    {
        ArgumentNullException.ThrowIfNull(exports);
        var allDiagnostics = new SortedSet<string>(StringComparer.Ordinal);
        var diagnosticsBytes = 0;
        var seenPaths = new HashSet<string>(StringComparer.Ordinal);
        var rowsWritten = 0;
        using (var output = new FileStream(spoolPath, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                   limits.BufferBytes, FileOptions.WriteThrough | FileOptions.SequentialScan))
        using (var bounded = new BoundedWriteStream(output, maxEntryBytes, limits.MaxRowBytes))
        using (var text = new StreamWriter(bounded, Utf8NoBom, limits.BufferBytes, leaveOpen: true))
        using (var writer = new JsonTextWriter(text) { Formatting = Formatting.None, CloseOutput = false })
        {
            foreach (var source in exports)
            {
                if (rowsWritten >= limits.MaxRows)
                    throw new InvalidDataException("Raw package record exceeds its configured row-count bound");
                if (source is null) throw new InvalidDataException("Raw package export iterator contains a null UObject");

                var sourcePath = ValidateMetadataString(source.GetPathName(), "source path", limits.MaxMetadataBytes);
                if (!seenPaths.Add(sourcePath))
                    throw new InvalidDataException("Raw package export iterator contains a duplicate source path");
                var ownerPackage = source.Owner?.Name;
                if (ownerPackage is not null) ValidateMetadataString(ownerPackage, "owner package name", limits.MaxMetadataBytes);
                var exportType = source.ExportType;
                if (exportType is not null) ValidateMetadataString(exportType, "source export type", limits.MaxMetadataBytes);

                var rowDiagnostics = new SortedSet<string>(StringComparer.Ordinal);
                var raw = MapBundleExport.Raw(source, rowDiagnostics);
                ValidateTokenDepth(raw, limits.MaxDepth);
                ValidateRawRowIdentity(raw, sourcePath);
                foreach (var message in rowDiagnostics)
                {
                    ValidateMetadataString(message, "raw diagnostic", limits.MaxMetadataBytes);
                    if (allDiagnostics.Add(message))
                    {
                        diagnosticsBytes = checked(diagnosticsBytes + Utf8Strict.GetByteCount(message));
                        if (diagnosticsBytes > limits.MaxMetadataBytes)
                            throw new InvalidDataException("Raw package record diagnostic metadata exceeds its bound");
                    }
                    if (allDiagnostics.Count > limits.MaxDiagnostics)
                        throw new InvalidDataException("Raw package record exceeds its configured diagnostic-count bound");
                }

                bounded.BeginRow();
                writer.WriteStartObject();
                writer.WritePropertyName("sourcePath"); writer.WriteValue(sourcePath);
                writer.WritePropertyName("ownerPackageName"); writer.WriteValue(ownerPackage);
                writer.WritePropertyName("sourceExportType"); writer.WriteValue(exportType);
                writer.WritePropertyName("diagnostics"); WriteStrings(writer, rowDiagnostics);
                writer.WritePropertyName("raw"); raw.WriteTo(writer);
                writer.WriteEndObject();
                writer.Flush();
                text.Flush();
                bounded.WriteByte((byte)'\n');
                rowsWritten++;
            }
            writer.Flush();
            text.Flush();
            output.Flush(flushToDisk: true);
        }

        var effectiveCoverageComplete = IsEffectiveSourceCoverageComplete(declaredSourceCoverageComplete, allDiagnostics);
        return new CapturedRows(rowsWritten, Array.AsReadOnly(allDiagnostics.ToArray()), effectiveCoverageComplete);
    }

    private static MemoryStream BuildHeader(
        string canonicalPackage,
        string captureProfileSha256,
        CapturedRows captured,
        bool declaredSourceCoverageComplete,
        MapRawPackageRecordLimits limits)
    {
        var stream = new MemoryStream();
        using (var bounded = new BoundedWriteStream(stream, limits.MaxMetadataBytes, limits.MaxMetadataBytes))
        using (var text = new StreamWriter(bounded, Utf8NoBom, limits.BufferBytes, leaveOpen: true))
        using (var writer = new JsonTextWriter(text) { Formatting = Formatting.None, CloseOutput = false })
        {
            writer.WriteStartObject();
            writer.WritePropertyName("schemaVersion"); writer.WriteValue(SchemaVersion);
            writer.WritePropertyName("scope"); writer.WriteValue(Scope);
            writer.WritePropertyName("canonicalPackage"); writer.WriteValue(canonicalPackage);
            writer.WritePropertyName("captureProfileSha256"); writer.WriteValue(captureProfileSha256);
            writer.WritePropertyName("captureTransactionComplete"); writer.WriteValue(true);
            writer.WritePropertyName("declaredSourceCoverageComplete"); writer.WriteValue(declaredSourceCoverageComplete);
            writer.WritePropertyName("effectiveSourceCoverageComplete"); writer.WriteValue(captured.EffectiveSourceCoverageComplete);
            writer.WritePropertyName("rowCount"); writer.WriteValue(captured.RowCount);
            writer.WritePropertyName("diagnostics"); WriteStrings(writer, captured.Diagnostics);
            writer.WriteEndObject();
            writer.Flush();
            text.Flush();
            bounded.WriteByte((byte)'\n');
            stream.Flush();
        }
        stream.Position = 0;
        return stream;
    }

    private static void WriteStrings(JsonWriter writer, IEnumerable<string> values)
    {
        writer.WriteStartArray();
        foreach (var value in values) writer.WriteValue(value);
        writer.WriteEndArray();
    }

    private static MapRawPackageRecordDescriptor? ReadExisting(
        MapPackageRecordStore store,
        MapPackageRecordKey key,
        MapRawPackageRecordLimits limits)
    {
        if (!store.TryOpenRead(key, out var record) || record is null) return null;
        using (record)
        using (var reader = new MapRawPackageRecordReader(record, key, limits))
        {
            while (reader.TryReadNext(out _)) { }
            return CreateDescriptor(reader.StoreRecord, reader.Header.CanonicalPackage,
                reader.Header.CaptureProfileSha256,
                new CapturedRows(reader.Header.RowCount, reader.Header.Diagnostics, reader.Header.EffectiveSourceCoverageComplete),
                reader.Header.DeclaredSourceCoverageComplete, cacheHit: true);
        }
    }

    private static MapRawPackageRecordDescriptor CreateDescriptor(
        MapPackageRecordDescriptor storeRecord,
        string canonicalPackage,
        string profileSha256,
        CapturedRows captured,
        bool declaredSourceCoverageComplete,
        bool cacheHit)
        => new(storeRecord, canonicalPackage, profileSha256, captured.RowCount,
            true, declaredSourceCoverageComplete, captured.EffectiveSourceCoverageComplete, cacheHit);

    private static void ValidateCanonicalPackage(string canonicalPackage, MapPackageRecordKey key)
    {
        ArgumentNullException.ThrowIfNull(canonicalPackage);
        if (!string.Equals(canonicalPackage, key.CanonicalPackage, StringComparison.Ordinal) ||
            !string.Equals(MapBundleDocument.CanonicalPackage(canonicalPackage), canonicalPackage, StringComparison.Ordinal))
            throw new ArgumentException("Raw package record canonical package must exactly match the record key", nameof(canonicalPackage));
    }

    private static string ProfileText(bool declaredSourceCoverageComplete)
        => ProfilePrefix + (declaredSourceCoverageComplete ? "true" : "false");

    private static string HashText(string value)
        => Convert.ToHexString(SHA256.HashData(Utf8Strict.GetBytes(value))).ToLowerInvariant();

    internal static string ValidateMetadataString(string value, string label, int maxBytes)
    {
        if (value.IndexOf('\0') >= 0) throw new InvalidDataException("Raw package record " + label + " contains a NUL");
        if (Utf8Strict.GetByteCount(value) > maxBytes)
            throw new InvalidDataException("Raw package record " + label + " exceeds its metadata bound");
        return value;
    }

    private static void ValidateRawRowIdentity(JObject raw, string sourcePath)
    {
        var rawPath = raw.Value<string>("path");
        if (!string.Equals(rawPath, sourcePath, StringComparison.Ordinal))
            throw new InvalidDataException("Raw serializer source path does not match the package record row");
    }

    private static void ValidateTokenDepth(JToken root, int maxDepth)
    {
        var stack = new Stack<(IEnumerator<JToken> Children, int Depth)>();
        if (root is not JContainer container) return;
        stack.Push((container.Children().GetEnumerator(), 1));
        try
        {
            while (stack.Count > 0)
            {
                var frame = stack.Pop();
                if (!frame.Children.MoveNext())
                {
                    frame.Children.Dispose();
                    continue;
                }
                stack.Push(frame);
                var child = frame.Children.Current;
                var depth = frame.Depth + 1;
                if (depth > maxDepth) throw new InvalidDataException("Raw package record row exceeds its configured JSON depth");
                if (child is JContainer nested) stack.Push((nested.Children().GetEnumerator(), depth));
            }
        }
        finally
        {
            while (stack.Count > 0) stack.Pop().Children.Dispose();
        }
    }

    private static void RejectReparsePoint(string path)
    {
        var attributes = File.GetAttributes(path);
        if ((attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Raw package spool root cannot be a reparse point");
    }

    private sealed record CapturedRows(int RowCount, IReadOnlyList<string> Diagnostics, bool EffectiveSourceCoverageComplete);

    private sealed class BoundedWriteStream(Stream inner, long maxTotalBytes, long maxRowBytes) : Stream
    {
        private long _totalBytes;
        private long _rowBytes;
        public override bool CanRead => false;
        public override bool CanSeek => false;
        public override bool CanWrite => true;
        public override long Length => _totalBytes;
        public override long Position { get => _totalBytes; set => throw new NotSupportedException(); }

        internal void BeginRow() => _rowBytes = 0;

        public override void Flush() => inner.Flush();

        public override void Write(byte[] buffer, int offset, int count)
        {
            ArgumentNullException.ThrowIfNull(buffer);
            if (offset < 0 || count < 0 || offset > buffer.Length - count) throw new ArgumentOutOfRangeException(nameof(offset));
            EnsureBudget(count);
            inner.Write(buffer, offset, count);
            _totalBytes += count;
            _rowBytes += count;
        }

        public override void Write(ReadOnlySpan<byte> buffer)
        {
            EnsureBudget(buffer.Length);
            inner.Write(buffer);
            _totalBytes += buffer.Length;
            _rowBytes += buffer.Length;
        }

        private void EnsureBudget(int count)
        {
            if (count < 0 || _totalBytes > maxTotalBytes - count)
                throw new IOException("Raw package spool exceeds its configured entry byte budget");
            if (_rowBytes > maxRowBytes - count)
                throw new InvalidDataException("Raw package row exceeds its configured byte budget");
        }

        protected override void Dispose(bool disposing) { base.Dispose(disposing); }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
    }

    private sealed class ConcatenatedReadStream(Stream first, Stream second) : Stream
    {
        private bool _firstComplete;
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }

        public override int Read(byte[] buffer, int offset, int count)
        {
            ArgumentNullException.ThrowIfNull(buffer);
            if (offset < 0 || count < 0 || offset > buffer.Length - count) throw new ArgumentOutOfRangeException(nameof(offset));
            if (count == 0) return 0;
            if (!_firstComplete)
            {
                var read = first.Read(buffer, offset, count);
                if (read > 0) return read;
                _firstComplete = true;
            }
            return second.Read(buffer, offset, count);
        }

        public override int Read(Span<byte> buffer)
        {
            if (buffer.Length == 0) return 0;
            if (!_firstComplete)
            {
                var read = first.Read(buffer);
                if (read > 0) return read;
                _firstComplete = true;
            }
            return second.Read(buffer);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) { first.Dispose(); second.Dispose(); }
            base.Dispose(disposing);
        }

        public override void Flush() { }
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
    }
}

public sealed class MapRawPackageRecordReader : IDisposable
{
    private readonly MapPackageRecordReadStream _record;
    private readonly MapRawPackageRecordLimits _limits;
    private readonly BoundedLineReader _lines;
    private readonly SortedSet<string> _seenDiagnostics = new(StringComparer.Ordinal);
    private readonly HashSet<string> _seenPaths = new(StringComparer.Ordinal);
    private bool _finished;
    private bool _disposed;

    public MapRawPackageRecordHeader Header { get; }
    public MapPackageRecordDescriptor StoreRecord => _record.Descriptor;
    public int RowsRead { get; private set; }

    internal MapRawPackageRecordReader(
        MapPackageRecordReadStream record,
        MapPackageRecordKey key,
        MapRawPackageRecordLimits limits)
    {
        _record = record;
        _limits = limits;
        _lines = new BoundedLineReader(record, limits.BufferBytes);
        if (!_lines.TryReadLine(limits.MaxMetadataBytes, out var line))
            throw new InvalidDataException("Raw package record header is missing");
        var document = ParseObject(line, limits.MaxDepth, "header");
        RequireExactFields(document, "schemaVersion", "scope", "canonicalPackage", "captureProfileSha256",
            "captureTransactionComplete", "declaredSourceCoverageComplete", "effectiveSourceCoverageComplete", "rowCount", "diagnostics");
        var schemaVersion = ReadInt32(document, "schemaVersion");
        var scope = ReadString(document, "scope");
        var canonicalPackage = ReadString(document, "canonicalPackage");
        var profileSha256 = ReadString(document, "captureProfileSha256");
        var captureComplete = ReadBoolean(document, "captureTransactionComplete");
        var declaredSourceCoverageComplete = ReadBoolean(document, "declaredSourceCoverageComplete");
        var effectiveCoverageComplete = ReadBoolean(document, "effectiveSourceCoverageComplete");
        var rowCount = ReadInt32(document, "rowCount");
        var diagnostics = ReadStrings(document, "diagnostics", limits.MaxDiagnostics);

        if (schemaVersion != MapRawPackageRecord.SchemaVersion || schemaVersion != key.SchemaVersion || scope != "raw-package-metadata")
            throw new InvalidDataException("Raw package record schema or scope is unsupported");
        if (canonicalPackage != key.CanonicalPackage)
            throw new InvalidDataException("Raw package record header belongs to another canonical package");
        if (!string.Equals(profileSha256, key.CaptureProfileSha256, StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(MapRawPackageRecord.CaptureProfileSha256(declaredSourceCoverageComplete), profileSha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Raw package record capture profile does not match its key");
        if (!captureComplete) throw new InvalidDataException("Incomplete raw package capture cannot be replayed");
        if (rowCount < 0 || rowCount > limits.MaxRows)
            throw new InvalidDataException("Raw package record row count exceeds its configured bound");
        var expectedEffective = MapRawPackageRecord.IsEffectiveSourceCoverageComplete(declaredSourceCoverageComplete, diagnostics);
        if (effectiveCoverageComplete != expectedEffective)
            throw new InvalidDataException("Raw package record effective coverage flag is inconsistent with its diagnostics");

        Header = new MapRawPackageRecordHeader(schemaVersion, scope, canonicalPackage, profileSha256,
            rowCount, captureComplete, declaredSourceCoverageComplete, effectiveCoverageComplete, Array.AsReadOnly(diagnostics));
    }

    public bool TryReadNext(out MapRawPackageRecordRow? row)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        row = null;
        if (_finished) return false;
        if (RowsRead >= Header.RowCount)
        {
            CompleteRows();
            return false;
        }
        if (!_lines.TryReadLine(_limits.MaxRowBytes, out var line))
            throw new InvalidDataException("Raw package record ended before its declared row count");
        var document = ParseObject(line, _limits.MaxDepth, "row");
        RequireExactFields(document, "sourcePath", "ownerPackageName", "sourceExportType", "diagnostics", "raw");
        var sourcePath = MapRawPackageRecord.ValidateMetadataString(
            ReadString(document, "sourcePath"), "source path", _limits.MaxMetadataBytes);
        var ownerName = ReadNullableString(document, "ownerPackageName");
        if (ownerName is not null) MapRawPackageRecord.ValidateMetadataString(ownerName, "owner package name", _limits.MaxMetadataBytes);
        var exportType = ReadNullableString(document, "sourceExportType");
        if (exportType is not null) MapRawPackageRecord.ValidateMetadataString(exportType, "source export type", _limits.MaxMetadataBytes);
        var diagnostics = ReadStrings(document, "diagnostics", _limits.MaxDiagnostics);
        var raw = document["raw"] as JObject ?? throw new InvalidDataException("Raw package record row has no raw JObject");
        if (!string.Equals(raw.Value<string>("path"), sourcePath, StringComparison.Ordinal))
            throw new InvalidDataException("Raw package row path does not match its raw document");
        if (!string.Equals(raw.Value<string>("kind"), exportType, StringComparison.Ordinal))
            throw new InvalidDataException("Raw package row export type does not match its raw document");
        if (!_seenPaths.Add(sourcePath)) throw new InvalidDataException("Raw package record contains a duplicate source path");
        foreach (var message in diagnostics)
        {
            _seenDiagnostics.Add(message);
            if (_seenDiagnostics.Count > _limits.MaxDiagnostics)
                throw new InvalidDataException("Raw package record diagnostics exceed their configured count bound");
        }
        RowsRead++;
        row = new MapRawPackageRecordRow(sourcePath, ownerName, exportType, raw, Array.AsReadOnly(diagnostics));
        return true;
    }

    private void CompleteRows()
    {
        if (_lines.TryReadLine(_limits.MaxRowBytes, out _))
            throw new InvalidDataException("Raw package record contains rows beyond its declared count");
        if (!_seenDiagnostics.SetEquals(Header.Diagnostics))
            throw new InvalidDataException("Raw package record header diagnostics do not match its rows");
        _finished = true;
    }

    private JObject ParseObject(MemoryStream line, int maxDepth, string label)
    {
        if (line.Length == 0) throw new InvalidDataException("Raw package record " + label + " line is empty");
        line.Position = 0;
        try
        {
            using var text = new StreamReader(line, new UTF8Encoding(false, true), detectEncodingFromByteOrderMarks: false,
                bufferSize: Math.Min(_limits.BufferBytes, 16 * 1024), leaveOpen: true);
            using var reader = new JsonTextReader(text) { MaxDepth = maxDepth, DateParseHandling = DateParseHandling.None, CloseInput = false };
            if (!reader.Read() || reader.TokenType != JsonToken.StartObject)
                throw new InvalidDataException("Raw package record " + label + " line must be a JSON object");
            var value = JObject.Load(reader, new JsonLoadSettings
            {
                DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error
            });
            if (reader.Read()) throw new InvalidDataException("Raw package record " + label + " line has trailing JSON");
            return value;
        }
        catch (JsonException exception)
        {
            throw new InvalidDataException("Raw package record " + label + " line contains invalid JSON or exceeds its depth bound", exception);
        }
    }

    private static void RequireExactFields(JObject value, params string[] expected)
    {
        var actual = value.Properties().Select(property => property.Name).Order(StringComparer.Ordinal);
        var wanted = expected.Order(StringComparer.Ordinal);
        if (!actual.SequenceEqual(wanted, StringComparer.Ordinal))
            throw new InvalidDataException("Raw package record object has missing or unsupported fields");
    }

    private static string ReadString(JObject value, string property)
    {
        if (value[property]?.Type != JTokenType.String)
            throw new InvalidDataException("Raw package record field " + property + " must be a string");
        return value.Value<string>(property)!;
    }

    private static string? ReadNullableString(JObject value, string property)
    {
        var token = value[property];
        if (token is null || token.Type == JTokenType.Null) throw new InvalidDataException("Raw package record field " + property + " is missing");
        if (token.Type == JTokenType.String) return token.Value<string>();
        throw new InvalidDataException("Raw package record field " + property + " must be string or null");
    }

    private static int ReadInt32(JObject value, string property)
    {
        if (value[property]?.Type != JTokenType.Integer)
            throw new InvalidDataException("Raw package record field " + property + " must be an integer");
        return value.Value<int>(property);
    }

    private static bool ReadBoolean(JObject value, string property)
    {
        if (value[property]?.Type != JTokenType.Boolean)
            throw new InvalidDataException("Raw package record field " + property + " must be a boolean");
        return value.Value<bool>(property);
    }

    private static string[] ReadStrings(JObject value, string property, int maxCount)
    {
        if (value[property] is not JArray array || array.Count > maxCount)
            throw new InvalidDataException("Raw package record field " + property + " must be a bounded string array");
        var items = array.Select(token => token.Type == JTokenType.String
            ? token.Value<string>()!
            : throw new InvalidDataException("Raw package record field " + property + " contains a non-string value")).ToArray();
        if (!items.SequenceEqual(items.Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal), StringComparer.Ordinal))
            throw new InvalidDataException("Raw package record field " + property + " must be sorted and unique");
        return items;
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _record.Dispose();
    }

    private sealed class BoundedLineReader(Stream source, int bufferBytes)
    {
        private readonly byte[] _buffer = new byte[Math.Min(bufferBytes, 16 * 1024)];
        private readonly MemoryStream _line = new(Math.Min(bufferBytes, 16 * 1024));
        private int _bufferOffset;
        private int _bufferCount;
        private bool _eof;

        internal bool TryReadLine(int maxBytes, out MemoryStream line)
        {
            _line.SetLength(0);
            _line.Position = 0;
            var hadBytes = false;
            while (true)
            {
                if (_bufferOffset >= _bufferCount)
                {
                    if (_eof)
                    {
                        line = _line;
                        return hadBytes;
                    }
                    _bufferCount = source.Read(_buffer, 0, _buffer.Length);
                    _bufferOffset = 0;
                    if (_bufferCount == 0)
                    {
                        _eof = true;
                        continue;
                    }
                }

                var current = _buffer[_bufferOffset++];
                if (current == (byte)'\n')
                {
                    if (_line.Length >= maxBytes)
                        throw new InvalidDataException("Raw package record line exceeds its configured byte bound");
                    line = _line;
                    return true;
                }
                if (_line.Length >= maxBytes)
                    throw new InvalidDataException("Raw package record line exceeds its configured byte bound");
                _line.WriteByte(current);
                hadBytes = true;
            }
        }
    }
}
