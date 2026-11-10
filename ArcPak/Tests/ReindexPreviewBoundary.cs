using System.Reflection;
using System.Reflection.Emit;

internal static class ReindexPreviewBoundary
{
    private static int Main(string[] arguments)
    {
        try
        {
            if (arguments.Length != 1)
                throw new ArgumentException("Pass the ArcPak.Export.dll under test");
            var assemblyPath = Path.GetFullPath(arguments[0]);
            var directory = Path.GetDirectoryName(assemblyPath)!;
            AppDomain.CurrentDomain.AssemblyResolve += (_, request) =>
            {
                var dependency = Path.Combine(directory, new AssemblyName(request.Name).Name + ".dll");
                return File.Exists(dependency) ? Assembly.LoadFrom(dependency) : null;
            };
            var assembly = Assembly.LoadFrom(assemblyPath);
            var program = assembly.GetType("ArcPak.Program", throwOnError: true)!;
            var reindex = program.GetMethod("Reindex", BindingFlags.NonPublic | BindingFlags.Static)!;
            var command = program.GetMethod("ReindexCommand", BindingFlags.NonPublic | BindingFlags.Static)!;
            if (Calls(reindex).Any(method => method.Name == "CachePreviewIcons"))
                throw new InvalidOperationException("Automatic reindex still calls bulk preview generation");
            if (!Calls(command).Any(method => method.Name == "CachePreviewIcons"))
                throw new InvalidOperationException("Explicit reindex no longer calls bulk preview generation");
            Console.WriteLine("REINDEX_PREVIEW_BOUNDARY_OK");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }

    private static IEnumerable<MethodBase> Calls(MethodInfo method)
    {
        var opcodes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
            .Where(field => field.FieldType == typeof(OpCode))
            .Select(field => (OpCode)field.GetValue(null)!)
            .ToDictionary(opcode => unchecked((ushort)opcode.Value));
        var bytes = method.GetMethodBody()!.GetILAsByteArray()!;
        var offset = 0;
        while (offset < bytes.Length)
        {
            ushort value = bytes[offset++];
            if (value == 0xfe)
                value = (ushort)(0xfe00 | bytes[offset++]);
            var opcode = opcodes[value];
            if (opcode.OperandType == OperandType.InlineMethod)
                yield return method.Module.ResolveMethod(BitConverter.ToInt32(bytes, offset))!;
            offset += opcode.OperandType switch
            {
                OperandType.InlineNone => 0,
                OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2,
                OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => 4 + 4 * BitConverter.ToInt32(bytes, offset),
                _ => 4
            };
        }
    }
}
