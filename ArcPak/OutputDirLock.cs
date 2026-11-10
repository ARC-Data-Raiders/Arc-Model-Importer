using Serilog;

namespace ArcPak;

/// <summary>
/// Exclusive per-directory lock so two ArcPak runs never write one out/index dir at once.
/// The OS releases the handle when the process exits, so a crashed run leaves no stale lock.
/// </summary>
internal sealed class OutputDirLock : IDisposable
{
    internal const string FileName = ".arcpak.lock";
    private readonly FileStream _stream;

    private OutputDirLock(FileStream stream) => _stream = stream;

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
            try
            {
                var stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.DeleteOnClose);
                stream.SetLength(0);
                var pid = System.Text.Encoding.ASCII.GetBytes(Environment.ProcessId.ToString());
                stream.Write(pid, 0, pid.Length);
                stream.Flush();
                return new OutputDirLock(stream);
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

    public void Dispose() => _stream.Dispose();

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
