using System.Diagnostics;
using System.Text;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.GameTypes.Theia.Encryption;
using CUE4Parse.GameTypes.Theia.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.IO.Objects;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Versions;
using CUE4Parse.Compression;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Textures.BC;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;
using SixLabors.ImageSharp;
using SixLabors.ImageSharp.PixelFormats;
using SixLabors.ImageSharp.Processing;

namespace ArcPak;

/// <summary>
/// Mount shipping Arc Raiders (UE 5.7) IoStore and write .uemodel + material JSON + PNG.
/// Game profile and AES are fixed. Usmap path comes from the addon.
/// </summary>
internal static class Program
{
    private const int Workers = 4;
    private static bool _compressionReady;

    public static int Main(string[] args)
    {
        Log.Logger = new LoggerConfiguration()
            .MinimumLevel.Information()
            .WriteTo.Console()
            .CreateLogger();

        try
        {
            if (args.Length == 0 || args[0] is "-h" or "--help" or "help")
            {
                PrintHelp();
                return args.Length == 0 ? 1 : 0;
            }

            if (string.Equals(args[0], "extract", StringComparison.OrdinalIgnoreCase))
                return Extract(args.Skip(1).ToArray());
            if (string.Equals(args[0], "index", StringComparison.OrdinalIgnoreCase))
                return BuildIndex(args.Skip(1).ToArray());
            if (string.Equals(args[0], "reindex", StringComparison.OrdinalIgnoreCase))
                return ReindexCommand(args.Skip(1).ToArray());

            Log.Error("Unknown command '{Cmd}'. Expected: extract, index, reindex", args[0]);
            PrintHelp();
            return 1;
        }
        catch (Exception ex)
        {
            Log.Error(ex, "ArcPak.Export failed");
            return 1;
        }
        finally
        {
            Log.CloseAndFlush();
        }
    }

    private static int BuildIndex(string[] args)
    {
        string gameDir = "";
        string outFile = "";
        for (var i = 0; i < args.Length; i++)
        {
            var a = args[i];
            string Next()
            {
                if (i + 1 >= args.Length)
                    throw new ArgumentException($"Missing value after {a}");
                return args[++i];
            }

            switch (a)
            {
                case "--game-dir":
                    gameDir = Next();
                    break;
                case "--out":
                    outFile = Next();
                    break;
                default:
                    throw new ArgumentException($"Unknown argument {a}");
            }
        }

        if (string.IsNullOrWhiteSpace(gameDir) || !Directory.Exists(gameDir))
            throw new ArgumentException("Missing or absent --game-dir (PioneerGame/Content/Paks)");
        if (string.IsNullOrWhiteSpace(outFile))
            throw new ArgumentException("Missing --out");

        PrepareCompression(gameDir);
        var build = GameBuild.Read(gameDir);
        using var provider = CreateShell(gameDir);
        MountEverything(provider);
        PakIndex.Write(provider, gameDir, outFile, build);
        Log.Information(
            "Wrote pak index {File} build={Build} packages={Packages} files={Files}",
            Path.GetFileName(outFile),
            build.IsKnown ? build.BuildId : "unknown",
            provider.Files.Values.Count(file => file is FIoStoreEntry { IsPackageData: true, IsOptionalPackage: false }),
            provider.Files.Count);
        return 0;
    }

    private static int ReindexCommand(string[] args)
    {
        string gameDir = "";
        string indexDir = "";
        string cacheDir = "";
        string usmap = "";
        for (var i = 0; i < args.Length; i++)
        {
            var a = args[i];
            string Next()
            {
                if (i + 1 >= args.Length)
                    throw new ArgumentException($"Missing value after {a}");
                return args[++i];
            }

            switch (a)
            {
                case "--game-dir":
                    gameDir = Next();
                    break;
                case "--index-dir":
                    indexDir = Next();
                    break;
                case "--out":
                    cacheDir = Next();
                    break;
                case "--usmap":
                    usmap = Next();
                    break;
                default:
                    throw new ArgumentException($"Unknown argument {a}");
            }
        }

        if (string.IsNullOrWhiteSpace(gameDir) || !Directory.Exists(gameDir))
            throw new ArgumentException("Missing or absent --game-dir (PioneerGame/Content/Paks)");
        if (string.IsNullOrWhiteSpace(indexDir))
            throw new ArgumentException("Missing --index-dir");
        if (!string.IsNullOrWhiteSpace(usmap) && !File.Exists(usmap))
            throw new ArgumentException("Missing or absent --usmap");

        PrepareCompression(gameDir);
        var build = GameBuild.Read(gameDir);
        using var provider = Reindex(gameDir, indexDir, build, usmap, cacheDir);
        Log.Information(
            "Reindexed build={Build} file={File} files={Files}",
            build.IsKnown ? build.BuildId : "unknown",
            PakIndex.FileName(build),
            provider.Files.Count);
        return 0;
    }

