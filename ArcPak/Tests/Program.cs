using System.Text;
using ArcPak;
using ArcPak.Tests;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Objects.Properties;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse_Conversion.Options;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;
using FModel.Exporters.MapPlacement;
using FModel.Views.Snooper.MapPlacement;
using Newtonsoft.Json.Linq;

if (args.FirstOrDefault() == "--dependency-entry-hash")
{
    if (args.Length < 3) throw new ArgumentException("--dependency-entry-hash requires evidence and fixture paths");
    var count = DependencyHashChecks.Run(Path.GetFullPath(args[1]), Path.GetFullPath(args[2]));
    Console.WriteLine($"PASS {count} dependency entry hash checks");
    return;
}if (args.Length > 0 && args[0].StartsWith("--child-", StringComparison.Ordinal))
{
    Environment.ExitCode = MapPackageRecordStoreChecks.RunChild(args[0], args[1], args.Length > 2 ? args[2] : "");
    return;
}
if (args.FirstOrDefault() == "--map-package-record-store")
{
    var storeEvidence = Path.GetFullPath(args.Length > 1 ? args[1] : Path.Combine(Path.GetTempPath(), "map-package-record-store-checks"));
    var storeChecks = MapPackageRecordStoreChecks.Run(storeEvidence);
    Console.WriteLine($"PASS {storeChecks} package record store checks");
    return;
}

if (args.FirstOrDefault() == "--map-archive-source-identity")
{
    var identityEvidence = Path.GetFullPath(args.Length > 1 ? args[1] : Path.Combine(Path.GetTempPath(), "map-archive-source-identity-checks"));
    var identityChecks = MapArchiveSourceIdentityChecks.Run(identityEvidence);
    Console.WriteLine($"PASS {identityChecks} map archive source identity checks");
    return;
}
if (args.FirstOrDefault() == "--map-raw-package-record")
{
    if (args.Length < 3) throw new ArgumentException("--map-raw-package-record requires evidence and fixture paths");
    var rawEvidence = Path.GetFullPath(args[1]);
    var fixtureRoot = Path.GetFullPath(args[2]);
    var rawChecks = MapRawPackageRecordChecks.Run(rawEvidence, fixtureRoot);
    Console.WriteLine($"PASS {rawChecks} raw package record checks");
    return;
}
if (args.FirstOrDefault() == "--map-pinned-provider-registration")
{
    if (args.Length < 3) throw new ArgumentException("--map-pinned-provider-registration requires evidence and fixture paths");
    var pinnedChecks = MapPinnedProviderRegistrationChecks.Run(Path.GetFullPath(args[1]), Path.GetFullPath(args[2]));
    Console.WriteLine($"PASS {pinnedChecks} pinned provider registration checks");
    return;
}
if (args.FirstOrDefault() is "--map-source-snapshot" or "--map-source-snapshot-red")
{
    var leaseEvidence = Path.GetFullPath(args.Length > 1 ? args[1] : Path.Combine(Path.GetTempPath(), "map-source-snapshot-checks"));
    var leaseChecks = args[0] == "--map-source-snapshot-red"
        ? MapSourceSnapshotChecks.RunRed(leaseEvidence)
        : MapSourceSnapshotChecks.Run(leaseEvidence);
    Console.WriteLine($"PASS {leaseChecks} map source snapshot checks");
    return;
}
if (args.FirstOrDefault() is "--map-package-memoization" or "--map-package-memoization-red")
{
    var memoizationEvidence = Path.GetFullPath(args.Length > 1 ? args[1] : Path.Combine(Path.GetTempPath(), "map-package-memoization-checks"));
    var memoizationChecks = await MapPackageMemoizationChecks.Run(memoizationEvidence, args[0] == "--map-package-memoization-red");
    Console.WriteLine($"PASS {memoizationChecks} map package memoization checks");
    return;
}
if (args.FirstOrDefault() == "--probe-exact-source")
{
    ExactSourceProbe.Run(args.Skip(1).ToArray());
    return;
}
if (args.FirstOrDefault() == "--verify-release")
{
    ReleaseChecks.Verify(args.Skip(1).ToArray());
    return;
}
if (args.FirstOrDefault() == "--probe-real")
{
    RealMapProbe.Run(args.Skip(1).ToArray());
    return;
}
if (args.FirstOrDefault() == "--probe-spline")
{
    RealSplineProbe.Run(args.Skip(1).ToArray());
    return;
}
if (args.FirstOrDefault() is "--map-bundle-reuse" or "--map-bundle-reuse-red" or "--map-bundle-trace")
{
    var reuseEvidence = Path.GetFullPath(args.Length > 1 ? args[1] : Path.Combine(Path.GetTempPath(), "map-bundle-reuse-checks"));
    var traceMode = args[0] == "--map-bundle-trace";
    var count = MapBundleReuseChecks.Run(reuseEvidence, args[0] == "--map-bundle-reuse-red", traceMode);
    Console.WriteLine($"PASS {count} {(traceMode ? "map bundle trace" : "map bundle reuse")} checks");
    return;
}
if (args.FirstOrDefault() is "--map-stable-identity" or "--map-stable-identity-red")
{
    var stableEvidence = Path.GetFullPath(args.Length > 1 ? args[1] : Path.Combine(Path.GetTempPath(), "map-stable-identity-checks"));
    var count = MapBundleReuseChecks.RunStableIdentityChecks(stableEvidence, args[0] == "--map-stable-identity-red");
    Console.WriteLine($"PASS {count} map stable identity checks");
    return;
}
var evidence = Path.GetFullPath(args.Length > 0 ? args[0] : Path.Combine(Path.GetTempPath(), "map-bundle-tests"));
Directory.CreateDirectory(evidence);
if (typeof(MapPlacementExtractorOptions).GetProperty("TraversalCallback") == null)
    throw new InvalidOperationException("FAIL shared all-object traversal callback is absent");
