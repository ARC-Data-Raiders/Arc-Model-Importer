using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Engine;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace ArcPak;

internal static class ClothWrapExport
{
    private static JArray Vector(JToken? value, int dimensions = 3)
        => new(Enumerable.Range(0, dimensions).Select(i => (double?)value?["XYZW"[i].ToString()] ?? 0));

    public static string? WrapMesh(IEnumerable<IPackage> packages, string meshPath)
    {
        foreach (var package in packages)
        foreach (var export in package.GetExports())
        {
            if (export.ExportType != "CustomizationVisualPartDataAsset") continue;
            var properties = JObject.FromObject(export)["Properties"];
            var bound = (string?)properties?["Mesh"]?["ObjectPath"];
            if (string.IsNullOrEmpty(bound) || !SamePackage(bound, meshPath)) continue;
            var wrap = properties?["WrapDeformation"]?["OptionalWrapDeformerMesh"];
            var path = (string?)wrap?["ObjectPath"] ?? (string?)wrap?["AssetPathName"];
            if (!string.IsNullOrWhiteSpace(path)) return path.Split('.')[0];
        }
        return null;
    }

    private static bool SamePackage(string left, string right)
    {
        static string Normalize(string path)
        {
            path = path.Replace('\\', '/').Trim('/').Split('.')[0];
            foreach (var prefix in new[] { "PioneerGame/Content/", "RemappedPlugins/Content/", "Game/" })
                if (path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return path[prefix.Length..];
            return path;
        }
        return string.Equals(Normalize(left), Normalize(right), StringComparison.OrdinalIgnoreCase);
    }

    public static bool Write(IPackage package, USkinnedAsset mesh, string canonicalPath, string outDir, string? wrapMesh = null)
    {
        try
        {
            var cages = new JArray();
            foreach (var export in package.GetExports().Where(value => value.ExportType == "ClothingAssetCommon"))
            {
                var properties = JObject.FromObject(export)["Properties"];
                if (properties?["LODData"] is not JArray lods) continue;
                for (var lod = 0; lod < lods.Count; lod++)
                {
                    var physical = lods[lod]?["PhysicalMeshData"];
                    if (physical is null) continue;
                    var morphs = new JArray();
                    foreach (var morph in physical["MorphTargets"] as JArray ?? new JArray())
                    {
                        var deltas = new JArray();
                        foreach (var delta in morph["Deltas"] as JArray ?? new JArray())
                        {
                            var row = new JArray((int?)delta["PhysMeshVertId"] ?? -1);
                            foreach (var value in Vector(delta["PositionDelta"])) row.Add(value.DeepClone());
                            foreach (var value in Vector(delta["NormalDelta"])) row.Add(value.DeepClone());
                            deltas.Add(row);
                        }
                        // Physical indices can retain a copied head's ordering, which
                        // differs from the source hair's render morph ordering.
                        morphs.Add(new JObject { ["index"] = (int?)morph["Index"] ?? -1, ["deltas"] = deltas });
                    }
                    cages.Add(new JObject {
                        ["slot"] = physical["CreatedByMeshSectionWithMaterialSlotName"]?.DeepClone(),
                        ["asset_guid"] = properties?["AssetGuid"]?.DeepClone(), ["lod"] = lod,
                        ["vertices"] = new JArray((physical["Vertices"] as JArray ?? new JArray()).Select(value => Vector(value))),
                        ["normals"] = new JArray((physical["Normals"] as JArray ?? new JArray()).Select(value => Vector(value))),
                        ["indices"] = physical["Indices"]?.DeepClone() ?? new JArray(), ["morph_targets"] = morphs,
                    });
                }
            }
            var sections = new JArray();
            var firstLod = mesh.LODModels?.FirstOrDefault(lod => !lod.SkipLod);
            if (firstLod is not null)
            foreach (var section in firstLod.Sections)
            {
                var mappings = section.ClothMappingDataLODs?.FirstOrDefault();
                if (mappings is null || mappings.Length == 0) continue;
                var clothing = JObject.FromObject(section.ClothingData);
                var guid = (string?)clothing["AssetGuid"];
                var clothLod = (int?)clothing["AssetLodIndex"] ?? 0;
                var cage = cages.OfType<JObject>().FirstOrDefault(value =>
                    string.Equals((string?)value["asset_guid"], guid, StringComparison.OrdinalIgnoreCase) && (int?)value["lod"] == clothLod);
                var rows = new JArray(mappings.Select(mapping => new JArray(
                    mapping.SourceMeshVertIndices.Select(value => (double)value)
                    .Concat(new[] { (double)mapping.PositionBaryCoordsAndDist.X, mapping.PositionBaryCoordsAndDist.Y, mapping.PositionBaryCoordsAndDist.Z, mapping.PositionBaryCoordsAndDist.W,
                        mapping.NormalBaryCoordsAndDist.X, mapping.NormalBaryCoordsAndDist.Y, mapping.NormalBaryCoordsAndDist.Z, mapping.NormalBaryCoordsAndDist.W,
                        mapping.TangentBaryCoordsAndDist.X, mapping.TangentBaryCoordsAndDist.Y, mapping.TangentBaryCoordsAndDist.Z, mapping.TangentBaryCoordsAndDist.W, mapping.Weight }))));
                sections.Add(new JObject { ["base_vertex"] = section.BaseVertexIndex, ["vertex_count"] = section.NumVertices,
                    ["cage_slot"] = cage?["slot"]?.DeepClone(), ["asset_guid"] = guid, ["cloth_lod"] = clothLod, ["mapping"] = rows });
            }
            var renderMorphs = new JArray(mesh.MorphTargets.Select(reference => reference.Load()?.Name ?? ""));
            var document = new JObject { ["schema"] = "dataraiders.cloth-wrap", ["version"] = 1,
                ["mesh"] = canonicalPath, ["coordinate_system"] = "unreal", ["units"] = "centimeters",
                ["lod"] = firstLod is null ? -1 : Array.IndexOf(mesh.LODModels!, firstLod), ["vertex_count"] = firstLod?.NumVertices ?? 0,
                ["wrap_mesh"] = wrapMesh, ["render_morph_names"] = renderMorphs,
                ["mapping_columns"] = new JArray("source0", "source1", "source2", "source3", "position_x", "position_y", "position_z", "position_distance",
                    "normal_x", "normal_y", "normal_z", "normal_distance", "tangent_x", "tangent_y", "tangent_z", "tangent_distance", "weight"),
                ["physical_morph_index_provenance"] = "authored; may retain copied head cage ordering; never infer from source render morph order",
                ["cages"] = cages, ["sections"] = sections };
            var file = Path.Combine(outDir, canonicalPath.Replace('/', Path.DirectorySeparatorChar) + ".arc-cloth.json");
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            File.WriteAllText(file, document.ToString(Formatting.None));
            Log.Information("cloth {Mesh} cages={Cages} sections={Sections}", mesh.Name, cages.Count, sections.Count);
            return true;
        }
        catch (Exception error) { Log.Error(error, "Cloth metadata export failed {Mesh}", mesh.Name); return false; }
    }
}
