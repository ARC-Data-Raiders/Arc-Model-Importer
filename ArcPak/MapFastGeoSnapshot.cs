using System.Security.Cryptography;
using System.Text;
using System.Runtime.Serialization;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Exports.FastGeoStreaming;
using CUE4Parse.UE4.Assets.Objects.Properties;
using CUE4Parse.UE4.Objects.Core.Math;
using FModel.Exporters.MapPlacement;
using FModel.Views.Snooper.MapPlacement;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;

namespace ArcPak;

internal sealed record FastGeoSpoolSlice(long Offset, int Length, string Sha256);
internal sealed record FastGeoArraySlices(string Name, IReadOnlyList<FastGeoSpoolSlice> Elements);
internal sealed record FastGeoStreamRow(JObject Header, IReadOnlyList<FastGeoArraySlices> Arrays,
    IReadOnlyList<FastGeoSpoolSlice> Placements);
internal sealed record FastGeoDetachedCapture(string SpoolPath, int SliceLimit,
    IReadOnlyList<FastGeoStreamRow> Rows, IReadOnlyList<string> Diagnostics,
    IReadOnlyList<string> Dependencies, int PeakPayloadBytes, long AggregateSpoolBytes,
    int PlacementCount);
internal sealed record FastGeoStreamWriteStats(int PeakPayloadBytes, long AggregateSpoolBytes);

internal static class FastGeoSnapshot
{
    internal static FastGeoDetachedCapture Capture(IEnumerable<MapTraversalObject> objects, string spoolDirectory, int sliceLimit)
    {
        if (sliceLimit < 1) throw new ArgumentOutOfRangeException(nameof(sliceLimit));
        Directory.CreateDirectory(spoolDirectory);
        var spoolPath = Path.Combine(spoolDirectory, "fastgeo.spool");
        var rows = new List<FastGeoStreamRow>();
        var diagnostics = new SortedSet<string>(StringComparer.Ordinal);
        var dependencies = new SortedSet<string>(StringComparer.Ordinal);
        var peak = 0;
        var placementsCount = 0;
        var serializer = CreateFastGeoSerializer();
        using var spool = new FileStream(spoolPath, FileMode.Create, FileAccess.Write, FileShare.Read, 1, FileOptions.SequentialScan);
        foreach (var item in objects.Where(value => value.Source is UFastGeoContainer))
        {
            var container = (UFastGeoContainer)item.Source!;
            var localDiagnostics = new SortedSet<string>(StringComparer.Ordinal);
            var header = new JObject
            {
                ["id"] = item.Id,
                ["sourcePath"] = item.SourcePath,
                ["worldInstancePath"] = item.WorldInstancePath,
                ["matrix"] = MapBundleDocument.Matrix(item.WorldTransformCm)
            };
            var arrays = new List<FastGeoArraySlices>();
            var rowCommitted = false;
            var rowPlacementSlices = new List<FastGeoSpoolSlice>();
            try
            {
                arrays.Add(WriteArray(spool, "clusters", (container.ComponentClusters ?? []).Cast<object>(), serializer, sliceLimit, ref peak));
                arrays.Add(WriteArray(spool, "hlods", (container.HLODs ?? []).Cast<object>(), serializer, sliceLimit, ref peak));
                arrays.Add(WriteArray(spool, "assets", (container.Assets ?? []).Cast<object>(), serializer, sliceLimit, ref peak));
                // The raw resource row is complete now. Later expansion failures keep this row and placements already emitted.
                rowCommitted = true;
                foreach (var cluster in container.ComponentClusters ?? [])
                {
                    foreach (var component in (cluster.StaticMeshComponents ?? []).Cast<FFastGeoStaticMeshComponentBase>()
                        .Concat(cluster.InstancedStaticMeshComponents ?? []))
                    {
                        var mesh = component.SceneProxyDesc?.StaticMeshSceneProxyDesc?.StaticMesh;
                        var meshPath = mesh?.ResolvedObject?.GetPathName();
                        if (string.IsNullOrEmpty(meshPath)) throw new InvalidDataException("FastGeo mesh reference unresolved");
                        var sourcePath = item.SourcePath + "/cluster:" + cluster.ComponentClusterIndex + "/component:" + component.ComponentIndex;
                        var overrides = (component.OverrideMaterials ?? []).Select(material =>
                            material == null || material.IsNull ? "" : material.ResolvedObject?.GetPathName() ?? "").ToArray();
                        void Add(FTransform transform, int? index)
                        {
                            var record = MapPlacementRecord.FromTransform(cluster.Name, meshPath, "FastGeoStaticMesh",
                                transform * component.WorldTransform * item.WorldTransformCm, item.WorldPath,
                                componentPath: sourcePath, instanceIndex: index, worldInstancePath: item.WorldInstancePath,
                                materialOverrides: overrides);
                            var slice = WritePlacement(spool, record, component, serializer, sliceLimit, ref peak);
                            rowPlacementSlices.Add(slice);
                            placementsCount++;
                            AddDependency(string.IsNullOrEmpty(record.AssetPath) ? "" : MapBundleDocument.CanonicalPackage(record.AssetPath), dependencies);
                            foreach (var material in record.MaterialOverrides)
                                AddDependency(string.IsNullOrEmpty(material) ? "" : MapBundleDocument.CanonicalObject(material), dependencies);
                        }
                        if (component is FFastGeoInstancedStaticMeshComponent instanced)
                        {
                            if (instanced.PerInstanceSMData == null) throw new InvalidDataException("FastGeo instance data unavailable");
                            for (var index = 0; index < instanced.PerInstanceSMData.Length; index++)
                                Add(instanced.PerInstanceSMData[index].TransformData, index);
                        }
                        else Add(FTransform.Identity, null);
                    }
                    if ((cluster.SkinnedMeshComponents?.Length ?? 0) + (cluster.InstancedSkinnedMeshComponents?.Length ?? 0) +
                        (cluster.ProceduralISMComponents?.Length ?? 0) > 0)
                        localDiagnostics.Add("FastGeo skinned or procedural components remain raw: " + item.SourcePath + ":" + cluster.ComponentClusterIndex);
                }
                if ((container.HLODs?.Length ?? 0) > 0)
                    localDiagnostics.Add("FastGeo HLOD data is retained without expansion: " + item.SourcePath);
            }
            catch (Exception exception)
            {
                localDiagnostics.Add("FastGeo expansion failed: " + item.SourcePath + ": " + exception.Message);
            }
            foreach (var diagnostic in localDiagnostics) diagnostics.Add(diagnostic);
            if (rowCommitted) rows.Add(new FastGeoStreamRow(header, arrays, rowPlacementSlices.ToArray()));
            else if (rowPlacementSlices.Count > 0)
                throw new InvalidDataException("FastGeo placement was emitted before its resource row was complete");
        }
        spool.Flush(true);
        return new FastGeoDetachedCapture(spoolPath, sliceLimit, rows.ToArray(), diagnostics.ToArray(), dependencies.ToArray(),
            peak, spool.Length, placementsCount);
    }

