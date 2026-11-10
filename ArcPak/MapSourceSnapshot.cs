using System.Collections.ObjectModel;

namespace ArcPak;

/// <summary>
/// Holds read-only Windows handles for a caller-provided source-file set.
/// </summary>
public sealed class MapSourceSnapshot : IDisposable
{
    private readonly List<FileStream> handles;
    private readonly ReadOnlyCollection<string> manifest;
    private int disposed;

    private MapSourceSnapshot(List<FileStream> handles, string[] manifest)
    {
        this.handles = handles;
        this.manifest = Array.AsReadOnly(manifest);
    }

    /// <summary>True while this lease still holds its source handles.</summary>
    public bool IsActive => Volatile.Read(ref disposed) == 0;

    /// <summary>The normalized paths acquired by this lease, in acquisition order.</summary>
    public IReadOnlyList<string> Manifest => manifest;

    /// <summary>
    /// Acquires every path in ordinal, case-insensitive order. The caller must
    /// provide the complete source-file set and separately prevent additions to
    /// directories used to discover that set.
    /// </summary>
    public static MapSourceSnapshot Acquire(IEnumerable<string> sourcePaths)
    {
        ArgumentNullException.ThrowIfNull(sourcePaths);
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("Map source read leases require Windows file sharing semantics.");

        var paths = new List<string>();
        foreach (var sourcePath in sourcePaths)
        {
            if (string.IsNullOrWhiteSpace(sourcePath))
                throw new ArgumentException("Source paths must be nonempty.", nameof(sourcePaths));

            string fullPath;
            try
            {
                fullPath = Path.GetFullPath(sourcePath);
            }
            catch (Exception exception) when (exception is ArgumentException or NotSupportedException or PathTooLongException)
            {
                throw new ArgumentException($"Invalid source path: {sourcePath}", nameof(sourcePaths), exception);
            }

            if (!File.Exists(fullPath))
                throw new FileNotFoundException("Source path does not resolve to an existing file.", fullPath);
            paths.Add(fullPath);
        }

        paths.Sort(StringComparer.OrdinalIgnoreCase);
        for (var index = 1; index < paths.Count; index++)
        {
            if (StringComparer.OrdinalIgnoreCase.Equals(paths[index - 1], paths[index]))
                throw new ArgumentException($"Duplicate source path: {paths[index]}", nameof(sourcePaths));
        }

        var opened = new List<FileStream>(paths.Count);
        try
        {
            foreach (var path in paths)
                opened.Add(new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read));

            return new MapSourceSnapshot(opened, paths.ToArray());
        }
        catch
        {
            for (var index = opened.Count - 1; index >= 0; index--)
                opened[index].Dispose();
            throw;
        }
    }

    /// <summary>Throws if the lease has been disposed.</summary>
    public void EnsureActive()
    {
        ObjectDisposedException.ThrowIf(!IsActive, this);
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0)
            return;

        for (var index = handles.Count - 1; index >= 0; index--)
            handles[index].Dispose();
    }
}
