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

namespace ArcPak;

internal static class MapBundleExport
{
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
                .OrderBy(file => file.Path, StringComparer.Ordinal).ToArray();
            if (entries.Length != 1) throw new InvalidDataException("Expected one exact map package, found " + entries.Length + ": " + mapId);
            mapId = MapBundleDocument.CanonicalPackage(entries[0].Path);
            bundle["mapId"] = mapId;
            var traversed = new List<MapTraversalObject>();
            var extraction = new MapPlacementExtractor(provider, message => Log.Information("map {Progress}", message),
                new MapPlacementExtractorOptions { CellWorkers = workers, TraversalCallback = traversed.Add }).ExtractFromPackage(entries[0]);
            Populate(bundle, extraction, traversed, provider, output, gameDir, usmap, diagnostics, options.ContainsKey("traversal-only"));
        }
        catch (Exception exception)
        {
            diagnostics.Add("Map export failed: " + exception.GetType().Name + ": " + exception.Message);
            bundle["complete"] = false;
        }
        bundle["diagnostics"] = new JArray(diagnostics);
        MapBundleDocument.WriteAtomic(output, "map_bundle.json", MapBundleDocument.Bytes(bundle));
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
        string output, string gameDir, string usmap, SortedSet<string> diagnostics, bool traversalOnly)
    {
        bundle["mapName"] = extraction.MapName;
        foreach (var diagnostic in extraction.Diagnostics) diagnostics.Add(diagnostic);
        var coverage = (JObject)bundle["coverage"]!;
        coverage["worldTraversal"] = !extraction.Cancelled && extraction.TotalFailures == 0 && extraction.VisitedWorlds.Count > 0;
        if (extraction.TotalFailures > 0) diagnostics.Add("Traversal failures: " + extraction.TotalFailures);
        coverage["allActorTransforms"] = coverage.Value<bool>("worldTraversal") && traversed.All(item => item.TransformCm != null);
        coverage["objectsCaptured"] = traversed.Count;
        coverage["unknownTransforms"] = traversed.Count(item => item.TransformCm == null);
        coverage["blockers"] = new JArray("World Partition and external actor discovery has not been independently proven complete",
            "FastGeo discovery, skinned/procedural components and HLOD expansion are not complete",
            "Terrain visibility holes and all streamed landscape proxies are not proven",
            "Unknown cooked payloads are decoded metadata rather than archival binary resources");
        diagnostics.Add("Coverage incomplete: unknown cooked payloads and unvisited world data cannot be certified from decoded traversal alone.");
        diagnostics.Add("Raw properties retain typed decoded tags and references, not an archival copy of unknown cooked binary payloads.");
        var resources = (JObject)bundle["resources"]!;
        resources["worlds"] = new JArray(extraction.VisitedWorlds.Order(StringComparer.Ordinal));
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
        MapResourceExport.FastGeo(traversed, placements, resources, diagnostics);
        foreach (var placement in placements.OfType<JObject>())
        {
            var asset = (string?)placement["asset"];
            if (!string.IsNullOrEmpty(asset)) needed.Add(asset);
            foreach (var material in placement["materials"]!.Values<string>()) if (!string.IsNullOrEmpty(material)) needed.Add(material);
        }
        var assets = (JObject)bundle["assets"]!;
        bundle["requiredAssets"] = new JArray(needed);
        if (traversalOnly)
        {
            coverage["assetConversion"] = false;
            diagnostics.Add("Traversal-only mode: referenced mesh, material, terrain and spline conversions were not run");
            return;
        }
        while (needed.Count > 0)
        {
            var package = needed.Min!;
            needed.Remove(package);
            try
            {
                var exported = ExportAsset(provider, package, output, gameDir, usmap, bundle.Value<string>("sourceRevision")!, diagnostics);
                assets[package] = exported;
                foreach (var material in exported["materials"]!.Values<string>())
                    if (!string.IsNullOrEmpty(material) && !assets.ContainsKey(material)) needed.Add(material);
            }
            catch (Exception exception) { diagnostics.Add("Asset export failed: " + package + ": " + exception.Message); }
        }
        MapResourceExport.Geometry(provider, extraction, traversed, bundle, output, diagnostics);
        coverage["assetConversion"] = ((JArray)bundle["requiredAssets"]!).Values<string>().All(package => assets.ContainsKey(package!));
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
        string gameDir, string usmap, string sourceRevision, SortedSet<string> diagnostics)
    {
        if (MapDependencyCache.TryReuse(output, packagePath, sourceRevision,
            path => provider.Files.TryGetValue(path, out var file) ? MapBundleDocument.Hash(file.Read()) : null, out var cached))
        {
            Log.Information("map cache hit {Package}", packagePath);
            return cached;
        }
        var package = Program.FindPackage(provider, packagePath) ?? throw new InvalidDataException("Referenced package could not be loaded");
        var exports = package.GetExports().ToArray();
        var asset = SelectAsset(exports, packagePath);
        var scratch = Path.Combine(Path.GetTempPath(), "arcpak-map-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(scratch);
        try
        {
            var session = new ExportSession { MaxDegreeOfParallelism = 2 };
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
            foreach (var reference in references)
            {
                if (reference == null || reference.IsNull) continue;
                var material = reference.Load<UMaterialInterface>() ?? throw new InvalidDataException("Mesh material reference could not be loaded");
                materials.Add(MapBundleDocument.CanonicalPackage(material.GetPathName()));
            }
            if (asset is UMaterialInterface directMaterial) Program.QueueMasterMaterialTextures(directMaterial, session, visited, scratch);
            else Program.QueueMeshMasterTextures(asset, session, visited, scratch);
            var results = session.RunAsync(scratch, CreateExportOptions()).GetAwaiter().GetResult();
            foreach (var result in results.Where(result => !result.Success)) diagnostics.Add("Dependency export failed: " + result.ObjectPath + ": " + result.Error?.Message);
            if (results.Count == 0 || results.Any(result => !result.Success) || metadataFailures() != 0)
                throw new InvalidDataException("Mesh/material dependency closure did not export successfully");
            var published = MapBundleDocument.PublishAsset(scratch, output, packagePath, sourceRevision, materials.ToArray());
            if (asset is USkinnedAsset or UStaticMesh && published["mesh"] == null)
                throw new InvalidDataException("Mesh export produced no UEModel or PSKX");
            try { MapDependencyCache.Save(output, packagePath, sourceRevision, MapDependencyCache.Capture(provider, package), published); }
            catch (Exception exception) { Log.Information("map dependency cache unavailable for {Package}: {Reason}", packagePath, exception.Message); }
            return published;
        }
        finally
        {
            var resolved = Path.GetFullPath(scratch);
            var temporary = Path.TrimEndingDirectorySeparator(Path.GetFullPath(Path.GetTempPath())) + Path.DirectorySeparatorChar;
            if (resolved.StartsWith(temporary, StringComparison.OrdinalIgnoreCase) && Path.GetFileName(resolved).StartsWith("arcpak-map-", StringComparison.Ordinal))
                Directory.Delete(resolved, true);
        }
    }

    internal static UObject SelectAsset(IEnumerable<UObject> exports, string packagePath)
    {
        var candidates = exports.Where(obj => obj is USkinnedAsset or UStaticMesh or UMaterialInterface).ToArray();
        var leaf = MapBundleDocument.CanonicalPackage(packagePath).Split('/').Last();
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
