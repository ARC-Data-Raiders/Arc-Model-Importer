using Serilog;

namespace ArcPak;

/// <summary>
/// Exclusive per-directory lock so two ArcPak runs never write one out/index dir at once.
/// The OS releases the handle when the process exits, so a crashed run leaves no stale lock.
/// </summary>
internal sealed class OutputDirLock : IDisposable
{
    internal const string FileName = ".arcpak.lock";
    private readonly Held _held;
    private int _disposed;

    // In-process holders share one file lock per directory, so concurrent serve requests in
    // this process do not block each other; other processes still wait. Requests that share a
    // directory must not write the same files.
    private sealed class Held { internal FileStream Stream = null!; internal int Count; internal string Key = ""; }
    private static readonly Dictionary<string, Held> _inProcess = new(StringComparer.OrdinalIgnoreCase);

    private OutputDirLock(Held held) => _held = held;

    private static bool TryShare(string key, out OutputDirLock shared)
    {
        lock (_inProcess)
        {
            if (_inProcess.TryGetValue(key, out var held)) { held.Count++; shared = new OutputDirLock(held); return true; }
        }
        shared = null!;
        return false;
    }

    internal static TimeSpan DefaultWait()
    {
        var text = Environment.GetEnvironmentVariable("ARCPAK_LOCK_WAIT_MINUTES");
        return double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var minutes) && minutes >= 0
            ? TimeSpan.FromMinutes(minutes)
            : TimeSpan.FromMinutes(10);
    }

    internal static OutputDirLock Acquire(string directory, TimeSpan wait)
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, FileName);
        var deadline = DateTime.UtcNow + wait;
        var logged = false;
        while (true)
        {
            if (TryShare(path, out var shared)) return shared;
            try
            {
                var stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.DeleteOnClose);
                stream.SetLength(0);
                var pid = System.Text.Encoding.ASCII.GetBytes(Environment.ProcessId.ToString());
                stream.Write(pid, 0, pid.Length);
                stream.Flush();
                var held = new Held { Stream = stream, Count = 1, Key = path };
                lock (_inProcess) _inProcess[path] = held;
                return new OutputDirLock(held);
            }
            catch (IOException) when (DateTime.UtcNow < deadline)
            {
                if (!logged)
                {
                    Log.Information("Waiting for another ArcPak run to release {Path}", path);
                    logged = true;
                }
                Thread.Sleep(500);
            }
            catch (IOException ex)
            {
                throw new IOException(
                    $"Another ArcPak run holds {path}; gave up after {wait.TotalMinutes:0.##} min. Wait for it to finish or set ARCPAK_LOCK_WAIT_MINUTES.", ex);
            }
        }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        lock (_inProcess)
        {
            if (--_held.Count > 0) return;
            _inProcess.Remove(_held.Key);
            _held.Stream.Dispose();
        }
    }

    /// <summary>Locks every distinct directory named by --out or --index-dir (a file value locks its parent), in sorted order.</summary>
    internal static LockSet AcquireForArgs(string[] args)
    {
        var dirs = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
        for (var i = 0; i + 1 < args.Length; i++)
        {
            if (args[i] is not ("--out" or "--index-dir")) continue;
            var value = args[i + 1];
            if (string.IsNullOrWhiteSpace(value) || value.StartsWith("--", StringComparison.Ordinal)) continue;
            var full = Path.GetFullPath(value);
            var dir = Directory.Exists(full) || string.IsNullOrEmpty(Path.GetExtension(full)) || args[i] == "--index-dir"
                ? full : Path.GetDirectoryName(full);
            if (!string.IsNullOrEmpty(dir)) dirs.Add(Path.TrimEndingDirectorySeparator(dir));
        }
        var wait = DefaultWait();
        var set = new LockSet();
        try { foreach (var dir in dirs) set.Locks.Add(Acquire(dir, wait)); }
        catch { set.Dispose(); throw; }
        return set;
    }

    internal sealed class LockSet : IDisposable
    {
        internal readonly List<OutputDirLock> Locks = new();
        public void Dispose() { foreach (var l in Locks) l.Dispose(); Locks.Clear(); }
    }
}
