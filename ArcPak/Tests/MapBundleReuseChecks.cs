using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ArcPak;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Exporters;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Writers.UEFormat.Enums;

internal static class MapBundleReuseChecks
{
    private static int checks;

    internal static int Run(string root, bool red, bool traceMode = false)
    {
        checks = 0;
        textureMetadataWrites = 0;
        Directory.CreateDirectory(root);
        if (traceMode) return RunTraceChecks(root);
        var material = Source("M_Shared");
        var texture = Source("T_Shared");
        var meshA = Source("MeshA");
        var meshB = Source("MeshB");
        var materialOutput = MakeState("material", ".arc-material.json", "material-bytes");
        var textureOutput = MakeState("texture", ".png", "texture-bytes");
        var meshAOutput = MakeState("mesh-a", ".uemodel", "mesh-a-bytes");
        var meshBOutput = MakeState("mesh-b", ".uemodel", "mesh-b-bytes");
        var dependencies = new Dictionary<string, (UObject Source, State State)[]>(StringComparer.Ordinal)
        {
            [meshA.Name] = [(material, materialOutput)],
            [meshB.Name] = [(material, materialOutput)],
            [material.Name] = [(texture, textureOutput)]
        };

        if (red)
        {
            RunAsset(root, "red-a", meshA, meshAOutput, null, dependencies);
            RunAsset(root, "red-b", meshB, meshBOutput, null, dependencies);
            Check(materialOutput.Writes == 1 && textureOutput.Writes == 1,
                $"expected one shared conversion, observed material={materialOutput.Writes}, texture={textureOutput.Writes}");
            throw new InvalidOperationException("RED check unexpectedly passed");
        }

        var revisions = new Dictionary<UObject, string>(ReferenceEqualityComparer.Instance);
        using var reuse = new ExportArtifactReuse(source => revisions.TryGetValue(source, out var value) ? value : "bundle-revision-1");
        var first = RunAsset(root, "asset-a", meshA, meshAOutput, reuse, dependencies);
        var second = RunAsset(root, "asset-b", meshB, meshBOutput, reuse, dependencies);
        Check(materialOutput.Writes == 1 && textureOutput.Writes == 1, "shared material and texture convert once across distinct asset roots");
        Check(first.Count == 3 && second.Count == 3 && first.All(item => item.Success) && second.All(item => item.Success), "each asset session reports complete successful conversions");
        Check(textureMetadataWrites == 2, "texture metadata callback regenerates once in each scratch root");
        AssertPublished(root, "asset-a", "/Game/Props/MeshA", "mesh-a-bytes", first);
        var publishedPath = AssertPublished(root, "asset-b", "/Game/Props/MeshB", "mesh-b-bytes", second);

        var sameName = Source("M_Shared");
        var identityState = MakeState("identity", ".arc-material.json", "other-identity");
        RunAsset(root, "identity", sameName, identityState, reuse, []);
        Check(identityState.Writes == 1, "distinct embedded object identity does not coalesce");

        revisions[material] = "bundle-revision-2";
        RunAsset(root, "revision", material, materialOutput, reuse, dependencies);
        Check(materialOutput.Writes == 2, "source revision change reconverts material");

        var failing = Source("M_Failing");
        var failureState = MakeState("failure", ".arc-material.json", "recover");
        failureState.Fail = true;
        var failed = RunAsset(root, "failure-1", failing, failureState, reuse, []);
        Check(failed.Count == 1 && !failed[0].Success, "failed conversion is reported as failed");
        Check(!Directory.EnumerateFiles(Path.Combine(root, "scratch", "failure-1"), "*", SearchOption.AllDirectories).Any(), "failed conversion leaves no files to publish");
        failureState.Fail = false;
        var recovered = RunAsset(root, "failure-2", failing, failureState, reuse, []);
        Check(recovered.Single().Success && failureState.Writes == 2, "failed output is neither published nor reused");

        var ordinary = Source("Ordinary");
        var ordinaryState = MakeState("ordinary", ".bin", "ordinary-bytes");
        RunAsset(root, "ordinary-1", ordinary, ordinaryState, null, []);
        RunAsset(root, "ordinary-2", ordinary, ordinaryState, null, []);
        Check(ordinaryState.Writes == 2, "default map session path does not reuse across runs");
        reuse.Dispose();
        Check(File.Exists(publishedPath), "disposing caller scope leaves published files intact");
        return checks;
    }

