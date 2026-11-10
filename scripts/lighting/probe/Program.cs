// Lighting-look probe: static, read-only package reads through an ArcPak.Export build.
// Writes bounded JSON evidence; never touches the game process. Paths come from the
// environment (see ../README.md); run with the ArcPak build folder as working directory.
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using CUE4Parse.UE4.Assets;

string Env(string name, string fallback) => Environment.GetEnvironmentVariable(name) is { Length: > 0 } v ? v : fallback;
var arcDir = Env("ARCPAK_BUILD", Directory.GetCurrentDirectory());
var outDir = Env("LIGHTING_EVIDENCE", Path.Combine(Directory.GetCurrentDirectory(), "lighting-evidence"));
var game = Env("ARC_GAME_DIR", @"C:\Program Files (x86)\Steam\steamapps\common\Arc Raiders");
var usmap = Env("ARC_USMAP", "");
Directory.CreateDirectory(outDir);
AppDomain.CurrentDomain.AssemblyResolve += (_, e) => {
    var p = Path.Combine(arcDir, new AssemblyName(e.Name).Name + ".dll");
    return File.Exists(p) ? Assembly.LoadFrom(p) : null;
};
var program = Assembly.LoadFrom(Path.Combine(arcDir, "ArcPak.Export.dll")).GetTypes().Single(t => t.Name == "ArcVerbCommon");
dynamic provider = program.GetMethod("Open", BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Public)!
    .Invoke(null, new object[] { game, usmap, false })!;
var findPackage = Assembly.LoadFrom(Path.Combine(arcDir, "ArcPak.Export.dll")).GetTypes().Single(t => t.Name == "Program")
    .GetMethod("FindPackage", BindingFlags.NonPublic | BindingFlags.Static)!;

