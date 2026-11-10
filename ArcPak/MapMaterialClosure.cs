using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse_Conversion;
using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapMaterialClosure
{
    internal static JArray Queue(UMaterialInterface material, ExportSession session, HashSet<string> visited, string scratch, SortedSet<string> diagnostics)
    {
        Program.QueueMasterMaterialTextures(material, session, visited, scratch);
        var dependencies = new JArray();
        var parents = new HashSet<string>(StringComparer.Ordinal);
        for (UUnrealMaterial? cursor = material; cursor != null && parents.Add(cursor.GetPathName()); cursor = (cursor as UMaterialInstance)?.Parent?.Load<UUnrealMaterial>())
        {
            var parameters = cursor is UMaterialInstanceConstant constant ? constant.TextureParameterValues : cursor.GetOrDefault<FTextureParameterValue[]>("TextureParameterValues", []);
            foreach (var parameter in parameters)
            {
                var dependency = new JObject { ["materialObject"] = cursor.GetPathName(), ["parameter"] = parameter.Name, ["reference"] = parameter.ParameterValue?.ResolvedObject?.GetPathName(), ["status"] = "unresolved" };
                dependencies.Add(dependency);
                try
                {
                    var texture = parameter.ParameterValue?.Load<UTexture>() ?? throw new InvalidDataException("Authored texture reference did not load");
                    session.Add(texture);
                    dependency["objectPath"] = texture.GetPathName();
                    dependency["status"] = "queued";
                }
                catch (Exception exception)
                {
                    dependency["error"] = exception.Message;
                    diagnostics.Add("Authored texture dependency unresolved: " + cursor.GetPathName() + ":" + parameter.Name + ": " + exception.Message);
                }
            }
        }
        return dependencies;
    }
}