    internal static int RunStableIdentityChecks(string root, bool baselineOnly)
    {
        checks = 0;
        Directory.CreateDirectory(root);
        var ordinary = new State("reference-default", ".bin", "same-artifact-bytes");
        var ordinarySourceA = Source("/Game/Props/Stable.Stable:Embedded");
        var ordinarySourceB = Source("/Game/Props/Stable.Stable:Embedded");
        RunIdentityPair(root, "reference-default", ordinarySourceA, ordinarySourceB, null, _ => null, "revision-1", "revision-1",
            new ExportOptions(meshFormat: EMeshFormat.USD), new ExportOptions(meshFormat: EMeshFormat.USD), ordinary);
        if (baselineOnly)
        {
            Check(ordinary.Writes == 1, "RED: distinct source instances reuse without caller identity");
            return checks;
        }
        Check(ordinary.Writes == 2, "reference-only default does not reuse across distinct instances");

        var stableSourceA = Source("/Game/Props/Stable.Stable:Embedded");
        var stableSourceB = Source("/Game/Props/Stable.Stable:Embedded");
        var equal = new State("stable-equal", ".bin", "authorized-artifact-bytes");
        RunIdentityPair(root, "stable-equal", stableSourceA, stableSourceB, null,
            source => Identity(source, "provider-A", Digest('A'), "mutation-1"), "revision-1", "revision-1",
            new ExportOptions(meshFormat: EMeshFormat.USD), new ExportOptions(meshFormat: EMeshFormat.USD), equal);
        Check(equal.Writes == 1, "equal authorized stable identity restores across distinct instances");

        RejectStableReuse(root, "provider", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-B", Digest('A'), "mutation-1"));
        RejectStableReuse(root, "embedded-path", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-A", Digest('A'), "mutation-1"),
            Source("/Game/Props/Stable.Stable:EmbeddedOne"), Source("/Game/Props/Stable.Stable:EmbeddedTwo"));
        var classA = Source("/Game/Props/Stable.Stable:Embedded");
        var classB = new DerivedFixtureObject { Name = "/Game/Props/Stable.Stable:Embedded" };
        RejectStableReuse(root, "class", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-A", Digest('A'), "mutation-1"), classA, classB);
        RejectStableReuse(root, "package-digest", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-A", Digest('B'), "mutation-1"));
        RejectStableReuse(root, "mutation-authority", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-A", Digest('A'), "mutation-2"));
        RejectStableReuse(root, "source-revision", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-A", Digest('A'), "mutation-1"), revisionA: "revision-1", revisionB: "revision-2");
        RejectStableReuse(root, "unresolved-source-revision", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-A", Digest('A'), "mutation-1"), revisionA: "", revisionB: "");
        RejectStableReuse(root, "export-options", (a, b) => Identity(a, "provider-A", Digest('A'), "mutation-1"),
            (a, b) => Identity(b, "provider-A", Digest('A'), "mutation-1"),
            optionsA: new ExportOptions(meshFormat: EMeshFormat.USD),
            optionsB: new ExportOptions(meshFormat: EMeshFormat.USD, textureQuality: 75));

        RejectStableReuse(root, "null-resolver", (_, _) => null, (_, _) => null);
        RejectStableReuse(root, "invalid-resolver", (a, b) => Identity(a, "provider-A", "not-a-digest", "mutation-1"),
            (a, b) => Identity(b, "provider-A", "not-a-digest", "mutation-1"));
        RejectStableReuse(root, "unresolved-mutation-authority", (a, b) => Identity(a, "provider-A", Digest('A'), " "),
            (a, b) => Identity(b, "provider-A", Digest('A'), " "));
        return checks;
    }

    private static void RejectStableReuse(string root, string label,
        Func<UObject, UObject, ExportArtifactStableIdentity?> firstIdentity,
        Func<UObject, UObject, ExportArtifactStableIdentity?> secondIdentity,
        UObject? sourceA = null, UObject? sourceB = null, string revisionA = "revision-1", string revisionB = "revision-1",
        ExportOptions? optionsA = null, ExportOptions? optionsB = null)
    {
        sourceA ??= Source("/Game/Props/Stable.Stable:Embedded");
        sourceB ??= Source("/Game/Props/Stable.Stable:Embedded");
        var state = new State(label, ".bin", "same-artifact-bytes");
        RunIdentityPair(root, label, sourceA, sourceB,
            source => ReferenceEquals(source, sourceA) ? revisionA : revisionB,
            source => ReferenceEquals(source, sourceA) ? firstIdentity(sourceA, sourceB) : secondIdentity(sourceA, sourceB),
            revisionA, revisionB, optionsA ?? new ExportOptions(meshFormat: EMeshFormat.USD),
            optionsB ?? new ExportOptions(meshFormat: EMeshFormat.USD), state);
        Check(state.Writes == 2, label + " identity difference or missing authority declines cross-instance reuse");
    }

    private static void RunIdentityPair(string root, string label, UObject sourceA, UObject sourceB,
        Func<UObject, string?>? revisionOverride, Func<UObject, ExportArtifactStableIdentity?>? stableIdentity,
        string revisionA, string revisionB, ExportOptions optionsA, ExportOptions optionsB, State state)
    {
        using var reuse = new ExportArtifactReuse(source => revisionOverride?.Invoke(source) ??
            (ReferenceEquals(source, sourceA) ? revisionA : revisionB), stableIdentity);
        var first = RunAsset(root, label + "-first", sourceA, state, reuse, [], optionsA);
        var second = RunAsset(root, label + "-second", sourceB, state, reuse, [], optionsB);
        Check(first.Single().Success && second.Single().Success, label + " production sessions complete");
        var firstBytes = File.ReadAllBytes(first.Single().DiskFilePaths!.Single());
        var secondBytes = File.ReadAllBytes(second.Single().DiskFilePaths!.Single());
        Check(firstBytes.SequenceEqual(secondBytes), label + " restored or converted bytes match");
    }

    private static ExportArtifactStableIdentity Identity(UObject source, string provider, string digest, string mutation)
        => new(provider, digest, source.GetPathName(), source.GetType().FullName!, mutation);

    private static string Digest(char value) => new(value, 64);

    private static int RunTraceChecks(string root)
    {
        var tracePath = Path.Combine(root, "trace.jsonl");
        var prior = Environment.GetEnvironmentVariable("DATARAIDERS_MAP_EXPORT_TRACE");
        try
        {
            Environment.SetEnvironmentVariable("DATARAIDERS_MAP_EXPORT_TRACE", null);
            var untraced = RunTraceScenario(Path.Combine(root, "disabled"));
            Environment.SetEnvironmentVariable("DATARAIDERS_MAP_EXPORT_TRACE", tracePath);
            var traced = RunTraceScenario(Path.Combine(root, "enabled"));
            Check(untraced.Calls == 4 && traced.Calls == 4,
                $"fixture makes four actual conversions with tracing disabled and enabled, observed {untraced.Calls} and {traced.Calls}");
            Check(untraced.Callbacks == 2 && traced.Callbacks == 2, "texture callbacks run twice in each mode and stay separate from conversion counts");
            Check(untraced.PublicationHashes.SequenceEqual(traced.PublicationHashes), "tracing preserves published output paths and bytes");

            var events = File.Exists(tracePath)
                ? File.ReadLines(tracePath).Where(line => !string.IsNullOrWhiteSpace(line)).Select(line => JsonDocument.Parse(line)).ToArray()
                : [];
            Check(events.Length > 0, "opt-in trace emits machine-readable diagnostics");
            var kinds = events.Select(document => document.RootElement.TryGetProperty("kind", out var value) ? value.GetString() : null).ToHashSet();
            Check(kinds.Contains("session-export") && kinds.Contains("artifact-reuse") && kinds.Contains("session-callback"),
                "fixture trace separates actual conversion, reuse restore, and OnExportBegin callback phases");
            Check(events.Any(document => document.RootElement.TryGetProperty("restoreFiles", out _) &&
                document.RootElement.TryGetProperty("restoreBytes", out _) && document.RootElement.TryGetProperty("restoreMs", out _)),
                "trace reports restored file, byte, and elapsed counts");
            Check(events.Where(document => document.RootElement.TryGetProperty("kind", out var kind) && kind.GetString() == "session-export")
                .Sum(document => document.RootElement.GetProperty("calls").GetInt32()) == 4, "trace counts actual ExportAsync calls, not callbacks or log lines");
            var reuseEvents = events.Where(document => document.RootElement.GetProperty("kind").GetString() == "artifact-reuse").ToArray();
            var restoreHits = reuseEvents.Where(document => document.RootElement.GetProperty("hit").GetBoolean()).ToArray();
            Check(restoreHits.Length == 2 && restoreHits.All(document => document.RootElement.GetProperty("restoreFiles").GetInt32() == 1 &&
                document.RootElement.GetProperty("restoreBytes").GetInt64() > 0 && document.RootElement.GetProperty("restoreMs").GetDouble() >= 0),
                "shared material and texture restore file, byte, and elapsed counts");
            Check(reuseEvents.All(document => !string.IsNullOrWhiteSpace(document.RootElement.GetProperty("sourceRefId").GetString()) &&
                !string.IsNullOrWhiteSpace(document.RootElement.GetProperty("revisionHash").GetString()) &&
                !string.IsNullOrWhiteSpace(document.RootElement.GetProperty("optionsHash").GetString())),
                "reuse records use stable reference identity and hashed revision and options identities");
            Check(events.Where(document => document.RootElement.GetProperty("kind").GetString() == "session-callback")
                .All(document => document.RootElement.GetProperty("callback").GetString() == "OnExportBegin" &&
                    document.RootElement.GetProperty("elapsedMs").GetDouble() >= 0), "callback duration records remain separate from conversion records");
            return checks;
        }
        finally { Environment.SetEnvironmentVariable("DATARAIDERS_MAP_EXPORT_TRACE", prior); }
    }

    private static (int Calls, int Callbacks, string[] PublicationHashes) RunTraceScenario(string root)
    {
        Directory.CreateDirectory(root);
        var material = Source("M_Shared");
        var texture = Source("T_Shared");
        var meshA = Source("MeshA");
        var meshB = Source("MeshB");
        var materialOutput = MakeState("material", ".arc-material.json", "material-bytes");
        var textureOutput = MakeState("texture", ".png", "texture-bytes");
        var meshAOutput = MakeState("mesh-a", ".uemodel", "mesh-a-bytes");
        var meshBOutput = MakeState("mesh-b", ".uemodel", "mesh-b-bytes");
        var dependencies = new Dictionary<string, (UObject Source, State State)[]>(StringComparer.Ordinal)
        {
            [meshA.Name] = [(material, materialOutput)],
            [meshB.Name] = [(material, materialOutput)],
            [material.Name] = [(texture, textureOutput)]
        };
        var callbacksBefore = textureMetadataWrites;
        using var reuse = new ExportArtifactReuse(_ => "bundle-revision-1");
        var first = RunAsset(root, "asset-a", meshA, meshAOutput, reuse, dependencies);
        var second = RunAsset(root, "asset-b", meshB, meshBOutput, reuse, dependencies);
        var firstPublished = MapBundleDocument.PublishAsset(Path.Combine(root, "scratch", "asset-a"), root, "/Game/Props/MeshA",
            "bundle-revision-1", ["/Game/Materials/M_Shared"], "/Game/Props/MeshA.MeshA", ["/Game/Materials/M_Shared.M_Shared"]);
        var secondPublished = MapBundleDocument.PublishAsset(Path.Combine(root, "scratch", "asset-b"), root, "/Game/Props/MeshB",
            "bundle-revision-1", ["/Game/Materials/M_Shared"], "/Game/Props/MeshB.MeshB", ["/Game/Materials/M_Shared.M_Shared"]);
        Check(first.All(result => result.Success) && second.All(result => result.Success), "trace fixture sessions succeed");
        Check(materialOutput.Writes == 1 && textureOutput.Writes == 1, "shared dependencies convert once and restore for the second asset");
        var hashes = PublishedHashes(root, firstPublished).Concat(PublishedHashes(root, secondPublished)).Order(StringComparer.Ordinal).ToArray();
        return (meshAOutput.Writes + meshBOutput.Writes + materialOutput.Writes + textureOutput.Writes,
            textureMetadataWrites - callbacksBefore, hashes);
    }

    private static IEnumerable<string> PublishedHashes(string root, Newtonsoft.Json.Linq.JObject publication)
        => publication["files"]!.Values<string>().Select(path =>
            path + " " + Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(MapBundleDocument.Inside(root, path)))));

    private static IReadOnlyList<ExportResult> RunAsset(string root, string run, UObject source, State meshState,
        ExportArtifactReuse? reuse, Dictionary<string, (UObject Source, State State)[]> dependencies, ExportOptions? options = null)
    {
        var scratch = Path.Combine(root, "scratch", run);
        Directory.CreateDirectory(scratch);
        var session = MapBundleExport.CreateAssetExportSession(reuse);
        Configure(session, dependencies);
        session.OnExportBegin = objectPath =>
        {
            if (objectPath.EndsWith("T_Shared", StringComparison.Ordinal))
            {
                Interlocked.Increment(ref textureMetadataWrites);
                MapBundleDocument.WriteAtomic(scratch, "T_Shared.arc-texture.json", Encoding.UTF8.GetBytes("native-metadata:" + run));
            }
        };
        AddWithIdentity(session, source, new FixtureExporter(source, meshState));
        var results = session.RunAsync(scratch, options ?? new ExportOptions(meshFormat: EMeshFormat.USD)).GetAwaiter().GetResult();
        return results;
    }

    private static int textureMetadataWrites;

    private static string AssertPublished(string root, string run, string package, string meshBytes, IReadOnlyList<ExportResult> results)
    {
        var scratch = Path.Combine(root, "scratch", run);
        var published = MapBundleDocument.PublishAsset(scratch, root, package, "bundle-revision-1",
            ["/Game/Materials/M_Shared"], package + "." + package.Split('/').Last(), ["/Game/Materials/M_Shared.M_Shared"]);
        var files = published["files"]!.Values<string>().ToArray();
        Check(files.Length == 4, run + " publication contains only its own mesh, material, texture, and metadata");
        Check(files.Any(path => path.EndsWith(".uemodel", StringComparison.Ordinal)), run + " publication includes its mesh");
        Check(files.Any(path => path.EndsWith(".arc-material.json", StringComparison.Ordinal)), run + " publication includes material bytes");
        Check(files.Any(path => path.EndsWith(".png", StringComparison.Ordinal)), run + " publication includes texture bytes");
        var metadata = files.Single(path => path.EndsWith(".arc-texture.json", StringComparison.Ordinal));
        Check(File.ReadAllText(MapBundleDocument.Inside(root, metadata)) == "native-metadata:" + run, run + " publication includes regenerated metadata");
        foreach (var path in files)
        {
            var fullPath = MapBundleDocument.Inside(root, path);
            Check(File.Exists(fullPath), run + " referenced publication path exists");
            var suffix = Path.GetFileName(fullPath);
            var expected = suffix.EndsWith(".uemodel", StringComparison.Ordinal) ? meshBytes
                : suffix.EndsWith(".arc-material.json", StringComparison.Ordinal) ? "material-bytes"
                : suffix.EndsWith(".png", StringComparison.Ordinal) ? "texture-bytes"
                : "native-metadata:" + run;
            Check(File.ReadAllText(fullPath) == expected, run + " referenced publication bytes match conversion output");
        }
        Check(results.All(result => (result.DiskFilePaths ?? []).All(path => path.StartsWith(scratch, StringComparison.OrdinalIgnoreCase))), run + " session paths stay in its own scratch root");
        return MapBundleDocument.Inside(root, (string)published["mesh"]!);
    }

    private static void Configure(ExportSession session, Dictionary<string, (UObject Source, State State)[]> dependencies)
    {
        Func<ExporterBase, CancellationToken, IEnumerable<(UObject Source, ExporterBase Exporter)>> resolver = (exporter, _) =>
            dependencies.TryGetValue(exporter.ObjectName, out var entries)
                ? entries.Select(entry => (entry.Source, (ExporterBase)new FixtureExporter(entry.Source, entry.State)))
                : [];
        typeof(ExportSession).GetProperty("DependencyResolverForTesting", BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(session, resolver);
    }

    private static void AddWithIdentity(ExportSession session, UObject source, ExporterBase exporter)
        => typeof(ExportSession).GetMethod("AddWithIdentityForTesting", BindingFlags.Instance | BindingFlags.NonPublic)!
            .Invoke(session, [source, exporter]);

    private static UObject Source(string name) => new() { Name = name };
    private static State MakeState(string name, string extension, string content) => new(name, extension, content);
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("ASSERTION FAILED: " + message);
        checks++;
        Console.WriteLine("PASS " + message);
    }

    private sealed class State(string name, string extension, string content)
    {
        public string Name { get; } = name;
        public string Extension { get; } = extension;
        public string Content { get; } = content;
        public int Writes;
        public int MetadataWrites;
        public bool Fail;
    }

    private sealed class FixtureExporter(UObject source, State state) : ExporterBase(source)
    {
        protected override IReadOnlyList<ExportFile> BuildExportFiles(CancellationToken ct = default)
        {
            Interlocked.Increment(ref state.Writes);
            if (state.Fail) throw new InvalidDataException("controlled fixture failure");
            return [new ExportFile(state.Extension, Encoding.UTF8.GetBytes(state.Content))];
        }
    }

    private sealed class DerivedFixtureObject : UObject { }
}