    private const int ConsumerBufferBudgetBytes = 12 * 1024;

    private static JsonSerializer CreateFastGeoSerializer()
    {
        var serializer = JsonSerializer.CreateDefault();
        if (serializer.ContractResolver is not DefaultContractResolver defaults)
            throw new InvalidDataException("FastGeo direct serialization requires a Newtonsoft DefaultContractResolver policy");
        serializer.ContractResolver = new FastGeoCanonicalContractResolver(defaults.NamingStrategy);
        return serializer;
    }

    private static FastGeoArraySlices WriteArray(Stream spool, string name, IEnumerable<object> values,
        JsonSerializer serializer, int limit, ref int peak)
    {
        var slices = new List<FastGeoSpoolSlice>();
        foreach (var value in values)
            slices.Add(WriteJsonRecord(spool, serializer, limit, ref peak, writer => serializer.Serialize(writer, value)));
        return new FastGeoArraySlices(name, slices.ToArray());
    }

    private static FastGeoSpoolSlice WritePlacement(Stream spool, MapPlacementRecord record,
        FFastGeoStaticMeshComponentBase component, JsonSerializer serializer, int limit, ref int peak)
    {
        if (string.IsNullOrWhiteSpace(record.PlacementId))
            throw new InvalidDataException("Placement has no stable component identity");
        var asset = string.IsNullOrEmpty(record.AssetPath) ? "" : MapBundleDocument.CanonicalPackage(record.AssetPath);
        var materialObjects = record.MaterialOverrides.Select(path => string.IsNullOrEmpty(path) ? "" : MapBundleDocument.CanonicalObject(path)).ToArray();
        var materialPackages = record.MaterialOverrides.Select(path => string.IsNullOrEmpty(path) ? "" : MapBundleDocument.CanonicalPackage(path)).ToArray();
        return WriteJsonRecord(spool, serializer, limit, ref peak, writer =>
        {
            writer.WriteStartObject();
            writer.WritePropertyName("asset"); writer.WriteValue(asset);
            writer.WritePropertyName("id"); writer.WriteValue(record.PlacementId);
            writer.WritePropertyName("kind"); writer.WriteValue(record.AssetKind);
            writer.WritePropertyName("materialObjects"); WriteStrings(writer, materialObjects);
            writer.WritePropertyName("materialPackages"); WriteStrings(writer, materialPackages);
            writer.WritePropertyName("materials"); WriteStrings(writer, materialObjects);
            writer.WritePropertyName("matrix"); WritePlacementMatrix(writer, record);
            writer.WritePropertyName("properties");
            writer.WriteStartObject();
            writer.WritePropertyName("actorName"); writer.WriteValue(record.ActorName);
            writer.WritePropertyName("componentPath"); writer.WriteValue(record.ComponentPath);
            writer.WritePropertyName("instanceIndex"); writer.WriteValue(record.InstanceIndex);
            writer.WritePropertyName("notes"); writer.WriteValue(record.Notes);
            writer.WritePropertyName("raw"); serializer.Serialize(writer, component);
            writer.WritePropertyName("sourceAssetKey"); writer.WriteValue(record.AssetPath);
            writer.WritePropertyName("sourceFile"); writer.WriteValue(record.SourceFile);
            writer.WritePropertyName("worldInstancePath"); writer.WriteValue(record.WorldInstancePath);
            writer.WriteEndObject();
            writer.WriteEndObject();
        });
    }

