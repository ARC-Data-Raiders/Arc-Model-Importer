using System.Collections.ObjectModel;
using CUE4Parse.GameTypes.Theia.FileProvider;
using CUE4Parse.GameTypes.Theia.Readers;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.IO;
using CUE4Parse.UE4.Readers;
using CUE4Parse.UE4.VirtualFileSystem;

namespace ArcPak;

/// <summary>
/// Registers only archives named by a leased source identity. This helper deliberately
/// does not call provider.Initialize, so directory additions cannot enter the provider.
/// </summary>
public static class MapPinnedProviderRegistration
{
    private static readonly StringComparer PathComparer = StringComparer.OrdinalIgnoreCase;

    public static MapPinnedProviderMountReceipt Register(
        TheiaFileProvider provider,
        MapArchiveSourceIdentity sourceIdentity,
        IEnumerable<MapArchiveSourceInput> sourceInputs,
        IEnumerable<string> archivePaths,
        IEnumerable<string> companionPaths,
        IEnumerable<string> requiredMountedArchivePaths,
        string? globalAuxiliaryArchivePath)
    {
        ArgumentNullException.ThrowIfNull(provider);
        ArgumentNullException.ThrowIfNull(sourceIdentity);
        ArgumentNullException.ThrowIfNull(sourceInputs);
        sourceIdentity.EnsureActive();
        if (provider.LooseFileCount != 0 || provider.Files.Values.Any(file =>
                file is not StreamedGameFile || !file.Path.Equals("PioneerGame/PioneerGame.uproject", StringComparison.OrdinalIgnoreCase)) ||
            provider.MountedVfs.Count != 0 || provider.UnloadedVfs.Count != 0)
            throw new InvalidDataException("Pinned provider registration requires a fresh provider with no loose files or registered readers");

        var inputs = ValidateInputs(sourceIdentity, sourceInputs);
        var archives = NormalizePathSet(archivePaths, nameof(archivePaths));
        var companions = NormalizePathSet(companionPaths, nameof(companionPaths));
        var requiredMounted = NormalizePathSet(requiredMountedArchivePaths, nameof(requiredMountedArchivePaths));
        var permittedUnloaded = globalAuxiliaryArchivePath is null
            ? Array.Empty<string>()
            : [NormalizePath(globalAuxiliaryArchivePath, nameof(globalAuxiliaryArchivePath))];
        ValidatePlan(inputs, archives, companions, requiredMounted, permittedUnloaded);

        var identity = sourceIdentity.IdentitySha256;
        var openedCompanions = new List<string>();
        var openedByPath = new Dictionary<string, FArchive>(PathComparer);
        foreach (var archivePath in archives.Order(PathComparer))
        {
            sourceIdentity.EnsureActive();
            var readersBefore = CurrentReaderPaths(provider);
            var primary = OpenTheiaArchive(archivePath, provider, inputs);
            var openedForThisReader = new List<FArchive> { primary };
            try
            {
                Func<string, FArchive>? openContainer = null;
                if (Path.GetExtension(archivePath).Equals(".utoc", StringComparison.OrdinalIgnoreCase))
                {
                    openContainer = CreateContainerOpener(archivePath, requestedPath =>
                    {
                        sourceIdentity.EnsureActive();
                        var normalized = NormalizePath(requestedPath, "IoStore partition path");
                        if (!IsOwnedPartitionPath(archivePath, normalized))
                            throw new InvalidDataException("IoStore requested a partition owned by another .utoc: " + normalized);
                        if (!companions.Contains(normalized, PathComparer))
                            throw new InvalidDataException("IoStore requested an unlisted container partition: " + normalized);
                        if (openedByPath.ContainsKey(normalized))
                            throw new InvalidDataException("IoStore requested a container partition more than once: " + normalized);
                        var container = OpenTheiaArchive(normalized, provider, inputs);
                        openedByPath.Add(normalized, container);
                        openedCompanions.Add(normalized);
                        openedForThisReader.Add(container);
                        return container;
                    });
                }

                provider.RegisterRandomAccessVfs(primary, (CUE4Parse.UE4.Readers.FArchive?)null, openContainer);
                var readersAfter = CurrentReaderPaths(provider);
                if (readersAfter.Count != readersBefore.Count + 1 ||
                    !readersAfter.Contains(NormalizePath(archivePath, nameof(archivePaths)), PathComparer))
                    throw new InvalidDataException("Provider failed to register the pinned archive: " + archivePath);
            }
            catch
            {
                var readersAfter = CurrentReaderPaths(provider);
                var registered = readersAfter.Contains(NormalizePath(archivePath, nameof(archivePaths)), PathComparer);
                if (!registered)
                {
                    for (var index = openedForThisReader.Count - 1; index >= 0; index--)
                    {
                        try { openedForThisReader[index].Dispose(); }
                        catch { }
                    }
                    foreach (var path in openedByPath.Where(pair => openedForThisReader.Contains(pair.Value))
                                 .Select(pair => pair.Key).ToArray())
                    {
                        openedByPath.Remove(path);
                        openedCompanions.Remove(path);
                    }
                }
                throw;
            }
        }

        sourceIdentity.EnsureActive();
        var registeredPaths = CurrentReaderPaths(provider);
        if (!SetEquals(registeredPaths, archives))
            throw new InvalidDataException("Provider registration set does not match the pinned archive set");

        var actualCompanions = new List<string>();
        foreach (var reader in provider.MountedVfs.Concat(provider.UnloadedVfs))
        {
            if (reader is not IoStoreReader ioStore) continue;
            foreach (var stream in ioStore.ContainerStreams)
                actualCompanions.Add(NormalizePath(stream.Name, "registered IoStore stream path"));
        }
        foreach (var reader in provider.MountedVfs.Concat(provider.UnloadedVfs).OfType<IoStoreReader>())
            foreach (var stream in reader.ContainerStreams)
                if (!IsOwnedPartitionPath(reader.Path, stream.Name))
                    throw new InvalidDataException("IoStore partition is attached to the wrong .utoc: " + stream.Name);
        if (!SetEquals(actualCompanions, companions) || !SetEquals(openedCompanions, companions))
            throw new InvalidDataException("IoStore partition streams do not match the pinned companion set");

        return new MapPinnedProviderMountReceipt(sourceIdentity, provider, identity,
            inputs.ManifestRows, registeredPaths, actualCompanions, requiredMounted, permittedUnloaded);
    }

