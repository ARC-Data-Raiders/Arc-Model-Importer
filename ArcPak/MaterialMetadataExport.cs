using System.Security.Cryptography;
using CUE4Parse.UE4.Assets.Exports.Material;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;
namespace ArcPak;
internal static class MaterialMetadataExport
{
    public const string Schema = "dataraiders.native-material-hierarchy";
    internal static JObject Source(UUnrealMaterial material)
    {
        var source = JObject.FromObject(material);
        source.Remove("LoadedMaterialResources");
        source.Remove("CachedData");
        source["Package"] = MapBundleDocument.CanonicalPackage(material.Owner?.Name ?? material.GetPathName());
        source["ObjectPath"] = MapBundleDocument.ExactObjectPath(material.GetPathName());
        return source;
    }
    internal static string OutputStem(UUnrealMaterial material) =>
        new CUE4Parse_Conversion.Exporters.MaterialExporter((UMaterialInterface)material).SavePath.Replace('\\', '/');
    private static string Fs(string path) { var p=Path.GetFullPath(path);return p.StartsWith(@"\\?\UNC\",StringComparison.OrdinalIgnoreCase)?@"\\"+p[8..]:p.StartsWith(@"\\?\",StringComparison.Ordinal)?p[4..]:p; }
    private static string Package(string reference) {
        var p=reference.Replace('\\','/').Trim('/');if(p.Contains('.'))p=p[..p.IndexOf('.')];
        foreach(var prefix in new[]{"PioneerGame/Content/","RemappedPlugins/Content/","Game/"})
            if(p.StartsWith(prefix,StringComparison.OrdinalIgnoreCase))return "PioneerGame/Content/"+p[prefix.Length..];
        // A mounted plugin's path is its own namespace and cache location (Banshee's
        // EmbarkScript/ClothingAssetTemplates/M_Character_ClothMaterial parent).
        var head=p.Split('/')[0];
        if(p.Contains('/')&&!p.Contains("/Content/",StringComparison.OrdinalIgnoreCase)&&
           !new[]{"PioneerGame","RemappedPlugins","Engine","Game"}.Contains(head,StringComparer.OrdinalIgnoreCase))return p;
        throw new InvalidDataException("Unsupported exact material mount (raw metadata supports Game/PioneerGame/RemappedPlugins/plugin mounts): "+reference);
    }
    public static int SelectIndex(string[] names, string[] objects, string requested) {
        if(names.Length!=objects.Length)throw new ArgumentException("Material descriptor count mismatch");
        var wanted=MapBundleDocument.CanonicalObject(requested);
        var matches=Enumerable.Range(0,names.Length).Where(i=>
            MapBundleDocument.CanonicalObject(objects[i]).Equals(wanted,StringComparison.OrdinalIgnoreCase)).ToArray();
        if(matches.Length!=1)throw new InvalidDataException("No unique exact named material object: "+requested+" (loaded "+objects.Length+": "+string.Join(", ",objects.Take(4))+")");
        return matches[0];
    }
    private static void Atomic(string target,string text) {
        Directory.CreateDirectory(Path.GetDirectoryName(target)!);var temp=target+".tmp-"+Guid.NewGuid().ToString("N");
        try {File.WriteAllText(temp,text);for(int attempt=0;;attempt++){try{File.Move(temp,target,true);break;}
            catch(Exception error) when(attempt<6&&(error is IOException or UnauthorizedAccessException)&&((error.HResult&0xffff)is 5 or 32 or 33)){Thread.Sleep(10<<attempt);}}}
        finally{try{if(File.Exists(temp))File.Delete(temp);}catch(IOException){}catch(UnauthorizedAccessException){}}
    }
    public static int Write(UMaterialInterface material,string requested,string root,string? cachedFile,GameBuild.Info build) {
        var canonical=Package(requested);if(!MapBundleDocument.CanonicalObject(material.GetPathName()).Equals(MapBundleDocument.CanonicalObject(requested),StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Loaded material identity differs from exact request");
        root=Fs(root).TrimEnd(Path.DirectorySeparatorChar);var mount="PioneerGame";
        if(!string.IsNullOrEmpty(cachedFile)){
            var file=Fs(cachedFile);var rel=Path.GetRelativePath(root,file).Replace('\\','/');
            if(rel.StartsWith("../",StringComparison.Ordinal)||Path.IsPathRooted(rel)||!File.Exists(cachedFile)||!rel.EndsWith(".json",StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Exact cached material file must exist below output root");
            if(rel.EndsWith(".arc-material.json",StringComparison.OrdinalIgnoreCase))rel=rel[..^18];else rel=rel[..^5];
            if(MapBundleDocument.CanonicalObject(material.GetPathName()).Contains('.') ? !rel.Equals(OutputStem(material),StringComparison.OrdinalIgnoreCase) : !Package(rel).Equals(canonical,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Cached material full package mismatch");
            if(canonical.StartsWith("PioneerGame/Content/",StringComparison.OrdinalIgnoreCase))mount=rel.Split('/')[0];
        }
        var visited=new HashSet<string>(StringComparer.OrdinalIgnoreCase);long total=0;var count=0;
        for(UUnrealMaterial? cursor=material;cursor is not null;cursor=(cursor as UMaterialInstance)?.Parent?.Load<UUnrealMaterial>()){
            if(count>=64||!visited.Add(cursor.GetPathName()))throw new InvalidDataException("Material parent cycle/depth budget");
            var package=Package(cursor.GetPathName());
            var relative=package.StartsWith("PioneerGame/Content/",StringComparison.OrdinalIgnoreCase)?mount+"/Content/"+package["PioneerGame/Content/".Length..]:package;
            if(MapBundleDocument.CanonicalObject(cursor.GetPathName()).Contains('.'))relative=OutputStem(cursor);
            var source=Source(cursor);
            var parent=(cursor as UMaterialInstance)?.Parent?.Load<UUnrealMaterial>();var provenance=new JObject{
                ["schema"]=Schema,["version"]=1,["package"]=package,["object_path"]=cursor.GetPathName(),
                ["build_id"]=build.BuildId,["depot_manifest"]=build.DepotManifest,["parent"]=parent?.GetPathName()??""};
            var payload=new JObject{["Exports"]=new JArray(source),["DataRaidersSource"]=provenance}.ToString(Formatting.Indented);
            total+=System.Text.Encoding.UTF8.GetByteCount(payload);if(total>128*1024*1024)throw new InvalidDataException("Material metadata 128MiB budget exceeded");
            Atomic(Path.Combine(root,relative.Replace('/',Path.DirectorySeparatorChar)+".arc-material.json"),payload);count++;
        }
        Log.Information("raw material hierarchy {Material} nodes={Count}; no texture/mesh exports",material.Name,count);return count;
    }
}