    private static void WriteStrings(JsonWriter writer, IEnumerable<string> values)
    {
        writer.WriteStartArray();
        foreach (var value in values) writer.WriteValue(value);
        writer.WriteEndArray();
    }

    private static void WritePlacementMatrix(JsonWriter writer, MapPlacementRecord record)
    {
        var transform = new FTransform(new FRotator(record.Pitch, record.Yaw, record.Roll),
            new FVector(record.X, record.Y, record.Z), new FVector(record.ScaleX, record.ScaleY, record.ScaleZ));
        var matrix = transform.ToMatrixWithScale();
        Span<float> values = stackalloc float[]
        {
            matrix.M00, matrix.M10, matrix.M20, matrix.M30,
            matrix.M01, matrix.M11, matrix.M21, matrix.M31,
            matrix.M02, matrix.M12, matrix.M22, matrix.M32,
            matrix.M03, matrix.M13, matrix.M23, matrix.M33
        };
        writer.WriteStartArray();
        foreach (var value in values)
        {
            if (!float.IsFinite(value)) throw new InvalidDataException("Non-finite placement transform");
            writer.WriteValue(value);
        }
        writer.WriteEndArray();
    }

    private static FastGeoSpoolSlice WriteJsonRecord(Stream spool, JsonSerializer serializer, int limit, ref int peak, Action<JsonWriter> write)
    {
        using var bounded = new BoundedHashingWriteStream(spool, limit);
        var oldContext = serializer.Context;
        serializer.Context = new StreamingContext(oldContext.State, new FastGeoRecordScratch(limit));
        try
        {
            using (var text = new StreamWriter(bounded, new UTF8Encoding(false), 1024, leaveOpen: true))
            using (var writer = new JsonTextWriter(text) { Formatting = Formatting.Indented, Indentation = 2, IndentChar = ' ', CloseOutput = false })
            {
                write(writer);
                writer.Flush();
                text.Write('\n');
                text.Flush();
            }
            return new FastGeoSpoolSlice(bounded.Offset, bounded.SliceLength, bounded.FinishHash());
        }
        finally
        {
            serializer.Context = oldContext;
            peak = Math.Max(peak, bounded.SliceLength);
        }
    }

    private static void EnsurePayloadLimit(int residentBytes, int limit)
    {
        if (residentBytes > limit)
            throw new InvalidDataException($"FastGeo resident payload exceeds byte limit: {residentBytes} > {limit}");
    }

    private sealed class FastGeoRecordScratch(int maximum)
    {
        private long used;
        internal long Maximum { get; } = maximum;

        internal void Reserve(long bytes)
        {
            if (bytes < 0 || used > maximum - bytes)
                throw new InvalidDataException($"FastGeo canonical property index exceeds byte limit: {used + bytes} > {maximum}");
            used += bytes;
        }

