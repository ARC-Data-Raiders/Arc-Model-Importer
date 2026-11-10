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
using FModel.Views.Snooper.MapPlacement;
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
        var materialPackage = new FixturePackage("/Game/Maps/Materials", provider);
        var materialA = materialPackage.Add(new CUE4Parse.UE4.Assets.Exports.Material.UMaterialInstanceConstant { Name = "/Game/Maps/Materials.DynamicA" });
        var materialB = materialPackage.Add(new CUE4Parse.UE4.Assets.Exports.Material.UMaterialInstanceConstant { Name = "/Game/Maps/Materials.DynamicB" });
        Check(ReferenceEquals(MapBundleExport.SelectAsset([materialA, materialB], materialB.GetPathName()), materialB),
            "exact material object selects the requested export in a multi-export package");
        Check(MapBundleDocument.CanonicalObject("MaterialInstanceConstant'PioneerGame/Content/Maps/World.World:PersistentLevel.Actor.Dynamic'") ==
            "/Game/Maps/World.World:PersistentLevel.Actor.Dynamic", "exact material canonicalization preserves nested object identity");
        Check(MapBundleDocument.CanonicalObject("/Game/Materials/MI_Test.MI_Test") == "/Game/Materials/MI_Test",
            "standalone matching material object retains its legacy package key");
        Check(MaterialMetadataExport.Source(materialA).Value<string>("ObjectPath") == materialA.GetPathName() &&
            MaterialMetadataExport.Source(materialB).Value<string>("ObjectPath") == materialB.GetPathName(),
            "authoritative raw material Exports retain exact per-export ObjectPath");
        Check(MaterialMetadataExport.SelectIndex(["DynamicA", "DynamicB"], [materialA.GetPathName(), materialB.GetPathName()], materialB.GetPathName()) == 1,
            "metadata-only exact selection shares the bundle object identity contract");
        var discoveryPaths = new[] { "PioneerGame/Content/Maps/Test/_Generated_/A.umap",
            "PioneerGame/Content/Maps/Test/_Generated_/B.umap", "PioneerGame/Content/__ExternalActors__/Maps/Test/token.uasset",
            "PioneerGame/Content/Maps/Other/_Generated_/C.umap", "PioneerGame/Content/Maps/Test/_Generated_/A.umap",
            "PioneerGame/Content/__ExternalActors__/Maps/Test/token.uasset" };
        var discovery = MapDiscovery.Document("/Game/Maps/Test", discoveryPaths,
            ["/Game/Maps/Test/_Generated_/A.A"], []);
        File.WriteAllText(Path.Combine(evidence, "source-census.json"), discovery.ToString(Newtonsoft.Json.Formatting.Indented));
        Check(discovery["unvisitedGeneratedWorlds"]!.Count() == 1 && discovery["uncapturedExternalPackages"]!.Count() == 1 &&
            !discovery.Value<bool>("independentlyComplete"), "mounted world census reports unvisited cells and external packages without certifying completeness");
        Check(discovery["sourcePackages"] is JArray, "discovery emits per-package source coverage records");
        var sourcePackages = (JArray)discovery["sourcePackages"]!;
        JObject? FindSource(string package, string kind) => sourcePackages.OfType<JObject>().SingleOrDefault(record =>
            record.Value<string>("package") == package && record.Value<string>("kind") == kind);
        var generatedA = FindSource("/Game/Maps/Test/_Generated_/A", "generated-world");
        var generatedB = FindSource("/Game/Maps/Test/_Generated_/B", "generated-world");
        var externalToken = FindSource("/Game/__ExternalActors__/Maps/Test/token", "external-package");
        Check(sourcePackages.Count == 3 && generatedA?.Value<string>("disposition") == "visited" &&
            generatedB?.Value<string>("disposition") == "unvisited" && externalToken?.Value<string>("disposition") == "uncaptured",
            "per-package records preserve exact identities and visited/captured dispositions");
        Check(!sourcePackages.OfType<JObject>().Any(record => record.Value<string>("package")!.Contains("/Other/", StringComparison.Ordinal)),
            "per-package census excludes unrelated map packages");
        var orderedKeys = sourcePackages.OfType<JObject>().Select(record => record.Value<string>("package")!).ToArray();
        Check(orderedKeys.SequenceEqual(orderedKeys.Order(StringComparer.Ordinal)), "per-package records use deterministic package ordering");
        var reorderedDiscovery = MapDiscovery.Document("/Game/Maps/Test", discoveryPaths.Reverse(),
            ["/Game/Maps/Test/_Generated_/A.A"], []);
        Check(JToken.DeepEquals(discovery["sourcePackages"], reorderedDiscovery["sourcePackages"]),
            "duplicate paths and source enumeration order do not change per-package records");
        var capturedDiscovery = MapDiscovery.Document("/Game/Maps/Test", discoveryPaths,
            ["/Game/Maps/Test/_Generated_/B.B", "/Game/Maps/Test/_Generated_/A.A"],
            ["/Game/__ExternalActors__/Maps/Test/token"]);
        var capturedPackages = (JArray)capturedDiscovery["sourcePackages"]!;
        Check(capturedPackages.OfType<JObject>().All(record => record.Value<string>("disposition") is "visited" or "captured") &&
            capturedDiscovery["unvisitedGeneratedWorlds"]!.Count() == 0 && capturedDiscovery["uncapturedExternalPackages"]!.Count() == 0,
            "per-package records agree with empty aggregate miss lists when all candidates are captured");
        Check(!capturedDiscovery.Value<bool>("independentlyComplete"),
            "all-captured mounted candidates still do not claim independent world completeness");
        var reconstructedUnvisited = new JArray(sourcePackages.OfType<JObject>().Where(record => record.Value<string>("kind") == "generated-world" &&
            record.Value<string>("disposition") == "unvisited").Select(record => record.Value<string>("package")));
        var reconstructedUncaptured = new JArray(sourcePackages.OfType<JObject>().Where(record => record.Value<string>("kind") == "external-package" &&
            record.Value<string>("disposition") == "uncaptured").Select(record => record.Value<string>("package")));
        Check(JToken.DeepEquals(reconstructedUnvisited, discovery["unvisitedGeneratedWorlds"]) &&
            JToken.DeepEquals(reconstructedUncaptured, discovery["uncapturedExternalPackages"]),
            "per-package records reconstruct the exact existing aggregate miss lists");
        var exactStage = Path.Combine(evidence, "exact-stage");
        MapBundleDocument.WriteAtomic(exactStage, "Dynamic.json", System.Text.Encoding.UTF8.GetBytes("{}"));
        var exactA = MapBundleDocument.PublishAsset(exactStage, evidence, materialA.GetPathName(), "revision", [], materialA.GetPathName(), [], "Dynamic.json");
        var exactB = MapBundleDocument.PublishAsset(exactStage, evidence, materialB.GetPathName(), "revision", [], materialB.GetPathName(), [], "Dynamic.json");
        Check(exactA.Value<string>("fingerprint") != exactB.Value<string>("fingerprint") && exactA.Value<string>("material") != exactB.Value<string>("material"),
            "two materials in one package retain distinct publication identity even with identical JSON");
        MapDependencyCache.Save(evidence, materialA.GetPathName(), "revision", new JObject { ["source"] = "same" }, exactA);
        MapDependencyCache.Save(evidence, materialB.GetPathName(), "revision", new JObject { ["source"] = "same" }, exactB);
        Check(MapDependencyCache.TryReuse(evidence, materialA.GetPathName(), "revision", _ => "same", out var cachedA) &&
            MapDependencyCache.TryReuse(evidence, materialB.GetPathName(), "revision", _ => "same", out var cachedB) &&
            cachedA.Value<string>("objectPath") != cachedB.Value<string>("objectPath"), "exact material cache receipts cannot alias sibling exports");
        var child = new FixturePackage("/Game/Maps/Child", provider);
        var childWorld = child.Add(new UWorld { Name = "Child" });
        var childLevel = child.Add(new ULevel { Name = "PersistentLevel" }, childWorld);
        var actor = child.Add(new UObject { Name = "UnsupportedGameplay" }, childLevel);
        var root = child.Add(new USceneComponent { Name = "Root" }, actor);
        var component = child.Add(new USceneComponent { Name = "ChildComponent" }, actor);
        Set(root, "RelativeLocation", new FVector(10, 20, 30));
        Set(component, "RelativeLocation", new FVector(5, 0, 0));
        var inheritedTemplate = child.Add(new USceneComponent { Name = "Template" });
        Set(inheritedTemplate, "RelativeRotation", new FRotator(0, 90, 0));
        Set(inheritedTemplate, "RelativeScale3D", new FVector(2, 3, 4));
        var inherited = child.Add(new USceneComponent { Name = "Inherited", Template = new ResolvedLoadedObject(inheritedTemplate) }, actor);
        Set(inherited, "RelativeLocation", new FVector(5, 0, 0));
        var inheritedTransform = MapTransformResolver.CalculateAbsoluteTransformCm(inherited);
        Check(Math.Abs(inheritedTransform.Rotation.Rotator().Yaw - 90) < .01 && inheritedTransform.Scale3D.Y == 3,
            "independent inherited rotation and scale survive a local location override");
        component.AttachParent = child.Index(root);
        Set(component, "bAbsoluteLocation", true);
        Set(component, "bAbsoluteRotation", true);
        Set(component, "bAbsoluteScale", true);
        Set(root, "RelativeRotation", new FRotator(0, 90, 0));
        Set(root, "RelativeScale3D", new FVector(2, 2, 2));
        var absoluteChannels = MapTransformResolver.CalculateAbsoluteTransformCm(component);
        Check(absoluteChannels.Translation.X == 5 && absoluteChannels.Translation.Y == 0 &&
            Math.Abs(absoluteChannels.Rotation.Rotator().Yaw) < .01 && absoluteChannels.Scale3D.X == 1,
            "absolute attachment channels do not inherit parent translation rotation or scale");
        component.Properties.RemoveAll(property => property.Name.Text is "bAbsoluteRotation" or "bAbsoluteScale");
        var mixedChannels = MapTransformResolver.CalculateSourceTransformCm(component);
        Check(mixedChannels.Translation.X == 5 && mixedChannels.Translation.Y == 0 &&
            Math.Abs(mixedChannels.Rotation.Rotator().Yaw - 90) < .01 && mixedChannels.Scale3D.X == 2,
            "absolute location still inherits qualified parent rotation and scale independently");
        component.Properties.RemoveAll(property => property.Name.Text.StartsWith("bAbsolute", StringComparison.Ordinal));
        root.Properties.RemoveAll(property => property.Name.Text is "RelativeRotation" or "RelativeScale3D");
        var socketPackage = new FixturePackage("/Game/Props/SocketMesh", provider);
        var socketMesh = socketPackage.Add(new UStaticMesh { Name = "SocketMesh" });
        var socket = socketPackage.Add(new UStaticMeshSocket { Name = "Mount" }, socketMesh);
        typeof(UStaticMeshSocket).GetProperty("SocketName")!.SetValue(socket, new FName("Mount"));
        typeof(UStaticMeshSocket).GetProperty("RelativeLocation")!.SetValue(socket, new FVector(7, 0, 0));
        typeof(UStaticMeshSocket).GetProperty("RelativeScale")!.SetValue(socket, FVector.OneVector);
        typeof(UStaticMesh).GetProperty("Sockets")!.SetValue(socketMesh, new[] { socketPackage.Index(socket) });
        var socketParent = socketPackage.Add(new UStaticMeshComponent { Name = "SocketParent" });
        socketParent.SetStaticMesh(socketPackage.Index(socketMesh));
        Set(socketParent, "RelativeLocation", new FVector(10, 20, 30));
        var socketChild = socketPackage.Add(new USceneComponent { Name = "SocketChild", AttachParent = socketPackage.Index(socketParent) });
        Set(socketChild, "RelativeLocation", new FVector(5, 0, 0));
        socketChild.Properties.Add(new FPropertyTag(new FName("NameProperty"), new NameProperty(new FName("Mount"))) { Name = new FName("AttachSocketName") });
        Check(MapTransformResolver.CalculateSourceTransformCm(socketChild).Translation.X == 22,
            "qualified direct static-mesh socket offsets compose with parent and child transforms");
        var socketTemplate = socketPackage.Add(new USceneComponent { Name = "SocketTemplate" });
        socketTemplate.Properties.Add(new FPropertyTag(new FName("NameProperty"), new NameProperty(new FName("Mount"))) { Name = new FName("AttachSocketName") });
        socketChild.Template = new ResolvedLoadedObject(socketTemplate);
        socketChild.Properties.RemoveAll(property => property.Name.Text == "AttachSocketName");
        socketChild.Properties.Add(new FPropertyTag(new FName("NameProperty"), new NameProperty(new FName("None"))) { Name = new FName("AttachSocketName") });
        Check(MapTransformResolver.GetAttachSocketName(socketChild) == "" && MapTransformResolver.CalculateSourceTransformCm(socketChild).Translation.X == 15,
            "explicit None socket overrides an inherited socket rather than reapplying it");
        socketChild.Properties.RemoveAll(property => property.Name.Text == "AttachSocketName");
        socketChild.Properties.Add(new FPropertyTag(new FName("NameProperty"), new NameProperty(new FName("Missing"))) { Name = new FName("AttachSocketName") });
        var unresolvedSocket = false;
        try { MapTransformResolver.CalculateSourceTransformCm(socketChild); }
        catch (InvalidOperationException) { unresolvedSocket = true; }
        Check(unresolvedSocket, "unresolved sockets cannot produce qualified geometry transforms");
        Set(actor, "RootComponent", child.Index(root));
        childLevel.Actors = [child.Index(actor)];
        Set(childWorld, "PersistentLevel", child.Index(childLevel));
        provider.Register(child);
        var externalPackage = new FixturePackage("/Game/__ExternalActors__/Maps/Child/fixture", provider);
        var externalActor = externalPackage.Add(new CUE4Parse.UE4.Assets.Exports.Actor.AActor { Name = "ExternalActor" });
        provider.Register(externalPackage);
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
        Check(seen.Count(item => item.Source == externalActor) == 2, "mounted external actor packages are discovered and replayed in both world instances");
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
        fastComponent.BodyInstance = new FStructFallback([
            new FPropertyTag { Name = new FName("zKey"), Tag = new StrProperty("first duplicate") },
            new FPropertyTag { Name = new FName("aKey"), Tag = new StrProperty("alphabetically first") },
            new FPropertyTag { Name = new FName("zKey"), Tag = new StrProperty("legacy replacement") },
            new FPropertyTag { Name = new FName("mapKey"), Tag = new MapProperty(new UScriptMap(new Dictionary<FPropertyTagType, FPropertyTagType?>
            {
                [new StrProperty("zMapKey")] = new StrProperty("zMapValue"),
                [new StrProperty("aMapKey")] = new StrProperty("aMapValue")
            })) }
        ]);
        var cluster = (FFastGeoComponentCluster)RuntimeHelpers.GetUninitializedObject(typeof(FFastGeoComponentCluster));
        cluster.Name = "2026-11-03T21:09:08.1234567Z";
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

        var priorJsonSettings = Newtonsoft.Json.JsonConvert.DefaultSettings;
        var writerProbe = new FastGeoCaptureWriterProbe();
        try
        {
            Newtonsoft.Json.JsonConvert.DefaultSettings = () => new Newtonsoft.Json.JsonSerializerSettings { Converters = { writerProbe } };
            var oversizedCluster = (FFastGeoComponentCluster)RuntimeHelpers.GetUninitializedObject(typeof(FFastGeoComponentCluster));
            oversizedCluster.Name = new string('X', 100_000);
            var oversizedContainer = new UFastGeoContainer { Name = "OversizedFastGeo", ComponentClusters = [oversizedCluster], HLODs = [], Assets = [] };
            var oversizedObject = new MapTraversalObject(oversizedContainer, "/Game/Maps/Typed.Typed:OversizedFastGeo", "OversizedFastGeo", "", "Typed", FTransform.Identity);
            var oversizedSpoolDirectory = Path.Combine(evidence, "oversized-fastgeo-spool");
            var oversizedCapture = MapResourceExport.CaptureFastGeo([oversizedObject], oversizedSpoolDirectory, 65_536);
            var oversizedSpoolBytes = new FileInfo(Path.Combine(oversizedSpoolDirectory, "fastgeo.spool")).Length;
            Check(!writerProbe.SawTokenWriter && writerProbe.SawJsonTextWriter &&
                oversizedCapture.Diagnostics.Any(message => message.Contains("FastGeo JSON slice exceeds byte limit", StringComparison.Ordinal)) &&
                oversizedSpoolBytes <= 65_536,
                "oversized FastGeo record is rejected incrementally before a JSON token graph");
        }
        finally { Newtonsoft.Json.JsonConvert.DefaultSettings = priorJsonSettings; }
        Console.WriteLine("FASTGEO_STREAMING_TESTS_ACTIVE");
        container.Assets = Enumerable.Repeat(child.Index(fastMesh), 4000).ToArray();
        var streamDirectory = Path.Combine(evidence, "fastgeo-stream-spool");
        var fastCapture = MapResourceExport.CaptureFastGeo([fastObject], streamDirectory, 65_536);
        var streamBundle = MapBundleExport.Empty("/Game/Maps/Child");
        var ordinaryPlacement = new JObject { ["id"] = "ordinary-first", ["kind"] = "Ordinary" };
        streamBundle["placements"] = new JArray(ordinaryPlacement.DeepClone());
        streamBundle["requiredAssets"] = new JArray(fastCapture.Dependencies);
        streamBundle["diagnostics"] = new JArray(fastCapture.Diagnostics);
        streamBundle["coverage"]!["fastGeoPlacements"] = fastCapture.PlacementCount > 0;
        var streamedPath = Path.Combine(evidence, "fastgeo-streamed.json");
        var streamStats = FastGeoSnapshot.WriteBundleAtomic(evidence, "fastgeo-streamed.json", streamBundle, fastCapture);
        var legacyPlacements = new JArray(ordinaryPlacement.DeepClone());
        var legacyResources = new JObject();
        var legacyDiagnostics = new SortedSet<string>(StringComparer.Ordinal);
        FrozenLegacyFastGeoOracle.FastGeo([fastObject], legacyPlacements, legacyResources, legacyDiagnostics);
        var legacyBundle = MapBundleExport.Empty("/Game/Maps/Child");
        legacyBundle["placements"] = legacyPlacements;
        legacyBundle["resources"]!["fastGeo"] = legacyResources["fastGeo"]!.DeepClone();
        legacyBundle["requiredAssets"] = new JArray(fastCapture.Dependencies);
        legacyBundle["diagnostics"] = new JArray(legacyDiagnostics);
        legacyBundle["coverage"]!["fastGeoPlacements"] = fastCapture.PlacementCount > 0;
        var expectedBytes = FrozenLegacyFastGeoOracle.Bytes(legacyBundle);
        var baselineHash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(expectedBytes));
        Console.WriteLine("FROZEN_LEGACY_ORACLE_SHA256=" + baselineHash);
        var actualBytes = File.ReadAllBytes(streamedPath);
        Check(actualBytes.SequenceEqual(expectedBytes), "streamed bundle bytes match the legacy schema 1 oracle");
        using (var streamReader = new Newtonsoft.Json.JsonTextReader(new StringReader(System.Text.Encoding.UTF8.GetString(actualBytes)))
            { DateParseHandling = Newtonsoft.Json.DateParseHandling.None })
        {
            var streamDocument = JObject.Load(streamReader);
            Check(streamDocument["resources"]!["fastGeo"]![0]!["clusters"]![0]!["Name"]!.Type == JTokenType.String &&
                streamDocument["resources"]!["fastGeo"]![0]!["clusters"]![0]!["Name"]!.Value<string>() == cluster.Name,
                "ISO-looking FastGeo schema string remains a JSON string during streamed replay");
        }
        var typedReplayPayload = new JObject
        {
            ["isoString"] = "2032-04-05T06:07:08.1234567Z",
            ["dateTime"] = new DateTime(2032, 4, 5, 6, 7, 8, DateTimeKind.Utc).AddTicks(1234567),
            ["dateTimeOffset"] = new DateTimeOffset(2032, 4, 5, 6, 7, 8, TimeSpan.FromHours(-7)).AddTicks(7654321),
            ["preciseDecimal"] = 12345678901234567890.12345678m,
            ["binaryFloatExponent"] = 1.2345e+100,
            ["escapedString"] = "slash/quote\"line\nUnicode-雪-🙂"
        };
        JToken depth64 = "depth64-leaf";
        for (var level = 0; level < 63; level++) depth64 = new JObject { ["nested"] = depth64 };
        typedReplayPayload["depth64"] = depth64;
        var typedSliceBytes = System.Text.Encoding.UTF8.GetBytes(
            FrozenLegacyFastGeoOracle.Ordered(typedReplayPayload).ToString(Newtonsoft.Json.Formatting.None));
        var typedSpool = Path.Combine(evidence, "typed-fastgeo-slice.spool");
        File.WriteAllBytes(typedSpool, typedSliceBytes);
        var typedSlice = new FastGeoSpoolSlice(0, typedSliceBytes.Length,
            Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(typedSliceBytes)).ToLowerInvariant());
        var typedHeader = new JObject
        {
            ["id"] = "typed-row", ["sourcePath"] = "/Game/Maps/Typed.FastGeo",
            ["worldInstancePath"] = "TypedInstance", ["matrix"] = MapBundleDocument.Matrix(FTransform.Identity)
        };
        var typedArrays = new FastGeoArraySlices[]
        {
            new("clusters", [typedSlice]), new("hlods", []), new("assets", [])
        };
        var typedCapture = new FastGeoDetachedCapture(typedSpool, 65_536,
            [new FastGeoStreamRow(typedHeader, typedArrays, [])], [], [], 0, typedSliceBytes.Length, 0);
        var typedStreamBundle = MapBundleExport.Empty("/Game/Maps/Typed");
        var typedActualPath = Path.Combine(evidence, "fastgeo-typed-replay.json");
        FastGeoSnapshot.WriteBundleAtomic(evidence, "fastgeo-typed-replay.json", typedStreamBundle, typedCapture);
        var typedLegacyBundle = MapBundleExport.Empty("/Game/Maps/Typed");
        typedLegacyBundle["resources"]!["fastGeo"] = new JArray(new JObject
        {
            ["id"] = typedHeader["id"]!.DeepClone(), ["sourcePath"] = typedHeader["sourcePath"]!.DeepClone(),
            ["worldInstancePath"] = typedHeader["worldInstancePath"]!.DeepClone(), ["matrix"] = typedHeader["matrix"]!.DeepClone(),
            ["clusters"] = new JArray(typedReplayPayload.DeepClone()), ["hlods"] = new JArray(), ["assets"] = new JArray()
        });
        Check(File.ReadAllBytes(typedActualPath).SequenceEqual(FrozenLegacyFastGeoOracle.Bytes(typedLegacyBundle)),
            "typed replay slice matches frozen legacy bytes for ISO string, DateTime, DateTimeOffset, and high-precision decimal");
        var malformedBytes = System.Text.Encoding.UTF8.GetBytes("{\"broken\": [}");
        var malformedSpool = Path.Combine(evidence, "malformed-fastgeo-slice.spool");
        File.WriteAllBytes(malformedSpool, malformedBytes);
        var malformedSlice = new FastGeoSpoolSlice(0, malformedBytes.Length,
            Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(malformedBytes)).ToLowerInvariant());
        var malformedCapture = new FastGeoDetachedCapture(malformedSpool, 65_536,
            [new FastGeoStreamRow(typedHeader, [new FastGeoArraySlices("clusters", [malformedSlice])], [])], [], [], 0, malformedBytes.Length, 0);
        var malformedRejected = false;
        try { FastGeoSnapshot.WriteBundleAtomic(evidence, "fastgeo-malformed-replay.json", MapBundleExport.Empty("/Game/Maps/Typed"), malformedCapture); }
        catch (System.Text.Json.JsonException) { malformedRejected = true; }
        Check(malformedRejected, "malformed FastGeo spool JSON remains rejected during streamed replay");
        Check(fastCapture.AggregateSpoolBytes > 65_536 && fastCapture.PeakPayloadBytes <= 65_536 &&
            streamStats.PeakPayloadBytes <= 65_536, "producer and consumer stay within 65,536-byte slices while aggregate spool exceeds the cap");
        Check(fastCapture.Rows.All(row => row.Header.Property("clusters") == null && row.Header.Property("hlods") == null &&
            row.Header.Property("assets") == null), "capture retains slice metadata without FastGeo JObject or JArray collections");
        Check(streamBundle["placements"]![0]!.Value<string>("id") == "ordinary-first" &&
            actualBytes.SequenceEqual(expectedBytes) && fastCapture.Dependencies.SequenceEqual(fastCapture.Dependencies.Order(StringComparer.Ordinal)),
            "stream preserves ordinary-before-FastGeo placements, required asset order, and counts");
        var mutatedBundle = (JObject)streamBundle.DeepClone();
        ((JArray)mutatedBundle["diagnostics"]!).Add("mutation breaks parity");
        FastGeoSnapshot.WriteBundleAtomic(evidence, "fastgeo-mutated.json", mutatedBundle, fastCapture);
        Check(!File.ReadAllBytes(Path.Combine(evidence, "fastgeo-mutated.json")).SequenceEqual(expectedBytes),
            "mutation to a diagnostic makes the parity check fail");

        var brokenFast = (FFastGeoInstancedStaticMeshComponent)RuntimeHelpers.GetUninitializedObject(typeof(FFastGeoInstancedStaticMeshComponent));
        typeof(FFastGeoComponent).GetField("ComponentIndex")!.SetValue(brokenFast, 99);
        var partialCluster = (FFastGeoComponentCluster)RuntimeHelpers.GetUninitializedObject(typeof(FFastGeoComponentCluster));
        partialCluster.Name = "Partial";
        partialCluster.ComponentClusterIndex = 9;
        partialCluster.InstancedStaticMeshComponents = [fastComponent, brokenFast];
        var partialContainer = new UFastGeoContainer { Name = "PartialFastGeo", ComponentClusters = [partialCluster], HLODs = [], Assets = [] };
        var partialObject = new MapTraversalObject(partialContainer, "/Game/Maps/Child.Child:PartialFastGeo", "PartialFastGeo", "", "Child", FTransform.Identity);
        var partialCapture = MapResourceExport.CaptureFastGeo([partialObject], Path.Combine(evidence, "partial-fastgeo-spool"), 65_536);
        Check(partialCapture.PlacementCount == 2 && partialCapture.Diagnostics.Any(message =>
            message == "FastGeo expansion failed: /Game/Maps/Child.Child:PartialFastGeo: FastGeo mesh reference unresolved"),
            "failure after emitted placements preserves them and records the existing diagnostic");
        var unresolvedCluster = (FFastGeoComponentCluster)RuntimeHelpers.GetUninitializedObject(typeof(FFastGeoComponentCluster));
        unresolvedCluster.Name = "Unresolved";
        unresolvedCluster.ComponentClusterIndex = 10;
        unresolvedCluster.InstancedStaticMeshComponents = [brokenFast];
        var unresolvedContainer = new UFastGeoContainer { Name = "UnresolvedFastGeo", ComponentClusters = [unresolvedCluster], HLODs = [], Assets = [] };
        var unresolvedObject = new MapTraversalObject(unresolvedContainer, "/Game/Maps/Child.Child:UnresolvedFastGeo", "UnresolvedFastGeo", "", "Child", FTransform.Identity);
        var unresolvedCapture = MapResourceExport.CaptureFastGeo([unresolvedObject], Path.Combine(evidence, "unresolved-fastgeo-spool"), 65_536);
        Check(unresolvedCapture.PlacementCount == 0 && unresolvedCapture.Diagnostics.Any(message => message.Contains("FastGeo mesh reference unresolved", StringComparison.Ordinal)),
            "unresolved FastGeo mesh fails closed without emitting a placement");

        childLevel.Actors = null!;
        var partial = new MapPlacementExtractor(provider, options: new MapPlacementExtractorOptions { TraversalCallback = _ => { } }).Extract(childWorld);
        Check(partial.TotalFailures > 0 && partial.Diagnostics.Any(message => message.Contains("actor array", StringComparison.Ordinal)),
            "partially decoded level cannot claim traversal success");
        Check(new MapPlacementExtractor(provider).Extract(childWorld).TotalFailures == 0, "partial-level guard does not alter callback-absent FModel behavior");

        var cacheRoot = Path.Combine(evidence, "early-cache");
        var stage = Path.Combine(evidence, "early-cache-stage");
        MapBundleDocument.WriteAtomic(stage, "Mesh_LOD0.uemodel", [1, 2, 3]);
        var asset = MapBundleDocument.PublishAsset(stage, cacheRoot, "/Game/Props/Mesh", "revision", [], textureDependencies: new JArray());
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
    private static void Set(UObject obj, string name, bool value) => obj.Properties.Add(
        new FPropertyTag(new FName("BoolProperty"), new BoolProperty(value)) { Name = new FName(name) });

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
        public string? ArchiveName => null;
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

