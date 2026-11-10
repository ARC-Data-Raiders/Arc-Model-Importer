using ArcPak;

internal static class MapSourceSnapshotChecks
{
    internal static int RunRed(string root)
    {
        Directory.CreateDirectory(root);
        var path = Path.Combine(root, "unrestricted-red.bin");
        File.WriteAllBytes(path, [1, 2, 3]);
        using (var reader = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        using (var writer = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
            writer.WriteByte(9);
        if (File.ReadAllBytes(path)[0] != 9)
            throw new InvalidOperationException("FAIL unrestricted reader did not allow mutation");
        Console.WriteLine("PASS RED baseline: unrestricted reader permits mutation");
        File.Delete(path);
        return 1;
    }

    internal static int Run(string root)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("This fixture requires Windows file sharing semantics.");
        Directory.CreateDirectory(root);
        var checks = 0;
        void Check(bool condition, string name)
        {
            if (!condition) throw new InvalidOperationException("FAIL " + name);
            checks++;
            Console.WriteLine("PASS " + name);
        }
        void Denied(Action operation, string name)
        {
            try { operation(); }
            catch (IOException) { Check(true, name); return; }
            catch (UnauthorizedAccessException) { Check(true, name); return; }
            throw new InvalidOperationException("FAIL " + name + " was allowed");
        }

        var unrestrictedPath = Path.Combine(root, "unrestricted.bin");
        File.WriteAllBytes(unrestrictedPath, [1, 2, 3]);
        using (var unrestricted = new FileStream(unrestrictedPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        {
            using (var writer = new FileStream(unrestrictedPath, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
                writer.WriteByte(9);
            Check(File.ReadAllBytes(unrestrictedPath)[0] == 9, "RED baseline: unrestricted reader permits mutation");
        }

        var paths = new[]
        {
            Path.Combine(root, "write.bin"), Path.Combine(root, "truncate.bin"),
            Path.Combine(root, "delete.bin"), Path.Combine(root, "rename.bin"),
            Path.Combine(root, "replace.bin")
        };
        foreach (var path in paths) File.WriteAllBytes(path, [1, 2, 3, 4]);
        var replaceTemp = Path.Combine(root, "replace-temp.bin");
        File.WriteAllBytes(replaceTemp, [7, 8]);

        var lease = MapSourceSnapshot.Acquire(paths.Reverse());
        Check(lease.IsActive && lease.Manifest.SequenceEqual(paths.OrderBy(path => path, StringComparer.OrdinalIgnoreCase)), "active state and deterministic manifest");
        Check(paths.Select(path => Task.Run(() => File.ReadAllBytes(path).Length)).Select(task => task.Result).All(length => length == 4), "concurrent reads remain allowed");
        Denied(() => File.WriteAllBytes(paths[0], [8]), "write denied while leased");
        Denied(() => new FileStream(paths[1], FileMode.Truncate, FileAccess.Write, FileShare.ReadWrite).Dispose(), "truncate denied while leased");
        Denied(() => File.Delete(paths[2]), "delete denied while leased");
        Denied(() => File.Move(paths[3], paths[3] + ".moved"), "rename denied while leased");
        Denied(() => File.Replace(replaceTemp, paths[4], null), "atomic replace denied while leased");
        Check(paths.All(File.Exists), "disposing never deletes source files");

        var secondLease = MapSourceSnapshot.Acquire([paths[0]]);
        lease.Dispose();
        lease.Dispose();
        Check(!lease.IsActive, "disposal is idempotent and marks inactive");
        try
        {
            lease.EnsureActive();
            throw new InvalidOperationException("FAIL guard allowed access after disposal");
        }
        catch (ObjectDisposedException) { Check(true, "guard rejects access after disposal"); }
        Check(paths.All(File.Exists), "disposal never deletes source files");
        Denied(() => File.WriteAllBytes(paths[0], [6]), "independent lease keeps its handle active");
        secondLease.Dispose();
        File.WriteAllBytes(paths[0], [6]);
        Check(File.ReadAllBytes(paths[0]).SequenceEqual(new byte[] { 6 }), "mutation is allowed after final lease disposal");
        using (new FileStream(paths[1], FileMode.Truncate, FileAccess.Write, FileShare.Read)) { }
        Check(new FileInfo(paths[1]).Length == 0, "truncate is allowed after disposal");
        File.Delete(paths[2]);
        Check(!File.Exists(paths[2]), "delete is allowed after disposal");
        File.WriteAllBytes(paths[2], [2]);
        File.Move(paths[3], paths[3] + ".moved");
        Check(File.Exists(paths[3] + ".moved"), "rename is allowed after disposal");
        File.Move(paths[3] + ".moved", paths[3]);
        File.Replace(replaceTemp, paths[4], null);
        Check(File.ReadAllBytes(paths[4]).SequenceEqual(new byte[] { 7, 8 }), "atomic replace is allowed after disposal");
        File.WriteAllBytes(replaceTemp, [7, 8]);

        var partialFirst = Path.Combine(root, "a-partial.bin");
        var blockedLater = Path.Combine(root, "z-blocked.bin");
        File.WriteAllBytes(partialFirst, [3]);
        File.WriteAllBytes(blockedLater, [5]);
        using (var exclusiveBlocker = new FileStream(blockedLater, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        {
            try
            {
                MapSourceSnapshot.Acquire([blockedLater, partialFirst]);
                throw new InvalidOperationException("FAIL blocked later acquisition unexpectedly succeeded");
            }
            catch (IOException)
            {
                File.WriteAllBytes(partialFirst, [4]);
                Check(File.ReadAllBytes(partialFirst).SequenceEqual(new byte[] { 4 }), "partial failure releases prior handles");
            }
        }

        try
        {
            MapSourceSnapshot.Acquire([paths[0], paths[0]]);
            throw new InvalidOperationException("FAIL duplicate paths unexpectedly succeeded");
        }
        catch (ArgumentException) { Check(true, "duplicate manifest path fails closed"); }

        using (var finalLease = MapSourceSnapshot.Acquire(paths))
            Check(finalLease.Manifest.Count == paths.Length && paths.All(File.Exists), "lease disposal leaves all sources present");
        foreach (var path in paths) File.Delete(path);
        File.Delete(partialFirst);
        File.Delete(blockedLater);
        File.Delete(unrestrictedPath);
        File.Delete(replaceTemp);
        return checks;
    }
}
