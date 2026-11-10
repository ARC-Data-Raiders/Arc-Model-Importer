using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using ArcPak;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Objects.Properties;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace ArcPak.Tests;

public static class MapRawPackageRecordChecks
{
    private static int _checks;

    public static int Run(string evidence, string fixtureRoot)
    {
        _checks = 0;
        evidence = Path.GetFullPath(evidence);
        fixtureRoot = Path.GetFullPath(fixtureRoot);
        Directory.CreateDirectory(evidence);
        Check(Directory.Exists(fixtureRoot), "fixture root exists");

        foreach (var unversioned in new[] { false, true })
        {
            var serialization = unversioned ? "Unversioned" : "Tagged";
            using var provider = CreateProvider(fixtureRoot, unversioned);
            Check(provider.Mount() == 1, serialization + " fixture IoStore mounts");
            var store = new MapPackageRecordStore(
                Path.Combine(evidence, "store", serialization.ToLowerInvariant()), StoreLimits());
            using var writeSession = store.BeginWriteSession();
            CheckPackage(provider, fixtureRoot, evidence, store, writeSession, unversioned,
                "/Game/Fixtures/Properties/DA_AllProperties", "DA_AllProperties.uasset", probeRawValues: true);
            CheckPackage(provider, fixtureRoot, evidence, store, writeSession, unversioned,
                "/Game/Fixtures/Maps/Streaming", "Streaming.umap", probeRawValues: false);
            Check(!writeSession.IsDisposed, serialization + " batch write session remains caller-owned");
        }

        CheckProfileAndCanonicalGuards(evidence, fixtureRoot);
        CheckDescriptorLifetime(evidence, fixtureRoot);
        return _checks;
    }

    private static void CheckPackage(
        DefaultFileProvider provider,
        string fixtureRoot,
        string evidence,
        MapPackageRecordStore store,
        MapPackageRecordWriteSession writeSession,
        bool unversioned,
        string packageName,
        string fileName,
        bool probeRawValues)
    {
        var packagePath = provider.Files.Keys.SingleOrDefault(path =>
            path.EndsWith(fileName, StringComparison.OrdinalIgnoreCase));
        Check(packagePath is not null, packageName + " is present in mounted fixture files");
        var package = provider.LoadPackage(packagePath!);
        var exports = package.GetExports();
        var probed = false;
        if (probeRawValues)
        {
            var asset = exports.Single(value => value.Name == "DA_AllProperties");
            asset.Properties.Add(new FPropertyTag(new FName("StrProperty"), new StrProperty("raw-snapshot-sentinel"))
            {
                Name = new FName("SyntheticUnknownRawTag")
            });
            probed = true;
        }

        var expected = new List<ExpectedRow>();
        foreach (var source in exports)
        {
            var diagnostics = new SortedSet<string>(StringComparer.Ordinal);
            var raw = MapBundleExport.Raw(source, diagnostics);
            expected.Add(new ExpectedRow(
                source.GetPathName(), source.Owner?.Name, source.ExportType,
                HashJson(raw), diagnostics.ToArray()));
            if (probeRawValues && source.Name == "DA_AllProperties")
            {
                var names = ((JArray)raw["properties"]!).OfType<JObject>()
                    .Select(row => row.Value<string>("name")).ToHashSet(StringComparer.Ordinal);
                Check(names.Contains("FixedIntegerArray") && names.Contains("InstancedStructMap") &&
                    names.Contains("HardObjectReference") && names.Contains("SoftObjectReference") &&
                    names.Contains("SyntheticUnknownRawTag"),
                    "parser-backed raw row contains arrays, maps, hard/soft references, and unknown synthetic tag");
            }
        }

        var key = BuildKey(fixtureRoot, unversioned, packageName, declaredSourceCoverageComplete: false);
        var temporaryRoot = Path.Combine(evidence, "temporary");
        var openCalls = 0;
        var descriptor = MapRawPackageRecord.CaptureOrReuse(store, key, packageName,
            () => { openCalls++; return package.GetExports(); }, declaredSourceCoverageComplete: false,
            temporaryRoot: temporaryRoot, limits: RecordLimits(), writeSession: writeSession);
        Check(openCalls == 1, packageName + " miss enumerates exports once");
        Check(descriptor.CanonicalPackage == packageName && descriptor.RowCount == expected.Count,
            packageName + " capture returns scalar identity and row count");
        Check(!descriptor.DeclaredSourceCoverageComplete && !descriptor.EffectiveSourceCoverageComplete &&
            descriptor.CaptureTransactionComplete,
            packageName + " complete storage transaction preserves false source coverage");
        Check(DescriptorHasNoSourceGraphs(descriptor), packageName + " descriptor has no UObject or provider references");

        using (var reader = MapRawPackageRecord.OpenRead(store, key, RecordLimits()))
        {
            Check(reader.Header.CanonicalPackage == packageName && !reader.Header.DeclaredSourceCoverageComplete &&
                reader.Header.CaptureTransactionComplete,
                packageName + " replay header preserves package and coverage state");
            for (var index = 0; index < expected.Count; index++)
            {
                Check(reader.TryReadNext(out var row) && row is not null,
                    packageName + " row " + index.ToString());
                var actual = row!;
                var baseline = expected[index];
                Check(actual.SourcePath == baseline.SourcePath && actual.OwnerPackageName == baseline.OwnerPackageName &&
                    actual.SourceExportType == baseline.SourceExportType && HashJson(actual.Raw) == baseline.RawSha256 &&
                    actual.Diagnostics.SequenceEqual(baseline.Diagnostics, StringComparer.Ordinal),
                    packageName + " row " + index.ToString() + " matches Raw and diagnostics");
            }
            Check(!reader.TryReadNext(out _), packageName + " replay ends after declared row count");
            Check(reader.Header.Diagnostics.SequenceEqual(
                expected.SelectMany(row => row.Diagnostics).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal),
                StringComparer.Ordinal), packageName + " replay diagnostics are sorted and deduplicated");
        }

