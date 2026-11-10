using ArcPak;
using Newtonsoft.Json.Linq;

internal static class ReleaseChecks
{
    internal static void Verify(string[] args)
    {
        var output = args[1];
        var bundle = JObject.Parse(File.ReadAllText(Path.Combine(output, "map_bundle.json")));
        if (bundle.Value<bool>("complete")) throw new InvalidDataException("Unproven bundle claims complete");
        if (args[0] == "texture")
        {
            var files = ((JObject)bundle["assets"]!).Properties().SelectMany(property => property.Value["files"]!.Values<string>()).ToArray();
            if (!files.Any(file => file!.EndsWith("/clouds_diverse.png", StringComparison.Ordinal)))
                throw new InvalidDataException("Required authored clouds_basetex has no declared clouds_diverse.png");
        }
        else
        {
            var derived = ((JObject)bundle["assets"]!).Properties().Where(property => property.Name.Contains("#terrain") || property.Name.Contains("#spline")).ToArray();
            if (derived.Length == 0) throw new InvalidDataException("No actual derived geometry");
            foreach (var asset in derived)
            {
                var binding = asset.Value["derivedGeometry"] as JObject ?? throw new InvalidDataException("Missing explicit derivedGeometry: " + asset.Name);
                if (asset.Value["materialSlots"] is not JArray slots || slots.Count == 0) throw new InvalidDataException("No authored section slots");
                if (binding["outputs"] is not JArray outputs || outputs.Count == 0) throw new InvalidDataException("Missing physical outputs");
                foreach (var physical in outputs.OfType<JObject>())
                {
                    var file = physical.Value<string>("file")!;
                    if (!asset.Value["files"]!.Values<string>().Contains(file)) throw new InvalidDataException("Physical output not declared");
                    if (MapBundleDocument.HashFile(MapBundleDocument.Inside(output, file)) != physical.Value<string>("sha256")) throw new InvalidDataException("Physical hash mismatch");
                    if (string.IsNullOrEmpty(physical.Value<string>("objectPath"))) throw new InvalidDataException("Missing physical UEModel identity");
                    UEModelMaterialProof.Verify(MapBundleDocument.Inside(output, file), physical, slots);
                    var forged = (JObject)physical.DeepClone();
                    forged["objectPath"] = physical.Value<string>("objectPath") + "_unrelated";
                    try
                    {
                        UEModelMaterialProof.Verify(MapBundleDocument.Inside(output, file), forged, slots);
                        throw new InvalidDataException("Forged derived physical identity was accepted");
                    }
                    catch (InvalidDataException exception) when (exception.Message == "Physical UEModel identity differs from binding")
                    {
                        Console.WriteLine("PASS forged physical identity is rejected without suffix inference");
                    }
                }
                if (binding["source"]?["objectPath"] == null || binding["source"]?["packageFiles"] is not JObject sourceFiles || !sourceFiles.HasValues) throw new InvalidDataException("No cooked source provenance");
                foreach (var material in asset.Value["materials"]!.Values<string>().OfType<string>())
                {
                    if (bundle["assets"]![material]?["material"] == null) throw new InvalidDataException("Missing actual raw material dependency: " + material);
                    var metadata = bundle["assets"]![material]!.Value<string>("material")!;
                    var raw = JObject.Parse(File.ReadAllText(MapBundleDocument.Inside(output, metadata)));
                    if (!raw["Exports"]!.OfType<JObject>().Any(export => MapBundleDocument.CanonicalObject(export.Value<string>("ObjectPath")!) == material))
                        throw new InvalidDataException("Raw authored material identity differs from section dependency");
                }
            }
        }
        Console.WriteLine("PASS actual " + args[0] + " bundle " + output);
    }
}
