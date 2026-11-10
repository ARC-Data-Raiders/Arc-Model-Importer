using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.UE4.Assets.Exports.Actor;
using CUE4Parse.UE4.Assets.Exports.FastGeoStreaming;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Formats.Meshes;
using CUE4Parse_Conversion.Options;
using FModel.Exporters.MapPlacement;
using FModel.Views.Snooper.MapPlacement;
using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapResourceExport
{
    internal static FastGeoDetachedCapture CaptureFastGeo(IEnumerable<MapTraversalObject> objects, string spoolDirectory, int sliceLimit) =>
        FastGeoSnapshot.Capture(objects, spoolDirectory, sliceLimit);

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

    internal static void Geometry(AbstractVfsFileProvider provider, MapPlacementExtractionResult extraction,
        IEnumerable<MapTraversalObject> objects, JObject bundle, string output, SortedSet<string> diagnostics, string gameDir, string usmap,
        ExportArtifactReuse? artifactReuse = null)
    {
        var assets = (JObject)bundle["assets"]!;
        var resources = (JObject)bundle["resources"]!;
        var revision = bundle.Value<string>("sourceRevision")!;
        foreach (var job in extraction.SplineBakeJobs.OrderBy(job => job.SplineAssetKey, StringComparer.Ordinal))
        {
            var scratch = Path.Combine(Path.GetTempPath(), "arcpak-map-resource-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(scratch);
            try
            {
                var mesh = job.Component.GetStaticMesh().Load<UStaticMesh>() ?? throw new InvalidDataException("Spline mesh reference unresolved");
                using var dto = new StaticMeshDto(mesh, EMeshQuality.All, ENaniteMeshFormat.NoNanite, job.Component);
                if (dto.LODs.Count == 0) throw new InvalidDataException("Spline bake produced no LODs");
                var files = new UEFormatMeshFormat().BuildStaticMesh(mesh.Name + "-" + job.MeshIdShort, mesh.GetPathName(), MapBundleExport.CreateExportOptions(), dto);
                if (files.Count == 0) throw new InvalidDataException("Spline bake wrote no files");
                for (var index = 0; index < files.Count; index++)
                    MapBundleDocument.WriteAtomic(scratch, "spline-" + index + ".uemodel", files[index].Data);
                var slots = MapDerivedGeometry.Slots(dto);
                var materialObjects = slots.OfType<JObject>().Select(slot => slot.Value<string>("materialObject")!).ToArray();
                var materials = materialObjects.Where(value => value.Length > 0).Select(MapBundleDocument.CanonicalPackage).Distinct(StringComparer.Ordinal).ToArray();
                MapDerivedGeometry.Materials(provider, bundle, slots, output, gameDir, usmap, diagnostics, artifactReuse);
                var sourceIds = bundle["placements"]!.OfType<JObject>().Where(placement => placement["properties"]?.Value<string>("sourceAssetKey") == job.SplineAssetKey).Select(placement => placement.Value<string>("id")!).ToArray();
                var sourceId = sourceIds.FirstOrDefault() ?? job.MeshIdShort;
                var binding = MapDerivedGeometry.Binding(provider, job.Component, sourceId, "spline", job.MeshIdShort, scratch);
                binding["sourceRecordIds"] = new JArray(sourceIds);
                binding["base"] = MapDerivedGeometry.Source(provider, mesh, MapBundleDocument.CanonicalObject(mesh.GetPathName()));
                binding["deformation"] = new JObject { ["algorithm"] = "map-spline-deformation-v2", ["signature"] = job.MeshIdShort,
                    ["parameters"] = JToken.FromObject(job.Component.SplineParams), ["forwardAxis"] = job.Component.ForwardAxis.ToString(),
                    ["upDir"] = JToken.FromObject(job.Component.SplineUpDir), ["boundaryMin"] = job.Component.SplineBoundaryMin,
                    ["boundaryMax"] = job.Component.SplineBoundaryMax, ["smoothInterpRollScale"] = job.Component.bSmoothInterpRollScale };
                assets[job.SplineAssetKey] = MapBundleDocument.PublishAsset(scratch, output, job.SplineAssetKey, revision, materials,
                    objectPath: binding["outputs"]![0]!.Value<string>("objectPath"), materialObjects: materialObjects, derivedGeometry: binding, materialSlots: slots);
                foreach (var placement in bundle["placements"]!.OfType<JObject>().Where(placement =>
                    placement["properties"]?.Value<string>("sourceAssetKey") == job.SplineAssetKey))
                    placement["asset"] = job.SplineAssetKey;
                foreach (var spline in resources["splines"]!.OfType<JObject>().Where(spline => spline.Value<string>("splineAssetKey") == job.SplineAssetKey))
                {
                    spline["baked"] = true;
                    spline["asset"] = job.SplineAssetKey;
                    spline["lods"] = dto.LODs.Count;
                }
                diagnostics.Remove("Spline deformation is retained but not baked: " + job.Component.GetPathName());
            }
            catch (Exception exception) { diagnostics.Add("Spline bake failed: " + job.SplineAssetKey + ": " + exception.Message); }
            finally { Directory.Delete(scratch, true); }
        }
        foreach (var item in objects.Where(item => item.Source is ALandscapeProxy))
        {
            var scratch = Path.Combine(Path.GetTempPath(), "arcpak-map-resource-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(scratch);
            try
            {
                var session = MapBundleExport.CreateAssetExportSession(artifactReuse);
                session.Add(item.Source);
                var results = session.RunAsync(scratch, MapBundleExport.CreateExportOptions()).GetAwaiter().GetResult();
                if (results.Count == 0 || results.Any(result => !result.Success)) throw new InvalidDataException("Terrain dependency conversion failed");
                var key = item.SourcePath + "#terrain";
                var proxy = (ALandscapeProxy)item.Source;
                using var dto = new LandscapeMeshDto(proxy, ELandscapeFlags.All);
                var authoredMaterials = new List<CUE4Parse.UE4.Objects.UObject.FPackageIndex>();
                var authoredComponents = new JArray();
                foreach (var componentReference in proxy.LandscapeComponents)
                {
                    var component = componentReference.Load<CUE4Parse.UE4.Assets.Exports.Component.Landscape.ULandscapeComponent>() ?? throw new InvalidDataException("Landscape component unavailable");
                    var authored = component.OverrideMaterial is { IsNull: false } material ? material : proxy.LandscapeMaterial;
                    authoredMaterials.Add(authored);
                    authoredComponents.Add(new JObject { ["objectPath"] = component.GetPathName(), ["materialObject"] = authored.ResolvedObject?.GetPathName(),
                        ["slotIndex"] = dto.Materials.Length == 1 ? 0 : authoredMaterials.Count - 1 });
                }
                if (dto.Materials.Length == authoredMaterials.Count)
                    for (var slotIndex = 0; slotIndex < dto.Materials.Length; slotIndex++)
                        dto.Materials[slotIndex] = new MeshMaterialDto(dto.Materials[slotIndex].SlotName, authoredMaterials[slotIndex]);
                else if (dto.Materials.Length == 1 && authoredMaterials.Select(material => material.ResolvedObject?.GetPathName()).Distinct(StringComparer.Ordinal).Count() == 1)
                    dto.Materials[0] = new MeshMaterialDto(dto.Materials[0].SlotName, authoredMaterials[0]);
                else
                    throw new InvalidDataException("Landscape has distinct authored section materials that this converter cannot bind safely");
                var slots = MapDerivedGeometry.Slots(dto);
                MapDerivedGeometry.Materials(provider, bundle, slots, output, gameDir, usmap, diagnostics, artifactReuse);
                var physical = Directory.EnumerateFiles(scratch, "*.uemodel", SearchOption.AllDirectories).Single();
                var header = MapDerivedGeometry.Header(physical);
                var meshes = new UEFormatMeshFormat().BuildStaticMesh(header.Name, header.Path, MapBundleExport.CreateExportOptions(), dto);
                if (meshes.Count != 1) throw new InvalidDataException("Terrain material binding produced ambiguous physical outputs");
                MapBundleDocument.WriteAtomic(scratch, Path.GetRelativePath(scratch, physical), meshes[0].Data);
                var binding = MapDerivedGeometry.Binding(provider, item.Source, item.Id, "terrain", key, scratch);
                binding["authoredComponents"] = authoredComponents;
                var materialObjects = slots.OfType<JObject>().Select(slot => slot.Value<string>("materialObject")!).ToArray();
                var materials = materialObjects.Where(value => value.Length > 0).Select(MapBundleDocument.CanonicalPackage).Distinct(StringComparer.Ordinal).ToArray();
                var exported = MapBundleDocument.PublishAsset(scratch, output, key, revision, materials, header.Path, materialObjects, derivedGeometry: binding, materialSlots: slots);
                if (exported["mesh"] == null) throw new InvalidDataException("Terrain exporter produced no geometry");
                assets[key] = exported;
                ((JArray)resources["landscapes"]!).Add(new JObject
                {
                    ["id"] = item.Id, ["asset"] = key, ["geometryExported"] = true,
                    ["matrix"] = MapBundleDocument.Matrix(item.TransformCm ?? FTransform.Identity), ["vertexSpace"] = "proxy-local-units",
                    ["transformKnown"] = item.TransformCm != null,
                    ["properties"] = MapBundleExport.Raw(item.Source, diagnostics)
                });
                if (item.TransformCm != null)
                    ((JArray)bundle["placements"]!).Add(new JObject
                    {
                        ["id"] = MapPlacementRecord.StableId(item.SourcePath + "#terrain", null, item.WorldInstancePath),
                        ["asset"] = key, ["kind"] = "Landscape", ["matrix"] = MapBundleDocument.Matrix(item.TransformCm.Value),
                        ["materials"] = new JArray(materialObjects), ["materialObjects"] = new JArray(materialObjects),
                        ["properties"] = new JObject { ["sourcePath"] = item.SourcePath, ["actorId"] = item.Id }
                    });
                diagnostics.Remove("Landscape geometry is not exported: " + item.SourcePath);
                diagnostics.Add("Terrain mesh and height/weight resources exported, but landscape visibility holes and full WP proxy discovery remain unproven: " + item.SourcePath);
            }
            catch (Exception exception) { diagnostics.Add("Terrain export failed: " + item.SourcePath + ": " + exception.Message); }
            finally { Directory.Delete(scratch, true); }
        }
    }
}
