using CUE4Parse.Compression;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.MappingsProvider;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Readers;
using CUE4Parse.UE4.Versions;

namespace ArcPak.Tests;

internal static class MapPackageMemoizationChecks
{
    public static async Task<int> Run(string evidencePath, bool red)
    {
        Directory.CreateDirectory(evidencePath);
        var checks = 0;
        void Check(bool condition, string name)
        {
            if (!condition) throw new InvalidOperationException("FAIL " + name);
            checks++;
            Console.WriteLine("PASS " + name);
        }

        var provider = new MemoProvider();
        var file = new MemoFile("/Game/Test/Test.uasset", provider.Versions);
        provider.Mount(file);

        if (red)
        {
            var before = provider.LoadPackage(file);
            var after = provider.LoadPackage(file);
            Check(!ReferenceEquals(before, after) && provider.LoadCount == 2, "RED default production boundary loads two package results before scope");
            return checks;
        }

        Check(!ReferenceEquals(provider.LoadPackage(file), provider.LoadPackage(file)), "default provider remains uncached");

        provider.Reset();
        using (provider.BeginBundlePackageLoadScope(() => true))
        {
            var first = provider.LoadPackage(file);
            Check(ReferenceEquals(first, provider.LoadPackage(file)), "sync repeated load returns same package reference");
            Check(provider.LoadCount == 1, "sync repeated load invokes parser once");
        }

        provider.Reset();
        var asyncScope = provider.BeginBundlePackageLoadScope(() => true);
        var asyncFirst = await provider.LoadPackageAsync(file);
        Check(ReferenceEquals(asyncFirst, await provider.LoadPackageAsync(file)), "async repeated load returns same package reference");
        Check(provider.LoadCount == 1, "async repeated load invokes parser once");
        asyncScope.Dispose();

        provider.Reset();
        using (provider.BeginBundlePackageLoadScope(() => true))
        {
            var mixed = await MixedSingleFlight(provider, file);
            Check(ReferenceEquals(mixed.Sync, mixed.Async) && provider.LoadCount == 1, "sync and async callers share one in-flight package");
        }

        provider.Reset();
        var provider2 = new MemoProvider();
        var file2 = new MemoFile(file.Path, provider2.Versions);
        provider2.Mount(file2);
        using (provider.BeginBundlePackageLoadScope(() => true))
        using (provider2.BeginBundlePackageLoadScope(() => true))
        {
            var p1 = provider.LoadPackage(file);
            var p2 = provider2.LoadPackage(file2);
            Check(!ReferenceEquals(p1, p2), "package results are isolated by provider and mounted file object");
        }

        provider.Reset();
        var foreign = new MemoFile(file.Path, provider.Versions);
        using (provider.BeginBundlePackageLoadScope(() => true))
        {
            var mountedResult = provider.LoadPackage(file);
            var foreignResult = provider.LoadPackage(foreign);
            Check(!ReferenceEquals(mountedResult, foreignResult) && provider.LoadCount == 2, "unmounted same-path file bypasses cache");
        }

        provider.Reset();
        var failOnce = true;
        provider.Factory = _ =>
        {
            if (failOnce)
            {
                failOnce = false;
                throw new InvalidOperationException("controlled parser failure");
            }
            return new MemoPackage("recovered");
        };
        using (provider.BeginBundlePackageLoadScope(() => true))
        {
            try { provider.LoadPackage(file); throw new InvalidOperationException("FAIL load failure propagated"); }
            catch (InvalidOperationException error) when (error.Message == "controlled parser failure") { Check(true, "parser failure propagates"); }
            Check(provider.LoadPackage(file).Name == "recovered" && provider.LoadCount == 2, "failed load is evicted and retried");
        }

        provider.Reset();
        var reentered = false;
        provider.Factory = p =>
        {
            if (!reentered)
            {
                reentered = true;
                _ = p.LoadPackage(file);
            }
            return new MemoPackage("reentrant");
        };
        using (provider.BeginBundlePackageLoadScope(() => true))
        {
            Check(provider.LoadPackage(file).Name == "reentrant" && provider.LoadCount == 2,
                "same-key reentry bypasses waiting and does not deadlock");
        }

        provider.Reset();
        var snapshotActive = true;
        var guardedScope = provider.BeginBundlePackageLoadScope(() => snapshotActive);
        var guarded = provider.LoadPackage(file);
        snapshotActive = false;
        try { _ = provider.LoadPackage(file); throw new InvalidOperationException("FAIL inactive snapshot should stop scoped work"); }
        catch (InvalidOperationException error) when (error.Message.Contains("source snapshot is inactive", StringComparison.Ordinal))
        {
            Check(!guardedScope.IsActive, "snapshot guard failure deactivates scope and drops cached references");
        }
        Check(!ReferenceEquals(guarded, provider.LoadPackage(file)), "load after snapshot failure uses uncached provider behavior");

        provider.Reset();
        var releaseSnapshot = true;
        provider.Factory = _ =>
        {
            releaseSnapshot = false;
            return new MemoPackage("snapshot-ended-during-parse");
        };
        using (provider.BeginBundlePackageLoadScope(() => releaseSnapshot))
        {
            var initial = provider.LoadPackage(file);
            provider.Factory = _ => new MemoPackage("after-snapshot");
            Check(!ReferenceEquals(initial, provider.LoadPackage(file)), "snapshot ending during parse denies cache acceptance");
        }

        provider.Reset();
        var disposable = provider.BeginBundlePackageLoadScope(() => true);
        var beforeDispose = provider.LoadPackage(file);
        disposable.Dispose();
        Check(!ReferenceEquals(beforeDispose, provider.LoadPackage(file)), "scope disposal releases retained package and restores default behavior");

        provider.Reset();
        using (provider.BeginBundlePackageLoadScope(() => true))
        {
            var initial = provider.LoadPackage(file);
            provider.UseLazyPackageSerialization = !provider.UseLazyPackageSerialization;
            var changedSettings = provider.LoadPackage(file);
            Check(!ReferenceEquals(initial, changedSettings) && provider.LoadCount == 2,
                "changed parser setting deactivates reuse and fails closed to uncached loading");
        }

        provider.Reset();
        var concurrentFile = new MemoFile("/Game/Test/Concurrent.uasset", provider.Versions);
        provider.Mount(concurrentFile);
        using (provider.BeginBundlePackageLoadScope(() => true))
        {
            var pair = await MixedSingleFlight(provider, concurrentFile);
            Check(ReferenceEquals(pair.Sync, pair.Async) && provider.LoadCount == 1,
                "concurrent sync and async load is single-flight for one file");
        }

        return checks;
    }

