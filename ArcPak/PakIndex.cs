using System.Text;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.UE4.IO.Objects;

namespace ArcPak;

/// <summary>
/// Maps a package path to the .utoc that holds it.
/// The file stores each container's length and content hash, plus the Steam
/// build id and depot manifest id the map was built from.
/// A different build, or hashes that do not match, is rebuilt once.
/// </summary>
internal sealed class PakIndex
{
    internal const uint Version = 6;
    private const ulong FnvOffset = 14695981039346656037UL;
    private const ulong FnvPrime = 1099511628211UL;

    internal readonly record struct ContainerStamp(string Name, long Length, ulong Hash);
    internal readonly record struct PathHit(ushort Container, ulong PackageId);
    internal readonly record struct StoredPath(string Path, ushort Container, ulong PackageId);

    internal ContainerStamp[] Containers { get; }
    internal string BuildId { get; }
    internal string DepotManifest { get; }
    internal int PackageCount { get; }

    private readonly Dictionary<ulong, ushort> _packageToContainer;
    private readonly Dictionary<ulong, PathHit> _pathToContainer;
    private readonly List<StoredPath> _storedPaths;
    private readonly uint _format;

    internal bool HasPaths => _storedPaths.Count > 0;
    internal bool IndexesDataAssets => _format >= 4;
    internal bool IndexesPreviews => _format >= 5;
    internal bool IndexesMountAliases => _format >= 6;

    private PakIndex(
        ContainerStamp[] containers,
        Dictionary<ulong, ushort> packageToContainer,
        Dictionary<ulong, PathHit> pathToContainer,
        List<StoredPath> storedPaths,
        string buildId,
        string depotManifest,
        uint format)
    {
        Containers = containers;
        _packageToContainer = packageToContainer;
        _pathToContainer = pathToContainer;
        _storedPaths = storedPaths;
        BuildId = buildId;
        DepotManifest = depotManifest;
        _format = format;
        PackageCount = packageToContainer.Count;
    }

    internal static string FileName(GameBuild.Info build)
        => build.IsKnown ? $"pak-index-{build.BuildId}.bin" : "pak-index.bin";

    internal bool MatchesBuild(GameBuild.Info live)
    {
        if (!live.IsKnown || !string.Equals(BuildId, live.BuildId, StringComparison.Ordinal))
            return false;
        if (live.DepotManifest.Length == 0 || DepotManifest.Length == 0)
            return true;
        return string.Equals(DepotManifest, live.DepotManifest, StringComparison.Ordinal);
    }

    internal IEnumerable<string> AlwaysMountNames()
    {
        foreach (var container in Containers)
        {
            if (container.Name.StartsWith("global", StringComparison.OrdinalIgnoreCase)
                && container.Name.EndsWith(".utoc", StringComparison.OrdinalIgnoreCase))
                yield return container.Name;
        }
    }

    internal bool TryGetContainer(ulong packageId, out string name)
    {
        if (_packageToContainer.TryGetValue(packageId, out var index) && index < Containers.Length)
        {
            name = Containers[index].Name;
            return true;
        }

        name = "";
        return false;
    }

    internal bool TryResolve(IReadOnlyList<string> assetPaths, out List<PathHit> hits)
    {
        hits = new List<PathHit>(assetPaths.Count);
        foreach (var assetPath in assetPaths)
        {
            PathHit? hit = null;
            foreach (var candidate in Candidates(assetPath))
            {
                if (!_pathToContainer.TryGetValue(Hash(candidate), out var found))
                    continue;
                if (found.Container == ushort.MaxValue)
                    return false;
                hit = found;
                break;
            }

            if (hit is null)
                return false;
            hits.Add(hit.Value);
        }

        return hits.Count > 0;
    }

    /// <summary>
    /// Exact package paths stay as-is. A folder prefix becomes the SK_/SM_ and DA_OI_ packages under it.
    /// Returns false when nothing matched. A false result with <see cref="HasPaths"/> false
    /// means the index still needs a path list. A false result when paths exist but
    /// <see cref="IndexesDataAssets"/> is false means colourway assets are missing and the
    /// caller should rebuild once. A false result after that means the folder has no match.
    /// </summary>
    internal bool TryPrepare(IReadOnlyList<string> assetPaths, out List<string> packages, out List<PathHit> hits)
    {
        packages = new List<string>();
        hits = new List<PathHit>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var unresolved = false;
        foreach (var assetPath in assetPaths)
        {
            if (TryExact(assetPath, out var hit, out var packagePath))
            {
                if (seen.Add(packagePath))
                {
                    packages.Add(packagePath);
                    hits.Add(hit);
                }
                continue;
            }

            unresolved = true;
            if (HasPaths)
                AppendMeshes(assetPath, packages, hits, seen);
        }

        if (unresolved && !HasPaths)
            return false;
        if (packages.Count == 0 && unresolved && !IndexesDataAssets)
            return false;
        return packages.Count > 0;
    }

