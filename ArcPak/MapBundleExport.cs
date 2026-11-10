using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Options;
using FModel.Exporters.MapPlacement;
using Newtonsoft.Json.Linq;
using Serilog;
using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ArcPak;

internal static class MapBundleExport
{
    private static readonly object TraceGate = new();
    private static bool TraceEnabled => !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DATARAIDERS_MAP_EXPORT_TRACE"));

    private static void Trace(object diagnostic)
    {
        var path = Environment.GetEnvironmentVariable("DATARAIDERS_MAP_EXPORT_TRACE");
        if (string.IsNullOrWhiteSpace(path)) return;
        try
        {
            var line = JsonSerializer.Serialize(diagnostic) + Environment.NewLine;
            lock (TraceGate)
            {
                var fullPath = Path.GetFullPath(path);
                Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
                File.AppendAllText(fullPath, line, Encoding.UTF8);
            }
        }
        catch { }
    }

    private static string Hash(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));

    internal static ExportSession CreateAssetExportSession(ExportArtifactReuse? artifactReuse = null)
    {
        return artifactReuse == null
            ? new ExportSession { MaxDegreeOfParallelism = 2 }
            : ExportSession.CreateWithArtifactReuse(artifactReuse);
    }

    internal static int Run(string[] args)
    {
        var options = ArcVerbCommon.Parse(args, ["game-dir", "usmap", "path", "out", "cell-workers"], ["traversal-only"]);
        var gameDir = Path.GetFullPath(ArcVerbCommon.Required(options, "game-dir"));
        var usmap = Path.GetFullPath(ArcVerbCommon.Required(options, "usmap"));
        var output = Path.GetFullPath(ArcVerbCommon.Required(options, "out"));
        var mapId = MapBundleDocument.CanonicalPackage(ArcVerbCommon.Required(options, "path"));
        if (!Directory.Exists(gameDir)) throw new ArgumentException("Missing or absent --game-dir");
        if (!File.Exists(usmap)) throw new ArgumentException("Missing or absent --usmap");
        var workers = options.ContainsKey("cell-workers") ? int.Parse(ArcVerbCommon.Required(options, "cell-workers")) : 2;
        if (workers is < 1 or > 64) throw new ArgumentException("--cell-workers must be between 1 and 64");
        Directory.CreateDirectory(output);
        var diagnostics = new SortedSet<string>(StringComparer.Ordinal);
        var bundle = Empty(mapId);
        FastGeoDetachedCapture? fastGeo = null;
        try
        {
            bundle["sourceRevision"] = SourceRevision(gameDir, usmap);
            using var provider = Program.CreateShell(gameDir);
            provider.ReadShaderMaps = false;
            using (var mappingInput = File.OpenRead(usmap))
                if (mappingInput.ReadByte() != 0xc4 || mappingInput.ReadByte() != 0x30)
                    throw new InvalidDataException("Usmap has invalid magic");
            Program.PrepareCompression(gameDir);
            provider.MappingsContainer = new MapBundleMappingsProvider(usmap);
            Program.MountEverything(provider);
            diagnostics.Add("Container mounting uses the correctness fallback. Only exact referenced package assets are exported.");
            var entries = provider.Files.Values.Where(file => file.Path.EndsWith(".umap", StringComparison.OrdinalIgnoreCase) &&
                MapBundleDocument.CanonicalPackage(file.Path).Equals(mapId, StringComparison.OrdinalIgnoreCase))
                .DistinctBy(file => file.Path, StringComparer.OrdinalIgnoreCase)
                .OrderBy(file => file.Path, StringComparer.Ordinal).ToArray();
            if (entries.Length != 1) throw new InvalidDataException("Expected one exact map package, found " + entries.Length + ": " + mapId);
            mapId = MapBundleDocument.CanonicalPackage(entries[0].Path);
            bundle["mapId"] = mapId;
            var traversed = new List<MapTraversalObject>();
            var extraction = new MapPlacementExtractor(provider, message => Log.Information("map {Progress}", message),
                new MapPlacementExtractorOptions { CellWorkers = workers, TraversalCallback = traversed.Add }).ExtractFromPackage(entries[0]);
            Populate(bundle, extraction, traversed, provider, output, gameDir, usmap, diagnostics, options.ContainsKey("traversal-only"), ref fastGeo);
        }
        catch (Exception exception)
        {
            diagnostics.Add("Map export failed: " + exception.GetType().Name + ": " + exception.Message);
            bundle["complete"] = false;
        }
        bundle["diagnostics"] = new JArray(diagnostics);
        FastGeoSnapshot.WriteBundleAtomic(output, "map_bundle.json", bundle, fastGeo);
        if (fastGeo != null && File.Exists(fastGeo.SpoolPath)) File.Delete(fastGeo.SpoolPath);
        Log.Information("map bundle {Path}, complete={Complete}", Path.Combine(output, "map_bundle.json"), bundle.Value<bool>("complete"));
        return bundle.Value<bool>("complete") ? 0 : 2;
    }

    internal static JObject Empty(string mapId) => new()
    {
        ["schemaVersion"] = 1, ["mapId"] = mapId, ["mapName"] = mapId.Split('/').Last(),
        ["sourceRevision"] = "", ["complete"] = false, ["diagnostics"] = new JArray(),
        ["placements"] = new JArray(), ["assets"] = new JObject(), ["actors"] = new JArray(),
        ["resources"] = new JObject
        {
            ["landscapes"] = new JArray(), ["splines"] = new JArray(), ["gameplay"] = new JArray(),
            ["rawExports"] = new JArray(), ["worlds"] = new JArray()
        },
        ["matrixConvention"] = "row-major-column-vector-unreal-centimeters",
        ["producer"] = MapBundleDocument.ProducerVersion,
        ["coverage"] = new JObject
        {
            ["worldTraversal"] = false, ["rawProperties"] = false, ["allActorTransforms"] = false,
            ["fastGeoPlacements"] = false, ["landscapeGeometry"] = false, ["splineBake"] = false
        }
    };

    private static void Populate(JObject bundle, MapPlacementExtractionResult extraction, List<MapTraversalObject> traversed, AbstractVfsFileProvider provider,
        string output, string gameDir, string usmap, SortedSet<string> diagnostics, bool traversalOnly, ref FastGeoDetachedCapture? fastGeo)
    {
        using var artifactReuse = new ExportArtifactReuse(_ => bundle.Value<string>("sourceRevision")!);
        bundle["mapName"] = extraction.MapName;
        foreach (var diagnostic in extraction.Diagnostics) diagnostics.Add(diagnostic);
        var coverage = (JObject)bundle["coverage"]!;
        coverage["decodedWorldTraversal"] = !extraction.Cancelled && extraction.TotalFailures == 0 && extraction.VisitedWorlds.Count > 0;
        coverage["worldTraversal"] = false;
        coverage["classification"] = "partial-decoded-coverage";
        if (extraction.TotalFailures > 0) diagnostics.Add("Traversal failures: " + extraction.TotalFailures);
        coverage["allActorTransforms"] = coverage.Value<bool>("decodedWorldTraversal") && traversed.All(item => item.TransformCm != null);
        coverage["objectsCaptured"] = traversed.Count;
        coverage["unknownTransforms"] = traversed.Count(item => item.TransformCm == null);
        coverage["nonSpatialObjects"] = traversed.Count(item => item.TransformCm == null && item.TransformIssue == "Object has no spatial component");
        coverage["unresolvedSpatialTransforms"] = traversed.Count(item => item.TransformCm == null && item.TransformIssue != "Object has no spatial component");
        coverage["blockers"] = new JArray("World Partition and external actor discovery has not been independently proven complete",
            "FastGeo discovery, skinned/procedural components and HLOD expansion are not complete",
            "Terrain visibility holes and all streamed landscape proxies are not proven",
            "Unknown cooked payloads are decoded metadata rather than archival binary resources");
        diagnostics.Add("Coverage incomplete: unknown cooked payloads and unvisited world data cannot be certified from decoded traversal alone.");
        diagnostics.Add("Raw properties retain typed decoded tags and references, not an archival copy of unknown cooked binary payloads.");
        var resources = (JObject)bundle["resources"]!;
        resources["worlds"] = new JArray(extraction.VisitedWorlds.Order(StringComparer.Ordinal));
        resources["discovery"] = MapDiscovery.Document(bundle.Value<string>("mapId")!, provider.Files.Values.Select(file => file.Path),
            extraction.VisitedWorlds, traversed.Select(item => item.Source?.Owner?.Name).OfType<string>());
        var raw = CaptureWorlds(extraction, provider, diagnostics, out var rawComplete);
        coverage["rawProperties"] = rawComplete;
        resources["rawExports"] = new JArray(raw.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => pair.Value));
        var placements = (JArray)bundle["placements"]!;
        foreach (var record in extraction.Records.OrderBy(record => record.PlacementId, StringComparer.Ordinal))
        {
            try
            {
                if (!raw.TryGetValue(record.ComponentPath, out var metadata))
                {
                    metadata = new JObject();
                    diagnostics.Add("Missing raw placement component: " + record.ComponentPath);
                    coverage["rawProperties"] = false;
                }
                placements.Add(MapBundleDocument.Placement(record, metadata));
            }
            catch (Exception exception) { diagnostics.Add("Placement omitted: " + record.ComponentPath + ": " + exception.Message); }
        }
        var actors = (JArray)bundle["actors"]!;
        foreach (var item in traversed.OrderBy(item => item.Id, StringComparer.Ordinal))
        {
            try
            {
                var metadata = item.Source == null ? new JObject { ["unresolvedReference"] = item.SourcePath } :
                    raw.TryGetValue(item.SourcePath, out var captured) ? captured : Raw(item.Source, diagnostics);
                actors.Add(MapBundleDocument.Actor(item, metadata));
                if (item.TransformCm == null)
                    diagnostics.Add("No composed transform for " + item.SourcePath + ": " + item.TransformIssue);
            }
            catch (Exception exception)
            {
                coverage["rawProperties"] = false;
                diagnostics.Add("Traversal object metadata failed: " + item.SourcePath + ": " + exception.Message);
                actors.Add(MapBundleDocument.Actor(item, new JObject { ["error"] = exception.Message }));
            }
        }
        var orderedActors = new JArray(actors.OrderBy(actor => (string?)actor["id"], StringComparer.Ordinal));
        bundle["actors"] = orderedActors;
        resources["gameplay"] = orderedActors.DeepClone();
        var landscapes = (JArray)resources["landscapes"]!;
        foreach (var landscape in extraction.Landscapes.OrderBy(value => value.GetPathName(), StringComparer.Ordinal))
        {
            landscapes.Add(Raw(landscape, diagnostics));
            diagnostics.Add("Landscape geometry is not exported: " + landscape.GetPathName());
        }
        var splines = (JArray)resources["splines"]!;
        foreach (var job in extraction.SplineBakeJobs.OrderBy(job => job.SplineAssetKey, StringComparer.Ordinal))
        {
            try
            {
                splines.Add(new JObject
                {
                    ["id"] = job.MeshIdShort, ["asset"] = MapBundleDocument.CanonicalPackage(job.BaseAssetPath),
                    ["splineAssetKey"] = job.SplineAssetKey, ["componentPath"] = job.Component.GetPathName(),
                    ["baked"] = false, ["parameters"] = JToken.FromObject(job.Component.SplineParams),
                    ["forwardAxis"] = job.Component.ForwardAxis.ToString(), ["upDir"] = JToken.FromObject(job.Component.SplineUpDir),
                    ["boundaryMin"] = job.Component.SplineBoundaryMin, ["boundaryMax"] = job.Component.SplineBoundaryMax,
                    ["smoothInterpRollScale"] = job.Component.bSmoothInterpRollScale, ["properties"] = Raw(job.Component, diagnostics)
                });
                diagnostics.Add("Spline deformation is retained but not baked: " + job.Component.GetPathName());
            }
            catch (Exception exception) { diagnostics.Add("Spline metadata omitted: " + job.SplineAssetKey + ": " + exception.Message); }
        }
        var needed = new SortedSet<string>(StringComparer.Ordinal);
        var spoolDirectory = Path.Combine(output, ".staging");
        fastGeo = MapResourceExport.CaptureFastGeo(traversed, spoolDirectory, 65_536);
        foreach (var message in fastGeo.Diagnostics) diagnostics.Add(message);
        needed.UnionWith(fastGeo.Dependencies);
        foreach (var placement in placements.OfType<JObject>())
        {
            var asset = (string?)placement["asset"];
            if (!string.IsNullOrEmpty(asset)) needed.Add(asset);
            foreach (var material in placement["materialObjects"]!.Values<string>()) if (!string.IsNullOrEmpty(material)) needed.Add(material);
        }
        var assets = (JObject)bundle["assets"]!;
        bundle["requiredAssets"] = new JArray(needed);
        if (traversalOnly)
        {
            coverage["assetConversion"] = false;
            diagnostics.Add("Traversal-only mode: referenced mesh, material, terrain and spline conversions were not run");
            return;
        }
        var attempted = new SortedSet<string>(StringComparer.Ordinal);
        while (needed.Count > 0)
        {
            var package = needed.Min!;
            needed.Remove(package);
            if (!attempted.Add(package)) continue;
            try
            {
                var exported = ExportAsset(provider, package, output, gameDir, usmap, bundle.Value<string>("sourceRevision")!, diagnostics, artifactReuse);
                assets[package] = exported;
                var objectPath = exported.Value<string>("objectPath");
                var packageId = MapBundleDocument.CanonicalPackage(package);
                if (exported["material"] != null && objectPath == packageId + "." + packageId.Split('/').Last())
                    assets[packageId] = exported.DeepClone();
                foreach (var material in exported["materialObjects"]!.Values<string>())
                    if (!string.IsNullOrEmpty(material) && !assets.ContainsKey(material)) needed.Add(material);
            }
            catch (Exception exception) { diagnostics.Add("Asset export failed: " + package + ": " + exception.Message); }
        }
        MapResourceExport.Geometry(provider, extraction, traversed, bundle, output, diagnostics, gameDir, usmap, artifactReuse);
        attempted.UnionWith(bundle["requiredAssets"]!.Values<string>().OfType<string>());
        attempted.UnionWith(extraction.SplineBakeJobs.Select(job => job.SplineAssetKey));
        attempted.UnionWith(traversed.Where(item => item.Source is CUE4Parse.UE4.Assets.Exports.Actor.ALandscapeProxy)
            .Select(item => item.SourcePath + "#terrain"));
        attempted.UnionWith(placements.OfType<JObject>().Select(placement => placement.Value<string>("asset")).OfType<string>().Where(key => key.Length > 0));
        attempted.UnionWith(fastGeo.Dependencies);
        var terrainRequested = traversed.Where(item => item.Source is CUE4Parse.UE4.Assets.Exports.Actor.ALandscapeProxy).Select(item => item.SourcePath).Distinct().Count();
        var terrainExported = resources["landscapes"]!.OfType<JObject>().Where(item => item.Value<bool>("geometryExported")).Select(item => item.Value<string>("asset")).Distinct().Count();
        var splineExported = resources["splines"]!.OfType<JObject>().Count(item => item.Value<bool>("baked"));
        var fastGeoPlaced = fastGeo.PlacementCount;
        coverage["landscapeGeometry"] = terrainRequested > 0 && terrainRequested == terrainExported;
        coverage["splineBake"] = extraction.SplineBakeJobs.Count > 0 && splineExported == extraction.SplineBakeJobs.Count;
        coverage["fastGeoPlacements"] = fastGeoPlaced > 0 && !diagnostics.Any(message => message.StartsWith("FastGeo expansion failed:", StringComparison.Ordinal) ||
            message.StartsWith("FastGeo skinned or procedural", StringComparison.Ordinal) || message.StartsWith("FastGeo HLOD", StringComparison.Ordinal));
        coverage["resourceConversions"] = new JObject
        {
            ["terrainRequested"] = terrainRequested, ["terrainExported"] = terrainExported,
            ["splineRequested"] = extraction.SplineBakeJobs.Count, ["splineExported"] = splineExported,
            ["fastGeoStaticPlacements"] = fastGeoPlaced, ["landscapeVisibilityVerified"] = false
        };
        bundle["requiredAssets"] = new JArray(attempted);
        coverage["dependencyClosure"] = new JObject
        {
            ["basis"] = "requested-mesh-material-and-derived-geometry-assets-not-all-cooked-UObject-references",
            ["requested"] = attempted.Count, ["exported"] = attempted.Count(assets.ContainsKey),
            ["missing"] = new JArray(attempted.Where(package => !assets.ContainsKey(package)))
        };
        coverage["authoredTextureClosure"] = attempted.All(assets.ContainsKey) && !assets.Properties().Any(property => property.Value["textureDependencies"]?.OfType<JObject>().Any(dependency => dependency.Value<string>("status") != "exported") == true);
        coverage["dependencySourceReceipts"] = attempted.All(assets.ContainsKey) && !diagnostics.Any(message => message.StartsWith("Dependency source receipt unavailable:", StringComparison.Ordinal));
        coverage["derivedGeometryBindings"] = terrainRequested + extraction.SplineBakeJobs.Count > 0 && terrainRequested == terrainExported && extraction.SplineBakeJobs.Count == splineExported;
        coverage["materialGraphCompleteness"] = false;
        coverage["assetConversion"] = ((JArray)bundle["requiredAssets"]!).Values<string>().All(package => assets.ContainsKey(package!)) && coverage.Value<bool>("authoredTextureClosure");
        bundle["complete"] = false;
    }

    private static Dictionary<string, JObject> CaptureWorlds(MapPlacementExtractionResult extraction, AbstractVfsFileProvider provider,
        SortedSet<string> diagnostics, out bool complete)
    {
        var before = diagnostics.Count;
        var raw = new Dictionary<string, JObject>(StringComparer.Ordinal);
        foreach (var world in extraction.VisitedWorlds.Order(StringComparer.Ordinal))
        {
            try
            {
                var package = Program.FindPackage(provider, world) ?? throw new InvalidDataException("Visited world package unavailable");
                var exports = package.GetExports().ToArray();
                void Remember(UObject obj)
                {
                    var metadata = Raw(obj, diagnostics);
                    metadata["worldSource"] = MapBundleDocument.CanonicalPackage(world);
                    raw[obj.GetPathName()] = metadata;
                }
                foreach (var obj in exports) Remember(obj);
                foreach (var sourceWorld in exports.OfType<UWorld>())
                {
                    if (!sourceWorld.TryLoadPersistentLevel(out var level, out _) || level == null)
                    {
                        diagnostics.Add("Raw persistent level unavailable: " + world);
                        continue;
                    }
                    foreach (var reference in level.Actors ?? [])
                    {
                        try
                        {
                            if (reference == null || reference.IsNull) continue;
                            var actor = reference.Load() ?? throw new InvalidDataException("Actor reference could not be loaded");
                            Remember(actor);
                            foreach (var component in MapTransformResolver.EnumerateActorMeshComponents(actor))
                                Remember(component);
                            if (actor.Owner is { } actorPackage && !ReferenceEquals(actorPackage, package))
                                foreach (var actorExport in actorPackage.GetExports()) Remember(actorExport);
                        }
                        catch (Exception exception) { diagnostics.Add("Raw actor export failed: " + world + ": " + exception.Message); }
                    }
                }
            }
            catch (Exception exception) { diagnostics.Add("Raw world export failed: " + world + ": " + exception.Message); }
        }
        complete = diagnostics.Count == before;
        return raw;
    }

    internal static JObject Raw(UObject obj, SortedSet<string> diagnostics)
    {
        var tags = new JArray();
        foreach (var tag in obj.Properties)
        {
            var property = new JObject
            {
                ["name"] = tag.Name.Text, ["type"] = tag.PropertyType.Text, ["arrayIndex"] = tag.ArrayIndex
            };
            try
            {
                property["references"] = MapRawReferences.Collect(tag.Tag);
                property["tagData"] = tag.TagData == null ? JValue.CreateNull() : JToken.FromObject(tag.TagData);
                property["value"] = tag.Tag == null ? JValue.CreateNull() : JToken.FromObject(tag.Tag);
            }
            catch (Exception exception)
            {
                property["error"] = exception.GetType().Name + ": " + exception.Message;
                diagnostics.Add("Raw property omitted: " + obj.GetPathName() + "." + tag.Name.Text + ": " + exception.Message);
            }
            tags.Add(property);
        }
        var result = new JObject
        {
            ["path"] = obj.GetPathName(), ["package"] = MapBundleDocument.CanonicalPackage(obj.Owner?.Name ?? obj.GetPathName()),
            ["name"] = obj.Name, ["kind"] = obj.ExportType, ["properties"] = tags
        };
        try { result["decoded"] = JObject.FromObject(obj); }
        catch (Exception exception) { diagnostics.Add("Decoded export omitted: " + obj.GetPathName() + ": " + exception.Message); }
        return result;
    }

    internal static JObject ExportAsset(AbstractVfsFileProvider provider, string packagePath, string output,
        string gameDir, string usmap, string sourceRevision, SortedSet<string> diagnostics, ExportArtifactReuse? artifactReuse = null)
    {
        var tracingEnabled = TraceEnabled;
        var assetStopwatch = tracingEnabled ? Stopwatch.StartNew() : null;
        var cacheStopwatch = tracingEnabled ? Stopwatch.StartNew() : null;
        var cacheReused = MapDependencyCache.TryReuse(output, packagePath, sourceRevision,
            path => provider.Files.TryGetValue(path, out var file) ? MapBundleDocument.HashSource(file) : null, out var cached);
        cacheStopwatch?.Stop();
        var cacheUsable = cacheReused && cached["textureDependencies"] != null;
        if (tracingEnabled) Trace(new
        {
            kind = "map-dependency-cache", package = packagePath,
            revisionHash = Hash(sourceRevision), result = cacheUsable ? "hit" : cacheReused ? "incomplete" : "miss",
            elapsedMs = cacheStopwatch!.Elapsed.TotalMilliseconds
        });
        if (cacheUsable)
        {
            Log.Information("map cache hit {Package}", packagePath);
            assetStopwatch?.Stop();
            if (tracingEnabled) Trace(new { kind = "map-asset", package = packagePath, revisionHash = Hash(sourceRevision), result = "cache-hit", elapsedMs = assetStopwatch!.Elapsed.TotalMilliseconds });
            return cached;
        }
        var package = Program.FindPackage(provider, MapBundleDocument.CanonicalPackage(packagePath)) ?? throw new InvalidDataException("Referenced package could not be loaded");
        var exports = package.GetExports().ToArray();
        var asset = SelectAsset(exports, packagePath);
        var sourceRefId = RuntimeHelpers.GetHashCode(asset).ToString("X8");
        var sourceObjectPath = asset.GetPathName();
        var scratch = Path.Combine(Path.GetTempPath(), "arcpak-map-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(scratch);
        try
        {
            var session = CreateAssetExportSession(artifactReuse);
            var metadataFailures = Program.AttachTextureMetadataWriter(session, provider, scratch, GameBuild.Read(gameDir), usmap);
            var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            session.Add(asset);
            var references = asset switch
            {
                USkinnedAsset skinned => skinned.Materials,
                UStaticMesh mesh => mesh.Materials,
                _ => []
            };
            var materials = new SortedSet<string>(StringComparer.Ordinal);
            var materialObjects = new List<string>();
            foreach (var reference in references)
            {
                if (reference == null || reference.IsNull) { materialObjects.Add(""); continue; }
                var material = reference.Load<UMaterialInterface>() ?? throw new InvalidDataException("Mesh material reference could not be loaded");
                materials.Add(MapBundleDocument.CanonicalPackage(material.GetPathName()));
                materialObjects.Add(MapBundleDocument.CanonicalObject(material.GetPathName()));
            }
            var textureDependencies = new JArray();
            var closureStopwatch = tracingEnabled ? Stopwatch.StartNew() : null;
            if (asset is UMaterialInterface directMaterial)
                textureDependencies = MapMaterialClosure.Queue(directMaterial, session, visited, scratch, diagnostics);
            else
                foreach (var reference in references)
                    if (reference?.Load<UMaterialInterface>() is { } meshMaterial)
                        foreach (var dependency in MapMaterialClosure.Queue(meshMaterial, session, visited, scratch, diagnostics)) textureDependencies.Add(dependency);
            closureStopwatch?.Stop();
            if (tracingEnabled) Trace(new
            {
                kind = "map-material-closure", package = packagePath, objectPath = sourceObjectPath, sourceRefId,
                revisionHash = Hash(sourceRevision), dependencyCount = textureDependencies.Count,
                visitedCount = visited.Count, elapsedMs = closureStopwatch!.Elapsed.TotalMilliseconds
            });
            var exportOptions = CreateExportOptions();
            var sessionStopwatch = tracingEnabled ? Stopwatch.StartNew() : null;
            IReadOnlyList<CUE4Parse_Conversion.ExportResult> results;
            try { results = session.RunAsync(scratch, exportOptions).GetAwaiter().GetResult(); }
            finally
            {
                sessionStopwatch?.Stop();
                if (tracingEnabled) Trace(new
                {
                    kind = "map-asset-session", package = packagePath, objectPath = sourceObjectPath, sourceRefId,
                    revisionHash = Hash(sourceRevision), optionsHash = Hash(string.Join("\u001f",
                        (int)exportOptions.MeshFormat, (int)exportOptions.NaniteMeshFormat, (int)exportOptions.MeshQuality,
                        (int)exportOptions.TexturePlatform, (int)exportOptions.TextureFormat, exportOptions.TextureQuality,
                        exportOptions.ExportHdrTexturesAsHdr, (int)exportOptions.MaterialDepth, exportOptions.ExportMaterials,
                        exportOptions.ExportMorphTargets, (int)exportOptions.SocketFormat, (int)exportOptions.CompressionFormat,
                        exportOptions.ExportAllTextureMips, exportOptions.WorldUsdPreview, exportOptions.WorldUsdPreviewMaxCells,
                        exportOptions.PngCompressionLevel, exportOptions.TgaRleCompression)),
                    elapsedMs = sessionStopwatch!.Elapsed.TotalMilliseconds
                });
            }
            foreach (var result in results.Where(result => !result.Success)) diagnostics.Add("Dependency export failed: " + result.ObjectPath + ": " + result.Error?.Message);
            if (results.Count == 0 || results.Any(result => !result.Success) || metadataFailures() != 0)
                throw new InvalidDataException("Mesh/material dependency closure did not export successfully");
            foreach (var dependency in textureDependencies.OfType<JObject>().Where(dependency => dependency.Value<string>("status") == "queued"))
            {
                var identity = MapBundleDocument.CanonicalObject(dependency.Value<string>("objectPath")!);
                dependency["status"] = results.Any(result => result.Success && MapBundleDocument.CanonicalObject(result.ObjectPath) == identity) ? "exported" : "unresolved";
                if (dependency.Value<string>("status") == "unresolved")
                    diagnostics.Add("Authored texture has no successful exact conversion: " + dependency.Value<string>("objectPath"));
            }
            var objectPath = MapBundleDocument.ExactObjectPath(asset.GetPathName());
            string? materialFile = null;
            if (asset is UMaterialInterface)
            {
                materialFile = MaterialMetadataExport.OutputStem((UMaterialInterface)asset) + ".arc-material.json";
            }
            var published = MapBundleDocument.PublishAsset(scratch, output, packagePath, sourceRevision, materials.ToArray(),
                objectPath, materialObjects.ToArray(), materialFile, textureDependencies: textureDependencies);
            if (asset is USkinnedAsset or UStaticMesh && published["mesh"] == null)
                throw new InvalidDataException("Mesh export produced no UEModel or PSKX");
            var saveStopwatch = tracingEnabled ? Stopwatch.StartNew() : null;
            try
            {
                MapDependencyCache.Save(output, packagePath, sourceRevision, MapDependencyCache.Capture(provider, package), published);
                saveStopwatch?.Stop();
                if (tracingEnabled) Trace(new { kind = "map-dependency-cache", package = packagePath, revisionHash = Hash(sourceRevision), action = "save", result = "saved", elapsedMs = saveStopwatch!.Elapsed.TotalMilliseconds });
            }
            catch (Exception exception)
            {
                saveStopwatch?.Stop();
                if (tracingEnabled) Trace(new { kind = "map-dependency-cache", package = packagePath, revisionHash = Hash(sourceRevision), action = "save", result = "failed", elapsedMs = saveStopwatch!.Elapsed.TotalMilliseconds });
                diagnostics.Add("Dependency source receipt unavailable: " + packagePath + ": " + exception.Message);
                Log.Information("map dependency cache unavailable for {Package}: {Reason}", packagePath, exception.Message);
            }
            return published;
        }
        finally
        {
            assetStopwatch?.Stop();
            if (tracingEnabled) Trace(new { kind = "map-asset", package = packagePath, objectPath = sourceObjectPath, sourceRefId, revisionHash = Hash(sourceRevision), result = "completed", elapsedMs = assetStopwatch!.Elapsed.TotalMilliseconds });
            var resolved = Path.GetFullPath(scratch);
            var temporary = Path.TrimEndingDirectorySeparator(Path.GetFullPath(Path.GetTempPath())) + Path.DirectorySeparatorChar;
            if (resolved.StartsWith(temporary, StringComparison.OrdinalIgnoreCase) && Path.GetFileName(resolved).StartsWith("arcpak-map-", StringComparison.Ordinal))
                Directory.Delete(resolved, true);
        }
    }

    internal static UObject SelectAsset(IEnumerable<UObject> exports, string packagePath)
    {
        var candidates = exports.Where(obj => obj is USkinnedAsset or UStaticMesh or UMaterialInterface).ToArray();
        var canonical = MapBundleDocument.CanonicalObject(packagePath);
        var packageOnly = MapBundleDocument.CanonicalPackage(packagePath);
        var supplied = packagePath.Trim().TrimEnd('\'');
        if (supplied.Contains('.') && !supplied.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) && !supplied.EndsWith(".umap", StringComparison.OrdinalIgnoreCase))
        {
            var exact = candidates.Where(obj => MapBundleDocument.CanonicalObject(obj.GetPathName()).Equals(canonical, StringComparison.OrdinalIgnoreCase)).ToArray();
            if (exact.Length == 1) return exact[0];
            throw new InvalidDataException("Expected one exact export, found " + exact.Length + ": " + canonical);
        }
        var leaf = packageOnly.Split('/').Last();
        var named = candidates.Where(obj => obj.Name.Equals(leaf, StringComparison.OrdinalIgnoreCase)).ToArray();
        if (named.Length == 1) return named[0];
        if (candidates.Length == 1) return candidates[0];
        throw new InvalidDataException(candidates.Length == 0 ? "Referenced package is not an exportable mesh or material" :
            "Ambiguous dependency package requires an exact object identity: " + packagePath);
    }

    internal static ExportOptions CreateExportOptions() => new(meshFormat: EMeshFormat.UEFormat,
        meshQuality: EMeshQuality.All, naniteMeshFormat: ENaniteMeshFormat.NaniteFirst,
        materialDepth: EMaterialDepth.AllLayersNoRef, exportMaterials: true, exportMorphTargets: true);

    internal static string SourceRevision(string gameDir, string usmap)
    {
        var build = GameBuild.Read(gameDir);
        var sources = new JArray();
        foreach (var path in Directory.EnumerateFiles(gameDir, "*", SearchOption.TopDirectoryOnly)
            .Where(path => new[] { ".utoc", ".pak", ".ucas" }.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase))
            .OrderBy(path => Path.GetFileName(path), StringComparer.Ordinal))
        {
            var extension = Path.GetExtension(path);
            sources.Add(new JObject
            {
                ["name"] = Path.GetFileName(path), ["length"] = new FileInfo(path).Length,
                ["tocHash"] = extension.Equals(".utoc", StringComparison.OrdinalIgnoreCase) ? MapBundleDocument.HashFile(path) : ""
            });
        }
        var signature = new JObject
        {
            ["build"] = build.BuildId, ["depot"] = build.DepotManifest,
            ["mapping"] = MapBundleDocument.HashFile(usmap), ["mappingName"] = Path.GetFileName(usmap), ["containers"] = sources,
            ["producer"] = MapBundleDocument.HashFile(typeof(MapBundleExport).Assembly.Location),
            ["parser"] = MapBundleDocument.HashFile(typeof(UObject).Assembly.Location),
            ["converter"] = MapBundleDocument.HashFile(typeof(ExportSession).Assembly.Location)
        };
        return MapBundleDocument.Hash(MapBundleDocument.Bytes(signature));
    }
}

