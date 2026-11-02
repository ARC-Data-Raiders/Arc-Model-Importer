namespace FModel.Views.Snooper.MapPlacement;

internal static class MapPlacementExporter
{
    public static string ResolveExportMapName(string packageStem, string? worldName = null, string? packagePath = null)
    {
        static string Sanitize(string value) => string.Concat(value.Select(character =>
            Path.GetInvalidFileNameChars().Contains(character) ? '_' : character));
        var stem = Sanitize(packageStem ?? "");
        if (!string.IsNullOrWhiteSpace(stem) && !stem.Equals("Map", StringComparison.OrdinalIgnoreCase) &&
            !stem.Equals("PersistentLevel", StringComparison.OrdinalIgnoreCase)) return stem;
        foreach (var part in (packagePath ?? "").Replace('\\', '/').Split('/', StringSplitOptions.RemoveEmptyEntries).Reverse())
        {
            var candidate = part.EndsWith(".umap", StringComparison.OrdinalIgnoreCase) ? part[..^5] : part;
            if (!new[] { "Maps", "Content", "Pioneer", "Game", "_Generated_" }.Contains(candidate, StringComparer.OrdinalIgnoreCase))
                return Sanitize(candidate);
        }
        return string.IsNullOrWhiteSpace(worldName) ? "Map" : Sanitize(worldName);
    }
}