var checks = ContinuationChecks.Run(evidence);
void Check(bool condition, string name)
{
    if (!condition) throw new InvalidOperationException("FAIL " + name);
    checks++;
    Console.WriteLine("PASS " + name);
}
void Reject(Action action, string name)
{
    try { action(); }
    catch (InvalidDataException) { Check(true, name); return; }
    throw new InvalidOperationException("FAIL " + name);
}
Check(MapBundleDocument.CanonicalPackage("PioneerGame/Content/Maps/Test.umap") == "/Game/Maps/Test", "cooked map canonicalization");
Check(MapBundleDocument.CanonicalPackage("StaticMesh'/Game/Props/Mesh.Mesh'") == "/Game/Props/Mesh", "object reference canonicalization");
Check(MapBundleDocument.CanonicalPackage("RemappedPlugins/EmbarkScript/Content/Props/Mesh.uasset") == "/EmbarkScript/Props/Mesh", "plugin mount canonicalization");
Check(MapBundleDocument.CanonicalPackage("/Game/Props/Mesh.Mesh#spline:abc") == "/Game/Props/Mesh", "spline asset uses canonical base package");
Reject(() => MapBundleDocument.CanonicalPackage("/Game/../Outside"), "package traversal rejected");
Reject(() => MapBundleDocument.CanonicalPackage("/Game/A.uasset/../Outside"), "traversal after extension rejected");
foreach (var relative in new[] { "../outside", "dir/../../outside", "C:/outside", "/outside", "dir\\..\\outside", "dir/file:stream" })
    Reject(() => MapBundleDocument.Inside(evidence, relative), "path confinement " + relative);
