using System.Diagnostics;
using System.Text;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.GameTypes.Theia.Encryption;
using CUE4Parse.GameTypes.Theia.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.IO.Objects;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Versions;
using CUE4Parse.Compression;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Exporters;
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
    // Default min(16, max(4, cores/2)); ARCPAK_WORKERS=<n> overrides (1..64).
    internal static readonly int WorkerCount = ResolveWorkers();
    private static int ResolveWorkers()
    {
        if (int.TryParse(Environment.GetEnvironmentVariable("ARCPAK_WORKERS"), out var forced) && forced is >= 1 and <= 64) return forced;
        return Math.Min(16, Math.Max(4, Environment.ProcessorCount / 2));
    }
    private static int Workers => WorkerCount;
#if ARCPAK_FPNG
    // ARCPAK_FAST_PNG=0 restores the Skia PNG encoder (fpng otherwise; Skia fallback when fpng.dll is absent).
    private static readonly CUE4Parse_Conversion.Options.EPngEncoder ArcPakPngEncoder =
        Environment.GetEnvironmentVariable("ARCPAK_FAST_PNG") == "0"
            ? CUE4Parse_Conversion.Options.EPngEncoder.Skia
            : CUE4Parse_Conversion.Options.EPngEncoder.Fpng;
#endif
    private static bool _compressionReady;

    public static int Main(string[] args)
    {
        Log.Logger = new LoggerConfiguration()
            .MinimumLevel.Information()
            .WriteTo.Console()
            .CreateLogger();

        AppDomain.CurrentDomain.FirstChanceException += (_, e) =>
        {
            if (e.Exception is OutOfMemoryException) Interlocked.Increment(ref _outOfMemorySeen);
        };
        if (args.Length > 0 && args[0] == "serve")
            return Serve();
        try
        {
            using var dirLocks = OutputDirLock.AcquireForArgs(args);
            var code = RunCommand(args);
            if (code == 0 && Volatile.Read(ref _outOfMemorySeen) != 0)
            {
                Log.Error("Out of memory during export; output is incomplete");
                return 1;
            }
            return code;
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

    private static int _outOfMemorySeen;

    internal static bool Serving;
    private static readonly Dictionary<string, FileUsmapTypeMappingsProvider> _mappings = new(StringComparer.OrdinalIgnoreCase);

    internal static FileUsmapTypeMappingsProvider Mappings(string usmap)
    {
        if (!Serving)
            return new FileUsmapTypeMappingsProvider(usmap);
        lock (_mappings)
        {
            if (!_mappings.TryGetValue(usmap, out var hit))
                _mappings[usmap] = hit = new FileUsmapTypeMappingsProvider(usmap);
            return hit;
        }
    }

    // Serve-only provider reuse. Key = verb kind + game dir + usmap + container file
    // signature (+ prepared package set for selective mounts). Requests are serial;
    // the lock keeps the cache consistent if that ever changes.
    private const int MaxCachedProviders = 4;
    private static readonly object _providerLock = new();
    private static readonly List<(string Key, AbstractVfsFileProvider Provider)> _providers = new();

    internal static string GameSignature(string gameDir)
    {
        var sb = new StringBuilder(Path.GetFullPath(gameDir)).Append('|');
        try
        {
            foreach (var file in new DirectoryInfo(gameDir).EnumerateFiles("*", SearchOption.TopDirectoryOnly)
                         .Where(f => f.Extension is ".utoc" or ".ucas" or ".pak" or ".sig")
                         .OrderBy(f => f.Name, StringComparer.OrdinalIgnoreCase))
                sb.Append(file.Name).Append(':').Append(file.Length).Append(':').Append(file.LastWriteTimeUtc.Ticks).Append(';');
        }
        catch (Exception) { sb.Append("unreadable"); }
        return sb.ToString();
    }

    private static string ProviderKey(string kind, string gameDir, string usmap, IEnumerable<string>? packages = null)
    {
        var sb = new StringBuilder(kind).Append('\n').Append(GameSignature(gameDir)).Append('\n').Append(Path.GetFullPath(usmap));
        if (packages is not null)
            foreach (var p in packages.OrderBy(p => p, StringComparer.OrdinalIgnoreCase))
                sb.Append('\n').Append(p);
        return sb.ToString();
    }

    private static bool TryCachedProvider(string key, out AbstractVfsFileProvider provider)
    {
        provider = null!;
        if (!Serving) return false;
        lock (_providerLock)
        {
            var at = _providers.FindIndex(e => e.Key == key);
            if (at < 0) return false;
            var hit = _providers[at];
            _providers.RemoveAt(at);
            _providers.Add(hit);
            provider = hit.Provider;
        }
        Log.Information("Reusing mounted provider ({Files} files)", provider.Files.Count);
        return true;
    }

    private static AbstractVfsFileProvider RememberProvider(string key, AbstractVfsFileProvider provider)
    {
        if (!Serving) return provider;
        lock (_providerLock)
        {
            while (_providers.Count >= MaxCachedProviders)
            {
                RetireProviderLocked(_providers[0].Provider);
                _providers.RemoveAt(0);
            }
            _providers.Add((key, provider));
        }
        return provider;
    }

    private static bool IsCachedProvider(AbstractVfsFileProvider provider)
    {
        lock (_providerLock)
            return _providers.Any(e => ReferenceEquals(e.Provider, provider));
    }

    private static void DropCachedProviders()
    {
        lock (_providerLock)
        {
            foreach (var e in _providers) RetireProviderLocked(e.Provider);
            _providers.Clear();
        }
    }

    // Requests in flight (serve). With one request, a dropped provider is disposed at once
    // (old behaviour); with several, another request may still read it, so disposal waits
    // until the last in-flight request ends.
    private static int _inFlight;
    private static readonly HashSet<AbstractVfsFileProvider> _retired = new(ReferenceEqualityComparer.Instance);

    private static void RetireProviderLocked(AbstractVfsFileProvider provider)
    {
        if (_inFlight <= 1) provider.Dispose();
        else _retired.Add(provider);
    }

    private static void ReleaseUncachedProvider(AbstractVfsFileProvider provider)
    {
        lock (_providerLock)
            if (!_providers.Any(e => ReferenceEquals(e.Provider, provider)))
                RetireProviderLocked(provider);
    }

    private static void BeginRequest() { lock (_providerLock) _inFlight++; }

    private static void EndRequest()
    {
        lock (_providerLock)
        {
            if (--_inFlight > 0) return;
            foreach (var p in _retired) p.Dispose();
            _retired.Clear();
        }
    }

    /// <summary>Console.Out/Error target in concurrent serve: each request's writes go to its own buffer.</summary>
    private sealed class RequestRouter : TextWriter
    {
        internal static readonly AsyncLocal<TextWriter?> Current = new();
        private readonly TextWriter _fallback;
        internal RequestRouter(TextWriter fallback) => _fallback = fallback;
        private TextWriter Target => Current.Value ?? _fallback;
        public override Encoding Encoding => Encoding.UTF8;
        public override void Write(char value) => Target.Write(value);
        public override void Write(string? value) => Target.Write(value);
        public override void Write(char[] buffer, int index, int count) => Target.Write(buffer, index, count);
        public override void WriteLine(string? value) => Target.WriteLine(value);
        public override void WriteLine() => Target.WriteLine();
        public override void Flush() => Target.Flush();
    }

    // JSON lines: in {"args":[...]}; out {"rc":N,"log":"..."}. Logs go to a per-request buffer.
    // A request with "id" (string or number) runs concurrently with other id requests; its reply
    // echoes the id ({"id":..,"rc":N,"log":".."}) and replies may arrive in any order. A request
    // without "id" waits for all in-flight requests, then runs alone (old sequential behaviour).
    private static int Serve()
    {
        Serving = true;
        var stdout = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        var stdin = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false));
        var stdoutLock = new object();
        var idle = new object();
        var concurrent = 0;
        RequestRouter? router = null;
        stdout.WriteLine("{\"ready\":true}");
        string? line;
        while ((line = stdin.ReadLine()) != null)
        {
            if (line.Length == 0) continue;
            System.Text.Json.Nodes.JsonNode? id = null;
            try
            {
                using var probe = System.Text.Json.JsonDocument.Parse(line);
                if (probe.RootElement.ValueKind == System.Text.Json.JsonValueKind.Object
                    && probe.RootElement.TryGetProperty("id", out var idEl)
                    && idEl.ValueKind is System.Text.Json.JsonValueKind.String or System.Text.Json.JsonValueKind.Number)
                    id = System.Text.Json.Nodes.JsonNode.Parse(idEl.GetRawText());
            }
            catch (Exception) { }

            if (id is null)
            {
                lock (idle) while (concurrent > 0) Monitor.Wait(idle);
                router = null;
                var reply = RunServeRequest(line, null, perRequestLogger: true);
                stdout.WriteLine(reply);
                continue;
            }

            if (router is null)
            {
                router = new RequestRouter(TextWriter.Null);
                Console.SetOut(router);
                Console.SetError(router);
                Log.Logger = new LoggerConfiguration().MinimumLevel.Information().WriteTo.Console().CreateLogger();
            }
            lock (idle) concurrent++;
            var reqLine = line;
            var reqId = id;
            Task.Run(() =>
            {
                string reply;
                try { reply = RunServeRequest(reqLine, reqId, perRequestLogger: false); }
                catch (Exception ex)
                {
                    reply = new System.Text.Json.Nodes.JsonObject { ["id"] = reqId.DeepClone(), ["rc"] = 1, ["log"] = "ArcPak.Export failed: " + ex }.ToJsonString();
                }
                lock (stdoutLock) stdout.WriteLine(reply);
                lock (idle) { concurrent--; Monitor.PulseAll(idle); }
            });
        }
        lock (idle) while (concurrent > 0) Monitor.Wait(idle);
        return 0;
    }

    private static string RunServeRequest(string line, System.Text.Json.Nodes.JsonNode? id, bool perRequestLogger)
    {
        var buffer = TextWriter.Synchronized(new StringWriter());
        int code;
        if (perRequestLogger)
        {
            Console.SetOut(buffer);
            Console.SetError(buffer);
            Log.Logger = new LoggerConfiguration().MinimumLevel.Information().WriteTo.Console().CreateLogger();
        }
        else RequestRouter.Current.Value = buffer;
        BeginRequest();
        try
        {
            var oomAtStart = Volatile.Read(ref _outOfMemorySeen);
            try
            {
                using var req = System.Text.Json.JsonDocument.Parse(line);
                var args = req.RootElement.GetProperty("args").EnumerateArray().Select(e => e.GetString() ?? "").ToArray();
                using (OutputDirLock.AcquireForArgs(args))
                    code = RunCommand(args);
                if (code == 0 && Volatile.Read(ref _outOfMemorySeen) != oomAtStart) code = 1;
            }
            catch (Exception ex)
            {
                buffer.WriteLine("ArcPak.Export failed: " + ex);
                code = 1;
            }
            // A failed request may leave a provider half-read or memory-starved; remount next time.
            if (code != 0) DropCachedProviders();
        }
        finally
        {
            EndRequest();
            if (perRequestLogger) Log.CloseAndFlush();
            else RequestRouter.Current.Value = null;
        }
        var reply = new System.Text.Json.Nodes.JsonObject();
        if (id is not null) reply["id"] = id.DeepClone();
        reply["rc"] = code;
        reply["log"] = buffer.ToString();
        return reply.ToJsonString();
    }

    private static int RunCommand(string[] args)
    {
        {
            if (args.Length == 0 || args[0] is "-h" or "--help" or "help")
            {
                PrintHelp();
                return args.Length == 0 ? 1 : 0;
            }

            if (string.Equals(args[0], "material-metadata", StringComparison.OrdinalIgnoreCase))
                return Extract(args.Skip(1).ToArray(), materialMetadataOnly: true);
            if (string.Equals(args[0], "mesh-metadata", StringComparison.OrdinalIgnoreCase))
                return Extract(args.Skip(1).ToArray(), metadataOnly: true);
            if (string.Equals(args[0], "extract", StringComparison.OrdinalIgnoreCase))
                return Extract(args.Skip(1).ToArray());
            if (string.Equals(args[0], "index", StringComparison.OrdinalIgnoreCase))
                return BuildIndex(args.Skip(1).ToArray());
            if (string.Equals(args[0], "reindex", StringComparison.OrdinalIgnoreCase))
                return ReindexCommand(args.Skip(1).ToArray());
            if (string.Equals(args[0], "previews", StringComparison.OrdinalIgnoreCase))
                return PreviewsCommand(args.Skip(1).ToArray());
            if (string.Equals(args[0], "shader-records", StringComparison.OrdinalIgnoreCase))
                return ShaderRecordExport.Run(args.Skip(1).ToArray());
            if (string.Equals(args[0], "material-shadermaps", StringComparison.OrdinalIgnoreCase))
                return MaterialShaderMapExport.Run(args.Skip(1).ToArray());
            if (string.Equals(args[0], "texture-raw", StringComparison.OrdinalIgnoreCase))
                return TextureRawExport.Run(args.Skip(1).ToArray());
            if (string.Equals(args[0], "level-inputs", StringComparison.OrdinalIgnoreCase))
                return LevelInputsExport.Run(args.Skip(1).ToArray());
            if (string.Equals(args[0], "map-export", StringComparison.OrdinalIgnoreCase))
                return MapBundleExport.Run(args.Skip(1).ToArray());
            if (string.Equals(args[0], "map-catalog", StringComparison.OrdinalIgnoreCase))
                return MapCatalogExport.Run(args.Skip(1).ToArray());

            Log.Error("Unknown command '{Cmd}'. Expected: extract, index, reindex", args[0]);
            PrintHelp();
            return 1;
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

    // Cache T_UI_Icon_* previews from a saved/bundled index without reindexing.
    private static int PreviewsCommand(string[] args)
    {
        string gameDir = "", indexDir = "", cacheDir = "", usmap = "";
        var indexPaths = new List<string>();
        for (var i = 0; i < args.Length; i++)
        {
            var a = args[i];
            if (i + 1 >= args.Length)
                throw new ArgumentException($"Missing value after {a}");
            var v = args[++i];
            switch (a)
            {
                case "--game-dir": gameDir = v; break;
                case "--index-dir": indexDir = v; break;
                case "--index": indexPaths.Add(v); break;
                case "--out": cacheDir = v; break;
                case "--usmap": usmap = v; break;
                default: throw new ArgumentException($"Unknown argument {a}");
            }
        }
        if (string.IsNullOrWhiteSpace(gameDir) || !Directory.Exists(gameDir))
            throw new ArgumentException("Missing or absent --game-dir (PioneerGame/Content/Paks)");
        if (string.IsNullOrWhiteSpace(cacheDir))
            throw new ArgumentException("Missing --out");
        var timer = Stopwatch.StartNew();
        PrepareCompression(gameDir);
        var build = GameBuild.Read(gameDir);
        var index = SelectIndex(gameDir, indexDir, indexPaths, build);
        if (index is null || !index.IndexesPreviews)
        {
            Log.Error("previews: no matching v5+ pak index for build {Build}", build.BuildId);
            return 3;
        }
        var names = index.PreviewContainerNames().Concat(index.AlwaysMountNames()).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        using var provider = CreateShell(gameDir);
        if (!string.IsNullOrWhiteSpace(usmap))
            provider.MappingsContainer = Program.Mappings(usmap);
        if (names.Count == 0 || !MountNames(provider, gameDir, names))
        {
            Log.Error("previews: could not mount {Count} preview containers", names.Count);
            return 4;
        }
        CachePreviewIcons(provider, cacheDir);
        Log.Information("previews: containers={Count} ms={Ms}", names.Count, timer.ElapsedMilliseconds);
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
        using var provider = Reindex(gameDir, indexDir, build, usmap);
        try
        {
            CachePreviewIcons(provider, cacheDir);
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "Could not cache preview icons");
        }
        Log.Information(
            "Reindexed build={Build} file={File} files={Files}",
            build.IsKnown ? build.BuildId : "unknown",
            PakIndex.FileName(build),
            provider.Files.Count);
        return 0;
    }

    private static int Extract(string[] args, bool metadataOnly = false, bool materialMetadataOnly = false)
    {
        string gameDir = "";
        string usmap = "";
        string outDir = "";
        var paths = new List<string>();
        var indexPaths = new List<string>();
        var modelFiles = new List<string>();
        var materialFiles = new List<string>();
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
                case "--material-file":
                    materialFiles.Add(Next());
                    break;
                case "--model-file":
                    modelFiles.Add(Next());
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

        if (modelFiles.Count > 0 && (!metadataOnly || modelFiles.Count != paths.Count))
            throw new ArgumentException("--model-file requires one exact file per mesh-metadata --path");
        if (materialFiles.Count > 0 && (!materialMetadataOnly || materialFiles.Count != paths.Count))
            throw new ArgumentException("--material-file requires one exact file per material-metadata --path");
        Directory.CreateDirectory(outDir);
        if (string.IsNullOrWhiteSpace(indexDir))
            indexDir = outDir;
        Directory.CreateDirectory(indexDir);
        var setup = Stopwatch.StartNew();
        PrepareCompression(gameDir);
        var provider = (metadataOnly || materialMetadataOnly) ? OpenMetadataProvider(gameDir, usmap, paths, indexPaths, indexDir, materialTextureClosure: materialMetadataOnly)
            : OpenProvider(gameDir, usmap, paths, indexPaths, indexDir);
        Log.Information("Request setup {Ms} ms (compression, index, mount)", setup.ElapsedMilliseconds);
        try
        {
            if (materialMetadataOnly) return ExportMaterialMetadata(provider, paths, outDir, GameBuild.Read(gameDir), materialFiles, usmap);
            return metadataOnly ? ExportMeshMetadata(provider, paths, outDir, GameBuild.Read(gameDir), modelFiles)
                : ExportQueued(provider, paths, outDir, GameBuild.Read(gameDir), usmap);
        }
        finally
        {
            Log.Information("Request total {Ms} ms", setup.ElapsedMilliseconds);
            if (!Serving) { if (!IsCachedProvider(provider)) provider.Dispose(); }
            else ReleaseUncachedProvider(provider);
        }
    }

    private static AbstractVfsFileProvider OpenMetadataProvider(
        string gameDir, string usmap, List<string> paths, List<string> indexPaths, string indexDir, bool materialTextureClosure = false)
    {
        if (materialTextureClosure)
        {
            // Inherited textures can live outside the root MI's selective
            // container set. Full mount is read-only and metadata-only here;
            // no browser/index/preview writes and no image encoder runs.
            var fullKey = Serving ? ProviderKey("meta-full", gameDir, usmap) : "";
            if (TryCachedProvider(fullKey, out var cachedAll))
            {
                Log.Information("Material/texture metadata full dependency mount; no mesh/image encoding or preview/index writes");
                return cachedAll;
            }
            var all = CreateShell(gameDir);
            all.ReadShaderMaps = false;
            all.MappingsContainer = Program.Mappings(usmap);
            MountEverything(all);
            Log.Information("Material/texture metadata full dependency mount; no mesh/image encoding or preview/index writes");
            return RememberProvider(fullKey, all);
        }

        // Metadata warmup never generates previews, faux browser paths or new indexes.
        var index = SelectIndex(gameDir, indexDir, indexPaths, GameBuild.Read(gameDir));
        if (index is not null && index.TryPrepare(paths, out var prepared, out var hits))
        {
            // Only provider mount selection uses prepared aliases. Keep caller
            // request/model-file pairs in their original exact order.
            var selKey = Serving ? ProviderKey("meta-sel", gameDir, usmap, prepared) : "";
            if (TryCachedProvider(selKey, out var cachedSel))
            {
                Log.Information("Metadata-only selective mount; no preview/index writes");
                return cachedSel;
            }
            var selective = TrySelective(gameDir, usmap, prepared, index, hits);
            if (selective is not null)
            {
                selective.ReadShaderMaps = false;
                Log.Information("Metadata-only selective mount; no preview/index writes");
                return RememberProvider(selKey, selective);
            }
        }
        var metaFullKey = Serving ? ProviderKey("meta-full", gameDir, usmap) : "";
        if (TryCachedProvider(metaFullKey, out var cachedFull))
        {
            Log.Information("Metadata-only full mount; no preview/index writes");
            return cachedFull;
        }
        var full = CreateShell(gameDir);
        full.ReadShaderMaps = false;
        full.MappingsContainer = Program.Mappings(usmap);
        MountEverything(full);
        Log.Information("Metadata-only full mount; no preview/index writes");
        return RememberProvider(metaFullKey, full);
    }

    private static AbstractVfsFileProvider OpenProvider(
        string gameDir,
        string usmap,
        List<string> paths,
        List<string> indexPaths,
        string indexDir)
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
            var selKey = Serving ? ProviderKey("extract-sel", gameDir, usmap, prepared) : "";
            var reused = TryCachedProvider(selKey, out var cachedSel);
            var selective = reused ? cachedSel : TrySelective(gameDir, usmap, paths, index, hits);
            if (selective is not null)
            {
                Log.Information(
                    "Mounted {Files} files in {Ms} ms (selective)",
                    selective.Files.Count,
                    timer.ElapsedMilliseconds);
                if (prepared.Count != requested.Count)
                    Log.Information("Resolved {Count} package(s)", prepared.Count);
                return reused ? selective : RememberProvider(selKey, selective);
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
                shell.MappingsContainer = Program.Mappings(usmap);
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

        var full = Reindex(gameDir, indexDir, build, usmap);
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
        string? usmap)
    {
        Log.Information(
            "Reindex build={Build} depot={Depot}",
            build.IsKnown ? build.BuildId : "unknown",
            build.DepotManifest.Length > 0 ? build.DepotManifest : "unknown");
        var full = CreateShell(gameDir);
        if (!string.IsNullOrWhiteSpace(usmap))
            full.MappingsContainer = Program.Mappings(usmap);
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
            exportMorphTargets: false
#if ARCPAK_FPNG
            , pngEncoder: ArcPakPngEncoder
#endif
            );
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
        provider.MappingsContainer = Program.Mappings(usmap);
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
                if (seen.Add(PakIndex.PackageIdentity(path)))
                    next.Add(path);
                continue;
            }

            var prefixes = PakIndex.Candidates(path).ToList();
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

                if (!match || !seen.Add(PakIndex.PackageIdentity(without)))
                    continue;
                next.Add(without.Replace('\\', '/'));
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

    internal static Func<int> AttachTextureMetadataWriter(
        ExportSession session, AbstractVfsFileProvider provider, string outDir,
        GameBuild.Info build, string? usmap)
    {
        var mappingHash = TextureMetadataDocument.MappingHash(usmap);
        var failures = 0;
        // Session hooks cover explicit textures AND textures dynamically queued
        // by material/mesh dependencies. Run before any exporter/cache shortcut.
        session.OnExportBegin += objectPath =>
        {
            try
            {
                var dot = objectPath.LastIndexOf('.');
                var name = dot < 0 ? objectPath[(objectPath.LastIndexOf('/') + 1)..] : objectPath[(dot + 1)..];
                var texture = FindPackage(provider, objectPath)?.GetExports().OfType<UTexture>()
                    .SingleOrDefault(value => value.Name.Equals(name, StringComparison.Ordinal));
                if (texture is null) return;
                if (TextureMetadataExport.Write(texture, outDir, build.BuildId, build.DepotManifest, mappingHash))
                    Log.Information("native texture metadata {Path}", objectPath);
                if (TextureMipGuard.TopMipMissing(texture) is { } missing)
                {
                    Interlocked.Increment(ref failures);
                    Log.Error("texture top mip unavailable {Path}: {Reason}", objectPath, missing);
                }
            }
            catch (Exception ex)
            {
                Interlocked.Increment(ref failures);
                Log.Error(ex, "native texture metadata failed {Path}", objectPath);
            }
        };
        return () => Volatile.Read(ref failures);
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

    internal static int QueueMasterMaterialTextures(
        UMaterialInterface material,
        ExportSession session,
        HashSet<string> visitedMaterials,
        string outDir)
    {
        // AllLayersNoRef preserves the authored MI parameter names. Master
        // materials can also reference textures directly in their expressions,
        // outside those parameters (glass/snow, breakup, overlays, etc.). Export
        // that dependency closure separately without changing shader metadata.
        var queued = 0;
        for (UUnrealMaterial? cursor = material;
             cursor is not null && visitedMaterials.Add(cursor.GetPathName());
             cursor = (cursor as UMaterialInstance)?.Parent?.Load<UUnrealMaterial>())
        {
            // The conversion JSON flattens parameters and drops Parent/default
            // sampler names. Keep authoritative named data beside it so enemy
            // presets can inherit NMR/Detail without suffix-based guesses.
            var rel = MaterialMetadataExport.OutputStem(cursor);
            var sourceFile = Path.Combine(outDir, rel.Replace('/', Path.DirectorySeparatorChar) + ".arc-material.json");
            Directory.CreateDirectory(Path.GetDirectoryName(sourceFile)!);
            var source = MaterialMetadataExport.Source(cursor);
            File.WriteAllText(sourceFile, new JObject { ["Exports"] = new JArray(source) }.ToString(Formatting.Indented));
            if (cursor is not UMaterial master) continue;
            foreach (var reference in master.ReferencedTextures)
            {
                // Cooked lists can retain empty references for inactive layers.
                if (reference is null) continue;
                try
                {
                    var texture = reference.Load<UTexture>();
                    if (texture is null) continue;
                    var before = session.TotalQueued;
                    session.Add(texture);
                    queued += session.TotalQueued - before;
                }
                catch (Exception ex)
                {
                    Log.Warning(ex, "Master material texture export skipped {Material} {Texture}", master.Name, reference.Name);
                }
            }
        }
        return queued;
    }

    internal static int QueueMeshMasterTextures(
        UObject mesh,
        ExportSession session,
        HashSet<string> visitedMaterials,
        string outDir)
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
                    queued += QueueMasterMaterialTextures(material, session, visitedMaterials, outDir);
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "Master material dependencies skipped for {Mesh}", mesh.Name);
            }
        }
        return queued;
    }

    private static int ExportMaterialMetadata(AbstractVfsFileProvider provider, List<string> paths, string outDir, GameBuild.Info build, List<string> files, string usmap)
    {
        if (paths.Count > 4096) throw new ArgumentException("Material metadata request budget exceeded");
        var count = 0;
        var textureFacts = 0;
        var mappingHash = TextureMetadataDocument.MappingHash(usmap);
        for (var index = 0; index < paths.Count; index++)
        {
            var package = FindPackage(provider, paths[index]);
            var materials = package?.GetExports().OfType<UMaterialInterface>().ToArray() ?? [];
            var selected = MaterialMetadataExport.SelectIndex(materials.Select(value => value.Name).ToArray(),
                materials.Select(value => value.GetPathName()).ToArray(), paths[index]);
            var material = materials[selected];
            count += MaterialMetadataExport.Write(material, paths[index], outDir, files.Count > 0 ? files[index] : null, build);
            var cachedFile = files.Count > 0 ? files[index] : null;
            var sidecar = cachedFile is null
                ? Path.Combine(outDir,CanonicalGamePackagePath(paths[index]).Replace('/',Path.DirectorySeparatorChar)+".arc-material.json")
                : cachedFile.EndsWith(".arc-material.json",StringComparison.OrdinalIgnoreCase) ? cachedFile : Path.ChangeExtension(cachedFile,".arc-material.json");
            textureFacts += MaterialTextureMetadataExport.Write(material,
                TextureMetadataExport.LogicalPackage(CanonicalGamePackagePath(paths[index])),outDir,sidecar,build,mappingHash);

        }
        Log.Information("Material metadata finished roots={Roots} nodes={Nodes} compact_textures={TextureFacts}; no mesh/image encoding or preview exports", paths.Count, count, textureFacts);
        return 0;
    }

    private static int ExportMeshMetadata(AbstractVfsFileProvider provider, List<string> paths, string outDir, GameBuild.Info build, List<string> modelFiles)
    {
        var ok = true;
        for (var index = 0; index < paths.Count; index++)
        {
            var path = paths[index];
            var package = FindPackage(provider, path);
            var mesh = package?.GetExports().OfType<USkinnedAsset>().FirstOrDefault();
            if (mesh is null) { Log.Error("Native skinned mesh not found {Path}", path); ok = false; continue; }
            var snapshot = MeshMaterialSlotsExport.Capture(mesh, CookedMountPath(NormalizePackagePath(path)).Trim('/'), build);
            ok &= MeshMaterialSlotsExport.Write(snapshot, outDir, modelFiles.Count > 0 ? modelFiles[index] : null);
        }
        return ok ? 0 : 1;
    }

    private static int ExportQueued(AbstractVfsFileProvider provider, List<string> paths, string outDir, GameBuild.Info build, string usmap)
    {
        var options = new ExportOptions(
            meshFormat: EMeshFormat.UEFormat,
            meshQuality: EMeshQuality.Highest,
            naniteMeshFormat: ENaniteMeshFormat.NaniteFirst,
            materialDepth: EMaterialDepth.AllLayersNoRef,
            exportMaterials: true,
            exportMorphTargets: true
#if ARCPAK_FPNG
            , pngEncoder: ArcPakPngEncoder
#endif
            );
        var session = new ExportSession { MaxDegreeOfParallelism = Workers };
        var animSession = new ExportSession { MaxDegreeOfParallelism = Workers };
        var textureMetadataFailures = AttachTextureMetadataWriter(session, provider, outDir, build, usmap);
        var queued = 0;
        var requiredExports = new RequiredExportStatus();
        var clothMetadataFailed = false;
        var materialSlotSnapshots = new List<JObject>();
        var materialModelFiles = new Dictionary<JObject, string>();
        var jsonPackages = new List<(IPackage Package, string Path)>();
        var visitedMaterials = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        ExpandFromFiles(provider, paths);
        // Load root packages (and their exports) in parallel; queueing below stays sequential in request order.
        var preloaded = new CUE4Parse.UE4.Assets.IPackage?[paths.Count];
        var preload = Stopwatch.StartNew();
        Parallel.For(0, paths.Count, new ParallelOptions { MaxDegreeOfParallelism = Workers }, index =>
        {
            try { preloaded[index] = FindPackage(provider, paths[index]); _ = preloaded[index]?.GetExports().Count(); }
            catch { preloaded[index] = null; }
        });
        Log.Information("Root package preload {Count} in {Ms} ms", paths.Count, preload.ElapsedMilliseconds);

        for (var pathIndex = 0; pathIndex < paths.Count; pathIndex++)
        {
            var path = paths[pathIndex];
            var package = preloaded[pathIndex] ?? FindPackage(provider, path);
            if (package is null)
            {
                requiredExports.Observe(false);
                Log.Error(
                    "load failed {Path} project={Project}",
                    NormalizePackagePath(path),
                    provider.ProjectName);
                continue;
            }

            var anim = package.GetExports().OfType<UAnimSequence>().FirstOrDefault();
            if (anim is not null)
            {
                // Poses for Blender: ActorX .psa (the UEFormat mesh session cannot write PSA)
                animSession.Add(anim);
                queued++;
                Log.Information("queue anim {Name}", anim.Name);
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
                queued += QueueMeshMasterTextures(mesh, session, visitedMaterials, outDir);
                var metadataStart = jsonPackages.Count;
                QueueVisualParts(provider, path, jsonPackages);
                if (mesh is USkinnedAsset skinned)
                {
                    materialSlotSnapshots.Add(MeshMaterialSlotsExport.Capture(skinned, CookedMountPath(NormalizePackagePath(path)).Trim('/'), build));
                    var wrapPath = ClothWrapExport.WrapMesh(jsonPackages.Skip(metadataStart).Select(value => value.Package), path);
                    if (!ClothWrapExport.Write(package, skinned, CookedMountPath(NormalizePackagePath(path)).Trim('/'), outDir, wrapPath))
                        clothMetadataFailed = true;
                    if (!string.IsNullOrWhiteSpace(wrapPath) && FindPackage(provider, wrapPath) is { } wrapPackage &&
                        wrapPackage.GetExports().OfType<USkinnedAsset>().FirstOrDefault() is { } wrapMesh)
                    {
                        session.Add(new SkinnedAssetExporter(wrapMesh, includeDisabledSections: true));
                        materialSlotSnapshots.Add(MeshMaterialSlotsExport.Capture(wrapMesh, CookedMountPath(NormalizePackagePath(wrapPath)).Trim('/'), build));
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
                    queued += QueueMasterMaterialTextures(material, session, visitedMaterials, outDir);
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
                    queued += QueueMasterMaterialTextures(material, session, visitedMaterials, outDir);
                }
                else Log.Warning("Authored material not mounted {Path}", reference);
            }
        }

        var wrote = false;
        if (animSession.HasQueuedItems)
        {
            var animOptions = new ExportOptions(meshFormat: EMeshFormat.ActorX);
            foreach (var result in animSession.RunAsync(outDir, animOptions).GetAwaiter().GetResult())
            {
                requiredExports.Observe(result.Success);
                if (result.Success) { wrote = true; Log.Information("ok {Path}", result.ObjectPath); }
                else Log.Error(result.Error, "export error {Path}", result.ObjectPath);
            }
        }
        if (session.HasQueuedItems)
        {
            var results = session.RunAsync(outDir, options).GetAwaiter().GetResult();
            foreach (var result in results)
            {
                requiredExports.Observe(result.Success);
                if (result.Success)
                {
                    wrote = true;
                    Log.Information("ok {Path}", result.ObjectPath);
                    foreach (var snapshot in materialSlotSnapshots.Where(snapshot => MeshMaterialSlotsExport.SameSource(snapshot, result.ObjectPath)))
                    {
                        var models = result.DiskFilePaths?.Where(file => file.EndsWith(".uemodel", StringComparison.OrdinalIgnoreCase)).ToArray() ?? [];
                        if (models.Length == 1) materialModelFiles[snapshot] = models[0];
                        else Log.Error("Expected one exact UEModel export result for {Path}", result.ObjectPath);
                    }
                }
                else
                    Log.Error(result.Error, "export error {Path}", result.ObjectPath);
            }
        }

        foreach (var snapshot in materialSlotSnapshots)
        {
            if (!materialModelFiles.TryGetValue(snapshot, out var exportedModel))
            { Log.Error("Native slot metadata has no successful exact UEModel export result"); clothMetadataFailed = true; continue; }
            if (!MeshMaterialSlotsExport.Write(snapshot, outDir, exportedModel)) clothMetadataFailed = true;
        }

        foreach (var (package, path) in jsonPackages)
        {
            if (WritePackageJson(package, path, outDir))
                wrote = true;
            else requiredExports.Observe(false);
        }

        Log.Information("Done queued={Queued} json={Json} required_failed={RequiredFailed}", queued, jsonPackages.Count, requiredExports.Failures);
        return requiredExports.ExitCode(wrote, !clothMetadataFailed && textureMetadataFailures() == 0);
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

    internal static CUE4Parse.UE4.Assets.IPackage? FindPackage(AbstractVfsFileProvider provider, string path)
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
        // A mounted plugin package (EmbarkScript/ClothingAssetTemplates/M_Character_ClothMaterial) is cooked
        // under RemappedPlugins/<Plugin>/Content; its logical /<Plugin>/ form also resolves.
        var parts = cooked.Split('/', 2);
        if (parts.Length == 2 && !parts[1].StartsWith("Content/", StringComparison.OrdinalIgnoreCase) &&
            !new[] { "PioneerGame", "RemappedPlugins", "Engine", "Game" }.Contains(parts[0], StringComparer.OrdinalIgnoreCase))
        {
            if (provider.TryLoadPackage("/" + cooked, out package) && package is not null) return package;
            if (provider.TryLoadPackage("RemappedPlugins/" + parts[0] + "/Content/" + parts[1], out package) && package is not null) return package;
        }
        var aliases = new HashSet<string>(PakIndex.Candidates(pkgPath), StringComparer.OrdinalIgnoreCase);
        foreach (var key in provider.Files.Keys)
        {
            if (!key.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || !aliases.Contains(PakIndex.Normalize(key))) continue;
            if (provider.TryLoadPackage(key[..^".uasset".Length], out package) && package is not null) return package;
        }
        return null;
    }

    internal static TheiaFileProvider CreateShell(string gameDir)
    {
        var version = new VersionContainer(EGame.GAME_ArcRaiders);
        var provider = new TheiaFileProvider(gameDir, SearchOption.AllDirectories, version, StringComparer.OrdinalIgnoreCase);
        // CUE4Parse maps /Game to <ProjectName>/Content and names the project after
        // a .uproject, else the first non-Engine path in parallel mount order. The paks
        // carry no .uproject and pakchunk0 starts with RemappedPlugins/AIScript/, so
        // some runs wrote /Game under RemappedPlugins/Content and lost soft texture paths.
        const string projectFile = "PioneerGame/PioneerGame.uproject";
        provider.Files.AddFiles(new Dictionary<string, GameFile>(provider.PathComparer)
        {
            [projectFile] = new StreamedGameFile(projectFile, Stream.Null, version),
        });
        return provider;
    }

    internal static int MountEverything(TheiaFileProvider provider)
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

    internal static void PrepareCompression(string gameDir)
    {
        if (_compressionReady)
            return;

        var zlibPath = Path.Combine(AppContext.BaseDirectory, ZlibHelper.DllName);
        ZlibHelper.Initialize(File.Exists(zlibPath) ? zlibPath : null);
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
            ArcPak.Export map-export --game-dir <Paks> --usmap <file.usmap> --path </Game/Maps/Map> --out <revision directory> [--traversal-only]
            ArcPak.Export map-catalog --game-dir <Paks> --out <catalog.json> [--usmap <file.usmap>] [--index <index.json>]
            ArcPak.Export material-metadata --game-dir <Paks> --usmap <file.usmap> --out <cache> --path <exact MI package> [--material-file <exact cached MI.json>]
            ArcPak.Export mesh-metadata --game-dir <Paks> --usmap <file.usmap> --out <existing cache> --path <exact mesh package>
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
