using System.Security.Cryptography;
using ArcPak;
using CUE4Parse.GameTypes.Theia.FileProvider;
using CUE4Parse.UE4.Versions;

namespace ArcPak.Tests;

/// <summary>
/// Fixture contract for a provider registration that is bound to a live source identity.
/// This is intentionally staged before MapPinnedProviderRegistration exists.
/// </summary>
internal static class MapPinnedProviderRegistrationChecks
{
    public static int Run(string evidenceRoot, string fixtureRoot)
    {
        Directory.CreateDirectory(evidenceRoot);
        var validRoot = Path.Combine(evidenceRoot, "valid");
        CopyFixtureSet(fixtureRoot, validRoot);
        var valid = CreateInputs(validRoot);
        using var identity = MapArchiveSourceIdentity.Acquire(valid.Inputs, ["archive", "mapping"]);
        var swappedBindings = valid.Inputs.ToArray();
        var s2Index = Array.FindIndex(swappedBindings, input => Path.GetFileName(input.Path).EndsWith("_s2.ucas", StringComparison.OrdinalIgnoreCase));
        var s3Index = Array.FindIndex(swappedBindings, input => Path.GetFileName(input.Path).EndsWith("_s3.ucas", StringComparison.OrdinalIgnoreCase));
        swappedBindings[s2Index] = swappedBindings[s2Index] with { Path = valid.Inputs[s3Index].Path };
        swappedBindings[s3Index] = swappedBindings[s3Index] with { Path = valid.Inputs[s2Index].Path };
        using (var bindingProvider = CreateProvider(valid.TaggedRoot))
            Reject(() => MapPinnedProviderRegistration.Register(bindingProvider, identity, swappedBindings, valid.ArchivePaths, valid.CompanionPaths,
                requiredMountedArchivePaths: valid.RequiredMountedPaths, globalAuxiliaryArchivePath: valid.AuxiliaryArchivePaths.Single()),
                "same physical set with swapped logical-path bindings is rejected");
        using var provider = CreateProvider(valid.TaggedRoot);
        var receipt = MapPinnedProviderRegistration.Register(
            provider, identity, valid.Inputs, valid.ArchivePaths, valid.CompanionPaths,
            requiredMountedArchivePaths: valid.RequiredMountedPaths, globalAuxiliaryArchivePath: valid.AuxiliaryArchivePaths.Single());

        Reject(() => receipt.CreateCompletePackageKey(provider, "/Game/Fixtures/Maps/Empty",
            valid.MappingName, HashFile(valid.MappingPath), Fingerprint("decoder-v1"), Fingerprint("producer-v1"),
            MapRawPackageRecord.CaptureProfileSha256(true), MapRawPackageRecord.SchemaVersion),
            "cannot build a key before all declared readers mount");
        Check(receipt.RegisteredArchivePaths, valid.ArchivePaths, "registered archive set is exact");
        Check(receipt.OpenedContainerPaths, valid.CompanionPaths, "opened IoStore partition set is exact");

        provider.Mount();
        provider.PostMount();
        receipt.EnsureCompleteMountedSet(provider);
        Check(receipt.MountedArchivePaths, valid.RequiredMountedPaths, "all and only required content archives mounted");
        Check(provider.UnloadedVfs.Select(reader => reader.Path), valid.AuxiliaryArchivePaths, "only declared global auxiliary reader remains unloaded");
        if (provider.GlobalData is null) throw new InvalidDataException("Declared global auxiliary data was not initialized");
        var key = receipt.CreateCompletePackageKey(provider, "/Game/Fixtures/Maps/Empty",
            valid.MappingName, HashFile(valid.MappingPath), Fingerprint("decoder-v1"), Fingerprint("producer-v1"),
            MapRawPackageRecord.CaptureProfileSha256(true), MapRawPackageRecord.SchemaVersion);
        if (!string.Equals(key.GameArchiveIdentitySha256, identity.IdentitySha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Complete package key did not bind the source identity");
        if (!string.Equals(key.MappingSha256, HashFile(valid.MappingPath), StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(key.CaptureProfileSha256, MapRawPackageRecord.CaptureProfileSha256(true), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Complete package key omitted mapping or raw-profile identity");
        var checks = 10;

        // The set is derived from the source snapshot and registration plan. A source input
        // classified as an archive cannot silently be omitted from explicit registration.
        var extraRoot = Path.Combine(evidenceRoot, "extra");
        CopyFixtureSet(fixtureRoot, extraRoot);
        var extra = CreateInputs(extraRoot);
        var roguePak = Path.Combine(extra.TaggedRoot, "unregistered-extra.pak");
        File.WriteAllBytes(roguePak, [0x50, 0x41, 0x4b, 0x00]);
        extra.Inputs.Add(new MapArchiveSourceInput("archive", "unregistered-extra.pak", roguePak));
        using (var extraIdentity = MapArchiveSourceIdentity.Acquire(extra.Inputs, ["archive", "mapping"]))
        using (var extraProvider = CreateProvider(extra.TaggedRoot))
        {
            Reject(() => MapPinnedProviderRegistration.Register(extraProvider, extraIdentity,
                extra.Inputs, extra.ArchivePaths, extra.CompanionPaths,
                requiredMountedArchivePaths: extra.RequiredMountedPaths, globalAuxiliaryArchivePath: extra.AuxiliaryArchivePaths.Single()), "extra source cannot be omitted from registration");
        }
        checks++;

        var unexpectedGlobalRoot = Path.Combine(evidenceRoot, "unexpected-global");
        CopyFixtureSet(fixtureRoot, unexpectedGlobalRoot);
        var unexpectedGlobal = CreateInputs(unexpectedGlobalRoot);
        var secondGlobalPath = Path.Combine(unexpectedGlobal.TaggedRoot, "unexpected", "global.utoc");
        Directory.CreateDirectory(Path.GetDirectoryName(secondGlobalPath)!);
        File.WriteAllBytes(secondGlobalPath, [0x55, 0x54, 0x4f, 0x43]);
        unexpectedGlobal.Inputs.Add(new MapArchiveSourceInput("archive", "unexpected/global.utoc", secondGlobalPath));
        unexpectedGlobal.ArchivePaths.Add(secondGlobalPath);
        unexpectedGlobal.RequiredMountedPaths.Add(secondGlobalPath);
        using (var unexpectedGlobalIdentity = MapArchiveSourceIdentity.Acquire(unexpectedGlobal.Inputs, ["archive", "mapping"]))
        using (var unexpectedGlobalProvider = CreateProvider(unexpectedGlobal.TaggedRoot))
            Reject(() => MapPinnedProviderRegistration.Register(unexpectedGlobalProvider, unexpectedGlobalIdentity,
                unexpectedGlobal.Inputs, unexpectedGlobal.ArchivePaths, unexpectedGlobal.CompanionPaths,
                requiredMountedArchivePaths: unexpectedGlobal.RequiredMountedPaths,
                globalAuxiliaryArchivePath: unexpectedGlobal.AuxiliaryArchivePaths.Single()),
                "an undeclared second global.utoc cannot enter the required reader set");
        checks++;
        // An IoStore callback asking for a companion absent from the frozen manifest must fail closed.
        var missingRoot = Path.Combine(evidenceRoot, "missing-partition");
        CopyFixtureSet(fixtureRoot, missingRoot);
        var missing = CreateInputs(missingRoot);
        var lastPartition = missing.CompanionPaths.Single(path => Path.GetFileName(path).EndsWith("_s4.ucas", StringComparison.OrdinalIgnoreCase));
        missing.Inputs.RemoveAll(input => string.Equals(input.Path, lastPartition, StringComparison.OrdinalIgnoreCase));
        missing.CompanionPaths.Remove(lastPartition);
        using (var missingIdentity = MapArchiveSourceIdentity.Acquire(missing.Inputs, ["archive", "mapping"]))
        using (var missingProvider = CreateProvider(missing.TaggedRoot))
            Reject(() => MapPinnedProviderRegistration.Register(missingProvider, missingIdentity,
                missing.Inputs, missing.ArchivePaths, missing.CompanionPaths,
                requiredMountedArchivePaths: missing.RequiredMountedPaths, globalAuxiliaryArchivePath: missing.AuxiliaryArchivePaths.Single()), "unlisted split partition cannot open");
        checks++;

        // A valid replacement at the same logical partition name changes the physical identity.
        var swappedRoot = Path.Combine(evidenceRoot, "swapped-partition");
        CopyFixtureSet(fixtureRoot, swappedRoot);
        var swapped = CreateInputs(swappedRoot);
        var sourcePart = swapped.CompanionPaths.Single(path => Path.GetFileName(path).EndsWith("_s2.ucas", StringComparison.OrdinalIgnoreCase));
        var targetPart = swapped.CompanionPaths.Single(path => Path.GetFileName(path).EndsWith("_s4.ucas", StringComparison.OrdinalIgnoreCase));
        File.Copy(sourcePart, targetPart, overwrite: true);
        using (var swappedIdentity = MapArchiveSourceIdentity.Acquire(swapped.Inputs, ["archive", "mapping"]))
        {
            if (string.Equals(identity.IdentitySha256, swappedIdentity.IdentitySha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Substituted partition retained the prior source identity");
        }
        checks++;

        var partitionedArchive = valid.ArchivePaths.Single(path => !Path.GetFileName(path).Equals("global.utoc", StringComparison.OrdinalIgnoreCase));
        var globalArchive = valid.ArchivePaths.Single(path => Path.GetFileName(path).Equals("global.utoc", StringComparison.OrdinalIgnoreCase));
        var partitionedCompanion = valid.CompanionPaths.Single(path => Path.GetFileName(path).EndsWith("_s1.ucas", StringComparison.OrdinalIgnoreCase));
        var globalCompanion = valid.CompanionPaths.Single(path => Path.GetFileName(path).Equals("global.ucas", StringComparison.OrdinalIgnoreCase));
        var callbackOpenedArchive = false;
        var partitionCallback = MapPinnedProviderRegistration.CreateContainerOpener(partitionedArchive, _ =>
        {
            callbackOpenedArchive = true;
            throw new InvalidDataException("Test open delegate should not run for a swapped owner path");
        });
        Reject(() => partitionCallback(globalCompanion), "the actual owner-bound IoStore callback rejects another reader's listed companion");
        if (callbackOpenedArchive)
            throw new InvalidDataException("A cross-container swapped partition reached the archive opener");
        if (!MapPinnedProviderRegistration.IsOwnedPartitionPath(partitionedArchive, partitionedCompanion) ||
            !MapPinnedProviderRegistration.IsOwnedPartitionPath(globalArchive, globalCompanion) ||
            MapPinnedProviderRegistration.IsOwnedPartitionPath(partitionedArchive, globalCompanion) ||
            MapPinnedProviderRegistration.IsOwnedPartitionPath(globalArchive, partitionedCompanion))
            throw new InvalidDataException("IoStore archive-to-partition ownership check returned an incorrect result");
        checks += 2;

        Console.WriteLine($"PASS {checks} pinned provider registration checks");
        return checks;
    }

    private static TheiaFileProvider CreateProvider(string root) => new(
        root, SearchOption.TopDirectoryOnly, new VersionContainer(EGame.GAME_UE5_8), StringComparer.OrdinalIgnoreCase);

    private static (string TaggedRoot, string MappingPath, string MappingName,
        List<MapArchiveSourceInput> Inputs, List<string> ArchivePaths, List<string> CompanionPaths,
        List<string> RequiredMountedPaths, List<string> AuxiliaryArchivePaths) CreateInputs(string root)
    {
        var tagged = Path.Combine(root, "UE5_8", "IoStore", "Tagged");
        var partitioned = Path.Combine(tagged, "OodlePartitioned");
        var mapping = Path.Combine(root, "UE5_8", "Mappings", "CUE4ParseFixtures-Oodle.usmap");
        var archives = new List<string>
        {
            Path.Combine(tagged, "global.utoc"),
            Path.Combine(partitioned, "CUE4ParseFixtures-Minimal-OodlePartitioned-Windows.utoc"),
        };
        var companions = new List<string> { Path.Combine(tagged, "global.ucas") };
        companions.AddRange(Directory.EnumerateFiles(partitioned, "*.ucas", SearchOption.TopDirectoryOnly)
            .Order(StringComparer.OrdinalIgnoreCase));
        var inputs = archives.Select(path => new MapArchiveSourceInput("archive", Path.GetRelativePath(root, path), path))
            .Concat(companions.Select(path => new MapArchiveSourceInput("archive", Path.GetRelativePath(root, path), path)))
            .Append(new MapArchiveSourceInput("mapping", Path.GetFileName(mapping), mapping))
            .ToList();
        var auxiliary = archives.Where(path => Path.GetFileName(path).Equals("global.utoc", StringComparison.OrdinalIgnoreCase)).ToList();
        var requiredMounted = archives.Except(auxiliary, StringComparer.OrdinalIgnoreCase).ToList();
        return (tagged, mapping, Path.GetFileName(mapping), inputs, archives, companions, requiredMounted, auxiliary);
    }

    private static void CopyFixtureSet(string fixtureRoot, string destinationRoot)
    {
        var sourceTagged = Path.Combine(fixtureRoot, "UE5_8", "IoStore", "Tagged");
        var sourcePartitioned = Path.Combine(sourceTagged, "OodlePartitioned");
        var sourceMapping = Path.Combine(fixtureRoot, "UE5_8", "Mappings", "CUE4ParseFixtures-Oodle.usmap");
        var targetTagged = Path.Combine(destinationRoot, "UE5_8", "IoStore", "Tagged");
        var targetPartitioned = Path.Combine(targetTagged, "OodlePartitioned");
        var targetMappingDir = Path.Combine(destinationRoot, "UE5_8", "Mappings");
        Directory.CreateDirectory(targetPartitioned);
        Directory.CreateDirectory(targetMappingDir);
        foreach (var source in Directory.EnumerateFiles(sourceTagged, "global.*", SearchOption.TopDirectoryOnly)
                     .Concat(Directory.EnumerateFiles(sourcePartitioned, "*", SearchOption.TopDirectoryOnly)))
            File.Copy(source, Path.Combine(source.Contains(sourcePartitioned, StringComparison.OrdinalIgnoreCase)
                ? targetPartitioned : targetTagged, Path.GetFileName(source)));
        File.Copy(sourceMapping, Path.Combine(targetMappingDir, Path.GetFileName(sourceMapping)));
    }

    private static string HashFile(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static string Fingerprint(string value) => Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static void Check(IEnumerable<string> actual, IEnumerable<string> expected, string name)
    {
        var actualSet = actual.Select(Path.GetFullPath).Order(StringComparer.OrdinalIgnoreCase).ToArray();
        var expectedSet = expected.Select(Path.GetFullPath).Order(StringComparer.OrdinalIgnoreCase).ToArray();
        if (!actualSet.SequenceEqual(expectedSet, StringComparer.OrdinalIgnoreCase))
            throw new InvalidDataException("FAIL " + name + ": expected [" + string.Join(",", expectedSet) + "] got [" + string.Join(",", actualSet) + "]");
        Console.WriteLine("PASS " + name);
    }

    private static void Reject(Action action, string name)
    {
        try { action(); }
        catch (Exception exception) when (exception is InvalidDataException or InvalidOperationException or FileNotFoundException)
        {
            Console.WriteLine("PASS " + name);
            return;
        }
        throw new InvalidDataException("FAIL " + name);
    }
}