// Frozen from the authoritative C: legacy MapResourceExport.FastGeo and
// MapBundleDocument.Ordered/Bytes implementations at the hashes recorded in
// the task receipt. Keep this independent of staged MapResourceExport helpers.
internal static class FrozenLegacyFastGeoOracle
{
    internal static void FastGeo(IEnumerable<MapTraversalObject> objects, JArray placements, JObject resources, SortedSet<string> diagnostics)
    {
        var preserved = new JArray();
        resources["fastGeo"] = preserved;
        foreach (var item in objects.Where(item => item.Source is UFastGeoContainer))
        {
            var container = (UFastGeoContainer)item.Source;
            try
            {
                preserved.Add(new JObject { ["id"] = item.Id, ["sourcePath"] = item.SourcePath,
                    ["worldInstancePath"] = item.WorldInstancePath, ["matrix"] = MapBundleDocument.Matrix(item.WorldTransformCm),
                    ["clusters"] = JToken.FromObject(container.ComponentClusters ?? []),
                    ["hlods"] = JToken.FromObject(container.HLODs ?? []), ["assets"] = JToken.FromObject(container.Assets ?? []) });
                foreach (var cluster in container.ComponentClusters ?? [])
                {
                    foreach (var component in (cluster.StaticMeshComponents ?? []).Cast<FFastGeoStaticMeshComponentBase>()
                        .Concat(cluster.InstancedStaticMeshComponents ?? []))
                    {
                        var mesh = component.SceneProxyDesc?.StaticMeshSceneProxyDesc?.StaticMesh;
                        var meshPath = mesh?.ResolvedObject?.GetPathName();
                        if (string.IsNullOrEmpty(meshPath)) throw new InvalidDataException("FastGeo mesh reference unresolved");
                        var sourcePath = item.SourcePath + "/cluster:" + cluster.ComponentClusterIndex + "/component:" + component.ComponentIndex;
                        var overrides = (component.OverrideMaterials ?? []).Select(material =>
                            material == null || material.IsNull ? "" : material.ResolvedObject?.GetPathName() ?? "").ToArray();
                        void Add(FTransform transform, int? index)
                        {
                            var record = MapPlacementRecord.FromTransform(cluster.Name, meshPath, "FastGeoStaticMesh",
                                transform * component.WorldTransform * item.WorldTransformCm, item.WorldPath,
                                componentPath: sourcePath, instanceIndex: index, worldInstancePath: item.WorldInstancePath, materialOverrides: overrides);
                            placements.Add(MapBundleDocument.Placement(record, JObject.FromObject(component)));
                        }
                        if (component is FFastGeoInstancedStaticMeshComponent instanced)
                        {
                            if (instanced.PerInstanceSMData == null) throw new InvalidDataException("FastGeo instance data unavailable");
                            for (var index = 0; index < instanced.PerInstanceSMData.Length; index++)
                                Add(instanced.PerInstanceSMData[index].TransformData, index);
                        }
                        else Add(FTransform.Identity, null);
                    }
                    if ((cluster.SkinnedMeshComponents?.Length ?? 0) + (cluster.InstancedSkinnedMeshComponents?.Length ?? 0) +
                        (cluster.ProceduralISMComponents?.Length ?? 0) > 0)
                        diagnostics.Add("FastGeo skinned or procedural components remain raw: " + item.SourcePath + ":" + cluster.ComponentClusterIndex);
                }
                if ((container.HLODs?.Length ?? 0) > 0) diagnostics.Add("FastGeo HLOD data is retained without expansion: " + item.SourcePath);
            }
            catch (Exception exception) { diagnostics.Add("FastGeo expansion failed: " + item.SourcePath + ": " + exception.Message); }
        }
    }

