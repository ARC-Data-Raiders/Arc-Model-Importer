using System.Buffers.Binary;
using System.Collections.ObjectModel;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace ArcPak;

public sealed record MapArchiveSourceInput(string Role, string LogicalName, string Path);

public sealed record MapArchiveSourceManifestEntry(string Role, string LogicalName, long Length, string Sha256);

internal readonly record struct MapArchiveSourceHashResult(long Length, string Sha256, int MaxReadRequestBytes);

/// <summary>
/// A deterministic hash manifest over an explicitly supplied physical source set.
/// The Windows read lease remains active for the lifetime of this identity.
/// </summary>
public sealed class MapArchiveSourceIdentity : IDisposable
{
    public const int DefaultBufferBytes = 64 * 1024;
    public const int MinBufferBytes = 4 * 1024;
    public const int MaxBufferBytes = 1024 * 1024;
    private const int MaxInputCount = 32_768;
    private const int MaxManifestMetadataBytes = 2 * 1024 * 1024;
    private const string FingerprintFormat = "map-archive-source-identity-v1";

    private readonly MapSourceSnapshot _lease;
    private readonly ReadOnlyCollection<MapArchiveSourceManifestEntry> _manifest;
    private readonly string[] _physicalPaths;
    private readonly ReadOnlyDictionary<string, string> _inputPathsByKey;
    private readonly string _identitySha256;
    private readonly int _bufferBytes;
    private int _disposed;

    private MapArchiveSourceIdentity(MapSourceSnapshot lease, string[] physicalPaths,
        MapArchiveSourceManifestEntry[] manifest, string identitySha256, int bufferBytes,
        Dictionary<string, string> inputPathsByKey)
    {
        _lease = lease;
        _physicalPaths = physicalPaths;
        _manifest = Array.AsReadOnly(manifest);
        _inputPathsByKey = new ReadOnlyDictionary<string, string>(inputPathsByKey);
        _identitySha256 = identitySha256;
        _bufferBytes = bufferBytes;
    }

    public bool IsActive => Volatile.Read(ref _disposed) == 0 && _lease.IsActive;

    public string IdentitySha256
    {
        get
        {
            EnsureActive();
            return _identitySha256;
        }
    }

    public IReadOnlyList<MapArchiveSourceManifestEntry> Manifest
    {
        get
        {
            EnsureActive();
            return _manifest;
        }
    }

    public int BufferBytes
    {
        get
        {
            EnsureActive();
            return _bufferBytes;
        }
    }