    private static int Extract(string[] args)
    {
        string gameDir = "";
        string usmap = "";
        string outDir = "";
        var paths = new List<string>();
        var indexPaths = new List<string>();
        string indexDir = "";

        for (var i = 0; i < args.Length; i++)
        {
            var a = args[i];
            string Next()
            {
                if (i + 1 >= args.Length)
                    throw new ArgumentException($"Missing value after {a}");
                return args[++i];
            }

            switch (a)
            {
                case "--game-dir":
                    gameDir = Next();
                    break;
                case "--usmap":
                    usmap = Next();
                    break;
                case "--out":
                    outDir = Next();
                    break;
                case "--path":
                    paths.Add(Next().Replace('\\', '/'));
                    break;
                case "--index":
                    indexPaths.Add(Next());
                    break;
                case "--index-dir":
                    indexDir = Next();
                    break;
                default:
                    throw new ArgumentException($"Unknown argument {a}");
            }
        }

        if (string.IsNullOrWhiteSpace(gameDir) || !Directory.Exists(gameDir))
            throw new ArgumentException("Missing or absent --game-dir (PioneerGame/Content/Paks)");
        if (string.IsNullOrWhiteSpace(usmap) || !File.Exists(usmap))
            throw new ArgumentException("Missing or absent --usmap");
        if (string.IsNullOrWhiteSpace(outDir))
            throw new ArgumentException("Missing --out");
        if (paths.Count == 0)
            throw new ArgumentException("Pass at least one --path");

        Directory.CreateDirectory(outDir);
        if (string.IsNullOrWhiteSpace(indexDir))
            indexDir = outDir;
        Directory.CreateDirectory(indexDir);
        PrepareCompression(gameDir);
        var provider = OpenProvider(gameDir, usmap, paths, indexPaths, indexDir, outDir);
        try
        {
            return ExportQueued(provider, paths, outDir);
        }
        finally
        {
            provider.Dispose();
        }
    }

    private static AbstractVfsFileProvider OpenProvider(
        string gameDir,
        string usmap,
        List<string> paths,
        List<string> indexPaths,
        string indexDir,
        string outDir)
    {
        var timer = Stopwatch.StartNew();
        var build = GameBuild.Read(gameDir);
        var index = SelectIndex(gameDir, indexDir, indexPaths, build);
        var rebuildPreviews = index != null && (!index.IndexesPreviews || !index.IndexesMountAliases);
        if (!rebuildPreviews && index != null && index.TryPrepare(paths, out var prepared, out var hits))
        {
            var requested = paths.ToList();
            paths.Clear();
            paths.AddRange(prepared);
            var selective = TrySelective(gameDir, usmap, paths, index, hits);
            if (selective is not null)
            {
                Log.Information(
                    "Mounted {Files} files in {Ms} ms (selective)",
                    selective.Files.Count,
                    timer.ElapsedMilliseconds);
                if (prepared.Count != requested.Count)
                    Log.Information("Resolved {Count} package(s)", prepared.Count);
                return selective;
            }

            Log.Information("Selective mount could not see the asset; reindexing");
            paths.Clear();
            paths.AddRange(requested);
        }
        else if (!rebuildPreviews && index != null && index.HasPaths && index.IndexesDataAssets)
        {
            Log.Error("No mesh or colourway package under {Paths}", string.Join("; ", paths));
            var shell = CreateShell(gameDir);
            if (!string.IsNullOrWhiteSpace(usmap))
                shell.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
            return shell;
        }
        else if (rebuildPreviews)
        {
            Log.Information("Pak index needs current mount aliases or previews; rebuilding once");
        }
        else if (index != null && index.HasPaths)
        {
            Log.Information("Pak index has no colourway list; rebuilding once");
        }
        else if (index == null)
        {
            Log.Information("Pak hashes do not match a saved index; reindexing");
        }
        else
        {
            Log.Information("Pak index has no folder list; rebuilding once");
        }

        var full = Reindex(gameDir, indexDir, build, usmap, outDir);
        Log.Information(
            "Mounted {Files} files in {Ms} ms (reindex)",
            full.Files.Count,
            timer.ElapsedMilliseconds);
        return full;
    }

    private static TheiaFileProvider Reindex(
        string gameDir,
        string indexDir,
        GameBuild.Info build,
        string? usmap,
        string? cacheDir)
    {
        Log.Information(
            "Reindex build={Build} depot={Depot}",
            build.IsKnown ? build.BuildId : "unknown",
            build.DepotManifest.Length > 0 ? build.DepotManifest : "unknown");
        var full = CreateShell(gameDir);
        if (!string.IsNullOrWhiteSpace(usmap))
            full.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
        MountEverything(full);
        Directory.CreateDirectory(indexDir);
        var path = Path.Combine(indexDir, PakIndex.FileName(build));
        try
        {
            PakIndex.Write(full, gameDir, path, build);
            Log.Information("Wrote pak index {File} build={Build}", Path.GetFileName(path), build.IsKnown ? build.BuildId : "unknown");
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "Could not write pak index");
        }