    internal static bool IsMeshPackage(string path)
    {
        var norm = Normalize(path);
        var slash = norm.LastIndexOf('/');
        var leaf = slash >= 0 ? norm[(slash + 1)..] : norm;
        if (leaf.Length == 0)
            return false;
        if (leaf.EndsWith("_physics", StringComparison.Ordinal) || leaf.EndsWith("_skeleton", StringComparison.Ordinal))
            return false;
        return leaf.StartsWith("sk_", StringComparison.Ordinal) || leaf.StartsWith("sm_", StringComparison.Ordinal);
    }

    internal static bool IsListedPackage(string path)
    {
        if (IsMeshPackage(path))
            return true;
        var norm = Normalize(path);
        var slash = norm.LastIndexOf('/');
        var leaf = slash >= 0 ? norm[(slash + 1)..] : norm;
        if (!leaf.StartsWith("da_oi_", StringComparison.Ordinal))
            return false;
        return !leaf.Contains("persistence", StringComparison.Ordinal);
    }

    /// <summary>
    /// Sidebar preview textures. Stored in the index and exported as PNG during reindex.
    /// Kept out of <see cref="IsListedPackage"/> so a folder extract does not spend its
    /// package cap on icons.
    /// </summary>
    internal static bool IsPreviewPackage(string path)
    {
        var norm = Normalize(path);
        if (!norm.Contains("/items/characters/skins/", StringComparison.Ordinal))
            return false;
        var slash = norm.LastIndexOf('/');
        var leaf = slash >= 0 ? norm[(slash + 1)..] : norm;
        return leaf.StartsWith("t_ui_icon_", StringComparison.Ordinal);
    }

    private bool TryExact(string assetPath, out PathHit hit, out string packagePath)
    {
        hit = default;
        packagePath = assetPath;
        foreach (var candidate in Candidates(assetPath))
        {
            if (!_pathToContainer.TryGetValue(Hash(candidate), out var found))
                continue;
            if (found.Container == ushort.MaxValue)
                return false;
            hit = found;
            packagePath = assetPath;
            return true;
        }

        return false;
    }

    private void AppendMeshes(string assetPath, List<string> packages, List<PathHit> hits, HashSet<string> seen)
    {
        var prefixes = Candidates(assetPath).Where(prefix => prefix.Length > 0).Distinct().ToList();
        if (prefixes.Count == 0)
            return;

        var added = 0;
        foreach (var stored in _storedPaths)
        {
            if (stored.Container == ushort.MaxValue || stored.Container >= Containers.Length)
                continue;
            if (!IsListedPackage(stored.Path))
                continue;
            var norm = Normalize(stored.Path);
            var match = false;
            foreach (var prefix in prefixes)
            {
                if (norm == prefix || norm.StartsWith(prefix + "/", StringComparison.Ordinal))
                {
                    match = true;
                    break;
                }
            }

            if (!match || !seen.Add(stored.Path))
                continue;
            packages.Add(stored.Path);
            hits.Add(new PathHit(stored.Container, stored.PackageId));
            if (++added >= 128)
                break;
        }
    }

    internal bool Matches(string gameDir)
    {
        List<string> files;
        try
        {
            files = Directory.EnumerateFiles(gameDir, "*.utoc", SearchOption.TopDirectoryOnly)
                .OrderBy(path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase)
                .ToList();
        }
        catch (Exception)
        {
            return false;
        }

        if (files.Count == 0 || files.Count != Containers.Length)
            return false;

        for (var i = 0; i < files.Count; i++)
        {
            var name = Path.GetFileName(files[i]);
            if (!name.Equals(Containers[i].Name, StringComparison.OrdinalIgnoreCase))
                return false;

            long length;
            try
            {
                length = new FileInfo(files[i]).Length;
            }
            catch (Exception)
            {
                return false;
            }

            if (length != Containers[i].Length)
                return false;
            if (HashFile(files[i]) != Containers[i].Hash)
                return false;
        }

        return true;
    }