    private static ValidatedInputs ValidateInputs(MapArchiveSourceIdentity identity,
        IEnumerable<MapArchiveSourceInput> sourceInputs)
    {
        var rows = new List<ValidatedInput>();
        var seenPaths = new HashSet<string>(PathComparer);
        var seenKeys = new HashSet<string>(StringComparer.Ordinal);
        foreach (var input in sourceInputs)
        {
            if (input is null) throw new InvalidDataException("Pinned source manifest contains a null row");
            var role = NormalizeRole(input.Role);
            var logicalName = NormalizeLogicalName(input.LogicalName);
            var path = NormalizePath(input.Path, "pinned source path");
            if (!seenPaths.Add(path)) throw new InvalidDataException("Pinned source manifest contains a duplicate physical path: " + path);
            if (!seenKeys.Add(role + "\n" + logicalName))
                throw new InvalidDataException("Pinned source manifest contains a duplicate role and logical name: " + role + ":" + logicalName);
            if (!File.Exists(path)) throw new FileNotFoundException("Pinned source file is absent", path);
            if (role == "loose") throw new InvalidDataException("Pinned archive registration does not accept loose-file inputs");
            rows.Add(new ValidatedInput(role, logicalName, path));
        }
        if (rows.Count == 0) throw new InvalidDataException("Pinned source manifest is empty");

        identity.EnsureInputBindings(rows.Select(row => new MapArchiveSourceInput(row.Role, row.LogicalName, row.Path)));
        var identityRows = identity.Manifest.ToDictionary(
            row => NormalizeRole(row.Role) + "\n" + NormalizeLogicalName(row.LogicalName), StringComparer.Ordinal);
        if (identityRows.Count != rows.Count)
            throw new InvalidDataException("Pinned source manifest rows do not match the leased identity manifest");
        foreach (var row in rows)
            if (!identityRows.ContainsKey(row.Role + "\n" + row.LogicalName))
                throw new InvalidDataException("Pinned source manifest role/name does not match the leased identity: " + row.LogicalName);

        var archiveInputs = rows.Where(row => row.Role == "archive").ToArray();
        if (!archiveInputs.Any(row => row.Role == "archive"))
            throw new InvalidDataException("Pinned source manifest has no archive role");
        if (!rows.Any(row => row.Role == "mapping"))
            throw new InvalidDataException("Pinned source manifest must include a mapping input");
        foreach (var row in archiveInputs)
        {
            var extension = Path.GetExtension(row.Path);
            if (!(extension.Equals(".pak", StringComparison.OrdinalIgnoreCase) ||
                  extension.Equals(".upak", StringComparison.OrdinalIgnoreCase) ||
                  extension.Equals(".utoc", StringComparison.OrdinalIgnoreCase) ||
                  extension.Equals(".ucas", StringComparison.OrdinalIgnoreCase)))
                throw new InvalidDataException("Archive source role contains an unsupported file: " + row.Path);
        }

        var archiveMetaInputs = rows.Where(row => row.Role == "archive-meta").Select(row => row.Path).ToArray();
        if (rows.Any(row => row.Role == "archive-meta" && !Path.GetExtension(row.Path).Equals(".meta", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("Archive metadata source must use the .meta extension");
        return new ValidatedInputs(rows, identityRows, archiveInputs.Select(row => row.Path).ToArray(), archiveMetaInputs);
    }

    private static void ValidatePlan(ValidatedInputs inputs, string[] archives, string[] companions,
        string[] requiredMounted, string[] permittedUnloaded)
    {
        if (archives.Length == 0) throw new InvalidDataException("Pinned archive set is empty");
        if (archives.Any(path => !(Path.GetExtension(path).Equals(".pak", StringComparison.OrdinalIgnoreCase) ||
                                   Path.GetExtension(path).Equals(".upak", StringComparison.OrdinalIgnoreCase) ||
                                   Path.GetExtension(path).Equals(".utoc", StringComparison.OrdinalIgnoreCase))))
            throw new InvalidDataException("Registered archive paths must be .pak, .upak or .utoc files");
        if (companions.Any(path => !Path.GetExtension(path).Equals(".ucas", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("IoStore companion paths must be .ucas files");
        if (archives.Intersect(companions, PathComparer).Any())
            throw new InvalidDataException("An archive path cannot also be a companion path");

        var archiveRolePaths = inputs.ArchiveRolePaths.Where(path =>
            Path.GetExtension(path).Equals(".pak", StringComparison.OrdinalIgnoreCase) ||
            Path.GetExtension(path).Equals(".upak", StringComparison.OrdinalIgnoreCase) ||
            Path.GetExtension(path).Equals(".utoc", StringComparison.OrdinalIgnoreCase)).ToArray();
        var companionRolePaths = inputs.ArchiveRolePaths.Where(path =>
            Path.GetExtension(path).Equals(".ucas", StringComparison.OrdinalIgnoreCase)).ToArray();
        if (!SetEquals(archiveRolePaths, archives))
            throw new InvalidDataException("Every declared archive source must be explicitly registered exactly once");
        if (!SetEquals(companionRolePaths, companions))
            throw new InvalidDataException("Every declared IoStore companion must be explicitly allow-listed exactly once");

        var expectedMetadata = archives.Concat(companions).Select(path => path + ".meta")
            .Where(File.Exists).ToArray();
        if (!SetEquals(expectedMetadata, inputs.ArchiveMetaPaths))
            throw new InvalidDataException("Theia .meta dependencies are missing from or extra in the pinned source manifest");
        if (archives.Concat(companions).Any(path => !File.Exists(path)))
            throw new FileNotFoundException("A declared archive or IoStore companion is absent");

        if (!SetEquals(requiredMounted.Concat(permittedUnloaded), archives))
            throw new InvalidDataException("Required-mounted and explicitly declared global auxiliary paths must exactly partition the archive set");
        if (requiredMounted.Intersect(permittedUnloaded, PathComparer).Any())
            throw new InvalidDataException("An archive cannot be both required-mounted and declared as the global auxiliary reader");
        var globalArchives = archives.Where(path => Path.GetFileName(path).Equals("global.utoc", StringComparison.OrdinalIgnoreCase)).ToArray();
        if (globalArchives.Length > 1 || !SetEquals(globalArchives, permittedUnloaded))
            throw new InvalidDataException("Every global.utoc must be explicitly declared as the sole unloaded auxiliary reader");
        if (permittedUnloaded.Any(path => !Path.GetExtension(path).Equals(".utoc", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("The explicit global auxiliary input must be a .utoc archive");
    }

    internal static Func<string, FArchive> CreateContainerOpener(string archivePath, Func<string, FArchive> openArchive)
    {
        ArgumentNullException.ThrowIfNull(openArchive);
        var normalizedArchive = NormalizePath(archivePath, "IoStore owner archive path");
        return requestedPath =>
        {
            var normalizedPartition = NormalizePath(requestedPath, "IoStore partition path");
            if (!IsOwnedPartitionPath(normalizedArchive, normalizedPartition))
                throw new InvalidDataException("IoStore requested a partition owned by another .utoc: " + normalizedPartition);
            return openArchive(normalizedPartition);
        };
    }

    internal static bool IsOwnedPartitionPath(string archivePath, string partitionPath)
    {
        var archive = NormalizePath(archivePath, "IoStore owner archive path");
        var partition = NormalizePath(partitionPath, "IoStore partition path");
        if (!Path.GetExtension(archive).Equals(".utoc", StringComparison.OrdinalIgnoreCase) ||
            !Path.GetExtension(partition).Equals(".ucas", StringComparison.OrdinalIgnoreCase) ||
            !PathComparer.Equals(Path.GetDirectoryName(archive), Path.GetDirectoryName(partition)))
            return false;
        var archiveStem = Path.GetFileNameWithoutExtension(archive);
        var partitionStem = Path.GetFileNameWithoutExtension(partition);
        if (partitionStem.Equals(archiveStem, StringComparison.OrdinalIgnoreCase)) return true;
        var splitPrefix = archiveStem + "_s";
        if (!partitionStem.StartsWith(splitPrefix, StringComparison.OrdinalIgnoreCase)) return false;
        var suffix = partitionStem[splitPrefix.Length..];
        return suffix.Length > 0 && suffix.All(character => character >= '0' && character <= '9') &&
               int.TryParse(suffix, out var partitionIndex) && partitionIndex > 0;
    }
    private static FArchive OpenTheiaArchive(string path, TheiaFileProvider provider, ValidatedInputs inputs)
    {
        var normalized = NormalizePath(path, "archive open path");
        var metadataPath = normalized + ".meta";
        if (File.Exists(metadataPath))
        {
            if (!inputs.ArchiveMetaPaths.Contains(metadataPath, PathComparer))
                throw new InvalidDataException("Theia archive metadata is not pinned: " + metadataPath);
            return new FTheiaArchive(normalized, provider.Versions);
        }
        return new FRandomAccessFileStreamArchive(normalized, provider.Versions);
    }

    internal static HashSet<string> CurrentReaderPathsForReceipt(TheiaFileProvider provider) => CurrentReaderPaths(provider);

    private static HashSet<string> CurrentReaderPaths(TheiaFileProvider provider) =>
        provider.MountedVfs.Concat(provider.UnloadedVfs)
            .Select(reader => NormalizePath(reader.Path, "provider reader path"))
            .ToHashSet(PathComparer);

    private static bool SetEquals(IEnumerable<string> left, IEnumerable<string> right) =>
        new HashSet<string>(left, PathComparer).SetEquals(right);

    private static string[] NormalizePathSet(IEnumerable<string> paths, string parameterName)
    {
        ArgumentNullException.ThrowIfNull(paths, parameterName);
        var result = new List<string>();
        var seen = new HashSet<string>(PathComparer);
        foreach (var path in paths)
        {
            var normalized = NormalizePath(path, parameterName);
            if (!seen.Add(normalized)) throw new InvalidDataException(parameterName + " contains a duplicate path: " + normalized);
            result.Add(normalized);
        }
        return result.ToArray();
    }

    private static string NormalizePath(string path, string label)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new InvalidDataException(label + " is empty");
        try { return Path.GetFullPath(path); }
        catch (Exception exception) when (exception is ArgumentException or NotSupportedException or PathTooLongException)
        { throw new InvalidDataException(label + " is invalid: " + path, exception); }
    }

    private static string NormalizeRole(string role)
    {
        ArgumentNullException.ThrowIfNull(role);
        if (role.Length is 0 or > 128 || role.Any(character =>
                !((character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
                  (character >= '0' && character <= '9') || character is '-' or '_' or '.')))
            throw new InvalidDataException("Pinned source role is invalid");
        return role.ToLowerInvariant();
    }

    private static string NormalizeLogicalName(string logicalName)
    {
        ArgumentNullException.ThrowIfNull(logicalName);
        var normalized = logicalName.Replace('\\', '/');
        if (normalized.Length is 0 or > 4096 || normalized.StartsWith('/') || normalized.Contains(':') ||
            normalized.Contains('\0') || normalized.Contains('\r') || normalized.Contains('\n') ||
            normalized.Split('/').Any(segment => segment.Length == 0 || segment is "." or ".."))
            throw new InvalidDataException("Pinned logical source name is invalid");
        return normalized;
    }

    private sealed record ValidatedInput(string Role, string LogicalName, string Path);

    private sealed class ValidatedInputs
    {
        public IReadOnlyList<ValidatedInput> Rows { get; }
        public IReadOnlyDictionary<string, MapArchiveSourceManifestEntry> ManifestRows { get; }
        public string[] ArchiveRolePaths { get; }
        public string[] ArchiveMetaPaths { get; }

        public ValidatedInputs(List<ValidatedInput> rows,
            IReadOnlyDictionary<string, MapArchiveSourceManifestEntry> manifestRows,
            string[] archiveRolePaths, string[] archiveMetaPaths)
        {
            Rows = Array.AsReadOnly(rows.ToArray());
            ManifestRows = manifestRows;
            ArchiveRolePaths = archiveRolePaths;
            ArchiveMetaPaths = archiveMetaPaths;
        }
    }
}

/// <summary>A receipt that gates package-key creation on exact provider membership.</summary>
public sealed class MapPinnedProviderMountReceipt
{
    private static readonly StringComparer PathComparer = StringComparer.OrdinalIgnoreCase;
    private readonly MapArchiveSourceIdentity _identity;
    private readonly TheiaFileProvider _provider;
    private readonly string _identitySha256;
    private readonly IReadOnlyDictionary<string, MapArchiveSourceManifestEntry> _manifestRows;
    private readonly string[] _registeredArchivePaths;
    private readonly string[] _openedContainerPaths;
    private readonly string[] _requiredMountedArchivePaths;
    private readonly string[] _permittedUnloadedArchivePaths;

    internal MapPinnedProviderMountReceipt(MapArchiveSourceIdentity identity, TheiaFileProvider provider,
        string identitySha256, IReadOnlyDictionary<string, MapArchiveSourceManifestEntry> manifestRows,
        IEnumerable<string> registeredArchivePaths, IEnumerable<string> openedContainerPaths,
        IEnumerable<string> requiredMountedArchivePaths, IEnumerable<string> permittedUnloadedArchivePaths)
    {
        _identity = identity;
        _provider = provider;
        _identitySha256 = identitySha256;
        _manifestRows = manifestRows;
        _registeredArchivePaths = registeredArchivePaths.Order(PathComparer).ToArray();
        _openedContainerPaths = openedContainerPaths.Order(PathComparer).ToArray();
        _requiredMountedArchivePaths = requiredMountedArchivePaths.Order(PathComparer).ToArray();
        _permittedUnloadedArchivePaths = permittedUnloadedArchivePaths.Order(PathComparer).ToArray();
    }

    public IReadOnlyList<string> RegisteredArchivePaths { get { EnsureActive(); return Array.AsReadOnly(_registeredArchivePaths); } }
    public IReadOnlyList<string> OpenedContainerPaths { get { EnsureActive(); return Array.AsReadOnly(_openedContainerPaths); } }
    public IReadOnlyList<string> MountedArchivePaths
    {
        get
        {
            EnsureActive();
            return Array.AsReadOnly(_provider.MountedVfs.Select(reader => NormalizePath(reader.Path, "mounted reader path")).Order(PathComparer).ToArray());
        }
    }
    public IReadOnlyList<string> RequiredMountedArchivePaths { get { EnsureActive(); return Array.AsReadOnly(_requiredMountedArchivePaths); } }
    public IReadOnlyList<string> PermittedUnloadedArchivePaths { get { EnsureActive(); return Array.AsReadOnly(_permittedUnloadedArchivePaths); } }

    public void EnsureCompleteMountedSet(TheiaFileProvider provider)
    {
        EnsureActive();
        if (!ReferenceEquals(provider, _provider)) throw new InvalidDataException("Mount receipt belongs to a different provider");
        var registered = MapPinnedProviderRegistration.CurrentReaderPathsForReceipt(provider);
        if (!SetEquals(registered, _registeredArchivePaths))
            throw new InvalidDataException("Provider reader membership changed after pinned registration");
        var mounted = provider.MountedVfs.Select(reader => NormalizePath(reader.Path, "mounted reader path")).ToArray();
        if (!SetEquals(mounted, _requiredMountedArchivePaths))
            throw new InvalidDataException("Required pinned archives are not all mounted");
        var unloaded = provider.UnloadedVfs.Select(reader => NormalizePath(reader.Path, "unloaded reader path")).ToArray();
        if (!SetEquals(unloaded, _permittedUnloadedArchivePaths))
            throw new InvalidDataException("Provider has an unaccounted unloaded archive");
        if (_permittedUnloadedArchivePaths.Length > 0 && provider.GlobalData is null)
            throw new InvalidDataException("The permitted global auxiliary reader did not initialize GlobalData");

        var actualContainerPaths = new List<string>();
        foreach (var reader in provider.MountedVfs.Concat(provider.UnloadedVfs))
        {
            if (reader is not IoStoreReader ioStore)
            {
                if (_registeredArchivePaths.Contains(NormalizePath(reader.Path, "reader path"), PathComparer) &&
                    Path.GetExtension(reader.Path).Equals(".utoc", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("A pinned .utoc reader is not an IoStoreReader");
                continue;
            }
            foreach (var stream in ioStore.ContainerStreams)
            {
                var partitionPath = NormalizePath(stream.Name, "IoStore partition path");
                if (!MapPinnedProviderRegistration.IsOwnedPartitionPath(ioStore.Path, partitionPath))
                    throw new InvalidDataException("IoStore partition is attached to the wrong .utoc: " + partitionPath);
                actualContainerPaths.Add(partitionPath);
            }
        }
        if (!SetEquals(actualContainerPaths, _openedContainerPaths))
            throw new InvalidDataException("IoStore partition stream membership changed after registration");
        _identity.EnsureActive();
    }

    public MapPackageRecordKey CreateCompletePackageKey(TheiaFileProvider provider,
        string canonicalPackage, string mappingName, string mappingSha256,
        string decoderFingerprintSha256, string captureProducerFingerprintSha256,
        string captureProfileSha256, int schemaVersion)
    {
        EnsureCompleteMountedSet(provider);
        if (!string.Equals(Path.GetFileName(mappingName), mappingName, StringComparison.Ordinal))
            throw new InvalidDataException("Mapping name must be a file name");
        var normalizedMappingName = mappingName;
        var mappingRows = _manifestRows.Values.Where(row => row.Role == "mapping" &&
            Path.GetFileName(row.LogicalName.Replace('/', Path.DirectorySeparatorChar)).Equals(normalizedMappingName, StringComparison.OrdinalIgnoreCase)).ToArray();
        if (mappingRows.Length != 1 || !string.Equals(mappingRows[0].Sha256, MapPackageRecordKey.NormalizeHash(mappingSha256, nameof(mappingSha256)), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Package key mapping identity does not match the pinned source manifest");

        var sourceHashes = _manifestRows.Values.Select(row => new MapPackageRecordSource(
                row.Role + "/" + row.LogicalName, row.Sha256))
            .ToArray();
        return new MapPackageRecordKey(_identitySha256, canonicalPackage, sourceHashes,
            normalizedMappingName, mappingSha256, decoderFingerprintSha256,
            captureProducerFingerprintSha256, captureProfileSha256, schemaVersion,
            sourceManifestComplete: true);
    }

    private void EnsureActive() => _identity.EnsureActive();

    private static bool SetEquals(IEnumerable<string> left, IEnumerable<string> right) =>
        new HashSet<string>(left, PathComparer).SetEquals(right);

    private static string NormalizePath(string path, string label)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new InvalidDataException(label + " is empty");
        return Path.GetFullPath(path);
    }
}