    /// <summary>
    /// Hashes a caller-enumerated, complete source set while its files are held by
    /// MapSourceSnapshot. This class does not discover provider inputs or prevent
    /// additions to source directories.
    /// </summary>
    public static MapArchiveSourceIdentity Acquire(IEnumerable<MapArchiveSourceInput> inputs,
        IEnumerable<string> requiredRoles, int bufferBytes = DefaultBufferBytes,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(inputs);
        ArgumentNullException.ThrowIfNull(requiredRoles);
        if (bufferBytes is < MinBufferBytes or > MaxBufferBytes)
            throw new ArgumentOutOfRangeException(nameof(bufferBytes), $"Buffer size must be between {MinBufferBytes} and {MaxBufferBytes} bytes.");

        var required = new HashSet<string>(StringComparer.Ordinal);
        foreach (var role in requiredRoles)
        {
            var normalizedRole = NormalizeRole(role);
            if (!required.Add(normalizedRole))
                throw new InvalidDataException("Required source roles contain a duplicate: " + normalizedRole);
        }
        if (required.Count == 0)
            throw new InvalidDataException("At least one required source role must be declared.");

        var rows = new List<(string Role, string LogicalName, string Path)>();
        var logicalKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var physicalPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long metadataBytes = 0;
        foreach (var input in inputs)
        {
            if (input is null)
                throw new InvalidDataException("Source manifest contains a null input.");
            if (rows.Count >= MaxInputCount)
                throw new InvalidDataException("Source manifest exceeds the entry bound.");
            var role = NormalizeRole(input.Role);
            var logicalName = NormalizeLogicalName(input.LogicalName);
            string fullPath;
            try
            {
                fullPath = Path.GetFullPath(input.Path);
            }
            catch (Exception exception) when (exception is ArgumentException or NotSupportedException or PathTooLongException)
            {
                throw new InvalidDataException("Source path is invalid: " + input.Path, exception);
            }
            if (!File.Exists(fullPath))
                throw new FileNotFoundException("Source manifest path does not resolve to a file.", fullPath);
            if (!logicalKeys.Add(role + "\n" + logicalName))
                throw new InvalidDataException("Duplicate source role and logical name: " + role + ":" + logicalName);
            if (!physicalPaths.Add(fullPath))
                throw new InvalidDataException("A physical source path is listed more than once: " + fullPath);

            metadataBytes = checked(metadataBytes + Encoding.UTF8.GetByteCount(role) +
                Encoding.UTF8.GetByteCount(logicalName) + Encoding.UTF8.GetByteCount(fullPath) + 96);
            if (metadataBytes > MaxManifestMetadataBytes)
                throw new InvalidDataException("Source manifest metadata exceeds its bound.");
            rows.Add((role, logicalName, fullPath));
        }
        if (rows.Count == 0)
            throw new InvalidDataException("Source manifest is empty.");
        var presentRoles = rows.Select(row => row.Role).ToHashSet(StringComparer.Ordinal);
        if (required.Any(role => !presentRoles.Contains(role)))
            throw new InvalidDataException("Source manifest does not include every declared required role.");

        rows.Sort((left, right) =>
        {
            var roleOrder = StringComparer.Ordinal.Compare(left.Role, right.Role);
            if (roleOrder != 0) return roleOrder;
            var foldedNameOrder = StringComparer.OrdinalIgnoreCase.Compare(left.LogicalName, right.LogicalName);
            return foldedNameOrder != 0 ? foldedNameOrder : StringComparer.Ordinal.Compare(left.LogicalName, right.LogicalName);
        });
        var sortedPaths = rows.Select(row => row.Path).OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ThenBy(path => path, StringComparer.Ordinal).ToArray();

        MapSourceSnapshot? lease = null;
        try
        {
            lease = MapSourceSnapshot.Acquire(sortedPaths);
            cancellationToken.ThrowIfCancellationRequested();
            var manifest = new MapArchiveSourceManifestEntry[rows.Count];
            for (var index = 0; index < rows.Count; index++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var row = rows[index];
                using var stream = new FileStream(row.Path, FileMode.Open, FileAccess.Read, FileShare.Read,
                    bufferSize: 1, FileOptions.SequentialScan);
                var sourceHash = HashStream(stream, bufferBytes, cancellationToken);
                if (sourceHash.Length != stream.Length)
                    throw new IOException("Source length changed while hashing: " + row.LogicalName);
                manifest[index] = new MapArchiveSourceManifestEntry(row.Role, row.LogicalName,
                    sourceHash.Length, sourceHash.Sha256);
            }
            var identity = ComputeIdentity(manifest);
            var inputPathsByKey = rows.ToDictionary(
                row => row.Role + "\n" + row.LogicalName,
                row => row.Path,
                StringComparer.Ordinal);
            return new MapArchiveSourceIdentity(lease, sortedPaths, manifest, identity, bufferBytes, inputPathsByKey);
        }
        catch
        {
            lease?.Dispose();
            throw;
        }
    }