    internal static JToken Ordered(JToken token) => token switch
    {
        JObject obj => new JObject(obj.Properties().OrderBy(property => property.Name, StringComparer.Ordinal)
            .Select(property => new JProperty(property.Name, Ordered(property.Value)))),
        JArray array => new JArray(array.Select(Ordered)),
        _ => token.DeepClone()
    };

    internal static byte[] Bytes(JToken token) => System.Text.Encoding.UTF8.GetBytes(Ordered(token).ToString(Newtonsoft.Json.Formatting.Indented) + "\n");
}



internal sealed class FastGeoCaptureWriterProbe : Newtonsoft.Json.JsonConverter<FFastGeoComponentCluster>
{
    internal bool SawTokenWriter { get; private set; }
    internal bool SawJsonTextWriter { get; private set; }

    public override void WriteJson(Newtonsoft.Json.JsonWriter writer, FFastGeoComponentCluster value, Newtonsoft.Json.JsonSerializer serializer)
    {
        SawTokenWriter |= writer is Newtonsoft.Json.Linq.JTokenWriter;
        SawJsonTextWriter |= writer is Newtonsoft.Json.JsonTextWriter;
        writer.WriteStartObject();
        writer.WritePropertyName(nameof(value.Name));
        writer.WriteValue(value.Name);
        writer.WriteEndObject();
    }

    public override FFastGeoComponentCluster ReadJson(Newtonsoft.Json.JsonReader reader, Type objectType,
        FFastGeoComponentCluster? existingValue, bool hasExistingValue, Newtonsoft.Json.JsonSerializer serializer) =>
        throw new NotSupportedException();
}
