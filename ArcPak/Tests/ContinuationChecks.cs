using ArcPak;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.GameTypes.Theia.FileProvider;
using CUE4Parse.MappingsProvider;
using CUE4Parse.UE4;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.FastGeoStreaming;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using System.Runtime.CompilerServices;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Objects.Properties;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using FModel.Exporters.MapPlacement;
using Newtonsoft.Json.Linq;

internal static class ContinuationChecks
{
    internal static int Run(string evidence)
    {
        var count = 0;
        void Check(bool condition, string name)
        {
            if (!condition) throw new InvalidOperationException("FAIL " + name);
            count++;
            Console.WriteLine("PASS " + name);
        }
        var catalog = MapCatalogExport.Document(["PioneerGame/Content/Maps/Test.umap", "PioneerGame/Content/Props/Mesh.uasset",
            "/Game/Maps/Test.umap", "PioneerGame/Content/Scenes/Preview.umap"]);
        Check(catalog.Value<int>("schemaVersion") == 1 && catalog["maps"]!.Count() == 2, "catalog excludes mesh browser assets and deduplicates true umap identities");
        Check(catalog["maps"]!.All(entry => entry.Value<string>("package")!.StartsWith("/Game/") &&
            entry.Value<string>("kind") == "map" && !string.IsNullOrEmpty(entry.Value<string>("name"))), "catalog schema and true map names");
        var namedMesh = new UStaticMesh { Name = "Mesh" };
        Check(ReferenceEquals(MapBundleExport.SelectAsset([new UStaticMesh { Name = "Other" }, namedMesh], "/Game/Props/Mesh"), namedMesh),
            "exact dependency package leaf selects the intended export instead of the first export");
        var rejectedAmbiguous = false;
        try { MapBundleExport.SelectAsset([new UStaticMesh { Name = "A" }, new UStaticMesh { Name = "B" }], "/Game/Maps/World"); }
        catch (InvalidDataException) { rejectedAmbiguous = true; }
        Check(rejectedAmbiguous, "ambiguous package dependencies are refused rather than assigned unrelated geometry or materials");

        using var provider = new FixtureProvider(evidence);
        var child = new FixturePackage("/Game/Maps/Child", provider);
        var childWorld = child.Add(new UWorld { Name = "Child" });
        var childLevel = child.Add(new ULevel { Name = "PersistentLevel" }, childWorld);
        var actor = child.Add(new UObject { Name = "UnsupportedGameplay" }, childLevel);
        var root = child.Add(new USceneComponent { Name = "Root" }, actor);
        var component = child.Add(new USceneComponent { Name = "ChildComponent" }, actor);
        Set(root, "RelativeLocation", new FVector(10, 20, 30));
        Set(component, "RelativeLocation", new FVector(5, 0, 0));
        component.AttachParent = child.Index(root);
        Set(actor, "RootComponent", child.Index(root));
        childLevel.Actors = [child.Index(actor)];
        Set(childWorld, "PersistentLevel", child.Index(childLevel));
        provider.Register(child);
        var parent = new FixturePackage("/Game/Maps/Parent", provider);
        var parentWorld = parent.Add(new UWorld { Name = "Parent" });
        var parentLevel = parent.Add(new ULevel { Name = "PersistentLevel" }, parentWorld);
        Set(parentWorld, "PersistentLevel", parent.Index(parentLevel));
        var instanceA = parent.Add(new UObject { Name = "InstanceA" }, parentLevel);
        var instanceB = parent.Add(new UObject { Name = "InstanceB" }, parentLevel);
        foreach (var instance in new[] { instanceA, instanceB })
        {
            var instanceRoot = parent.Add(new USceneComponent { Name = "Root" }, instance);
            Set(instanceRoot, "RelativeLocation", new FVector(instance == instanceA ? 1000 : 5000, 2000, 3000));
            Set(instanceRoot, "RelativeRotation", new FRotator(0, 90, 0));
            Set(instanceRoot, "RelativeScale3D", new FVector(2, 2, 2));
            Set(instance, "RootComponent", parent.Index(instanceRoot));
            instance.Properties.Add(new FPropertyTag(new FName("SoftObjectProperty"),
                new SoftObjectProperty(new FSoftObjectPath(new FName("/Game/Maps/Child.Child"), ""))) { Name = new FName("WorldAsset") });
        }
        parentLevel.Actors = [parent.Index(instanceA), parent.Index(instanceB)];
        provider.Register(parent);
        var seen = new List<MapTraversalObject>();
        var result = new MapPlacementExtractor(provider, options: new MapPlacementExtractorOptions
            { CellWorkers = 1, TraversalCallback = seen.Add }).Extract(parentWorld);
        if (result.TotalFailures > 0) Console.WriteLine("Fixture failures: " + result.TotalFailures + ": " + string.Join("; ", result.Diagnostics));
        Check(result.TotalFailures == 0, "actual shared walker traverses synthetic instanced worlds without failure");
        var replayed = seen.Where(item => item.Source == component).ToArray();
        Check(replayed.Length == 2 && replayed[0].Id != replayed[1].Id, "actual callback replays all components with distinct world-instance IDs");
        var first = replayed.Single(item => item.WorldInstancePath.EndsWith("InstanceA"));
        var matrix = MapBundleDocument.Matrix(first.TransformCm!.Value).Values<float>().ToArray();
        Check(Math.Abs(matrix[3] - 960) < .01 && Math.Abs(matrix[7] - 2030) < .01 && Math.Abs(matrix[11] - 3060) < .01,
            "actual callback composes attachment and world-instance transforms");
        Check(first.ParentId == seen.Single(item => item.Source == root && item.WorldInstancePath == first.WorldInstancePath).Id,
            "callback preserves parent identity within each world instance");
        var before = MapBundleDocument.Actor(first, new JObject { ["value"] = 1 });
        var changed = MapBundleDocument.Actor(first with { TransformCm = new FTransform(new FVector(99, 99, 99)) }, new JObject { ["value"] = 2 });
        Check(before.Value<string>("id") == changed.Value<string>("id"), "actor and component IDs ignore mutable properties and transforms");
        Check(before.Value<string>("id") != FModel.Views.Snooper.MapPlacement.MapPlacementRecord.StableId(first.SourcePath, null, first.WorldInstancePath),
            "object and geometry IDs occupy separate namespaces");
        var baseline = new MapPlacementExtractor(provider, options: new MapPlacementExtractorOptions { CellWorkers = 1 }).Extract(parentWorld);
        Check(result.Records.Count == baseline.Records.Count && result.GameplayRecords.Count == baseline.GameplayRecords.Count,
            "absent callback preserves legacy mesh and gameplay result behavior");
        var raw = MapBundleExport.Raw(actor, new SortedSet<string>());
        Check(raw["properties"]!.Any(property => property.Value<string>("name") == "RootComponent"), "callback source preserves actual typed references");
        var missingReference = new FPackageIndex((IPackage)null!, -77);
        var referenceTag = new MapProperty(new UScriptMap(new Dictionary<FPropertyTagType, FPropertyTagType?>
            { [new StrProperty("missing")] = new ObjectProperty(missingReference) }));
        var preservedReference = MapRawReferences.Collect(referenceTag).Single();
        Check(preservedReference.Value<int>("index") == -77 && preservedReference.Value<bool>("unresolved"),
            "nested unresolved references retain raw package indices instead of becoming null");
        var standin = MapBundleDocument.Actor(first with { TransformCm = null, TransformIssue = "unknown" }, new JObject());
        Check(!standin["properties"]!.Value<bool>("transformKnown") && standin["matrix"]!.Count() == 16,
            "unknown transforms remain explicitly marked schema-valid standins");
        var bundle = MapBundleExport.Empty("/Game/Maps/Parent");
        bundle["actors"] = new JArray(seen.Select(item => MapBundleDocument.Actor(item, MapBundleExport.Raw(item.Source, new SortedSet<string>()))));
        MapBundleDocument.WriteAtomic(evidence, "callback-bundle.json", MapBundleDocument.Bytes(bundle));

        var fastMesh = child.Add(new UStaticMesh { Name = "/Game/Props/FastMesh.FastMesh" });
        var fastComponent = (FFastGeoInstancedStaticMeshComponent)RuntimeHelpers.GetUninitializedObject(typeof(FFastGeoInstancedStaticMeshComponent));
        typeof(FFastGeoComponent).GetField("ComponentIndex")!.SetValue(fastComponent, 7);
        typeof(FFastGeoComponent).GetField("WorldTransform")!.SetValue(fastComponent, new FTransform(new FVector(10, 20, 30)));
        var staticDesc = (FStaticMeshSceneProxyDesc)RuntimeHelpers.GetUninitializedObject(typeof(FStaticMeshSceneProxyDesc));
        staticDesc.StaticMesh = child.Index(fastMesh);
        fastComponent.SceneProxyDesc = new FSceneProxyDesc { StaticMeshSceneProxyDesc = staticDesc };
        fastComponent.OverrideMaterials = [];
        fastComponent.PerInstanceSMData = [new(new FTransform(new FVector(5, 0, 0))), new(new FTransform(new FVector(15, 0, 0)))];
        var cluster = (FFastGeoComponentCluster)RuntimeHelpers.GetUninitializedObject(typeof(FFastGeoComponentCluster));
        cluster.Name = "Cluster";
        cluster.ComponentClusterIndex = 3;
        cluster.InstancedStaticMeshComponents = [fastComponent];
        var container = new UFastGeoContainer { Name = "FastGeo", ComponentClusters = [cluster], HLODs = [], Assets = [child.Index(fastMesh)] };
        var worldTransform = new FTransform(new FRotator(0, 90, 0), new FVector(1000, 2000, 3000), new FVector(2, 2, 2));
        var fastObject = new MapTraversalObject(container, "/Game/Maps/Child.Child:FastGeo", "FastGeo", "", "Child", FTransform.Identity)
            .Compose(worldTransform, "InstanceA");
        var fastPlacements = new JArray();
        var fastResources = new JObject();
        var fastDiagnostics = new SortedSet<string>();
        MapResourceExport.FastGeo([fastObject], fastPlacements, fastResources, fastDiagnostics);
        Check(fastPlacements.Count == 2 && fastDiagnostics.Count == 0, "actual FastGeo exporter expands binary static-mesh instances");
        var fastMatrix = fastPlacements[0]["matrix"]!.Values<float>().ToArray();
        Check(Math.Abs(fastMatrix[3] - 960) < .01 && Math.Abs(fastMatrix[7] - 2030) < .01 && Math.Abs(fastMatrix[11] - 3060) < .01,
            "FastGeo composes binary world and per-instance transforms exactly once");
        Check(fastPlacements[0].Value<string>("id") != fastPlacements[1].Value<string>("id") && fastResources["fastGeo"]!.Count() == 1,
            "FastGeo preserves source data and stable per-instance identities");

        childLevel.Actors = null!;
        var partial = new MapPlacementExtractor(provider, options: new MapPlacementExtractorOptions { TraversalCallback = _ => { } }).Extract(childWorld);
        Check(partial.TotalFailures > 0 && partial.Diagnostics.Any(message => message.Contains("actor array", StringComparison.Ordinal)),
            "partially decoded level cannot claim traversal success");
        Check(new MapPlacementExtractor(provider).Extract(childWorld).TotalFailures == 0, "partial-level guard does not alter callback-absent FModel behavior");

        var cacheRoot = Path.Combine(evidence, "early-cache");
        var stage = Path.Combine(evidence, "early-cache-stage");
        MapBundleDocument.WriteAtomic(stage, "Mesh_LOD0.uemodel", [1, 2, 3]);
        var asset = MapBundleDocument.PublishAsset(stage, cacheRoot, "/Game/Props/Mesh", "revision", []);
        var dependencies = new JObject { ["source.uasset"] = "source-hash" };
        MapDependencyCache.Save(cacheRoot, "/Game/Props/Mesh", "revision", dependencies, asset);
        var reads = 0;
        string? Source(string path) { reads++; return path == "source.uasset" ? "source-hash" : null; }
        Check(MapDependencyCache.TryReuse(cacheRoot, "/Game/Props/Mesh", "revision", Source, out var reused) && reads == 1 &&
            JToken.DeepEquals(asset, reused), "valid early cache reuses verified files before conversion");
        const string sourcePath = "PioneerGame/Content/Props/Mesh.uasset";
        provider.Files.AddFiles(new Dictionary<string, GameFile>(provider.PathComparer)
            { [sourcePath] = new StreamedGameFile(sourcePath, new MemoryStream([10, 20]), provider.Versions) });
        MapDependencyCache.Save(cacheRoot, "/Game/Props/Mesh", "revision", new JObject { [sourcePath] = MapBundleDocument.Hash([10, 20]) }, asset);
        var packageLoads = provider.PackageLoads;
        var early = MapBundleExport.ExportAsset(provider, "/Game/Props/Mesh", cacheRoot, "unused", "unused", "revision", new SortedSet<string>());
        Check(provider.PackageLoads == packageLoads && JToken.DeepEquals(early, asset), "actual asset exporter returns valid receipt before package parsing or staging");
        MapDependencyCache.Save(cacheRoot, "/Game/Props/Mesh", "revision", dependencies, asset);
        Check(!MapDependencyCache.TryReuse(cacheRoot, "/Game/Props/Mesh", "revision", _ => "changed", out _), "changed dependency invalidates early cache");
        Check(!MapDependencyCache.TryReuse(cacheRoot, "/Game/Props/Mesh", "revision", _ => null, out _), "missing dependency invalidates early cache");
        reads = 0;
        Check(!MapDependencyCache.TryReuse(cacheRoot, "/Game/Props/Mesh", "changed-game-mapping-exporter", Source, out _) && reads == 0,
            "game mapping or exporter receipt mismatch rejects reuse before source reads");
        var receiptPath = MapBundleDocument.Inside(cacheRoot, "dependency-receipts/" +
            MapBundleDocument.Hash(System.Text.Encoding.UTF8.GetBytes("/Game/Props/Mesh")) + ".json");
        var tampered = JObject.Parse(File.ReadAllText(receiptPath));
        tampered["asset"]!["mesh"] = "../escape.uemodel";
        File.WriteAllBytes(receiptPath, MapBundleDocument.Bytes(tampered));
        Check(!MapDependencyCache.TryReuse(cacheRoot, "/Game/Props/Mesh", "revision", Source, out _), "tampered early receipt cannot redirect cached output paths");
        MapDependencyCache.Save(cacheRoot, "/Game/Props/Mesh", "revision", dependencies, asset);
        File.WriteAllBytes(MapBundleDocument.Inside(cacheRoot, asset.Value<string>("mesh")!), [9, 9, 9]);
        Check(!MapDependencyCache.TryReuse(cacheRoot, "/Game/Props/Mesh", "revision", Source, out _), "corrupted published mesh invalidates early cache");
        return count;
    }

