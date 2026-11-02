namespace ArcPak;

// A successful sibling cannot turn a failed required asset export into exit0.
internal sealed class RequiredExportStatus
{
    private int _failures;
    internal int Failures => Volatile.Read(ref _failures);
    internal void Observe(bool success) { if (!success) Interlocked.Increment(ref _failures); }
    internal int ExitCode(bool wroteAnything, bool metadataComplete) =>
        wroteAnything && metadataComplete && Failures == 0 ? 0 : 1;
}