var mode = args.Length > 0 ? args[0] : "list";
IEnumerable<string> keys = ((System.Collections.IEnumerable)provider.Files.Keys).Cast<string>().ToList();
if (mode == "list") {
    var pats = new[] { "lightingscenario", "/lighting/", "hdri", "pioneer_sky", "kodak", "rgbtable", "/sky/", "skysphere", "skydome", "weathermanager", "postprocess", "colorgrading", "lut" };
    var hits = keys.Where(k => pats.Any(p => k.Contains(p, StringComparison.OrdinalIgnoreCase))).OrderBy(k => k, StringComparer.OrdinalIgnoreCase).ToList();
    File.WriteAllLines(Path.Combine(outDir, "file-list-lighting.txt"), hits);
    Console.WriteLine($"files={keys.Count()} hits={hits.Count}");
    return;
}
if (mode == "grep") {
    var rx = new System.Text.RegularExpressions.Regex(args[1], System.Text.RegularExpressions.RegexOptions.IgnoreCase);
    var hits = keys.Where(k => rx.IsMatch(k)).OrderBy(k => k, StringComparer.OrdinalIgnoreCase).ToList();
    File.WriteAllLines(Path.Combine(outDir, args[2]), hits); Console.WriteLine($"grep hits={hits.Count}"); return;
}
if (mode == "cells") {
    var persist = args[1]; var dir = persist[..persist.LastIndexOf('.')] + "/_Generated_/";
    var cells = keys.Where(k => k.StartsWith(dir, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k, StringComparer.OrdinalIgnoreCase).ToList();
    File.WriteAllLines(Path.Combine(outDir, args[2]), new[] { persist }.Concat(cells));
    Console.WriteLine($"cells={cells.Count}"); return;
}
if (mode == "types") {
    // args[1] = package key, args[2] = comma type substrings, args[3] = output file name
    var key = args[1][..args[1].LastIndexOf('.')];
    var package = (IPackage?)findPackage.Invoke(null, new object[] { provider, key });
    var filt = args[2].Split(',');
    var arr = new JArray();
    foreach (var export in package!.GetExports()) {
        if (!filt.Any(f => export.ExportType.Contains(f, StringComparison.OrdinalIgnoreCase))) continue;
        try { arr.Add(JObject.FromObject(export)); } catch (Exception ex) { arr.Add(new JObject { ["error"] = ex.Message, ["name"] = export.Name }); }
    }
    File.WriteAllText(Path.Combine(outDir, args[3]), arr.ToString());
    Console.WriteLine($"types n={arr.Count}"); return;
}
if (mode == "exports") {
    // args[1] = package-list file, args[2] = output file: every export of each package
    var all = new JObject();
    foreach (var raw in File.ReadAllLines(args[1]).Select(l => l.Trim()).Where(l => l.Length > 0)) {
        var key = raw.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) ? raw[..raw.LastIndexOf('.')] : raw;
        var package = (IPackage?)findPackage.Invoke(null, new object[] { provider, key });
        var arr = new JArray();
        if (package is not null)
            foreach (var export in package.GetExports()) {
                try { arr.Add(JObject.FromObject(export)); } catch (Exception ex) { arr.Add(new JObject { ["error"] = ex.Message, ["name"] = export.Name }); }
            }
        all[key] = arr;
    }
    File.WriteAllText(Path.Combine(outDir, args[2]), all.ToString());
    Console.WriteLine($"exports packages={all.Count}"); return;
}
if (mode == "mesh") {
    // args[1] = static mesh package key, args[2] = output file: LOD0 positions and every UV channel
    var package = (IPackage?)findPackage.Invoke(null, new object[] { provider, args[1] });
    var mesh = package!.GetExports().OfType<CUE4Parse.UE4.Assets.Exports.StaticMesh.UStaticMesh>().First();
    var lod = mesh.RenderData!.LODs[0];
    var pos = lod.PositionVertexBuffer!.Verts.Select(v => new[] { v.X, v.Y, v.Z }).ToArray();
    var uvs = lod.VertexBuffer!.UV.Select(item => item.UV.Select(t => new[] { t.U, t.V }).ToArray()).ToArray();
    File.WriteAllText(Path.Combine(outDir, args[2]), JsonConvert.SerializeObject(new { mesh = args[1], num_tex_coords = lod.VertexBuffer.NumTexCoords, positions = pos, uvs }));
    Console.WriteLine($"mesh verts={pos.Length} uv={lod.VertexBuffer.NumTexCoords}"); return;
}
// mode "dump": args[1] = package-list file (one provider key per line, with or without extension)
var wanted = new[] { "PostProcess", "SkyLight", "SkyAtmosphere", "DirectionalLight", "HeightFog", "VolumetricCloud", "Pioneer_Sky", "WeatherManager", "Coldsnap", "ColdSnap", "DataLayer", "LightingScenario", "ReflectionCapture", "Sky" };
long saved = 0; const long Max = 96L * 1024 * 1024;
var summary = new JArray();
foreach (var raw in File.ReadAllLines(args[1]).Select(l => l.Trim()).Where(l => l.Length > 0)) {
    var key = raw.EndsWith(".umap", StringComparison.OrdinalIgnoreCase) || raw.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) ? raw[..raw.LastIndexOf('.')] : raw;
    var package = (IPackage?)findPackage.Invoke(null, new object[] { provider, key });
    if (package is null) { summary.Add(new JObject { ["package"] = key, ["status"] = "missing" }); continue; }
    var chosen = new JArray(); int total = 0;
    foreach (var export in package.GetExports()) {
        total++;
        JObject j;
        try { j = JObject.FromObject(export); } catch (Exception ex) { chosen.Add(new JObject { ["error"] = ex.Message, ["name"] = export.Name }); continue; }
        var type = (string?)j["Type"] ?? ""; var props = j["Properties"] as JObject;
        bool pp = props is not null && (props["Settings"] is JObject s && s.Properties().Any(p => p.Name.StartsWith("bOverride_")) || props["PostProcessSettings"] is not null);
        bool outerSky = ((string?)j["Outer"]?["ObjectName"] ?? "").Contains("Sky", StringComparison.OrdinalIgnoreCase);
        if (pp || outerSky || wanted.Any(w => type.Contains(w, StringComparison.OrdinalIgnoreCase))) chosen.Add(j);
    }
    var sub = args.Length > 2 ? args[2] : "dump";
    var file = Path.Combine(outDir, sub, key.Replace('/', '_') + ".json");
    Directory.CreateDirectory(Path.GetDirectoryName(file)!);
    var bytes = System.Text.Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(new { package = key, exports = total, selected = chosen.Count, items = chosen }, Formatting.Indented));
    if (saved + bytes.Length > Max) { Console.WriteLine("budget reached"); break; }
    if (chosen.Count > 0) { File.WriteAllBytes(file, bytes); saved += bytes.Length; }
    summary.Add(new JObject { ["package"] = key, ["status"] = "read", ["exports"] = total, ["selected"] = chosen.Count });
}
File.WriteAllText(Path.Combine(outDir, (args.Length > 2 ? args[2] : "dump") + "-summary.json"), summary.ToString());
Console.WriteLine($"saved={saved}");
