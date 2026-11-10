using System.IO.Compression;
using System.Text;
using ArcPak;
using Newtonsoft.Json.Linq;

internal static class UEModelMaterialProof
{
    private static string Text(BinaryReader reader)
    {
        var count = reader.ReadInt32();
        if (count == 0) return "";
        if (Math.Abs(count) > 1048576) throw new InvalidDataException("Unbounded UEModel string");
        return (count < 0 ? Encoding.Unicode : Encoding.UTF8).GetString(reader.ReadBytes(count < 0 ? -count * 2 : count)).TrimEnd((char)0);
    }

    private static Dictionary<string, byte[]> Attributes(BinaryReader reader)
    {
        var count = reader.ReadInt32();
        var attributes = new Dictionary<string, byte[]>(StringComparer.Ordinal);
        if (count < 0 || count > 10000) throw new InvalidDataException("Unbounded UEModel attributes");
        for (var index = 0; index < count; index++)
        {
            var name = Text(reader);
            var length = reader.ReadInt32();
            if (length < 0 || length > reader.BaseStream.Length - reader.BaseStream.Position) throw new InvalidDataException("Invalid UEModel attribute size");
            attributes.Add(name, reader.ReadBytes(length));
        }
        return attributes;
    }

    internal static void Verify(string file, JObject physical, JArray slots)
    {
        using var input = new BinaryReader(File.OpenRead(file));
        if (Encoding.ASCII.GetString(input.ReadBytes(8)) != "UEFORMAT" || Text(input) != "UEMODEL" || input.ReadByte() != 10) throw new InvalidDataException("Unexpected UEModel format");
        if (Text(input) != physical.Value<string>("objectName") || Text(input) != physical.Value<string>("objectPath")) throw new InvalidDataException("Physical UEModel identity differs from binding");
        byte[] payload;
        if (input.ReadBoolean())
        {
            var compression = Text(input);
            var size = input.ReadInt32();
            var compressed = input.ReadBytes(input.ReadInt32());
            if (compression == "ZSTD")
            {
                using var decoder = new ZstdSharp.Decompressor();
                payload = decoder.Unwrap(compressed).ToArray();
            }
            else if (compression == "GZIP")
            {
                using var decoder = new GZipStream(new MemoryStream(compressed), CompressionMode.Decompress);
                using var buffer = new MemoryStream();
                decoder.CopyTo(buffer);
                payload = buffer.ToArray();
            }
            else throw new InvalidDataException("Unknown UEModel compression");
            if (payload.Length != size) throw new InvalidDataException("UEModel payload size mismatch");
        }
        else payload = input.ReadBytes((int)(input.BaseStream.Length - input.BaseStream.Position));
        using var root = new BinaryReader(new MemoryStream(payload));
        using var lods = new BinaryReader(new MemoryStream(Attributes(root)["LODS"]));
        var lodCount = lods.ReadInt32();
        var checkedSections = 0;
        for (var lodIndex = 0; lodIndex < lodCount; lodIndex++)
        {
            Text(lods);
            using var materials = new BinaryReader(new MemoryStream(Attributes(lods)["MATERIALS"]));
            var sectionCount = materials.ReadInt32();
            for (var sectionIndex = 0; sectionIndex < sectionCount; sectionIndex++)
            {
                var slotName = Text(materials);
                var materialObject = Text(materials);
                var firstIndex = materials.ReadInt32();
                var numFaces = materials.ReadInt32();
                var matches = slots.OfType<JObject>().SelectMany(slot => slot["sections"]!.OfType<JObject>().Select(section => (slot, section)))
                    .Where(pair => pair.section.Value<int>("lod") == lodIndex && pair.section.Value<int>("section") == sectionIndex).ToArray();
                if (matches.Length != 1) throw new InvalidDataException("No unique authored slot for emitted section");
                var match = matches[0];
                if (slotName != match.slot.Value<string>("slotName") || (materialObject.Length == 0 ? "" : MapBundleDocument.CanonicalObject(materialObject)) != match.slot.Value<string>("materialObject") ||
                    firstIndex != match.section.Value<int>("firstIndex") || numFaces != match.section.Value<int>("numFaces")) throw new InvalidDataException("Emitted material section differs from authored slot binding");
                checkedSections++;
            }
        }
        if (checkedSections != slots.OfType<JObject>().Sum(slot => slot["sections"]!.Count())) throw new InvalidDataException("Authored section was not emitted");
        Console.WriteLine("PASS serialized UEModel identity and authored sections: " + checkedSections);
    }
}
