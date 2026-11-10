using System.IO.Compression;
using System.Text;
using CUE4Parse.UE4.Assets.Exports.Material;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace ArcPak;

internal static class MaterialShaderMapExport
{
    internal static int Run(string[] args)
    {
        try
        {
            var o = ArcVerbCommon.Parse(args, new[] { "game-dir", "usmap", "out", "material", "material-file" }, new[] { "census" });
            var game = ArcVerbCommon.Required(o, "game-dir");
            var usmap = ArcVerbCommon.Required(o, "usmap");
            var output = ArcVerbCommon.Required(o, "out");
            var requested = o.TryGetValue("material", out var direct) ? new List<string>(direct) : new List<string>();
            if (o.TryGetValue("material-file", out var files)) foreach (var f in files) requested.AddRange(ArcVerbCommon.ReadList(f));
            if (requested.Count == 0) throw new ArgumentException("At least one material is required");
            using var provider = ArcVerbCommon.Open(game, usmap, true);
            var rows = new SortedDictionary<string, string[]>(StringComparer.Ordinal);
            var nodes = new Dictionary<string, UUnrealMaterial>(StringComparer.Ordinal);
            foreach (var path in requested)
            {
                try
                {
                    var pkg = Program.FindPackage(provider, path) ?? throw new InvalidDataException("Package not found: " + path);
                    var mats = pkg.GetExports().OfType<UMaterialInterface>().ToArray();
                    var i = MaterialMetadataExport.SelectIndex(mats.Select(m => m.Name).ToArray(), mats.Select(m => m.GetPathName()).ToArray(), path);
                    var chain = new List<UUnrealMaterial>();
                    var seen = new HashSet<string>(StringComparer.Ordinal);
                    for (UUnrealMaterial? c = mats[i]; c != null; c = (c as UMaterialInstance)?.Parent?.Load<UUnrealMaterial>())
                    {
                        var key = c.GetPathName();
                        if (chain.Count >= 64 || !seen.Add(key)) throw new InvalidDataException("Material parent cycle/depth budget");
                        if (!nodes.TryGetValue(key, out var existing)) nodes[key] = existing = c;
                        chain.Add(existing);
                    }
                    if (o.ContainsKey("census"))
                    {
                        for (var n = 0; n < chain.Count; n++)
                        {
                            var node = (UMaterialInterface)chain[n];
                            var parent = (node as UMaterialInstance)?.Parent?.Load<UUnrealMaterial>()?.GetPathName() ?? "";
                            var flag = node.TryGetValue(out bool b, "bHasStaticPermutationResource") ? (b ? "true" : "false") : "absent";
                            var resources = node.LoadedMaterialResources;
                            rows[node.GetPathName()] = new[] { node.GetPathName(), node.ExportType, parent, chain[^1].GetPathName(), (chain.Count - n - 1).ToString(System.Globalization.CultureInfo.InvariantCulture), flag, resources.Count.ToString(System.Globalization.CultureInfo.InvariantCulture), string.Join(";", resources.Select(r => r.LoadedShaderMap?.ResourceHash?.ToString() ?? "")), string.Join(";", resources.Select(r => r.LoadedShaderMap?.ShaderMapId?.FeatureLevel.ToString() ?? "")), string.Join(";", resources.Select(r => r.LoadedShaderMap?.ShaderMapId?.QualityLevel.ToString() ?? "")), string.Join(";", resources.Select(r => r.LoadedShaderMap?.ShaderPlatform.ToString() ?? "")), node.CachedExpressionData != null ? "present" : "absent", "" };
                        }
                    }
                    else
                    {
                        var entries = new JArray();
                        foreach (var node in chain) entries.Add(new JObject { ["object_path"] = node.GetPathName(), ["class"] = node.ExportType, ["parent"] = (node as UMaterialInstance)?.Parent?.Load<UUnrealMaterial>()?.GetPathName(), ["json"] = JObject.FromObject(node) });
                        WriteFull(output, path, new JObject { ["schema"] = "dr.material-shadermaps/1", ["build_id"] = GameBuild.Read(game).BuildId, ["requested"] = path, ["chain"] = entries });
                    }
                }
                catch (Exception e)
                {
                    var error = e.GetType().Name + ": " + e.Message;
                    if (o.ContainsKey("census")) rows[path] = new[] { path, "", "", "", "", "", "", "", "", "", "", "", error };
                    else WriteFull(output, path, new JObject { ["schema"] = "dr.material-shadermaps/1", ["build_id"] = GameBuild.Read(game).BuildId, ["requested"] = path, ["chain"] = new JArray(), ["error"] = error });
                }
            }
            if (o.ContainsKey("census"))
            {
                var header = "object_path,class,parent,root,depth_to_root,has_static_permutation_resource,lmr_count,resource_hashes,feature_levels,quality_levels,shader_platforms,cached_expression_data,error";
                var csv = header + "\r\n" + string.Concat(rows.Values.Select(row => string.Join(",", row.Select(v => "\"" + v.Replace("\"", "\"\"") + "\"")) + "\r\n"));
                ArcVerbCommon.WriteAtomic(Path.Combine(output, "census.csv"), Encoding.UTF8.GetBytes(csv));
            }
            return 0;
        }
        catch (ArgumentException e)
        {
            Log.Error(e.Message);
            return 1;
        }
    }

    private static void WriteFull(string output, string path, JObject data)
    {
        using var bytes = new MemoryStream();
        using (var gz = new GZipStream(bytes, CompressionLevel.Optimal, true))
        {
            var content = Encoding.UTF8.GetBytes(data.ToString(Formatting.None));
            gz.Write(content);
        }
        ArcVerbCommon.WriteAtomic(Path.Combine(output, "full", path.TrimStart('/').Replace('/', Path.DirectorySeparatorChar) + ".shadermaps.json.gz"), bytes.ToArray());
    }
}