    /// <summary>
    /// Checks a freshly discovered physical path set against the leased input set.
    /// It does not prove that a provider mounted that set; the caller must perform
    /// this check at the mount boundary and use the same frozen list for mounting.
    /// </summary>
    public void EnsureDiscoveredFileSet(IEnumerable<string> discoveredPaths)
    {
        EnsureActive();
        ArgumentNullException.ThrowIfNull(discoveredPaths);
        var current = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var path in discoveredPaths)
        {
            if (string.IsNullOrWhiteSpace(path))
                throw new InvalidDataException("Discovered source set contains an empty path.");
            string fullPath;
            try
            {
                fullPath = Path.GetFullPath(path);
            }
            catch (Exception exception) when (exception is ArgumentException or NotSupportedException or PathTooLongException)
            {
                throw new InvalidDataException("Discovered source path is invalid: " + path, exception);
            }
            if (!seen.Add(fullPath))
                throw new InvalidDataException("Discovered source set contains a duplicate path: " + fullPath);
            current.Add(fullPath);
            if (current.Count > MaxInputCount)
                throw new InvalidDataException("Discovered source set exceeds the entry bound.");
        }
        current.Sort(StringComparer.OrdinalIgnoreCase);
        if (current.Count != _physicalPaths.Length || !current.SequenceEqual(_physicalPaths, StringComparer.OrdinalIgnoreCase))
            throw new InvalidDataException("Discovered source file set changed after identity capture.");
    }

    /// <summary>
    /// Verifies that each caller-supplied role/logical-name row is still bound to
    /// the physical path that was hashed for that row during this identity's one
    /// bounded hash pass. This detects swaps without hashing large files again.
    /// </summary>
    public void EnsureInputBindings(IEnumerable<MapArchiveSourceInput> inputs)
    {
        EnsureActive();
        ArgumentNullException.ThrowIfNull(inputs);
        var current = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var input in inputs)
        {
            if (input is null)
                throw new InvalidDataException("Source binding set contains a null input.");
            var role = NormalizeRole(input.Role);
            var logicalName = NormalizeLogicalName(input.LogicalName);
            string fullPath;
            try
            {
                fullPath = Path.GetFullPath(input.Path);
            }
            catch (Exception exception) when (exception is ArgumentException or NotSupportedException or PathTooLongException)
            {
                throw new InvalidDataException("Source path is invalid: " + input.Path, exception);
            }

            var key = role + "\n" + logicalName;
            if (!current.TryAdd(key, fullPath))
                throw new InvalidDataException("Source binding set contains a duplicate role and logical name: " + logicalName);
            if (!_inputPathsByKey.TryGetValue(key, out var expectedPath) ||
                !string.Equals(fullPath, expectedPath, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Source role/logical name is bound to a different physical path: " + logicalName);
            if (current.Count > MaxInputCount)
                throw new InvalidDataException("Source binding set exceeds the entry bound.");
        }

        if (current.Count != _inputPathsByKey.Count)
            throw new InvalidDataException("Source binding set does not include every identity input.");
        EnsureDiscoveredFileSet(current.Values);
    }
    public void EnsureActive()
    {
        ObjectDisposedException.ThrowIf(!IsActive, this);
        _lease.EnsureActive();
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
            return;
        _lease.Dispose();
    }

    internal static MapArchiveSourceHashResult HashStream(Stream source, int bufferBytes,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(source);
        if (!source.CanRead) throw new ArgumentException("Source stream must be readable.", nameof(source));
        if (bufferBytes is < MinBufferBytes or > MaxBufferBytes)
            throw new ArgumentOutOfRangeException(nameof(bufferBytes));

        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[bufferBytes];
        long length = 0;
        var maxReadRequest = 0;
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            maxReadRequest = Math.Max(maxReadRequest, buffer.Length);
            var read = source.Read(buffer, 0, buffer.Length);
            if (read == 0) break;
            hash.AppendData(buffer, 0, read);
            length = checked(length + read);
        }
        return new MapArchiveSourceHashResult(length,
            Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant(), maxReadRequest);
    }

    private static string ComputeIdentity(IReadOnlyList<MapArchiveSourceManifestEntry> manifest)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        AppendString(hash, FingerprintFormat);
        AppendInt32(hash, manifest.Count);
        foreach (var entry in manifest)
        {
            AppendString(hash, entry.Role);
            AppendString(hash, entry.LogicalName);
            AppendInt64(hash, entry.Length);
            AppendString(hash, entry.Sha256);
        }
        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    private static void AppendString(IncrementalHash hash, string value)
    {
        var bytes = Encoding.UTF8.GetBytes(value);
        AppendInt32(hash, bytes.Length);
        hash.AppendData(bytes);
    }

    private static void AppendInt32(IncrementalHash hash, int value)
    {
        Span<byte> bytes = stackalloc byte[sizeof(int)];
        BinaryPrimitives.WriteInt32LittleEndian(bytes, value);
        hash.AppendData(bytes);
    }

    private static void AppendInt64(IncrementalHash hash, long value)
    {
        Span<byte> bytes = stackalloc byte[sizeof(long)];
        BinaryPrimitives.WriteInt64LittleEndian(bytes, value);
        hash.AppendData(bytes);
    }

    private static string NormalizeRole(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        if (value.Length is 0 or > 128 || value.Any(character =>
            !((character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
              (character >= '0' && character <= '9') || character is '-' or '_' or '.')))
            throw new InvalidDataException("Source role must be a short stable label.");
        return value.ToLowerInvariant();
    }

    private static string NormalizeLogicalName(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        var normalized = value.Replace('\\', '/');
        if (normalized.Length is 0 or > 4096 || normalized.StartsWith('/') || normalized.Contains(':') ||
            normalized.Contains('\0') || normalized.Contains('\r') || normalized.Contains('\n') ||
            normalized.Split('/').Any(segment => segment.Length == 0 || segment is "." or ".."))
            throw new InvalidDataException("Logical source name must be a normalized relative name.");
        return normalized;
    }
}