    private static async Task<(IPackage Sync, IPackage Async)> MixedSingleFlight(MemoProvider provider, GameFile file)
    {
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        provider.Factory = _ =>
        {
            entered.Set();
            if (!release.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException("single-flight release timed out");
            return new MemoPackage("single-flight");
        };
        var sync = Task.Run(() => provider.LoadPackage(file));
        if (!entered.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException("single-flight loader did not start");
        var async = provider.LoadPackageAsync(file);
        release.Set();
        return (await sync, await async);
    }

    private sealed class MemoProvider : AbstractFileProvider
    {
        public int LoadCount;
        public Func<MemoProvider, IPackage> Factory = _ => new MemoPackage("package");

        public MemoProvider() : base() { }

        public void Mount(GameFile file) => Files.AddFiles(new Dictionary<string, GameFile> { [file.Path] = file });

        public void Reset()
        {
            LoadCount = 0;
            Factory = _ => new MemoPackage("package");
            UseLazyPackageSerialization = true;
        }

        protected override IPackage LoadPackageCore(GameFile file)
        {
            Interlocked.Increment(ref LoadCount);
            return Factory(this);
        }

        protected override Task<IPackage> LoadPackageAsyncCore(GameFile file) => Task.FromResult(LoadPackageCore(file));
    }

    private sealed class MemoFile(string path, VersionContainer versions) : VersionedGameFile(path, 1, versions)
    {
        public override bool IsEncrypted => false;
        public override CompressionMethod CompressionMethod => CompressionMethod.None;
        public override byte[] Read(FByteBulkDataHeader? header = null) => [1];
    }

    private sealed class MemoPackage(string name) : IPackage
    {
        public string Name { get; set; } = name;
        public IFileProvider? Provider => null;
        public string? ArchiveName => null;
        public TypeMappings? Mappings => null;
        public FPackageFileSummary Summary => null!;
        public FNameEntrySerialized[] NameMap => [];
        public int ImportMapLength => 0;
        public int ExportMapLength => 0;
        public Lazy<UObject>[] ExportsLazy => [];
        public bool IsFullyLoaded => true;
        public bool CanDeserialize => true;
        public bool HasFlags(EPackageFlags flags) => false;
        public int GetExportIndex(string name, StringComparison comparisonType = StringComparison.Ordinal) => -1;
        public ResolvedObject? ResolvePackageIndex(FPackageIndex? index) => null;
    }
}
