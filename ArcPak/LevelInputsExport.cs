using System.Text;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.FastGeoStreaming;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace ArcPak;

internal static class LevelInputsExport
{
    internal static int Run(string[] args)
    {
        try
        {
            var o = ArcVerbCommon.Parse(args, new[] { "game-dir", "usmap", "out", "map-name", "limit-packages" }, Array.Empty<string>());
            var name = ArcVerbCommon.Required(o, "map-name");
            var output = Path.Combine(ArcVerbCommon.Required(o, "out"), name);
            using var provider = ArcVerbCommon.Open(ArcVerbCommon.Required(o, "game-dir"), ArcVerbCommon.Required(o, "usmap"), false);
            var persistent = provider.Files.Keys.Where(k => Path.GetFileName(k).Equals(name + ".umap", StringComparison.OrdinalIgnoreCase)).ToArray();
            if (persistent.Length != 1)
            {
                ArcVerbCommon.WriteAtomic(Path.Combine(output, "candidates.txt"), Encoding.UTF8.GetBytes(string.Join("\n", provider.Files.Keys.Where(k => k.Contains(name, StringComparison.OrdinalIgnoreCase)).Take(500)) + "\n"));
                return 4;
            }
            var prefix = persistent[0][..^5] + "/_Generated_/";
            var cells = provider.Files.Keys.Where(k => k.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k, StringComparer.OrdinalIgnoreCase).ToArray();
            if (cells.Length == 0)
            {
                ArcVerbCommon.WriteAtomic(Path.Combine(output, "layout_probe.txt"), Encoding.UTF8.GetBytes(string.Join("\n", provider.Files.Keys.Where(k => k.Contains(name, StringComparison.OrdinalIgnoreCase)).Take(500)) + "\n"));
                return 5;
            }
            var packages = new[] { persistent[0] }.Concat(cells).ToArray();
            if (o.ContainsKey("limit-packages"))
            {
                if (!int.TryParse(ArcVerbCommon.Required(o, "limit-packages"), out var limit) || limit < 0) throw new ArgumentException("Invalid --limit-packages");
                packages = packages.Take(limit).ToArray();
            }
            var records = new List<JObject>();
            var failed = new JArray();
            var errors = new JArray();
            var histogram = new SortedDictionary<string, int>(StringComparer.Ordinal);
            var loaded = 0;
            foreach (var package in packages)
            {
                UObject[] exports;
                try
                {
                    var pkg = Program.FindPackage(provider, package[..^5]) ?? throw new InvalidDataException("Package not found");
                    exports = pkg.GetExports().ToArray();
                    loaded++;
                }
                catch (Exception e)
                {
                    failed.Add(new JObject { ["package"] = package, ["error"] = e.GetType().Name + ": " + e.Message });
                    continue;
                }
                foreach (var obj in exports)
                {
                    histogram[obj.ExportType] = histogram.GetValueOrDefault(obj.ExportType) + 1;
                    try
                    {
                        var record = new JObject { ["package"] = package, ["export_path"] = obj.GetPathName(), ["class"] = obj.ExportType, ["outer"] = obj.Outer?.GetPathName() ?? "", ["kind"] = "" };
                        if (obj is UStaticMeshComponent smc)
                        {
                            record["kind"] = "smc";
                            record["static_mesh"] = Ref(smc.GetStaticMesh());
                            record["override_materials"] = smc.TryGetValue(out FPackageIndex[] materials, "OverrideMaterials") ? new JArray(materials.Select(Ref)) : JValue.CreateNull();
                            record["overlay_material"] = TagRef(smc, "OverlayMaterial");
                            record["mesh_paint_texture_cooked"] = Ref(smc.MeshPaintTextureCooked);
                            record["custom_primitive_data"] = smc.TryGetValue(out FStructFallback custom, "CustomPrimitiveData") && custom.TryGetValue(out float[] data, "Data") ? JArray.FromObject(data) : JValue.CreateNull();
                            var colors = new JArray();
                            if (smc.LODData != null) for (var i = 0; i < smc.LODData.Length; i++)
                            {
                                var c = smc.LODData[i].OverrideVertexColors;
                                if (c == null) continue;
                                var bytes = c.Data.SelectMany(v => new[] { v.B, v.G, v.R, v.A }).ToArray();
                                var hash = ArcVerbCommon.Sha256(bytes);
                                var relative = "vertexcolors/" + hash + ".bin";
                                if (!File.Exists(Path.Combine(output, relative))) ArcVerbCommon.WriteAtomic(Path.Combine(output, relative), bytes);
                                colors.Add(new JObject { ["lod"] = i, ["num_vertices"] = c.NumVertices, ["sha256"] = hash, ["file"] = relative });
                            }
                            record["lod_vertex_colors"] = colors;
                            Transform(record, smc);
                            if (smc is UInstancedStaticMeshComponent ism)
                            {
                                record["instance_count"] = ism.PerInstanceSMData?.Length ?? 0;
                                record["num_custom_data_floats"] = Tag(ism, "NumCustomDataFloats");
                                record["instancing_random_seed"] = Tag(ism, "InstancingRandomSeed");
                                record["additional_random_seeds"] = Tag(ism, "AdditionalRandomSeeds");
                                if (ism.PerInstanceSMCustomData is { } instanceCustom)
                                {
                                    var bytes = new byte[instanceCustom.Length * 4];
                                    using (var stream = new MemoryStream(bytes)) using (var writer = new BinaryWriter(stream)) foreach (var value in instanceCustom) writer.Write(value);
                                    var hash = ArcVerbCommon.Sha256(bytes);
                                    var relative = "ismcustom/" + hash + ".bin";
                                    if (!File.Exists(Path.Combine(output, relative))) ArcVerbCommon.WriteAtomic(Path.Combine(output, relative), bytes);
                                    record["per_instance_custom_data"] = new JObject { ["count"] = instanceCustom.Length, ["sha256"] = hash, ["file"] = relative };
                                }
                                else record["per_instance_custom_data"] = JValue.CreateNull();
                            }
                        }
                        else if (obj is UDecalComponent dc)
                        {
                            record["kind"] = "decal";
                            record["decal_material"] = Ref(dc.DecalMaterial);
                            record["decal_size"] = dc.DecalSize is { } size ? new JArray(size.X, size.Y, size.Z) : JValue.CreateNull();
                            foreach (var tag in new[] { "SortOrder", "FadeScreenSize", "FadeStartDelay", "FadeDuration" }) record[tag] = Tag(dc, tag);
                            Transform(record, dc);
                        }
                        else if (obj is UFastGeoContainer fg)
                        {
                            record["kind"] = "fastgeo";
                            record["clusters"] = fg.ComponentClusters.Length;
                            record["StaticMeshComponents"] = fg.ComponentClusters.Sum(c => c.StaticMeshComponents.Length);
                            record["InstancedStaticMeshComponents"] = fg.ComponentClusters.Sum(c => c.InstancedStaticMeshComponents.Length);
                            record["SkinnedMeshComponents"] = fg.ComponentClusters.Sum(c => c.SkinnedMeshComponents.Length);
                            record["InstancedSkinnedMeshComponents"] = fg.ComponentClusters.Sum(c => c.InstancedSkinnedMeshComponents.Length);
                            record["ProceduralISMComponents"] = fg.ComponentClusters.Sum(c => c.ProceduralISMComponents.Length);
                            var components = fg.ComponentClusters.SelectMany(c => c.StaticMeshComponents.Cast<FFastGeoComponent>().Concat(c.InstancedStaticMeshComponents).Concat(c.SkinnedMeshComponents).Concat(c.InstancedSkinnedMeshComponents).Concat(c.ProceduralISMComponents)).ToArray();
                            record["with_custom_primitive_data"] = components.OfType<FFastGeoPrimitiveComponent>().Count(c => c.CustomPrimitiveData is { Length: > 0 });
                            var instances = components.OfType<FFastGeoInstancedStaticMeshComponent>().ToArray();
                            record["PerInstanceSMData"] = instances.Sum(c => c.PerInstanceSMData.Length);
                            record["PerInstanceSMCustomData"] = instances.Sum(c => c.PerInstanceSMCustomData.Length);
                            record["PerInstanceRandomIDs"] = instances.Sum(c => c.PerInstanceRandomIDs.Length);
                        }
                        else continue;
                        records.Add(record);
                    }
                    catch (Exception e)
                    {
                        errors.Add(new JObject { ["package"] = package, ["export_path"] = obj.GetPathName(), ["error"] = e.GetType().Name + ": " + e.Message });
                    }
                }
            }
            var lines = string.Concat(records.OrderBy(r => (string?)r["package"], StringComparer.Ordinal).ThenBy(r => (string?)r["export_path"], StringComparer.Ordinal).Select(r => r.ToString(Formatting.None) + "\n"));
            ArcVerbCommon.WriteAtomic(Path.Combine(output, "components.jsonl"), Encoding.UTF8.GetBytes(lines));
            var smcs = records.Where(r => (string?)r["kind"] == "smc").Select(r => JObject.Parse(r.ToString(Formatting.None))).ToArray();
            var summary = new JObject { ["map"] = name, ["persistent"] = persistent[0], ["cell_count"] = cells.Length, ["packages_loaded"] = loaded, ["packages_failed"] = failed, ["export_errors"] = errors, ["class_histogram"] = JObject.FromObject(histogram), ["smc"] = smcs.Length, ["ism"] = smcs.Count(r => r.ContainsKey("instance_count")), ["hism"] = smcs.Count(r => (string?)r["class"] == "HierarchicalInstancedStaticMeshComponent"), ["decals"] = records.Count(r => (string?)r["kind"] == "decal"), ["fastgeo_containers"] = records.Count(r => (string?)r["kind"] == "fastgeo"), ["with_override_vertex_colors"] = smcs.Count(r => ((JArray)r["lod_vertex_colors"]!).Count > 0), ["with_custom_primitive_data"] = smcs.Count(r => r["custom_primitive_data"] is JArray { Count: > 0 }), ["with_overlay_material"] = smcs.Count(r => r["overlay_material"]!.Type != JTokenType.Null), ["with_mesh_paint_texture"] = smcs.Count(r => r["mesh_paint_texture_cooked"]!.Type != JTokenType.Null), ["ism_with_custom_data"] = smcs.Count(r => r["per_instance_custom_data"] is JObject c && (int)c["count"]! > 0) };
            ArcVerbCommon.WriteAtomic(Path.Combine(output, "summary.json"), Encoding.UTF8.GetBytes(summary.ToString(Formatting.Indented)));
            return 0;
        }
        catch (ArgumentException e)
        {
            Log.Error(e.Message);
            return 1;
        }
    }

    private static string? Ref(FPackageIndex? index) => index?.ResolvedObject?.GetPathName();
    private static string? TagRef(UObject obj, string name) => obj.TryGetValue(out FPackageIndex index, name) ? Ref(index) : null;
    private static JToken Tag(UObject obj, string name)
    {
        var tag = obj.Properties.FirstOrDefault(t => t.Name.Text == name);
        return tag?.Tag != null ? JToken.FromObject(tag.Tag) : JValue.CreateNull();
    }
    private static void Transform(JObject record, UObject obj)
    {
        var transform = new JObject();
        foreach (var name in new[] { "RelativeLocation", "RelativeRotation", "RelativeScale3D", "Mobility" }) transform[name] = Tag(obj, name);
        record["transform"] = transform;
        record["attach_parent"] = TagRef(obj, "AttachParent");
    }
}