    internal static PakIndex? TryLoad(string path)
    {
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: false);
            var magic = reader.ReadBytes(8);
            if (magic.Length != 8 || Encoding.ASCII.GetString(magic) != "ARCPAKIX")
                return null;
            var format = reader.ReadUInt32();
            if (format is not (1 or 2 or 3 or 4 or 5 or 6))
                return null;
            var buildId = "";
            var depotManifest = "";
            if (format >= 2)
            {
                buildId = ReadName(reader);
                depotManifest = ReadName(reader);
            }

            var containerCount = reader.ReadInt32();
            if (containerCount <= 0 || containerCount >= ushort.MaxValue)
                return null;

            var containers = new ContainerStamp[containerCount];
            for (var i = 0; i < containerCount; i++)
            {
                var name = ReadName(reader);
                if (string.IsNullOrEmpty(name))
                    return null;
                var length = reader.ReadInt64();
                var hash = reader.ReadUInt64();
                containers[i] = new ContainerStamp(name, length, hash);
            }

            var packageCount = reader.ReadInt32();
            if (packageCount < 0)
                return null;
            var packages = new Dictionary<ulong, ushort>(packageCount);
            for (var i = 0; i < packageCount; i++)
            {
                var id = reader.ReadUInt64();
                var container = reader.ReadUInt16();
                if (container >= containerCount)
                    return null;
                packages[id] = container;
            }

            var pathCount = reader.ReadInt32();
            if (pathCount < 0)
                return null;
            var paths = new Dictionary<ulong, PathHit>(pathCount);
            for (var i = 0; i < pathCount; i++)
            {
                var pathHash = reader.ReadUInt64();
                var container = reader.ReadUInt16();
                var packageId = reader.ReadUInt64();
                if (container != ushort.MaxValue && container >= containerCount)
                    return null;
                paths[pathHash] = new PathHit(container, packageId);
            }

            var stored = new List<StoredPath>();
            if (format >= 3)
            {
                var stringCount = reader.ReadInt32();
                if (stringCount < 0)
                    return null;
                stored.Capacity = stringCount;
                for (var i = 0; i < stringCount; i++)
                {
                    var text = ReadName(reader);
                    var container = reader.ReadUInt16();
                    var packageId = reader.ReadUInt64();
                    if (string.IsNullOrEmpty(text))
                        return null;
                    if (container != ushort.MaxValue && container >= containerCount)
                        return null;
                    stored.Add(new StoredPath(text, container, packageId));
                }
            }

