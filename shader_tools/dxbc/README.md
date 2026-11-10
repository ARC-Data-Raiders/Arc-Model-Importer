# DXBC disassembly capture

Run from the repository root:

`python -m shader_tools.dxbc.cli <shader-records.manifest.json> <outdir>`

Uses only Windows d3dcompiler_47.dll and Python standard libraries. The parser preserves declarations, numbered instructions and operand texts; unknown operands and round-trip mismatches are recorded without grammar extensions. Literal hex values retain uint32 and float32 interpretations. Outputs follow `dr.dxbc-asm/1`.
