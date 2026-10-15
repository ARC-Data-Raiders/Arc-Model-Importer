namespace ArcPak;

/// <summary>
/// Steam appmanifest next to the install. buildid is the installed build.
/// The content depot's manifest id is the pak snapshot for that build.
/// </summary>
internal static class GameBuild
{
    internal const string AppId = "1808500";
    internal const string ContentDepot = "1808501";

    internal readonly record struct Info(string BuildId, string DepotManifest)
    {
        internal bool IsKnown => BuildId.Length > 0;
    }

    internal static Info Read(string gameDir)
    {
        var manifest = FindAppManifest(gameDir);
        if (manifest == null)
            return new Info("", "");

        string text;
        try
        {
            text = File.ReadAllText(manifest);
        }
        catch (Exception)
        {
            return new Info("", "");
        }

        var buildId = Digits(QuotedAfter(text, "buildid"));
        var depotManifest = Digits(DepotManifest(text, ContentDepot));
        return new Info(buildId, depotManifest);
    }

    private static string? FindAppManifest(string gameDir)
    {
        var dir = new DirectoryInfo(gameDir);
        for (var i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            if (!dir.Name.Equals("steamapps", StringComparison.OrdinalIgnoreCase))
                continue;
            var candidate = Path.Combine(dir.FullName, $"appmanifest_{AppId}.acf");
            if (File.Exists(candidate))
                return candidate;
        }

        return null;
    }

    private static string DepotManifest(string text, string depotId)
    {
        var depot = text.IndexOf($"\"{depotId}\"", StringComparison.Ordinal);
        if (depot < 0)
            return "";
        var window = text.Substring(depot, Math.Min(400, text.Length - depot));
        var end = window.IndexOf('}');
        if (end >= 0)
            window = window[..end];
        return QuotedAfter(window, "manifest");
    }

    private static string QuotedAfter(string text, string key)
    {
        var token = $"\"{key}\"";
        var at = text.IndexOf(token, StringComparison.OrdinalIgnoreCase);
        if (at < 0)
            return "";
        var open = text.IndexOf('"', at + token.Length);
        if (open < 0 || open + 1 >= text.Length)
            return "";
        var close = text.IndexOf('"', open + 1);
        if (close < 0)
            return "";
        return text[(open + 1)..close].Trim();
    }

    private static string Digits(string value)
    {
        if (value.Length == 0 || value.Length > 32)
            return "";
        foreach (var ch in value)
        {
            if (ch is < '0' or > '9')
                return "";
        }

        return value;
    }
}
