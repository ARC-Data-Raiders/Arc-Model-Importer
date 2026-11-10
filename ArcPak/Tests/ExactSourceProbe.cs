using ArcPak;
using CUE4Parse.UE4.Assets.Exports.Material;
using Newtonsoft.Json.Linq;

internal static class ExactSourceProbe
{
    internal static void Run(string[] args)
    {
        using var provider = ArcPak.Program.CreateShell(args[1]);
        provider.ReadShaderMaps = false;
        ArcPak.Program.PrepareCompression(args[1]);
        provider.MappingsContainer = new MapBundleMappingsProvider(args[2]);
        ArcPak.Program.MountEverything(provider);
        var rows = new JArray();
        foreach (var selector in args.Skip(3))
        {
            var canonical = MapBundleDocument.CanonicalPackage(selector);
            var files = provider.Files.Values.Where(file => file.IsUePackage && MapBundleDocument.CanonicalPackage(file.Path).Equals(canonical, StringComparison.OrdinalIgnoreCase)).DistinctBy(file => file.Path).ToArray();
            var row = new JObject { ["selector"] = selector, ["mountedSources"] = new JArray(files.Select(file => new JObject { ["path"] = file.Path, ["sha256"] = MapBundleDocument.Hash(file.Read()) })) };
            rows.Add(row);
            try
            {
                var package = ArcPak.Program.FindPackage(provider, canonical) ?? throw new InvalidDataException("Exact mounted package unavailable");
                var exports = package.GetExports().ToArray();
                row["exports"] = new JArray(exports.Select(obj => new JObject { ["path"] = obj.GetPathName(), ["type"] = obj.ExportType }));
                row["landscapes"] = new JArray(exports.OfType<CUE4Parse.UE4.Assets.Exports.Actor.ALandscapeProxy>().Select(proxy =>
                {
                    using var dto = new CUE4Parse_Conversion.Dto.LandscapeMeshDto(proxy, CUE4Parse_Conversion.Options.ELandscapeFlags.All);
                    return new JObject { ["path"] = proxy.GetPathName(), ["converterMaterials"] = new JArray(dto.Materials.Select(material => material.Material?.ResolvedObject?.GetPathName())),
                        ["sections"] = JToken.FromObject(dto.LODs.Select(lod => lod.Sections.Select(section => section.MaterialIndex))),
                        ["authored"] = new JArray(proxy.LandscapeComponents.Select(reference => reference.Load<CUE4Parse.UE4.Assets.Exports.Component.Landscape.ULandscapeComponent>()).Select(component =>
                            new JObject { ["path"] = component?.GetPathName(), ["override"] = component?.OverrideMaterial.ResolvedObject?.GetPathName(), ["default"] = proxy.LandscapeMaterial.ResolvedObject?.GetPathName() })) };
                }));
                row["parameters"] = new JArray(exports.OfType<UMaterialInstanceConstant>().Where(material => material.GetPathName() == selector).SelectMany(material => material.TextureParameterValues.Select(value =>
                {
                    var parameter = new JObject { ["name"] = value.Name, ["rawReference"] = value.ParameterValue?.ResolvedObject?.GetPathName() };
                    try { var loaded = value.ParameterValue?.Load(); parameter["loaded"] = loaded?.GetPathName(); parameter["type"] = loaded?.ExportType; }
                    catch (Exception exception) { parameter["error"] = exception.ToString(); }
                    return parameter;
                })));
                try { row["receiptSources"] = MapDependencyCache.Capture(provider, package); }
                catch (Exception exception) { row["receiptError"] = exception.Message; }
            }
            catch (Exception exception) { row["error"] = exception.ToString(); }
        }
        MapBundleDocument.WriteAtomic(args[0], "exact-source.json", MapBundleDocument.Bytes(rows));
        Console.WriteLine(rows.ToString(Newtonsoft.Json.Formatting.Indented));
    }
}