        internal void Release(long bytes) => used -= bytes;
    }

    private sealed class FastGeoCanonicalContractResolver : DefaultContractResolver
    {
        internal FastGeoCanonicalContractResolver(NamingStrategy? namingStrategy) => NamingStrategy = namingStrategy;

        protected override IList<JsonProperty> CreateProperties(Type type, MemberSerialization memberSerialization) =>
            base.CreateProperties(type, memberSerialization)
                .OrderBy(property => property.PropertyName, StringComparer.Ordinal).ToList();

        protected override JsonContract CreateContract(Type objectType)
        {
            var contract = base.CreateContract(objectType);
            if (objectType == typeof(FStructFallback)) contract.Converter = OrderedStructFallbackConverter.Instance;
            return contract;
        }
    }

    private sealed class OrderedStructFallbackConverter : JsonConverter<FStructFallback>
    {
        internal static readonly OrderedStructFallbackConverter Instance = new();

        public override void WriteJson(JsonWriter writer, FStructFallback? value, JsonSerializer serializer)
        {
            if (value == null) { writer.WriteNull(); return; }
            if (serializer.Context.Context is not FastGeoRecordScratch scratch)
                throw new InvalidDataException("FastGeo fallback serialization has no bounded record scratch budget");
            var properties = new SortedDictionary<string, FPropertyTag>(StringComparer.Ordinal);
            long reservedBytes = 0;
            try
            {
                foreach (var property in value.Properties)
                {
                    if (property == null) continue;
                    var baseName = property.Name.Text;
                    var suffix = property.ArrayIndex > 0 ? "[" + property.ArrayIndex.ToString(System.Globalization.CultureInfo.InvariantCulture) + "]" : "";
                    if ((long)baseName.Length + suffix.Length > scratch.Maximum / 2)
                        throw new InvalidDataException("FastGeo canonical property key exceeds byte limit");
                    var name = suffix.Length == 0 ? baseName : baseName + suffix;
                    if (!properties.ContainsKey(name))
                    {
                        // Reserve for the key string, sorted-tree node, references, and bucket/object overhead.
                        var reservation = 96L + name.Length * 2L;
                        scratch.Reserve(reservation);
                        reservedBytes += reservation;
                    }
                    properties[name] = property;
                }
                writer.WriteStartObject();
                foreach (var property in properties)
                {
                    writer.WritePropertyName(property.Key);
                    serializer.Serialize(writer, property.Value.Tag);
                }
                writer.WriteEndObject();
            }
            finally { scratch.Release(reservedBytes); }
        }

        public override FStructFallback? ReadJson(JsonReader reader, Type objectType, FStructFallback? existingValue,
            bool hasExistingValue, JsonSerializer serializer) => throw new NotSupportedException();
    }

