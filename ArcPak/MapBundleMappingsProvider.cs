using CUE4Parse.GameTypes.ArcRaiders.Mappings;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;

namespace ArcPak;

internal sealed class MapBundleMappingsProvider : AbstractTypeMappingsProvider
{
    private readonly string _path;
    public override TypeMappings? MappingsForGame { get; protected set; }

    internal MapBundleMappingsProvider(string path)
    {
        _path = path;
        Load(path);
    }

    public override void Load(string path, StringComparer? comparer = null)
    {
        var parser = new UsmapParser(File.ReadAllBytes(path), comparer: comparer);
        MappingsForGame = parser.Mappings;
        ArcRaidersUsmapFixes.Apply(MappingsForGame, path);
    }

    public override void Load(byte[] bytes, StringComparer? comparer = null)
    {
        var parser = new UsmapParser(bytes, comparer: comparer);
        MappingsForGame = parser.Mappings;
        ArcRaidersUsmapFixes.Apply(MappingsForGame);
    }

    public override void Reload() => Load(_path);
}