    private static void Set(UObject obj, string name, FPackageIndex value) => obj.Properties.Add(
        new FPropertyTag(new FName("ObjectProperty"), new ObjectProperty(value)) { Name = new FName(name) });
    private static void Set(UObject obj, string name, IUStruct value) => obj.Properties.Add(
        new FPropertyTag(new FName("StructProperty"), new StructProperty(new FScriptStruct(value))) { Name = new FName(name) });

    private sealed class FixtureProvider(string directory) : TheiaFileProvider(directory, SearchOption.TopDirectoryOnly,
        new VersionContainer(EGame.GAME_ArcRaiders))
    {
        private readonly Dictionary<string, IPackage> _packages = new(StringComparer.OrdinalIgnoreCase);
        internal int PackageLoads { get; private set; }
        internal void Register(FixturePackage package)
        {
            var path = "PioneerGame/Content" + package.Name["/Game".Length..] + ".umap";
            _packages[path] = package;
            Files.AddFiles(new Dictionary<string, GameFile>(PathComparer) { [path] = new StreamedGameFile(path, Stream.Null, Versions) });
        }
        public override IPackage LoadPackage(GameFile file) { PackageLoads++; return _packages[file.Path]; }
    }

    private sealed class FixturePackage(string name, IFileProvider provider) : IPackage
    {
        private readonly List<UObject> _objects = [];
        public string Name { get; set; } = name;
        public IFileProvider? Provider => provider;
        public TypeMappings? Mappings => null;
        public FPackageFileSummary Summary => new();
        public FNameEntrySerialized[] NameMap => [];
        public int ImportMapLength => 0;
        public int ExportMapLength => _objects.Count;
        public Lazy<UObject>[] ExportsLazy => _objects.Select(obj => new Lazy<UObject>(() => obj)).ToArray();
        public bool IsFullyLoaded => true;
        public bool CanDeserialize => true;
        public bool HasFlags(EPackageFlags flags) => false;
        public int GetExportIndex(string name, StringComparison comparisonType = StringComparison.Ordinal) =>
            _objects.FindIndex(obj => obj.Name.Equals(name, comparisonType));
        public ResolvedObject? ResolvePackageIndex(FPackageIndex? index) => index is { Index: > 0 } && index.Index <= _objects.Count
            ? new ResolvedLoadedObject(_objects[index.Index - 1]) : null;
        internal T Add<T>(T obj, UObject? outer = null) where T : UObject
        {
            obj.Outer = outer == null ? new ResolvedPackageObject(this) : new ResolvedLoadedObject(outer);
            _objects.Add(obj);
            return obj;
        }
        internal FPackageIndex Index(UObject obj) => new(this, _objects.IndexOf(obj) + 1);
    }
}