Check(MapBundleDocument.Inside(evidence, "assets/mesh.uemodel").StartsWith(evidence, StringComparison.OrdinalIgnoreCase), "relative path accepted");
var matrix = MapBundleDocument.Matrix(100, 200, 300, 0, 90, 0, 2, 3, 4).Values<float>().ToArray();
Check(matrix.Length == 16 && matrix.All(float.IsFinite), "matrix finite row-major length");
Check(matrix[3] == 100 && matrix[7] == 200 && matrix[11] == 300 && matrix[15] == 1, "centimeter translation uses last column");
Check(Math.Abs(matrix[0]) < 0.0001 && Math.Abs(matrix[4] - 2) < 0.0001 && Math.Abs(matrix[1] + 3) < 0.0001 && matrix[10] == 4, "Unreal rotation and nonuniform scale preserved");
Reject(() => MapBundleDocument.Matrix(float.NaN, 0, 0, 0, 0, 0, 1, 1, 1), "nonfinite matrix rejected");
var parent = new FTransform(new FRotator(0, 90, 0), new FVector(1000, 2000, 3000), new FVector(2, 2, 2));
var child = new FTransform(new FVector(10, 20, 30));
var record = MapPlacementRecord.FromTransform("Actor", "/Game/Props/Mesh.Mesh", "StaticMesh", child * parent,
    "/Game/Maps/Test.Test", componentPath: "/Game/Maps/Test.Test:PersistentLevel.Actor.Mesh", instanceIndex: 3,
    worldInstancePath: "/Game/Maps/Root.Root:InstanceA", materialOverrides: ["/Game/Materials/MI_Test.MI_Test", ""]);
var other = MapPlacementRecord.StableId(record.ComponentPath, record.InstanceIndex, "/Game/Maps/Root.Root:InstanceB");
Check(record.PlacementId.Length == 64 && record.PlacementId != other, "world instance placement identities differ");
var placement = MapBundleDocument.Placement(record, new JObject { ["properties"] = new JArray(new JObject { ["type"] = "ObjectProperty", ["value"] = "/Game/Unknown.Asset" }) });
Check((string?)placement["materials"]![0] == "/Game/Materials/MI_Test" && (string?)placement["materials"]![1] == "", "material overrides retain slot holes");
var composed = placement["matrix"]!.Values<float>().ToArray();
Check(Math.Abs(composed[3] - 960) < 0.01 && Math.Abs(composed[7] - 2020) < 0.01 && Math.Abs(composed[11] - 3060) < 0.01, "composed world placement transform");
Check(placement["properties"]!["raw"]!["properties"]![0]!["type"]!.Value<string>() == "ObjectProperty", "raw typed metadata retained");
var first = new JObject { ["z"] = 1, ["a"] = new JObject { ["y"] = 2, ["b"] = 3 } };
var second = new JObject { ["a"] = new JObject { ["b"] = 3, ["y"] = 2 }, ["z"] = 1 };
Check(MapBundleDocument.Bytes(first).SequenceEqual(MapBundleDocument.Bytes(second)), "canonical JSON property ordering");
var stage = Path.Combine(evidence, "stage");
Directory.CreateDirectory(stage);
MapBundleDocument.WriteAtomic(stage, "PioneerGame/Content/Props/Mesh_LOD0.uemodel", [1, 2, 3]);
MapBundleDocument.WriteAtomic(stage, "PioneerGame/Content/Props/Mesh_LOD1.uemodel", [4, 5]);
MapBundleDocument.WriteAtomic(evidence, "keep.txt", Encoding.UTF8.GetBytes("unrelated"));
var assetFirst = MapBundleDocument.PublishAsset(stage, evidence, "/Game/Props/Mesh", "revision", ["/Game/Materials/MI_Test"]);
var exported = MapBundleDocument.Inside(evidence, (string)assetFirst["mesh"]!);
var previousWrite = File.GetLastWriteTimeUtc(exported);
foreach (var path in Directory.EnumerateFiles(stage, "*", SearchOption.AllDirectories)) File.SetLastWriteTimeUtc(path, DateTime.UnixEpoch);
var assetSecond = MapBundleDocument.PublishAsset(stage, evidence, "/Game/Props/Mesh", "revision", ["/Game/Materials/MI_Test"]);
Check(MapBundleDocument.Bytes(assetFirst).SequenceEqual(MapBundleDocument.Bytes(assetSecond)), "fingerprint ignores source timestamps");
Check(File.GetLastWriteTimeUtc(exported) == previousWrite, "valid content receipt avoids rewriting cached output");
var receiptPath = MapBundleDocument.Inside(evidence, "receipts/" + (string)assetFirst["fingerprint"]! + ".json");
var malformed = JObject.Parse(File.ReadAllText(receiptPath));
malformed["asset"]!["mesh"] = "../outside.uemodel";
File.WriteAllBytes(receiptPath, MapBundleDocument.Bytes(malformed));
var recovered = MapBundleDocument.PublishAsset(stage, evidence, "/Game/Props/Mesh", "revision", ["/Game/Materials/MI_Test"]);
Check(JToken.DeepEquals(recovered, assetFirst), "tampered receipt paths are not reused");
Check(assetFirst["meshFiles"]!.Count() == 2, "all exported mesh files retained");
Check(MapBundleExport.CreateExportOptions().MeshQuality == EMeshQuality.All &&
    MapBundleExport.CreateExportOptions().NaniteMeshFormat == ENaniteMeshFormat.NaniteFirst, "all authored LODs requested with highest Nanite detail first");