        try
        {
            CachePreviewIcons(full, cacheDir);
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "Could not cache preview icons");
        }

        return full;
    }

    private static void CachePreviewIcons(AbstractVfsFileProvider provider, string? cacheDir)
    {
        if (string.IsNullOrWhiteSpace(cacheDir))
            return;
        Directory.CreateDirectory(cacheDir);
        var options = new ExportOptions(
            meshFormat: EMeshFormat.UEFormat,
            meshQuality: EMeshQuality.Highest,
            exportMaterials: false,
            exportMorphTargets: false);
        var session = new ExportSession { MaxDegreeOfParallelism = Workers };
        var queued = 0;
        var skipped = 0;
        foreach (var file in provider.Files.Values)
        {
            if (file is not FIoStoreEntry { IsPackageData: true, IsOptionalPackage: false } entry)
                continue;
            var text = entry.PathWithoutExtension.Replace('\\', '/');
            if (!PakIndex.IsPreviewPackage(text))
                continue;
            if (PreviewPngExists(cacheDir, text))
            {
                skipped++;
                continue;
            }

            IPackage? package;
            try
            {
                package = FindPackage(provider, text);
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "preview load skipped {Path}", PakIndex.Normalize(text));
                continue;
            }

            var texture = package?.GetExports().OfType<UTexture>().FirstOrDefault();
            if (texture is null)
                continue;
            try
            {
                session.Add(texture);
                queued++;
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "preview queue skipped {Name}", texture.Name);
            }
        }

        if (queued == 0)
        {
            if (skipped > 0)
                Log.Information("Preview icons already cached ({Count})", skipped);
            CropCachedOutfitPreviews(cacheDir);
            return;
        }

        var wrote = 0;
        var results = session.RunAsync(cacheDir, options).GetAwaiter().GetResult();
        foreach (var result in results)
        {
            if (result.Success)
                wrote++;
            else
                Log.Warning("preview failed {Path}", result.ObjectPath);
        }

        Log.Information("Cached {Wrote} preview icon(s) ({Skipped} already present)", wrote, skipped);
        CropCachedOutfitPreviews(cacheDir);
    }

    /// <summary>
    /// Same framing as assets/outfit_previews: a 326px square from the top of a 512px
    /// icon, centered on the opaque pixels, resized to 256. The full PNG stays put.
    /// </summary>
    private static void CropCachedOutfitPreviews(string cacheDir)
    {
        var root = Path.Combine(
            cacheDir,
            "PioneerGame",
            "Content",
            "Pioneer",
            "Items",
            "Characters",
            "Skins",
            "Outfit");
        if (!Directory.Exists(root))
            return;
        var destDir = Path.Combine(cacheDir, "outfit_previews");
        Directory.CreateDirectory(destDir);
        var wrote = 0;
        foreach (var png in Directory.EnumerateFiles(root, "T_UI_Icon_Outfit_*.png", SearchOption.AllDirectories))
        {
            var name = Path.GetFileNameWithoutExtension(png);
            const string prefix = "T_UI_Icon_Outfit_";
            if (!name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                continue;
            var stem = name[prefix.Length..];
            if (stem.Length == 0)
                continue;
            var dest = Path.Combine(destDir, stem + ".png");
            if (File.Exists(dest) && File.GetLastWriteTimeUtc(dest) >= File.GetLastWriteTimeUtc(png))
                continue;
            try
            {
                CropOutfitPreview(png, dest);
                wrote++;
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "outfit preview crop skipped {Name}", stem);
            }
        }

        if (wrote > 0)
            Log.Information("Cropped {Count} outfit preview(s)", wrote);
    }

    private static void CropOutfitPreview(string sourcePng, string destPng)
    {
        using var image = Image.Load<Rgba32>(sourcePng);
        var side = (int)Math.Round(Math.Min(image.Width, image.Height) * (326.0 / 512.0));
        side = Math.Clamp(side, 1, Math.Min(image.Width, image.Height));
        var minX = int.MaxValue;
        var maxX = int.MinValue;
        image.ProcessPixelRows(accessor =>
        {
            for (var y = 0; y < accessor.Height; y++)
            {
                var row = accessor.GetRowSpan(y);
                for (var x = 0; x < row.Length; x++)
                {
                    if (row[x].A <= 8)
                        continue;
                    if (x < minX)
                        minX = x;
                    if (x > maxX)
                        maxX = x;
                }
            }
        });
        int left;
        if (minX <= maxX)
        {
            var center = (minX + maxX + 1) / 2.0;
            left = (int)Math.Round(center - side / 2.0);
            left = Math.Clamp(left, 0, image.Width - side);
        }
        else
        {
            left = (image.Width - side) / 2;
        }

        image.Mutate(ctx => ctx
            .Crop(new Rectangle(left, 0, side, side))
            .Resize(256, 256, KnownResamplers.Lanczos3));
        Directory.CreateDirectory(Path.GetDirectoryName(destPng)!);
        image.SaveAsPng(destPng);
    }

    private static bool PreviewPngExists(string cacheDir, string packagePath)
    {
        var png = PreviewPngPath(cacheDir, packagePath);
        return png.Length > 0 && File.Exists(png);
    }

    private static string PreviewPngPath(string cacheDir, string packagePath)
    {
        var norm = packagePath.Replace('\\', '/').Trim('/');
        const string marker = "Items/Characters/Skins/";
        var idx = norm.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
        if (idx < 0)
            return "";
        var rel = "PioneerGame/Content/Pioneer/" + norm[idx..];
        return Path.Combine(cacheDir, rel.Replace('/', Path.DirectorySeparatorChar) + ".png");
    }

    private static PakIndex? SelectIndex(
        string gameDir,
        string indexDir,
        List<string> indexPaths,
        GameBuild.Info build)
    {
        if (build.IsKnown)
        {
            var versioned = Path.Combine(indexDir, PakIndex.FileName(build));
            var saved = PakIndex.TryLoad(versioned);
            if (saved != null && saved.MatchesBuild(build))
            {
                if (!saved.Matches(gameDir))
                {
                    Log.Information("Pak index for build {Build} does not match game files", build.BuildId);
                    return null;
                }

                Log.Information(
                    "Pak index for build {Build} ({File}) packages={Packages}",
                    build.BuildId,
                    Path.GetFileName(versioned),
                    saved.PackageCount);
                return saved;
            }
        }

        foreach (var path in indexPaths)
        {
            if (!File.Exists(path))
                continue;
            var index = PakIndex.TryLoad(path);
            if (index == null)
            {
                Log.Information("Pak index unreadable {File}", Path.GetFileName(path));
                continue;
            }

            if (!index.Matches(gameDir))
            {
                Log.Information("Pak index does not match game files {File}", Path.GetFileName(path));
                continue;
            }

            RememberBuild(index, indexDir, build);
            Log.Information(
                "Pak index hashes match {File} packages={Packages}",
                Path.GetFileName(path),
                index.PackageCount);
            return index;
        }

        return null;
    }

    private static void RememberBuild(PakIndex index, string indexDir, GameBuild.Info build)
    {
        if (!build.IsKnown)
            return;
        var path = Path.Combine(indexDir, PakIndex.FileName(build));
        if (File.Exists(path))
            return;
        try
        {
            index.Save(path, build);
            Log.Information("Saved pak index for build {Build}", build.BuildId);
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "Could not save build pak index");
        }
    }

    private static TheiaFileProvider? TrySelective(
        string gameDir,
        string usmap,
        List<string> paths,
        PakIndex index,
        List<PakIndex.PathHit> hits)
    {
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var hit in hits)
            names.Add(index.Containers[hit.Container].Name);
        foreach (var name in index.AlwaysMountNames())
            names.Add(name);

        var provider = CreateShell(gameDir);
        provider.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
        try
        {
            if (!MountNames(provider, gameDir, names))
            {
                provider.Dispose();
                return null;
            }

            var mounted = new HashSet<string>(names, StringComparer.OrdinalIgnoreCase);
            if (!ExpandImports(provider, index, gameDir, hits.Select(hit => hit.PackageId), mounted))
            {
                provider.Dispose();
                return null;
            }

            // Visual-part assets own default outfit/gear materials, while the
            // mesh only imports a preview MI. Mount the DA_VP import closure too.
            var partDirectories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var path in paths)
                foreach (var candidate in PakIndex.Candidates(path))
                {
                    var slash = candidate.LastIndexOf('/');
                    if (slash >= 0) partDirectories.Add(candidate[..(slash + 1)]);
                }
            var partIds = new List<ulong>();
            var partContainers = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var file in provider.Files.Values)
            {
                if (file is not FIoStoreEntry { IsPackageData: true, IsOptionalPackage: false } entry) continue;
                var path = entry.PathWithoutExtension.Replace('\\', '/');
                var slash = path.LastIndexOf('/');
                if (slash < 0 || !path[(slash + 1)..].StartsWith("DA_VP_", StringComparison.OrdinalIgnoreCase) ||
                    !partDirectories.Contains(path[..(slash + 1)])) continue;
                if (!index.TryResolve(new[] { path }, out var partHits)) continue;
                foreach (var hit in partHits)
                {
                    partIds.Add(hit.PackageId);
                    var name = index.Containers[hit.Container].Name;
                    if (mounted.Add(name)) partContainers.Add(name);
                }
            }
            if (partContainers.Count > 0 && !MountNames(provider, gameDir, partContainers))
                throw new InvalidOperationException("Visual-part containers could not mount");
            if (!ExpandImports(provider, index, gameDir, partIds, mounted))
                throw new InvalidOperationException("Visual-part material dependencies could not mount");
            provider.PostMount();
            if (!CanLoad(provider, paths))
                Log.Information("Package path did not load directly; searching mounted files");

            Log.Information(
                "Selective containers {Count}: {Names}",
                mounted.Count,
                string.Join(", ", mounted.OrderBy(name => name, StringComparer.OrdinalIgnoreCase)));
            return provider;
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "Selective mount failed");
            provider.Dispose();
            return null;
        }
    }

    private static bool ExpandImports(
        TheiaFileProvider provider,
        PakIndex index,
        string gameDir,
        IEnumerable<ulong> rootIds,
        HashSet<string> mounted)
    {
        var seen = new HashSet<ulong>();
        var wave = new List<ulong>();
        foreach (var id in rootIds)
        {
            if (seen.Add(id))
                wave.Add(id);
        }

        for (var depth = 0; depth < 32 && wave.Count > 0; depth++)
        {
            var next = new List<ulong>();
            var toMount = new List<string>();
            foreach (var id in wave)
            {
                var store = provider.TryFindStoreEntry(new FPackageId(id));
                if (store?.ImportedPackages == null)
                    continue;
                foreach (var imported in store.ImportedPackages)
                {
                    if (!seen.Add(imported.id))
                        continue;
                    if (!index.TryGetContainer(imported.id, out var name))
                        continue;
                    next.Add(imported.id);
                    if (mounted.Add(name))
                        toMount.Add(name);
                }
            }

            // Packages in already-mounted containers can still import a new
            // container on the next wave. Traverse them instead of stopping.
            if (toMount.Count > 0 && !MountNames(provider, gameDir, toMount))
                return false;
            wave = next;
        }

        return true;
    }

    private static void ExpandFromFiles(AbstractVfsFileProvider provider, List<string> paths)
    {
        var next = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var path in paths)
        {
            if (FindPackage(provider, path) != null)
            {
                if (seen.Add(path))
                    next.Add(path);
                continue;
            }

            var prefixes = PakIndex.Candidates(path).ToList();
            var added = 0;
            foreach (var key in provider.Files.Keys)
            {
                if (!key.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase))
                    continue;
                var without = key[..^".uasset".Length];
                if (!PakIndex.IsListedPackage(without))
                    continue;
                var norm = PakIndex.Normalize(without);
                var match = false;
                foreach (var prefix in prefixes)
                {
                    if (norm == prefix || norm.StartsWith(prefix + "/", StringComparison.Ordinal))
                    {
                        match = true;
                        break;
                    }
                }

                if (!match || !seen.Add(without))
                    continue;
                next.Add(without.Replace('\\', '/'));
                if (++added >= 128)
                    break;
            }
        }

        if (next.Count == 0)
            return;
        paths.Clear();
        paths.AddRange(next);
    }

    private static bool CanLoad(AbstractVfsFileProvider provider, IReadOnlyList<string> paths)
    {
        foreach (var path in paths)
        {
            if (FindPackage(provider, path) == null)
                return false;
        }

        return true;
    }

    private static bool IsSiblingTexture(string leaf)
    {
        if (leaf.StartsWith("T_", StringComparison.OrdinalIgnoreCase))
            return true;
        // Hair card atlases are Hair_* / Beard_* , not T_.
        if (leaf.StartsWith("Hair_", StringComparison.OrdinalIgnoreCase))
            return true;
        if (leaf.StartsWith("Beard_", StringComparison.OrdinalIgnoreCase))
            return true;
        if (leaf.Contains("CardsAtlas", StringComparison.OrdinalIgnoreCase))
            return true;
        return false;
    }

    private static int QueueSiblingTextures(
        AbstractVfsFileProvider provider,
        string packagePath,
        ExportSession session)
    {
        var norm = CanonicalGamePackagePath(packagePath);
        var slash = norm.LastIndexOf('/');
        if (slash < 0)
            return 0;
        var dir = norm[..(slash + 1)];
        var queued = 0;
        foreach (var file in provider.Files.Values)
        {
            if (file is not FIoStoreEntry { IsPackageData: true, IsOptionalPackage: false } entry)
                continue;
            var originalPath = entry.PathWithoutExtension.Replace('\\', '/');
            var text = CanonicalGamePackagePath(originalPath);
            if (!text.StartsWith(dir, StringComparison.OrdinalIgnoreCase))
                continue;
            if (text.Contains("/Skins/", StringComparison.OrdinalIgnoreCase))
                continue;
            var leaf = text[(text.LastIndexOf('/') + 1)..];
            if (!IsSiblingTexture(leaf))
                continue;
            IPackage? package;
            try
            {
                package = FindPackage(provider, originalPath);
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "texture package skipped {Path}", text);
                continue;
            }

            var texture = package?.GetExports().OfType<UTexture>().FirstOrDefault();
            if (texture is null)
                continue;
            try
            {
                session.Add(texture);
                queued++;
                Log.Information("queue texture {Name}", texture.Name);
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "texture export skipped {Name}", texture.Name);
            }
        }

        return queued;
    }

    private static void QueueVisualParts(AbstractVfsFileProvider provider, string meshPath, List<(IPackage Package, string Path)> packages)
    {
        var normalized = CanonicalGamePackagePath(meshPath);
        var slash = normalized.LastIndexOf('/');
        if (slash < 0) return;
        var directory = normalized[..(slash + 1)];
        foreach (var file in provider.Files.Values)
        {
            if (file is not FIoStoreEntry { IsPackageData: true, IsOptionalPackage: false } entry) continue;
            var path = entry.PathWithoutExtension.Replace('\\', '/');
            var canonical = CanonicalGamePackagePath(path);
            var leaf = path[(path.LastIndexOf('/') + 1)..];
            if (!canonical.StartsWith(directory, StringComparison.OrdinalIgnoreCase) ||
                !(leaf.StartsWith("DA_VP_", StringComparison.OrdinalIgnoreCase) || leaf.StartsWith("Part_Head_", StringComparison.OrdinalIgnoreCase)) ||
                !string.Equals(canonical[..(canonical.LastIndexOf('/') + 1)], directory, StringComparison.OrdinalIgnoreCase)) continue;
            var package = FindPackage(provider, path);
            if (package is not null) packages.Add((package, path));
        }
    }

    private static int QueueMasterMaterialTextures(
        UMaterialInterface material,
        ExportSession session,
        HashSet<string> visitedMaterials)
    {
        // AllLayersNoRef preserves the authored MI parameter names. Master
        // materials can also reference textures directly in their expressions,
        // outside those parameters (glass/snow, breakup, overlays, etc.). Export
        // that dependency closure separately without changing shader metadata.
        var queued = 0;
        for (UUnrealMaterial? cursor = material;
             cursor is not null && visitedMaterials.Add(cursor.GetPathName());
             cursor = (cursor as UMaterialInstance)?.Parent)
        {
            if (cursor is not UMaterial master) continue;
            foreach (var texture in master.ReferencedTextures)
            {
                // Cooked lists can retain empty references for inactive layers.
                if (texture is null) continue;
                try
                {
                    var before = session.TotalQueued;
                    session.Add(texture);
                    queued += session.TotalQueued - before;
                }
                catch (Exception ex)
                {
                    Log.Warning(ex, "Master material texture export skipped {Material} {Texture}", master.Name, texture.Name);
                }
            }
        }
        return queued;
    }

    private static int QueueMeshMasterTextures(
        UObject mesh,
        ExportSession session,
        HashSet<string> visitedMaterials)
    {
        var materials = mesh switch
        {
            USkinnedAsset skinned => skinned.Materials,
            UStaticMesh staticMesh => staticMesh.Materials,
            _ => []
        };
        var queued = 0;
        foreach (var reference in materials)
        {
            try
            {
                if (reference?.Load<UMaterialInterface>() is { } material)
                    queued += QueueMasterMaterialTextures(material, session, visitedMaterials);
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "Master material dependencies skipped for {Mesh}", mesh.Name);
            }
        }
        return queued;
    }

    private static int ExportQueued(AbstractVfsFileProvider provider, List<string> paths, string outDir)
    {
        var options = new ExportOptions(
            meshFormat: EMeshFormat.UEFormat,
            meshQuality: EMeshQuality.Highest,
            naniteMeshFormat: ENaniteMeshFormat.NaniteFirst,
            materialDepth: EMaterialDepth.AllLayersNoRef,
            exportMaterials: true,
            exportMorphTargets: true);
        var session = new ExportSession { MaxDegreeOfParallelism = Workers };
        var queued = 0;
        var clothMetadataFailed = false;
        var jsonPackages = new List<(IPackage Package, string Path)>();
        var visitedMaterials = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        ExpandFromFiles(provider, paths);

        foreach (var path in paths)
        {
            var package = FindPackage(provider, path);
            if (package is null)
            {
                Log.Error(
                    "load failed {Path} project={Project}",
                    NormalizePackagePath(path),
                    provider.ProjectName);
                continue;
            }

            UObject? mesh = package.GetExports().OfType<USkinnedAsset>().FirstOrDefault();
            mesh ??= package.GetExports().OfType<UStaticMesh>().FirstOrDefault();
            if (mesh is not null)
            {
                session.Add(mesh);
                queued++;
                Log.Information("queue {Type} {Name}", mesh.ExportType, mesh.Name);
                queued += QueueSiblingTextures(provider, path, session);
                queued += QueueMeshMasterTextures(mesh, session, visitedMaterials);
                var metadataStart = jsonPackages.Count;
                QueueVisualParts(provider, path, jsonPackages);
                if (mesh is USkinnedAsset skinned)
                {
                    var wrapPath = ClothWrapExport.WrapMesh(jsonPackages.Skip(metadataStart).Select(value => value.Package), path);
                    if (!ClothWrapExport.Write(package, skinned, CookedMountPath(NormalizePackagePath(path)).Trim('/'), outDir, wrapPath))
                        clothMetadataFailed = true;
                    if (!string.IsNullOrWhiteSpace(wrapPath) && FindPackage(provider, wrapPath) is { } wrapPackage &&
                        wrapPackage.GetExports().OfType<USkinnedAsset>().FirstOrDefault() is { } wrapMesh)
                    {
                        session.Add(wrapMesh);
                        queued++;
                        if (!ClothWrapExport.Write(wrapPackage, wrapMesh, CookedMountPath(NormalizePackagePath(wrapPath)).Trim('/'), outDir))
                            clothMetadataFailed = true;
                    }
                }
                continue;
            }

            var material = package.GetExports().OfType<UMaterialInterface>().FirstOrDefault();
            if (material is not null)
            {
                try
                {
                    session.Add(material);
                    queued++;
                    queued += QueueMasterMaterialTextures(material, session, visitedMaterials);
                    Log.Information("queue {Type} {Name}", material.ExportType, material.Name);
                    continue;
                }
                catch (Exception ex)
                {
                    Log.Warning(ex, "material export skipped {Name}", material.Name);
                }
            }

            var texture = package.GetExports().OfType<UTexture>().FirstOrDefault();
            if (texture is not null)
            {
                session.Add(texture);
                queued++;
                Log.Information("queue texture {Name}", texture.Name);
                continue;
            }

            jsonPackages.Add((package, path));
            queued++;
            Log.Information("queue json {Path}", NormalizePackagePath(path));
        }

        if (queued == 0)
        {
            Log.Error("Nothing queued.");
            return 1;
        }

        foreach (var (package, _) in jsonPackages)
        {
            var document = JObject.FromObject(new { Exports = package.GetExports().ToList() });
            foreach (var value in document.Descendants().OfType<JValue>())
            {
                if (value.Value is not string reference || !reference.Contains('/')) continue;
                var leaf = reference.Split('/').Last().Split('.')[0];
                if (!leaf.StartsWith("MI_", StringComparison.OrdinalIgnoreCase)) continue;
                var material = FindPackage(provider, reference)?.GetExports().OfType<UMaterialInterface>().FirstOrDefault();
                if (material is not null)
                {
                    session.Add(material);
                    queued += QueueMasterMaterialTextures(material, session, visitedMaterials);
                }
                else Log.Warning("Authored material not mounted {Path}", reference);
            }
        }

        var wrote = false;
        if (session.HasQueuedItems)
        {
            var results = session.RunAsync(outDir, options).GetAwaiter().GetResult();
            foreach (var result in results)
            {
                if (result.Success)
                {
                    wrote = true;
                    Log.Information("ok {Path}", result.ObjectPath);
                }
                else
                    Log.Error(result.Error, "export error {Path}", result.ObjectPath);
            }
        }

        foreach (var (package, path) in jsonPackages)
        {
            if (WritePackageJson(package, path, outDir))
                wrote = true;
        }

        Log.Information("Done queued={Queued} json={Json}", queued, jsonPackages.Count);
        return wrote && !clothMetadataFailed ? 0 : 1;
    }

    private static bool WritePackageJson(IPackage package, string packagePath, string outDir)
    {
        try
        {
            var rel = NormalizePackagePath(packagePath).Replace('\\', '/').Trim('/');
            if (rel.StartsWith("Game/", StringComparison.OrdinalIgnoreCase))
                rel = "PioneerGame/Content/" + rel["Game/".Length..];
            var file = Path.Combine(outDir, rel.Replace('/', Path.DirectorySeparatorChar) + ".json");
            var directory = Path.GetDirectoryName(file);
            if (!string.IsNullOrEmpty(directory))
                Directory.CreateDirectory(directory);
            var exports = package.GetExports().ToList();
            var doc = new JObject { ["Exports"] = JArray.FromObject(exports) };
            File.WriteAllText(file, doc.ToString(Formatting.Indented));
            Log.Information("json {Name}", Path.GetFileName(file));
            return true;
        }
        catch (Exception ex)
        {
            Log.Error(ex, "json failed {Path}", NormalizePackagePath(packagePath));
            return false;
        }
    }

    private static CUE4Parse.UE4.Assets.IPackage? FindPackage(AbstractVfsFileProvider provider, string path)
    {
        var pkgPath = NormalizePackagePath(path);
        if (provider.TryLoadPackage(pkgPath, out var package) && package is not null)
            return package;

        var alt = CookedMountPath(pkgPath);
        if (!string.Equals(alt, pkgPath, StringComparison.OrdinalIgnoreCase)
            && provider.TryLoadPackage(alt, out package))
            return package;
        // A shipping mount may expose the same logical /Game package under
        // RemappedPlugins/Content. Resolve that exact path, never a basename.
        var cooked = CookedMountPath(pkgPath).TrimStart('/');
        const string prefix = "PioneerGame/Content/";
        if (cooked.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            var remapped = "RemappedPlugins/Content/" + cooked[prefix.Length..];
            if (provider.TryLoadPackage(remapped, out package) && package is not null) return package;
        }
        var aliases = new HashSet<string>(PakIndex.Candidates(pkgPath), StringComparer.OrdinalIgnoreCase);
        foreach (var key in provider.Files.Keys)
        {
            if (!key.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || !aliases.Contains(PakIndex.Normalize(key))) continue;
            if (provider.TryLoadPackage(key[..^".uasset".Length], out package) && package is not null) return package;
        }
        return null;
    }

    private static TheiaFileProvider CreateShell(string gameDir)
    {
        var version = new VersionContainer(EGame.GAME_ArcRaiders);
        return new TheiaFileProvider(gameDir, SearchOption.AllDirectories, version);
    }

    private static int MountEverything(TheiaFileProvider provider)
    {
        provider.Initialize();
        SubmitArcKey(provider);
        provider.PostMount();
        return provider.MountedVfs.Count;
    }

    private static bool MountNames(TheiaFileProvider provider, string gameDir, IEnumerable<string> names)
    {
        var any = false;
        foreach (var name in names)
        {
            var path = Path.Combine(gameDir, name);
            if (!File.Exists(path))
            {
                Log.Warning("Missing container {Name}", name);
                return false;
            }

            provider.RegisterVfs(new FileInfo(path));
            any = true;
        }

        if (any)
            SubmitArcKey(provider);
        return true;
    }

    private static void SubmitArcKey(AbstractVfsFileProvider provider)
    {
        var keyBytes = new byte[32];
        Buffer.BlockCopy(TheiaDecryptor.ArcRaidersKey, 0, keyBytes, 0, 32);
        provider.SubmitKey(new FGuid(), new FAesKey(keyBytes));
    }

    private static void PrepareCompression(string gameDir)
    {
        if (_compressionReady)
            return;

        ZlibHelper.Initialize();
        var oodle = FindGameOodle(gameDir);
        if (oodle is not null)
        {
            Log.Information("Oodle {File}", Path.GetFileName(oodle));
            OodleHelper.Initialize(oodle);
        }
        else
        {
            // This shipping build links Oodle into the game exe, so there is no loose oo2core to load.
            // Fetch the matching OodleUE build beside the helper on first run. It is not shipped in the addon.
            var beside = Path.Combine(AppContext.BaseDirectory, "oodle-data-shared.dll");
            Log.Information("No game oo2core dll; OodleUE {File}", Path.GetFileName(beside));
            OodleHelper.Initialize(beside);
        }

        // BC7 (CR / NOM / ColorMask) goes through Detex. DXT decodes without it, so a
        // missing init leaves item and clothing meshes with no maps.
        var detexPath = Path.Combine(AppContext.BaseDirectory, DetexHelper.DLL_NAME);
        if (DetexHelper.LoadDll(detexPath))
            DetexHelper.Initialize(detexPath);
        else
            Log.Warning("Detex.dll did not load; BC7 textures will be skipped");

        _compressionReady = true;
    }

    private static string? FindGameOodle(string gameDir)
    {
        string[] names = ["oo2core_9_win64.dll", "oodle-data-shared.dll"];
        string[] relatives =
        [
            Path.Combine("Binaries", "Win64"),
            Path.Combine("PioneerGame", "Binaries", "Win64"),
            Path.Combine("Engine", "Binaries", "ThirdParty", "Oodle", "Win64"),
            Path.Combine("Engine", "Binaries", "Win64"),
        ];

        var roots = new List<string>();
        if (!string.IsNullOrWhiteSpace(gameDir))
            roots.Add(gameDir);
        var dir = Directory.GetParent(gameDir);
        for (var i = 0; i < 5 && dir is not null; i++, dir = dir.Parent)
            roots.Add(dir.FullName);

        foreach (var root in roots)
        {
            foreach (var rel in relatives)
            {
                foreach (var name in names)
                {
                    var candidate = Path.Combine(root, rel, name);
                    if (File.Exists(candidate))
                        return candidate;
                }
            }

            foreach (var name in names)
            {
                var candidate = Path.Combine(root, name);
                if (File.Exists(candidate))
                    return candidate;
            }
        }

        return null;
    }

    private static string CanonicalGamePackagePath(string path)
    {
        var normalized = NormalizePackagePath(path).Replace('\\', '/').Trim('/');
        foreach (var prefix in new[] { "PioneerGame/Content/", "RemappedPlugins/Content/", "Game/" })
            if (normalized.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                return "PioneerGame/Content/" + normalized[prefix.Length..];
        return normalized;
    }

    private static string CookedMountPath(string path)
    {
        var p = path.Replace('\\', '/');
        if (p.StartsWith('/'))
            p = p[1..];
        if (p.StartsWith("Game/", StringComparison.OrdinalIgnoreCase))
            return "PioneerGame/Content/" + p["Game/".Length..];
        return path;
    }

    private static string NormalizePackagePath(string path)
    {
        var p = path.Replace('\\', '/').Trim();
        if (p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase))
            p = p[..^".uasset".Length];
        if (p.Contains('.', StringComparison.Ordinal))
            p = p[..p.IndexOf('.')];
        return p;
    }

    private static void PrintHelp()
    {
        var text = """
            ArcPak.Export extract --game-dir <Paks> --usmap <file.usmap> --out <cache> --path </Game/...> [--index <pak-index.bin>] [--index-dir <dir>]
            ArcPak.Export reindex --game-dir <Paks> --index-dir <dir> [--out <cache>] [--usmap <file.usmap>]
            ArcPak.Export index --game-dir <Paks> --out <pak-index.bin>

            Game is fixed to Arc Raiders UE 5.7. AES is built in. Repeat --path for more meshes.
            A folder path exports the SK_, SM_, and DA_OI_ packages inside it.
            Writes .uemodel, FModel-style package JSON, and PNG under --out.
            Reindex stores those packages plus T_UI_Icon_ preview textures, and writes the preview PNGs into --out.

            --index is a package-to-container map. Repeat it. An index is used when its Steam
            build id matches appmanifest, or when each .utoc name, length, and content hash
            still matches. Otherwise reindex mounts every container, reads the manifest build
            id, and writes pak-index-<buildid>.bin. Later imports of that build skip the full mount.
            """;
        Console.OutputEncoding = Encoding.UTF8;
        Console.WriteLine(text);
    }
}