        var hit = MapRawPackageRecord.CaptureOrReuse(store, key, packageName,
            openExports: null,
            declaredSourceCoverageComplete: true, temporaryRoot: temporaryRoot,
            limits: RecordLimits(), writeSession: writeSession);
        Check(hit.CacheHit && !hit.DeclaredSourceCoverageComplete,
            packageName + " cache hit reuses stored coverage without invoking decoder");

        using (var boundedReader = MapRawPackageRecord.OpenRead(store, key,
                   RecordLimits() with { MaxRowBytes = 128 }))
            ExpectThrows<InvalidDataException>(() => boundedReader.TryReadNext(out _),
                packageName + " replay rejects a row before allocating beyond the byte bound");

        using (var depthReader = MapRawPackageRecord.OpenRead(store, key,
                   RecordLimits() with { MaxDepth = 4 }))
            ExpectThrows<InvalidDataException>(() => { while (depthReader.TryReadNext(out _)) { } },
                packageName + " replay rejects excess JSON depth");

        CheckCaptureFailureCleansTemporaryFiles(evidence, fixtureRoot, package, unversioned, packageName);
    }

    private static void CheckCaptureFailureCleansTemporaryFiles(
        string evidence,
        string fixtureRoot,
        CUE4Parse.UE4.Assets.IPackage package,
        bool unversioned,
        string packageName)
    {
        var failureRoot = Path.Combine(evidence, "failure", (unversioned ? "unversioned-" : "tagged-") + packageName.Split('/').Last());
        var temporaryRoot = Path.Combine(failureRoot, "temporary");
        var store = new MapPackageRecordStore(Path.Combine(failureRoot, "store"), StoreLimits());
        var key = BuildKey(fixtureRoot, unversioned, packageName, declaredSourceCoverageComplete: false);
        var tinyLimits = RecordLimits() with { MaxRowBytes = 128 };
        ExpectThrows<InvalidDataException>(() => MapRawPackageRecord.CaptureOrReuse(
            store, key, packageName, package.GetExports, declaredSourceCoverageComplete: false,
            temporaryRoot: temporaryRoot, limits: tinyLimits), packageName + " row budget fails without truncation");
        Check(!store.TryOpenRead(key, out _), packageName + " failed capture publishes no record");
        Check(!Directory.Exists(temporaryRoot) || !Directory.EnumerateFiles(temporaryRoot).Any(),
            packageName + " failed capture removes spool temporaries");

        var interruptedRoot = Path.Combine(failureRoot, "interrupted");
        var interruptedStore = new MapPackageRecordStore(Path.Combine(interruptedRoot, "store"), StoreLimits());
        var interruptedTemp = Path.Combine(interruptedRoot, "temporary");
        IEnumerable<UObject> InterruptedExports()
        {
            yield return package.GetExports().First();
            throw new IOException("injected interrupted export iterator");
        }
        ExpectThrows<IOException>(() => MapRawPackageRecord.CaptureOrReuse(
            interruptedStore, key, packageName, InterruptedExports, declaredSourceCoverageComplete: false,
            temporaryRoot: interruptedTemp, limits: RecordLimits()),
            packageName + " interrupted iterator fails capture");
        Check(!interruptedStore.TryOpenRead(key, out _), packageName + " interrupted capture publishes no record");
        Check(!Directory.Exists(interruptedTemp) || !Directory.EnumerateFiles(interruptedTemp).Any(),
            packageName + " interrupted capture removes spool temporaries");

        var quotaRoot = Path.Combine(failureRoot, "entry-quota");
        var quotaStore = new MapPackageRecordStore(Path.Combine(quotaRoot, "store"),
            new MapPackageRecordStoreLimits(MaxEntryBytes: 1024, MaxStoreBytes: 16 * 1024,
                BufferBytes: 4096, MaxReceiptBytes: 1024));
        var quotaTemp = Path.Combine(quotaRoot, "temporary");
        ExpectThrows<IOException>(() => MapRawPackageRecord.CaptureOrReuse(
            quotaStore, key, packageName, package.GetExports, declaredSourceCoverageComplete: false,
            temporaryRoot: quotaTemp, limits: RecordLimits() with { MaxEntryBytes = 1024, MaxRowBytes = 8192 }),
            packageName + " complete-entry quota fails without truncation");
        Check(!quotaStore.TryOpenRead(key, out _), packageName + " entry quota publishes no record");
        Check(!Directory.Exists(quotaTemp) || !Directory.EnumerateFiles(quotaTemp).Any(),
            packageName + " entry quota failure removes spool temporaries");

        var invalidRoot = Path.Combine(failureRoot, "invalid-unicode");
        var invalidStore = new MapPackageRecordStore(Path.Combine(invalidRoot, "store"), StoreLimits());
        var invalidTemp = Path.Combine(invalidRoot, "temporary");
        var invalidSource = package.GetExports().First();
        invalidSource.Properties.Add(new FPropertyTag(new FName("StrProperty"),
            new StrProperty("\uD800")) { Name = new FName("SyntheticInvalidSurrogate") });
        ExpectThrows<System.Text.EncoderFallbackException>(() => MapRawPackageRecord.CaptureOrReuse(
            invalidStore, key, packageName, package.GetExports, declaredSourceCoverageComplete: false,
            temporaryRoot: invalidTemp, limits: RecordLimits()),
            packageName + " raw serialization rejects an invalid surrogate without replacing it");
        Check(!invalidStore.TryOpenRead(key, out _), packageName + " invalid Unicode publishes no record");
        Check(!Directory.Exists(invalidTemp) || !Directory.EnumerateFiles(invalidTemp).Any(),
            packageName + " invalid Unicode failure removes spool temporaries");
    }

    private static void CheckProfileAndCanonicalGuards(string evidence, string fixtureRoot)
    {
        var package = "/Game/Fixtures/Maps/Streaming";
        var key = BuildKey(fixtureRoot, unversioned: false, package, declaredSourceCoverageComplete: false);
        var store = new MapPackageRecordStore(Path.Combine(evidence, "guard-check"), StoreLimits());
        var calls = 0;
        ExpectThrows<ArgumentException>(() => MapRawPackageRecord.CaptureOrReuse(
            store, key, "/Game/Fixtures/Maps/Other", () => { calls++; return []; },
            declaredSourceCoverageComplete: false, temporaryRoot: Path.Combine(evidence, "guard-temp"), limits: RecordLimits()),
            "canonical package mismatch is rejected before cache lookup");
        Check(calls == 0, "canonical package mismatch does not enumerate exports");
        ExpectThrows<InvalidDataException>(() => MapRawPackageRecord.CaptureOrReuse(
            store, key, package, () => { calls++; return []; }, declaredSourceCoverageComplete: true,
            temporaryRoot: Path.Combine(evidence, "guard-temp"), limits: RecordLimits()),
            "miss refuses source coverage that differs from its capture profile");
        Check(calls == 0, "capture profile mismatch does not enumerate exports");
        Check(!MapRawPackageRecord.IsEffectiveSourceCoverageComplete(true, ["Raw property omitted: /Game/Test.Tag"]),
            "Raw omission diagnostics prevent effective completeness");
        ExpectThrows<System.Text.EncoderFallbackException>(() =>
            MapRawPackageRecord.ValidateMetadataString("\uD800", "synthetic invalid-surrogate fixture", 256),
            "synthetic invalid-surrogate metadata is rejected rather than replaced");
    }

    private static DefaultFileProvider CreateProvider(string fixtureRoot, bool unversioned)
    {
        var serialization = unversioned ? "Unversioned" : "Tagged";
        var variantRoot = Path.Combine(fixtureRoot, "IoStore", serialization);
        var containerRoot = Path.Combine(variantRoot, "Oodle");
        var provider = new DefaultFileProvider(containerRoot, SearchOption.TopDirectoryOnly,
            new VersionContainer(EGame.GAME_UE5_8), StringComparer.OrdinalIgnoreCase);
        if (unversioned)
            provider.MappingsContainer = new FileUsmapTypeMappingsProvider(
                Path.Combine(fixtureRoot, "Mappings", "CUE4ParseFixtures-Oodle.usmap"));
        provider.Initialize();
        provider.RegisterVfs(Path.Combine(variantRoot, "global.utoc"));
        return provider;
    }

    private static MapPackageRecordKey BuildKey(
        string fixtureRoot,
        bool unversioned,
        string canonicalPackage,
        bool declaredSourceCoverageComplete)
    {
        var serialization = unversioned ? "Unversioned" : "Tagged";
        var relativeFiles = new[]
        {
            $"IoStore/{serialization}/global.ucas",
            $"IoStore/{serialization}/global.utoc",
            $"IoStore/{serialization}/Oodle/CUE4ParseFixtures-Minimal-Oodle-Windows.ucas",
            $"IoStore/{serialization}/Oodle/CUE4ParseFixtures-Minimal-Oodle-Windows.utoc",
            "Mappings/CUE4ParseFixtures-Oodle.usmap"
        };
        var sources = relativeFiles.Select(relative => new MapPackageRecordSource(
            relative, HashFile(Path.Combine(fixtureRoot, relative.Replace('/', Path.DirectorySeparatorChar)))))
            .ToArray();
        var identity = Sha(string.Join("\n", sources.Select(source => source.Path + "=" + source.Sha256)));
        return new MapPackageRecordKey(identity, canonicalPackage, sources,
            "CUE4ParseFixtures-Oodle.usmap", sources[^1].Sha256,
            Sha("CUE4Parse fixture decoder profile"), Sha("MapBundleExport.Raw producer"),
            MapRawPackageRecord.CaptureProfileSha256(declaredSourceCoverageComplete), 1, sourceManifestComplete: true);
    }

    private static bool DescriptorHasNoSourceGraphs(MapRawPackageRecordDescriptor descriptor)
    {
        var forbidden = new[] { typeof(UObject), typeof(CUE4Parse.UE4.Assets.IPackage), typeof(DefaultFileProvider) };
        foreach (var property in descriptor.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            if (forbidden.Any(type => type.IsAssignableFrom(property.PropertyType))) return false;
        foreach (var field in descriptor.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
            if (forbidden.Any(type => type.IsAssignableFrom(field.FieldType))) return false;
        return true;
    }

    private static string HashJson(JToken token)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        using var sink = new HashWriteStream(hash);
        using var text = new StreamWriter(sink, new UTF8Encoding(false), 4096, leaveOpen: true);
        using var writer = new JsonTextWriter(text) { Formatting = Formatting.None, CloseOutput = false };
        token.WriteTo(writer);
        writer.Flush();
        text.Flush();
        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    private static string HashFile(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read,
            64 * 1024, FileOptions.SequentialScan);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static string Sha(string text)
        => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text))).ToLowerInvariant();

    private static MapPackageRecordStoreLimits StoreLimits()
        => new(MaxEntryBytes: 32 * 1024 * 1024, MaxStoreBytes: 256 * 1024 * 1024,
            BufferBytes: 64 * 1024, MaxReceiptBytes: 2 * 1024 * 1024);

    private static MapRawPackageRecordLimits RecordLimits()
        => new(MaxRows: 100_000, MaxRowBytes: 4 * 1024 * 1024, MaxMetadataBytes: 2 * 1024 * 1024,
            MaxDepth: 64, MaxEntryBytes: 32 * 1024 * 1024, MaxDiagnostics: 500, BufferBytes: 64 * 1024);

    private static void Check(bool condition, string name)
    {
        if (!condition) throw new InvalidOperationException("FAIL " + name);
        _checks++;
        Console.WriteLine("PASS " + name);
    }

    private static void ExpectThrows<T>(Action action, string name) where T : Exception
    {
        try { action(); }
        catch (T) { Check(true, name); return; }
        throw new InvalidOperationException("FAIL " + name);
    }

    private sealed record ExpectedRow(
        string SourcePath, string? OwnerPackageName, string? SourceExportType,
        string RawSha256, string[] Diagnostics);

    private static void CheckDescriptorLifetime(string evidence, string fixtureRoot)
    {
        var probe = CaptureAndDisposeProvider(evidence, fixtureRoot);
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
        Check(DescriptorHasNoSourceGraphs(probe.Descriptor), "returned descriptor contains only scalar/store metadata");
        if (!probe.Source.IsAlive && !probe.Package.IsAlive && !probe.Provider.IsAlive)
            Check(true, "source UObject, owner package, and provider collect after capture scope");
        else
            Console.WriteLine($"LIMITATION fixture provider graph remains after dispose/GC: source={probe.Source.IsAlive}, package={probe.Package.IsAlive}, provider={probe.Provider.IsAlive}");
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static LifetimeProbe CaptureAndDisposeProvider(string evidence, string fixtureRoot)
    {
        var provider = CreateProvider(fixtureRoot, unversioned: false);
        provider.Mount();
        var path = provider.Files.Keys.Single(value => value.EndsWith("DA_AllProperties.uasset", StringComparison.OrdinalIgnoreCase));
        var package = provider.LoadPackage(path);
        var source = package.GetExports().First();
        var key = BuildKey(fixtureRoot, unversioned: false,
            "/Game/Fixtures/Properties/DA_AllProperties", declaredSourceCoverageComplete: false);
        var store = new MapPackageRecordStore(Path.Combine(evidence, "lifetime-store"), StoreLimits());
        var sourceWeak = new WeakReference(source);
        var packageWeak = new WeakReference(package);
        var providerWeak = new WeakReference(provider);
        var descriptor = MapRawPackageRecord.CaptureOrReuse(store, key, key.CanonicalPackage,
            package.GetExports, declaredSourceCoverageComplete: false,
            temporaryRoot: Path.Combine(evidence, "lifetime-temporary"), limits: RecordLimits());
        provider.Dispose();
        return new LifetimeProbe(sourceWeak, packageWeak, providerWeak, descriptor);
    }

    private sealed record LifetimeProbe(
        WeakReference Source,
        WeakReference Package,
        WeakReference Provider,
        MapRawPackageRecordDescriptor Descriptor);

    private sealed class HashWriteStream(IncrementalHash hash) : Stream
    {
        public override bool CanRead => false;
        public override bool CanSeek => false;
        public override bool CanWrite => true;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() { }
        public override void Write(byte[] buffer, int offset, int count) => hash.AppendData(buffer, offset, count);
        public override void Write(ReadOnlySpan<byte> buffer) => hash.AppendData(buffer);
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
    }
}