File.WriteAllBytes(exported, [9]);
MapBundleDocument.PublishAsset(stage, evidence, "/Game/Props/Mesh", "revision", ["/Game/Materials/MI_Test"]);
Check(File.ReadAllBytes(exported).SequenceEqual(new byte[] { 1, 2, 3 }), "corrupt output receipt rebuilt");
MapBundleDocument.WriteAtomic(stage, "PioneerGame/Content/Props/Mesh_LOD0.uemodel", [8, 9]);
var changed = MapBundleDocument.PublishAsset(stage, evidence, "/Game/Props/Mesh", "revision", []);
Check((string?)changed["fingerprint"] != (string?)assetFirst["fingerprint"], "changed content invalidates fingerprint");
Check(File.ReadAllText(Path.Combine(evidence, "keep.txt")) == "unrelated", "unrelated output preserved");
var bundle = MapBundleExport.Empty("/Game/Maps/Test");
bundle["placements"] = new JArray(placement);
bundle["assets"] = new JObject { ["/Game/Props/Mesh"] = assetFirst };
bundle["diagnostics"] = new JArray("Synthetic contract fixture. No game data was read.");
MapBundleDocument.WriteAtomic(evidence, "map_bundle.json", MapBundleDocument.Bytes(bundle));
var roundtrip = JObject.Parse(File.ReadAllText(Path.Combine(evidence, "map_bundle.json")));
Check(new[] { "schemaVersion", "mapId", "mapName", "sourceRevision", "complete", "diagnostics", "placements", "assets", "actors", "resources" }.All(key => roundtrip.ContainsKey(key)), "required bundle contract fields");
Check(!roundtrip.Value<bool>("complete"), "unverified coverage cannot report success");
var previous = File.ReadAllBytes(Path.Combine(evidence, "map_bundle.json"));
Reject(() => MapBundleDocument.WriteAtomic(evidence, "../map_bundle.json", []), "atomic destination confinement");
Check(File.ReadAllBytes(Path.Combine(evidence, "map_bundle.json")).SequenceEqual(previous), "failed atomic write preserves bundle");
Check(!Directory.EnumerateFiles(evidence, "*.tmp", SearchOption.AllDirectories).Any(), "atomic write leaves no temporary files");
Check(!typeof(MapBundleExport).Assembly.GetReferencedAssemblies().Any(assembly => assembly.Name is "PresentationFramework" or "PresentationCore" or "WindowsBase" or "FModel"), "headless assembly has no WPF or FModel executable dependency");
using (var provider = ArcPak.Program.CreateShell(evidence))
{
    Check(provider.PathComparer.Equals("T_Lowerbody_OcclusionCurvatureMaterialID.ubulk", "T_LowerBody_OcclusionCurvatureMaterialID.ubulk"), "native texture payload paths ignore authored case differences");
    Check(!provider.PathComparer.Equals("T_Lowerbody_Normal.ubulk", "T_Upperbody_Normal.ubulk"), "case-insensitive payload matching preserves distinct asset identities");
    var failedWalk = new MapPlacementExtractor(provider).Extract(new UWorld { Name = "Missing" }, "/Game/Maps/Missing.Missing");
    Check(failedWalk.WorldLoadFailures > 0 && failedWalk.TotalFailures > 0, "actual shared walker records a missing persistent level");
    Check(failedWalk.Diagnostics.Any(message => message.Contains("PersistentLevel", StringComparison.Ordinal)), "actual shared walker reports traversal failure diagnostics");
}
var rawDiagnostics = new SortedSet<string>(StringComparer.Ordinal);
var rawActor = new UObject
{
    Name = "/Game/Maps/Test.Test:PersistentLevel.Actor"
};
rawActor.Properties.Add(new FPropertyTag(new FName("StrProperty"), new StrProperty("/Game/Unsupported.Asset"))
{
    Name = new FName("UnknownReference"), ArrayIndex = 2
});
var rawDocument = MapBundleExport.Raw(rawActor, rawDiagnostics);
Check(rawDiagnostics.Count == 0 && (string?)rawDocument["properties"]![0]!["type"] == "StrProperty" &&
    (int?)rawDocument["properties"]![0]!["arrayIndex"] == 2 &&
    rawDocument["properties"]![0]!["value"]!.ToString().Contains("/Game/Unsupported.Asset", StringComparison.Ordinal), "producer preserves typed unsupported actor tags and references");