    private sealed class BoundedHashingWriteStream(Stream destination, int maximum) : Stream
    {
        private readonly IncrementalHash hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        public long Offset { get; } = destination.Position;
        public int SliceLength { get; private set; }
        public override long Length => SliceLength;
        public override bool CanRead => false;
        public override bool CanSeek => false;
        public override bool CanWrite => true;
        public override long Position { get => SliceLength; set => throw new NotSupportedException(); }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override void Flush() => destination.Flush();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count)
        {
            EnsureCapacity(count);
            destination.Write(buffer, offset, count);
            hash.AppendData(buffer, offset, count);
            SliceLength += count;
        }
        public override void Write(ReadOnlySpan<byte> buffer)
        {
            EnsureCapacity(buffer.Length);
            destination.Write(buffer);
            hash.AppendData(buffer);
            SliceLength += buffer.Length;
        }
        public override void WriteByte(byte value)
        {
            EnsureCapacity(1);
            destination.WriteByte(value);
            Span<byte> one = stackalloc byte[1];
            one[0] = value;
            hash.AppendData(one);
            SliceLength++;
        }
        public string FinishHash() => Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
        private void EnsureCapacity(int count)
        {
            if (count < 0 || SliceLength + count > maximum)
                throw new InvalidDataException($"FastGeo JSON slice exceeds byte limit: {SliceLength + count} > {maximum}");
        }
        protected override void Dispose(bool disposing)
        {
            if (disposing) hash.Dispose();
            base.Dispose(disposing);
        }
    }

    private static void AddDependency(string? value, ISet<string> dependencies)
    {
        if (!string.IsNullOrEmpty(value)) dependencies.Add(value);
    }

    internal static FastGeoStreamWriteStats WriteBundleAtomic(string root, string relative, JObject bundle, FastGeoDetachedCapture? capture)
    {
        var destination = MapBundleDocument.Inside(root, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        var temporary = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
        var consumerPeak = 0;
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1, FileOptions.SequentialScan))
            using (var text = new StreamWriter(stream, new UTF8Encoding(false), 1024, leaveOpen: true))
            using (var writer = new JsonTextWriter(text) { Formatting = Formatting.Indented, Indentation = 2, IndentChar = ' ', CloseOutput = false })
            {
                writer.WriteStartObject();
                foreach (var property in bundle.Properties().OrderBy(property => property.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    if (property.Name == "placements" && capture != null)
                        WritePlacements(writer, (JArray)property.Value, capture, ref consumerPeak);
                    else if (property.Name == "resources" && capture != null)
                        WriteResources(writer, (JObject)property.Value, capture, ref consumerPeak);
                    else WriteOrdered(writer, property.Value);
                }
                writer.WriteEndObject();
                writer.Flush();
                text.Flush();
                stream.WriteByte((byte)'\n');
                stream.Flush(true);
            }
            File.Move(temporary, destination, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
        return new FastGeoStreamWriteStats(consumerPeak, capture?.AggregateSpoolBytes ?? 0);
    }

    private static void WritePlacements(JsonWriter writer, JArray ordinary, FastGeoDetachedCapture capture, ref int peak)
    {
        writer.WriteStartArray();
        foreach (var token in ordinary) WriteOrdered(writer, token);
        using var spool = OpenSpool(capture);
        foreach (var row in capture.Rows)
            foreach (var slice in row.Placements) WriteSlice(writer, spool, capture, slice, ref peak);
        writer.WriteEndArray();
    }

    private static void WriteResources(JsonWriter writer, JObject resources, FastGeoDetachedCapture capture, ref int peak)
    {
        writer.WriteStartObject();
        foreach (var property in resources.Properties().Where(property => property.Name != "fastGeo")
            .Select(property => (Name: property.Name, Value: property.Value))
            .Append((Name: "fastGeo", Value: (JToken?)null))
            .OrderBy(property => property.Name, StringComparer.Ordinal))
        {
            writer.WritePropertyName(property.Name);
            if (property.Name == "fastGeo") WriteFastGeoRows(writer, capture, ref peak);
            else WriteOrdered(writer, property.Value!);
        }
        writer.WriteEndObject();
    }

    private static void WriteFastGeoRows(JsonWriter writer, FastGeoDetachedCapture capture, ref int peak)
    {
        writer.WriteStartArray();
        using var spool = OpenSpool(capture);
        foreach (var row in capture.Rows)
        {
            writer.WriteStartObject();
            var arrays = row.Arrays.ToDictionary(value => value.Name, StringComparer.Ordinal);
            var keys = row.Header.Properties().Select(property => property.Name).Concat(arrays.Keys).Order(StringComparer.Ordinal);
            foreach (var key in keys)
            {
                writer.WritePropertyName(key);
                if (arrays.TryGetValue(key, out var array))
                {
                    writer.WriteStartArray();
                    foreach (var slice in array.Elements) WriteSlice(writer, spool, capture, slice, ref peak);
                    writer.WriteEndArray();
                }
                else WriteOrdered(writer, row.Header[key]!);
            }
            writer.WriteEndObject();
        }
        writer.WriteEndArray();
    }

    private static void WriteOrdered(JsonWriter writer, JToken token)
    {
        switch (token)
        {
            case JObject obj:
                writer.WriteStartObject();
                foreach (var property in obj.Properties().OrderBy(property => property.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    WriteOrdered(writer, property.Value);
                }
                writer.WriteEndObject();
                break;
            case JArray array:
                writer.WriteStartArray();
                foreach (var child in array) WriteOrdered(writer, child);
                writer.WriteEndArray();
                break;
            default:
                token.WriteTo(writer);
                break;
        }
    }

    private static FileStream OpenSpool(FastGeoDetachedCapture capture) =>
        new(capture.SpoolPath, FileMode.Open, FileAccess.Read, FileShare.Read, 1, FileOptions.SequentialScan);

    private static void WriteSlice(JsonWriter writer, Stream spool, FastGeoDetachedCapture capture, FastGeoSpoolSlice slice, ref int peak)
    {
        if (slice.Offset < 0 || slice.Length < 0 || slice.Length > capture.SliceLimit || slice.Offset > spool.Length - slice.Length)
            throw new InvalidDataException("FastGeo spool slice is outside its validated bounds");
        if (!HashSlice(spool, slice).Equals(slice.Sha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("FastGeo spool slice SHA-256 mismatch");
        var residentPayload = checked(slice.Length * 6 + ConsumerBufferBudgetBytes);
        EnsurePayloadLimit(residentPayload, capture.SliceLimit);
        peak = Math.Max(peak, residentPayload);
        var payload = new byte[slice.Length];
        spool.Position = slice.Offset;
        var bytesRead = 0;
        while (bytesRead < payload.Length)
        {
            var read = spool.Read(payload, bytesRead, payload.Length - bytesRead);
            if (read == 0) throw new EndOfStreamException("FastGeo spool slice ended early");
            bytesRead += read;
        }
        var reader = new System.Text.Json.Utf8JsonReader(payload, isFinalBlock: true, state: default);
        var rootStarted = false;
        var rootComplete = false;
        var depth = 0;
        while (reader.Read())
        {
            var token = reader.TokenType;
            if (token is System.Text.Json.JsonTokenType.StartObject or System.Text.Json.JsonTokenType.StartArray)
            {
                if (depth == 0)
                {
                    if (rootStarted) throw new System.Text.Json.JsonException("FastGeo spool slice contains more than one JSON value");
                    rootStarted = true;
                }
                depth++;
            }
            else if (token is System.Text.Json.JsonTokenType.EndObject or System.Text.Json.JsonTokenType.EndArray)
            {
                depth--;
                if (depth < 0) throw new System.Text.Json.JsonException("FastGeo spool slice has invalid JSON structure");
                if (depth == 0) rootComplete = true;
            }
            else if (token == System.Text.Json.JsonTokenType.PropertyName)
            {
                if (depth == 0) throw new System.Text.Json.JsonException("FastGeo spool slice has a property outside its JSON value");
            }
            else if (depth == 0)
            {
                if (rootStarted) throw new System.Text.Json.JsonException("FastGeo spool slice contains more than one JSON value");
                rootStarted = true;
                rootComplete = true;
            }

            switch (token)
            {
                case System.Text.Json.JsonTokenType.StartObject: writer.WriteStartObject(); break;
                case System.Text.Json.JsonTokenType.EndObject: writer.WriteEndObject(); break;
                case System.Text.Json.JsonTokenType.StartArray: writer.WriteStartArray(); break;
                case System.Text.Json.JsonTokenType.EndArray: writer.WriteEndArray(); break;
                case System.Text.Json.JsonTokenType.PropertyName: writer.WritePropertyName(reader.GetString()!); break;
                case System.Text.Json.JsonTokenType.String: writer.WriteValue(reader.GetString()); break;
                case System.Text.Json.JsonTokenType.Number:
                    writer.WriteRawValue(Encoding.UTF8.GetString(reader.ValueSpan));
                    break;
                case System.Text.Json.JsonTokenType.True: writer.WriteValue(true); break;
                case System.Text.Json.JsonTokenType.False: writer.WriteValue(false); break;
                case System.Text.Json.JsonTokenType.Null: writer.WriteNull(); break;
                default: throw new System.Text.Json.JsonException("FastGeo spool slice contains an unsupported JSON token");
            }
        }
        if (!rootStarted || !rootComplete || depth != 0)
            throw new System.Text.Json.JsonException("FastGeo spool slice is not one complete JSON value");
    }

    private static string HashSlice(Stream spool, FastGeoSpoolSlice slice)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[4096];
        var remaining = slice.Length;
        spool.Position = slice.Offset;
        while (remaining > 0)
        {
            var read = spool.Read(buffer, 0, Math.Min(buffer.Length, remaining));
            if (read == 0) throw new EndOfStreamException("FastGeo spool slice ended early");
            hash.AppendData(buffer, 0, read);
            remaining -= read;
        }
        return Convert.ToHexString(hash.GetHashAndReset());
    }

    private sealed class SliceReadStream(Stream source, int length) : Stream
    {
        private int remaining = length;
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => length;
        public override long Position { get => length - remaining; set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count)
        {
            var read = source.Read(buffer, offset, Math.Min(count, remaining));
            remaining -= read;
            return read;
        }
        public override int Read(Span<byte> buffer)
        {
            var read = source.Read(buffer[..Math.Min(buffer.Length, remaining)]);
            remaining -= read;
            return read;
        }
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }
}

