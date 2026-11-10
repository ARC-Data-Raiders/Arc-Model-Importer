using ArcPak;
using CUE4Parse.UE4.Assets.Exports.Component.SplineMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;
using FModel.Exporters.MapPlacement;
using FModel.Views.Snooper.MapPlacement;
using Newtonsoft.Json.Linq;

internal static class RealSplineProbe
{
    internal static void Run(string[] args)
    {
        var output = Path.GetFullPath(args[0]);
        using var provider = ArcPak.Program.CreateShell(args[1]);
        provider.ReadShaderMaps = false;
        ArcPak.Program.PrepareCompression(args[1]);
        provider.MappingsContainer = new MapBundleMappingsProvider(args[2]);
        ArcPak.Program.MountEverything(provider);
        var package = ArcPak.Program.FindPackage(provider, args[3]) ?? throw new InvalidDataException("Real spline map unavailable");
        var spline = package.GetExports().OfType<USplineMeshComponent>().OrderBy(obj => obj.GetPathName(), StringComparer.Ordinal).First();
        var mesh = spline.GetStaticMesh().Load<UStaticMesh>() ?? throw new InvalidDataException("Real spline base mesh unavailable");
        using var original = new StaticMeshDto(mesh, EMeshQuality.All, ENaniteMeshFormat.NoNanite);
        using var deformed = new StaticMeshDto(mesh, EMeshQuality.All, ENaniteMeshFormat.NoNanite, spline);
        var originalPositions = original.LODs.SelectMany(lod => lod.Vertices).Select(vertex => vertex.Position).ToArray();
        var deformedPositions = deformed.LODs.SelectMany(lod => lod.Vertices).Select(vertex => vertex.Position).ToArray();
        if (originalPositions.Length == 0 || originalPositions.Length != deformedPositions.Length)
            throw new InvalidDataException("Spline proof requires matching nonempty source and baked topology");
        var changed = originalPositions.Zip(deformedPositions).Count(pair =>
            Math.Abs(pair.First.X - pair.Second.X) + Math.Abs(pair.First.Y - pair.Second.Y) + Math.Abs(pair.First.Z - pair.Second.Z) > .001);
        if (changed == 0) throw new InvalidDataException("Spline converter did not deform any source vertices");
        var meshPath = mesh.GetPathName();
        var shortId = MapPlacementSplineNotes.MeshIdShort(spline, meshPath);
        var key = MapPlacementSplineNotes.MakeSplineAssetKey(meshPath, shortId);
        var extraction = new MapPlacementExtractionResult();
        extraction.SplineBakeJobs.Add(new MapPlacementSplineBakeJob
            { BaseAssetPath = meshPath, SplineAssetKey = key, MeshIdShort = shortId, Component = spline });
        var bundle = MapBundleExport.Empty(MapBundleDocument.CanonicalPackage(args[3]));
        bundle["sourceRevision"] = MapBundleExport.SourceRevision(args[1], args[2]);
        bundle["resources"]!["splines"] = new JArray(new JObject
            { ["splineAssetKey"] = key, ["componentPath"] = spline.GetPathName(), ["baked"] = false });
        var record = MapPlacementRecord.FromTransform(spline.Name, key, "SplineMesh", MapTransformResolver.CalculateAbsoluteTransformCm(spline), args[3],
            componentPath: spline.GetPathName());
        ((JArray)bundle["placements"]!).Add(MapBundleDocument.Placement(record, MapBundleExport.Raw(spline, new SortedSet<string>())));
        var diagnostics = new SortedSet<string>(StringComparer.Ordinal)
            { "One actual cooked spline component sampled from the source map. This is not full-map traversal." };
        var baseAsset = MapBundleDocument.CanonicalPackage(meshPath);
        ((JObject)bundle["assets"]!)[baseAsset] = MapBundleExport.ExportAsset(provider, meshPath, output, args[1], args[2], bundle.Value<string>("sourceRevision")!, diagnostics);
        bundle["requiredAssets"] = new JArray(baseAsset, key);
        MapResourceExport.Geometry(provider, extraction, [], bundle, output, diagnostics, args[1], args[2]);
        var assets = (JObject)bundle["assets"]!;
        if (assets[key]?["mesh"] == null) throw new InvalidDataException("Actual spline resource exporter did not publish geometry: " + string.Join("; ", diagnostics));
        bundle["coverage"]!["assetConversion"] = bundle["requiredAssets"]!.Values<string>().All(identity => assets.ContainsKey(identity!));
        bundle["coverage"]!["authoredTextureClosure"] = !assets.Properties().Any(property => property.Value["textureDependencies"]?.OfType<JObject>().Any(dependency => dependency.Value<string>("status") != "exported") == true);
        bundle["coverage"]!["dependencySourceReceipts"] = !diagnostics.Any(message => message.StartsWith("Dependency source receipt unavailable:", StringComparison.Ordinal));
        bundle["coverage"]!["materialGraphCompleteness"] = false;
        bundle["coverage"]!["derivedGeometryBindings"] = assets[key]?["derivedGeometry"] != null;
        bundle["coverage"]!["splineBake"] = true;
        bundle["diagnostics"] = new JArray(diagnostics);
        MapBundleDocument.WriteAtomic(output, "map_bundle.json", MapBundleDocument.Bytes(bundle));
        var proof = new JObject { ["sourceMap"] = args[3], ["sourceComponent"] = spline.GetPathName(), ["baseMesh"] = meshPath,
            ["sourceLods"] = original.LODs.Count, ["bakedLods"] = deformed.LODs.Count, ["vertices"] = originalPositions.Length,
            ["changedVertices"] = changed, ["publishedMesh"] = assets[key]!["mesh"]!.DeepClone() };
        MapBundleDocument.WriteAtomic(output, "spline-proof.json", MapBundleDocument.Bytes(proof));
        Console.WriteLine(proof.ToString(Newtonsoft.Json.Formatting.None));
    }
}
