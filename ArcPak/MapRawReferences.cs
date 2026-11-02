using System.Collections;
using System.Reflection;
using CUE4Parse.UE4;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Objects.Properties;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json.Linq;

namespace ArcPak;

internal static class MapRawReferences
{
    internal static JArray Collect(FPropertyTagType? tag)
    {
        var result = new JArray();
        var seen = new HashSet<object>(ReferenceEqualityComparer.Instance);
        void Visit(object? value, string location, int depth)
        {
            if (value == null || value is string) return;
            if (depth > 48) throw new InvalidDataException("Reference property nesting exceeds bound");
            if (value is FPackageIndex reference)
            {
                result.Add(new JObject
                {
                    ["location"] = location, ["index"] = reference.Index, ["owner"] = reference.Owner?.Name,
                    ["path"] = reference.ResolvedObject?.GetPathName(),
                    ["unresolved"] = !reference.IsNull && reference.ResolvedObject == null
                });
                return;
            }
            if (value is FSoftObjectPath soft)
            {
                result.Add(new JObject { ["location"] = location, ["softPath"] = soft.AssetPathName.Text, ["subPath"] = soft.SubPathString });
                return;
            }
            if (!value.GetType().IsValueType && !seen.Add(value)) return;
            switch (value)
            {
                case FPropertyTagType property: Visit(property.GenericValue, location, depth + 1); break;
                case FScriptStruct script: Visit(script.StructType, location, depth + 1); break;
                case IPropertyHolder holder:
                    foreach (var property in holder.Properties) Visit(property.Tag, location + "." + property.Name.Text, depth + 1);
                    break;
                case UScriptArray array: Visit(array.Properties, location, depth + 1); break;
                case UScriptSet set: Visit(set.Properties, location, depth + 1); break;
                case UScriptMap map:
                    var entryIndex = 0;
                    foreach (var entry in map.Properties)
                    {
                        Visit(entry.Key, location + "[" + entryIndex + "].key", depth + 1);
                        Visit(entry.Value, location + "[" + entryIndex++ + "].value", depth + 1);
                    }
                    break;
                case IEnumerable sequence:
                    var index = 0;
                    foreach (var entry in sequence) Visit(entry, location + "[" + index++ + "]", depth + 1);
                    break;
                case IUStruct structure:
                    foreach (var field in structure.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
                        Visit(field.GetValue(structure), location + "." + field.Name, depth + 1);
                    break;
            }
        }
        Visit(tag, "$", 0);
        return result;
    }
}
