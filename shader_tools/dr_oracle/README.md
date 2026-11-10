# D3D11 shader oracle

Build from this directory with `build.cmd <OUTDIR>`. Run `dr_oracle.exe <job.txt>`. Jobs use DRJOB version 1 and exactly one TAB between directive fields. WARP and hardware devices execute controlled vertex/pixel shaders only. Output render targets are tight top-first float32 RGBA plus JSON device metadata. DDS input requires the DX10 header.

Run `python tests/run_selftest.py --run <RUN>` for T1 through T10 on both devices. All generated fixtures, jobs and results remain under RUN.

The `tbuf\tvs|ps\t<slot>\t<dxgi_format>\t<path>` directive binds a typed Buffer SRV. Supported formats: 2 (float4), 41 (float), 42 (uint), 3 (uint4). File size must be a multiple of the format size; element count is file size divided by format size.