var invalidGame = Path.Combine(evidence, "invalid-game");
Directory.CreateDirectory(invalidGame);
var invalidMapping = Path.Combine(evidence, "invalid.usmap");
File.WriteAllBytes(invalidMapping, [0, 0, 0, 0]);
try { _ = new MapBundleMappingsProvider(invalidMapping); }
catch (CUE4Parse.UE4.Exceptions.ParserException) { }
using (var unlockedInvalid = new FileStream(invalidMapping, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
    Check(unlockedInvalid.Length > 0, "invalid mapping parser releases the source file");
var partialRoot = Path.Combine(evidence, "partial-command");
var partialCode = MapBundleExport.Run(["--game-dir", invalidGame, "--usmap", invalidMapping,
    "--path", "/Game/Maps/Missing", "--out", partialRoot]);
var partialBundle = JObject.Parse(File.ReadAllText(Path.Combine(partialRoot, "map_bundle.json")));
Check(partialCode == 2 && !partialBundle.Value<bool>("complete") && partialBundle["diagnostics"]!.Any(), "actual command writes an incomplete bundle on mapping failure");
var revisionBefore = MapBundleExport.SourceRevision(invalidGame, invalidMapping);
File.SetLastWriteTimeUtc(invalidMapping, DateTime.UnixEpoch);
Check(MapBundleExport.SourceRevision(invalidGame, invalidMapping) == revisionBefore, "source revision ignores mapping timestamps");
File.WriteAllBytes(invalidMapping, [1, 0, 0, 0]);
Check(MapBundleExport.SourceRevision(invalidGame, invalidMapping) != revisionBefore, "mapping content invalidates source revision");
var mappingAlias = Path.Combine(evidence, "renamed.usmap");
File.Copy(invalidMapping, mappingAlias, true);
Check(MapBundleExport.SourceRevision(invalidGame, mappingAlias) != MapBundleExport.SourceRevision(invalidGame, invalidMapping),
    "mapping filename-scoped parser fixes invalidate source revision");
if (args.Length > 1)
{
    var mappingCopy = Path.Combine(evidence, Path.GetFileName(args[1]));
    File.Copy(args[1], mappingCopy, true);
    var actualMapping = new MapBundleMappingsProvider(mappingCopy).MappingsForGame!;
    using (var unlocked = new FileStream(mappingCopy, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        Check(unlocked.Length > 0, "valid mapping parser releases the source file");
    var legacyMapping = new FileUsmapTypeMappingsProvider(mappingCopy).MappingsForGame!;
    JToken MappingFacts(TypeMappings mapping) => new JObject
    {
        ["types"] = new JObject(mapping.Types.Select(pair => new JProperty(pair.Key, new JObject
        {
            ["name"] = pair.Value.Name, ["super"] = pair.Value.SuperType,
            ["propertyCount"] = pair.Value.PropertyCount, ["properties"] = JObject.FromObject(pair.Value.Properties)
        }))),
        ["enums"] = JObject.FromObject(mapping.Enums)
    };
    Check(actualMapping.Types.Count > 0 && MapBundleDocument.Hash(MapBundleDocument.Bytes(MappingFacts(actualMapping))) ==
        MapBundleDocument.Hash(MapBundleDocument.Bytes(MappingFacts(legacyMapping))), "actual usmap mappings and filename-scoped fixes match the existing provider");
}
Console.WriteLine("PASS " + checks + " focused map export checks");

