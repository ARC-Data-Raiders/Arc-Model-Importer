using System.Text;
using System.Text.RegularExpressions;
using CUE4Parse.UE4.Shaders;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace ArcPak;

internal static class ShaderRecordExport
{
    internal static int Run(string[] args)
    {
        try
        {
            var o = ArcVerbCommon.Parse(args, new[] { "game-dir", "usmap", "out", "map-hash", "map-hash-file", "archive-contains" }, new[] { "all-maps" });
            var game = ArcVerbCommon.Required(o, "game-dir");
            var usmap = ArcVerbCommon.Required(o, "usmap");
            var output = ArcVerbCommon.Required(o, "out");
            var hashes = new SortedSet<string>(StringComparer.Ordinal);
            if (o.TryGetValue("map-hash", out var direct)) foreach (var h in direct) hashes.Add(h.ToUpperInvariant());
            if (o.TryGetValue("map-hash-file", out var files)) foreach (var file in files) foreach (var h in ArcVerbCommon.ReadList(file)) hashes.Add(h.ToUpperInvariant());
            if (hashes.Any(h => !Regex.IsMatch(h, "^[0-9A-F]{40}$"))) throw new ArgumentException("Map hash must be full 40 hex");
            var all = o.ContainsKey("all-maps");
            var contains = all ? ArcVerbCommon.Required(o, "archive-contains") : null;
            if (hashes.Count == 0 && !all) throw new ArgumentException("At least one selector is required");
            using var provider = ArcVerbCommon.Open(game, usmap, false);
            var found = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var skipped = new JArray();
            var failed = new JArray();
            var records = new List<JObject>();
            foreach (var e in provider.Files.Values.Where(e => e.Extension.Equals("ushaderbytecode", StringComparison.OrdinalIgnoreCase) || e.Extension.Equals("ushadercode", StringComparison.OrdinalIgnoreCase)).OrderBy(e => e.Path, StringComparer.OrdinalIgnoreCase))
            {
                using var reader = e.CreateReader();
                var parsed = new FShaderCodeArchive(reader);
                if (parsed.SerializedShaders is not FIoStoreShaderCodeArchive io)
                {
                    skipped.Add(e.Path);
                    continue;
                }
                var cache = new Dictionary<uint, byte[]?>();
                var offsets = io.ShaderEntries.GroupBy(entry => entry.ShaderGroupIndex).ToDictionary(g => g.Key, g => g.Select(entry => entry.UncompressedOffsetInGroup).Distinct().OrderBy(v => v).ToArray());
                for (var m = 0; m < io.ShaderMapHashes.Length; m++)
                {
                    var mapHash = io.ShaderMapHashes[m].ToString().ToUpperInvariant();
                    if (!hashes.Contains(mapHash) && !(all && e.Path.Contains(contains!, StringComparison.OrdinalIgnoreCase))) continue;
                    found.Add(mapHash);
                    var map = io.ShaderMapEntries[m];
                    for (uint n = 0; n < map.NumShaders; n++)
                    {
                        var s = (int)io.ShaderIndices[map.ShaderIndicesOffset + n];
                        var entry = io.ShaderEntries[s];
                        var g = entry.ShaderGroupIndex;
                        var meta = io.ShaderGroupEntries[g];
                        if (!cache.TryGetValue(g, out var bytes))
                        {
                            bytes = null;
                            if (IoStoreShaderCodeExtractor.TryReadChunk(provider, io.ShaderGroupIoHashes[g], out var c))
                            {
                                if (meta.CompressedSize > 0 && c.Length > meta.CompressedSize) c = c.AsSpan(0, (int)meta.CompressedSize).ToArray();
                                if (IoStoreShaderCodeExtractor.TryDecompressGroup(c, (int)meta.UncompressedSize, out var decoded)) bytes = decoded;
                            }
                            cache[g] = bytes;
                        }
                        if (bytes == null)
                        {
                            failed.Add(new JObject { ["archive_name"] = e.NameWithoutExtension, ["map_hash"] = mapHash, ["shader_index"] = s, ["reason"] = "group_failed" });
                            continue;
                        }
                        var off = entry.UncompressedOffsetInGroup;
                        var end = offsets[g].FirstOrDefault(v => v > off && v < meta.UncompressedSize, meta.UncompressedSize);
                        if (off >= bytes.Length || end > bytes.Length || end <= off)
                        {
                            failed.Add(new JObject { ["archive_name"] = e.NameWithoutExtension, ["map_hash"] = mapHash, ["shader_index"] = s, ["reason"] = "slice_out_of_range" });
                            continue;
                        }
                        var raw = bytes.AsSpan((int)off, (int)(end - off)).ToArray();
                        var trimmed = IoStoreShaderCodeExtractor.TrimToGpuBinary(raw);
                        var containerOffset = raw.AsSpan().IndexOf(trimmed);
                        var magic = trimmed.Length >= 4 ? Encoding.ASCII.GetString(trimmed, 0, 4) : "NONE";
                        if (magic != "DXBC" && magic != "DXIL") magic = "NONE";
                        var legacyEqual = trimmed.SequenceEqual(IoStoreShaderCodeExtractor.SliceShaderFromGroup(bytes, io, s));
                        var frequency = entry.Frequency.ToString();
                        var shaderHash = io.ShaderHashes[s].ToString().ToUpperInvariant();
                        var stem = $"{e.NameWithoutExtension}/{mapHash}/{s:D4}_{frequency}_{shaderHash}";
                        var recordFile = stem + ".ushaderrecord";
                        var containerFile = stem + (magic == "DXBC" ? ".dxbc" : magic == "DXIL" ? ".dxil" : ".bin");
                        ArcVerbCommon.WriteAtomic(Path.Combine(output, recordFile.Replace('/', Path.DirectorySeparatorChar)), raw);
                        ArcVerbCommon.WriteAtomic(Path.Combine(output, containerFile.Replace('/', Path.DirectorySeparatorChar)), trimmed);
                        records.Add(new JObject { ["archive_path"] = e.Path, ["archive_name"] = e.NameWithoutExtension, ["map_hash"] = mapHash, ["map_index"] = m, ["shader_index"] = s, ["shader_hash"] = shaderHash, ["frequency"] = frequency, ["frequency_raw"] = (int)entry.Frequency, ["group_index"] = g, ["offset_in_group"] = off, ["record_size"] = raw.Length, ["record_sha256"] = ArcVerbCommon.Sha256(raw), ["container_magic"] = magic, ["container_offset"] = containerOffset, ["container_size"] = trimmed.Length, ["trailer_size"] = raw.Length - containerOffset - trimmed.Length, ["container_sha256"] = ArcVerbCommon.Sha256(trimmed), ["legacy_equal"] = legacyEqual, ["record_file"] = recordFile, ["container_file"] = containerFile });
                    }
                }
            }
            var manifest = new JObject { ["schema"] = "dr.shader-records/1", ["build_id"] = GameBuild.Read(game).BuildId, ["usmap_sha256"] = ArcVerbCommon.Sha256(File.ReadAllBytes(usmap)), ["requested_map_hashes"] = new JArray(hashes), ["missing_map_hashes"] = new JArray(hashes.Where(h => !found.Contains(h))), ["skipped_archives"] = skipped, ["failed"] = failed, ["records"] = new JArray(records.OrderBy(r => (string?)r["archive_name"], StringComparer.Ordinal).ThenBy(r => (string?)r["map_hash"], StringComparer.Ordinal).ThenBy(r => (int)r["shader_index"]!)) };
            ArcVerbCommon.WriteAtomic(Path.Combine(output, "shader-records.manifest.json"), Encoding.UTF8.GetBytes(manifest.ToString(Formatting.Indented)));
            return 0;
        }
        catch (ArgumentException e)
        {
            Log.Error(e.Message);
            return 1;
        }
    }
}