            if (stream.Position != stream.Length)
                return null;
            return new PakIndex(containers, packages, paths, stored, buildId, depotManifest, format);
        }
        catch (Exception)
        {
            return null;
        }
    }

    internal void Save(string indexPath, GameBuild.Info build)
    {
        var directory = Path.GetDirectoryName(indexPath);
        if (!string.IsNullOrEmpty(directory))
            Directory.CreateDirectory(directory);

        var temp = indexPath + ".tmp";
        using (var stream = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None))
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: false))
        {
            writer.Write(Encoding.ASCII.GetBytes("ARCPAKIX"));
            writer.Write(_format == 0 ? Version : _format);
            WriteName(writer, build.IsKnown ? build.BuildId : BuildId);
            WriteName(writer, build.DepotManifest.Length > 0 ? build.DepotManifest : DepotManifest);
            writer.Write(Containers.Length);
            foreach (var container in Containers)
            {
                WriteName(writer, container.Name);
                writer.Write(container.Length);
                writer.Write(container.Hash);
            }

            writer.Write(_packageToContainer.Count);
            foreach (var (id, container) in _packageToContainer)
            {
                writer.Write(id);
                writer.Write(container);
            }

            writer.Write(_pathToContainer.Count);
            foreach (var (pathHash, hit) in _pathToContainer)
            {
                writer.Write(pathHash);
                writer.Write(hit.Container);
                writer.Write(hit.PackageId);
            }

            if (_format >= 3)
            {
                writer.Write(_storedPaths.Count);
                foreach (var stored in _storedPaths)
                {
                    WriteName(writer, stored.Path);
                    writer.Write(stored.Container);
                    writer.Write(stored.PackageId);
                }
            }
        }

        File.Move(temp, indexPath, overwrite: true);
    }

    internal static void Write(AbstractVfsFileProvider provider, string gameDir, string indexPath, GameBuild.Info build)
    {
        var files = Directory.EnumerateFiles(gameDir, "*.utoc", SearchOption.TopDirectoryOnly)
            .OrderBy(path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase)
            .ToList();
        if (files.Count == 0)
            throw new InvalidOperationException("No .utoc files in the paks folder.");

        var containers = new ContainerStamp[files.Count];
        var nameToIndex = new Dictionary<string, ushort>(StringComparer.OrdinalIgnoreCase);
        for (var i = 0; i < files.Count; i++)
        {
            var name = Path.GetFileName(files[i]);
            containers[i] = new ContainerStamp(name, new FileInfo(files[i]).Length, HashFile(files[i]));
            nameToIndex[name] = (ushort)i;
        }

        var packages = new Dictionary<ulong, ushort>();
        var paths = new Dictionary<ulong, PathHit>();
        var stored = new Dictionary<string, StoredPath>(StringComparer.Ordinal);
        foreach (var file in provider.Files.Values)
        {
            if (file is not FIoStoreEntry { IsPackageData: true, IsOptionalPackage: false } entry)
                continue;
            if (!nameToIndex.TryGetValue(entry.Vfs.Name, out var container))
                continue;

            var packageId = entry.ChunkId.AsPackageId().id;
            packages.TryAdd(packageId, container);
            AddPath(paths, entry.PathWithoutExtension, container, packageId);
            var text = entry.PathWithoutExtension.Replace('\\', '/');
            var norm = Normalize(text);
            if (norm.Length > 0 && (IsListedPackage(text) || IsPreviewPackage(text)))
                stored.TryAdd(norm, new StoredPath(text, container, packageId));
        }

        var index = new PakIndex(
            containers, packages, paths, stored.Values.ToList(), build.BuildId, build.DepotManifest, Version);
        index.Save(indexPath, build);
    }

    internal static IEnumerable<string> Candidates(string path)
    {
        var normalized = Normalize(path);
        if (normalized.Length == 0)
            yield break;

        yield return normalized;
        const string cooked = "pioneergame/content/";
        const string game = "game/";
        if (normalized.StartsWith(game, StringComparison.Ordinal))
            yield return cooked + normalized[game.Length..];
        else if (normalized.StartsWith(cooked, StringComparison.Ordinal))
            yield return game + normalized[cooked.Length..];
        const string remapped = "remappedplugins/content/";
        if (normalized.StartsWith(cooked, StringComparison.Ordinal))
            yield return remapped + normalized[cooked.Length..];
        else if (normalized.StartsWith(game, StringComparison.Ordinal))
            yield return remapped + normalized[game.Length..];
        else if (normalized.StartsWith(remapped, StringComparison.Ordinal))
        {
            yield return cooked + normalized[remapped.Length..];
            yield return game + normalized[remapped.Length..];
        }
    }

    internal static string Normalize(string path)
    {
        var text = path.Replace('\\', '/').Trim();
        if (text.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase))
            text = text[..^".uasset".Length];
        var slash = text.LastIndexOf('/');
        var dot = text.LastIndexOf('.');
        if (dot > slash)
            text = text[..dot];
        return text.Trim('/').ToLowerInvariant();
    }

    private static void AddPath(Dictionary<ulong, PathHit> paths, string storedPath, ushort container, ulong packageId)
    {
        foreach (var candidate in Candidates(storedPath))
        {
            var hash = Hash(candidate);
            if (paths.TryGetValue(hash, out var existing))
            {
                if (existing.Container != container)
                    paths[hash] = new PathHit(ushort.MaxValue, 0);
                continue;
            }

            paths[hash] = new PathHit(container, packageId);
        }
    }

    private static ulong Hash(string text)
    {
        var hash = FnvOffset;
        foreach (var b in Encoding.UTF8.GetBytes(text))
        {
            hash ^= b;
            hash *= FnvPrime;
        }

        return hash;
    }

    private static ulong HashFile(string path)
    {
        var hash = FnvOffset;
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        var buffer = new byte[1024 * 1024];
        int read;
        while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
        {
            for (var i = 0; i < read; i++)
            {
                hash ^= buffer[i];
                hash *= FnvPrime;
            }
        }

        return hash;
    }

    private static string ReadName(BinaryReader reader)
    {
        var length = reader.ReadUInt16();
        var bytes = reader.ReadBytes(length);
        if (bytes.Length != length)
            return "";
        return Encoding.UTF8.GetString(bytes);
    }

    private static void WriteName(BinaryWriter writer, string name)
    {
        var bytes = Encoding.UTF8.GetBytes(name);
        if (bytes.Length > ushort.MaxValue)
            throw new InvalidOperationException("Container name is too long.");
        writer.Write((ushort)bytes.Length);
        writer.Write(bytes);
    }
}
