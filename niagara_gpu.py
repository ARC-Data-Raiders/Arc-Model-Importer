"""GPU emitter emulation (spec N-O4): run each GPU emitter's own cooked compute shader (DXBC SM5) under D3D11 WARP per tick.

CB0 ($Globals) is assembled from the frozen FShader::Bindings exported by CUE4Parse (niagara-ns-maps -> data/gpu_bindings), keyed by
C++ FNiagaraShader::FParameters byte offsets (5.7 layout, verified on NS_FrontEnd_RoofSmoke_01). Particle data is component-major
(component c of instance i at [c * stride + i]), exactly as FNiagaraDataBuffer's GPU float/int buffers.
"""
import glob
import json
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import niagara_di
import niagara_ir as ir

INDEX_NONE = 0xFFFFFFFF
INT32_MAX = 0x7FFFFFFF
MAX_SPAWN_INFOS = 8
THREADS = 64
MAX_GROUPS_X = 65535
STATIC_PARAMETERS_SIZE = 1728       # C++ sizeof(FNiagaraShader::FParameters) before DI structs (5.7; first DI struct of M2 at 1728)
# C++ FParameters offsets (spec N-O4 section 1)
OFF = dict(ComponentBufferSizeRead=0, ComponentBufferSizeWrite=4, SimStart=8, InputFloat=16, InputHalf=24, InputInt=32, StaticInputFloat=40,
           RWOutputFloat=48, RWOutputHalf=56, RWOutputInt=64, RWInstanceCounts=72, ReadInstanceCountOffset=80, WriteInstanceCountOffset=84,
           FreeIDList=88, RWIDToIndexTable=96, SimulationStageIterationInfo=112, ParticleIterationStateInfo=128, EmitterTickCounter=140,
           EmitterSpawnInfoOffsets=144, EmitterSpawnInfoParams=176, NumSpawnedInstances=304, DispatchThreadIdBounds=320,
           DispatchThreadIdToLinear=336, IndirectDispatchArgs=352, IndirectDispatchArgsOffset=360,
           Global=384, System=448, Owner=576, Emitter=1600)
CURVES = {'NiagaraDataInterfaceCurve', 'NiagaraDataInterfaceVector2DCurve', 'NiagaraDataInterfaceVectorCurve', 'NiagaraDataInterfaceColorCurve',
          'NiagaraDataInterfaceVector4Curve'}
VECTOR_FIELD = 'NiagaraDataInterfaceVectorField'
SPRITE_INFO = 'NiagaraDataInterfaceSpriteRendererInfo'
CAMERA = 'NiagaraDataInterfaceCamera'
COLLISION = 'NiagaraDataInterfaceCollisionQuery'
LANDSCAPE = 'NiagaraDataInterfaceLandscape'
PARTICLE_READ = 'NiagaraDataInterfaceParticleRead'
GBUFFER = 'NiagaraDataInterfaceGBuffer'
# Array DIs (NiagaraDataInterfaceArrayImplHelpers.h): {FIntPoint ArrayBufferParams, SRV ArrayReadBuffer @8, UAV ArrayRWBuffer @16} -> 32 B.
ARRAY_FORMATS = {'NiagaraDataInterfaceArrayFloat': ('float', 1), 'NiagaraDataInterfaceArrayFloat2': ('float2', 1),
                 'NiagaraDataInterfaceArrayFloat3': ('float', 3), 'NiagaraDataInterfaceArrayPosition': ('float', 3),
                 'NiagaraDataInterfaceArrayColor': ('float4', 1), 'NiagaraDataInterfaceArrayFloat4': ('float4', 1),
                 'NiagaraDataInterfaceArrayBool': ('r8uint', 1)}
# Included FGlobalDistanceFieldParameters2 (shared by every CollisionQuery DI of a script; the first one owns it): 544 B.
# Offsets observed in the frozen bindings (C++ struct + a 16-byte insert after the four textures, spec N-O4 section 5).
GDF = dict(PageAtlasTexture=0, CoverageAtlasTexture=8, PageTableTexture=16, MipTexture=24, CenterAndExtent=48, WorldToUVAddAndMul=144,
           MipWorldToUVScale=240, MipWorldToUVBias=336, MipFactor=432, MipTransition=436, ClipmapSizeInPages=440, InvPageAtlasSize=448,
           InvCoverageAtlasSize=464, VolumeDimension=476, VolumeTexelSize=480, MaxDFAOConeDistance=484, NumClipmaps=488,
           CoveredExpandSurfaceScale=492, NotCoveredExpandSurfaceScale=496, NotCoveredMinStepScale=500, DitheredStepThreshold=504,
           DitheredTraceThreshold=508, CoverageSampler=512, PageAtlasSampler=520, MipSampler=528)
GDF_SIZE = 544
# C++ nested FShaderParameters sizes (16-aligned):
#   curves       {MinTime, MaxTime, InvTimeRange, CurveLUTNumMinusOne, LUTOffset, SRV CurveLUT}          -> 32
#   vector field {float3 TilingAxes, Dimensions, MinBounds, MaxBounds (16 each), Texture3D @64, Sampler @72} -> 80
#   sprite info  {bIsValid, SourceMode, Alignment, FacingMode, bSubImageBlend, float2 SubImageSize @24}      -> 32
#   landscape    {BaseColor VT: SRV, uint4 page table @8, sampler @16, LWCTile @32, WorldToUv @48, UnpackSRGB/YCoCg/Enabled @112,
#                 PackedUniform0/1 + Uniforms @128; Height VT @176 (same shape, Enabled @288); Normal VT: SRVs @352/360, page table @368,
#                 samplers @376/384, LWCTile @400, WorldToUv @416, uniforms @480..528, UnpackMode @544, Enabled @548; cached height:
#                 texture @552, sampler @560, Enabled @568, LWCTile @576, WorldToUv @592, UvToWorld @656, UvScaleBias @720, WorldGridSize @736,
#                 Dimension @744; PointClampedSampler @752; Texture2D<uint> CachedPhysMat @760, Dimension @768}  -> 784
#   particle read {IsLocalSpace, NumSpawnedParticles, SpawnedParticlesAcquireTag, InstanceCountOffset, ParticleStrideFloat/Int/Half,
#                 AcquireTagRegisterIndex, SRVs SpawnedIDs @32, IDToIndexTable @40, InputFloat @48, InputInt @56, InputHalf @64} -> 80,
#                 then loose int4 AttributeIndices[N4] and AttributeCompressed[N4], N4 = max(1, ceil(GeneratedFunctions / 4))
#   gbuffer      {RDG Texture2D VelocityTexture, sampler @8} -> 16; the G-buffer itself is the SceneTexturesStruct uniform buffer
DI_STRUCT_SIZE = {**{c: 32 for c in CURVES}, VECTOR_FIELD: 80, SPRITE_INFO: 32, CAMERA: 16,   # camera {int32 SplitscreenMode}
                  **{c: 32 for c in ARRAY_FORMATS}, LANDSCAPE: 784, GBUFFER: 16}
LANDSCAPE_TEXTURES = {0: 'half4', 8: 'uint4', 176: 'half4', 184: 'uint4', 352: 'half4', 360: 'half4', 368: 'uint4', 552: 'half4', 760: 'uint4'}
LANDSCAPE_IDENTITY = (48, 224, 416, 592, 656)
LANDSCAPE_SAMPLERS = {16: 'bilinear', 192: 'bilinear', 376: 'bilinear', 384: 'bilinear', 560: 'bilinear', 752: 'point'}
PR_SRVS = dict(SpawnedIDsBuffer=32, IDToIndexTable=40, InputFloatBuffer=48, InputIntBuffer=56, InputHalfBuffer=64)
# GetValueTypeFromFuncName + CheckVariableType: 'Get <Value> By Index|ID' -> (int registers?, accepted variable types)
PR_VALUE_TYPES = {'Int': (True, ("Class'NiagaraInt32'",)), 'Bool': (True, ("Class'NiagaraBool'",)), 'Float': (False, ("Class'NiagaraFloat'",)),
                  'Vector 2D': (False, ("Class'Vector2f'",)), 'Vector': (False, ("Class'Vector3f'", "Class'NiagaraPosition'")),
                  'Vector 4': (False, ("Class'Vector4f'",)), 'Color': (False, ("Class'LinearColor'",)),
                  'Position': (False, ("Class'NiagaraPosition'",)), 'Quaternion': (False, ("Class'Quat4f'",)), 'ID': (True, ("Class'NiagaraID'",))}
SPRITE_ENUMS = {'SourceMode': ['Particles', 'Emitter'], 'Alignment': ['Unaligned', 'VelocityAligned', 'CustomAlignment', 'Automatic'],
                'FacingMode': ['FaceCamera', 'FaceCameraPlane', 'CustomFacingVector', 'FaceCameraPosition', 'FaceCameraDistanceBlend', 'Automatic']}
FILTER_LINEAR, FILTER_POINT, ADDRESS_WRAP, ADDRESS_CLAMP = 0x15, 0x0, 1, 3
# SceneTexturesStruct (FSceneTextureUniformParameters, resources only): resource-table index -> member. The game layout has 17 resources
# (PointClampSampler is 16; stock 5.3 has 14, 5.7 15) and keeps the stock order for 0-5. The 121 SM5 corpus records that bind the buffer
# use only 1 (depth, through InvDeviceZToWorldZTransform), 3 (GBufferA normal, N * 2 - 1), 4 (GBufferB.a shading model), 5 (GBufferC) and 16.
SCENE_TEXTURES = {0: 'SceneColorTexture', 1: 'SceneDepthTexture', 2: 'ScenePartialDepthTexture', 3: 'GBufferATexture', 4: 'GBufferBTexture',
                  5: 'GBufferCTexture', 16: 'PointClampSampler'}
SCENE_DEPTHS = ('SceneDepthTexture', 'ScenePartialDepthTexture')
SHADING_MODEL_DEFAULT_LIT = 1


class GpuError(ValueError):
    pass


def f32w(v):
    return struct.unpack('<I', struct.pack('<f', float(v)))[0]


def _ref_index(ref):
    return int(ref['ObjectPath'].rsplit('.', 1)[1])


def find_binding(bindings_dir, system_stem, script_name, outer_name, platform='SP_PCD3D_SM5'):
    path = Path(bindings_dir) / (system_stem + '.json')
    if not path.is_file():
        raise GpuError('gpu_bindings_missing:' + system_stem)
    j = json.loads(path.read_text(encoding='utf-8'))
    for s in j.get('scripts', []):
        if s.get('name') == script_name and s.get('outer') == outer_name:
            for r in s.get('resources', []):
                if r.get('shaderPlatform') == platform and r.get('shaders'):
                    return r['resourceHash'], r['shaders'][0]['bindings']
    raise GpuError('gpu_binding_not_found:%s.%s' % (outer_name, script_name))


def _long(path):
    path = os.path.abspath(path)
    return '\\\\?\\' + path if os.name == 'nt' and len(path) >= 260 and not path.startswith('\\\\?\\') else path


def _read_record(shader_root, resource_hash):
    """Shader record split around its DXBC container: (FShaderResourceTable header, trailer), or None without a record."""
    hits = glob.glob(str(Path(shader_root) / '*SM5*' / resource_hash / '*.ushaderrecord'))
    if not hits:
        return None
    with open(_long(hits[0]), 'rb') as f:
        data = f.read()
    i = data.find(b'DXBC')
    return data[:i], data[i + struct.unpack_from('<I', data, i + 24)[0]:]


def record_uniform_buffers(shader_root, resource_hash):
    """Uniform buffer names in cbuffer-slot order from the shader record trailer ('u' chunk; $Globals is slot 0)."""
    record = _read_record(shader_root, resource_hash)
    if record is None:
        return ['$Globals']
    trailer = record[1]
    if len(trailer) < 4 or struct.unpack_from('<I', trailer, len(trailer) - 4)[0] != len(trailer):
        return ['$Globals']
    off, chunks = 0, {}
    while off < len(trailer) - 4:
        key, size = chr(trailer[off]), struct.unpack_from('<I', trailer, off + 1)[0]
        chunks[key] = trailer[off + 5:off + 5 + size]
        off += 5 + size
    u = chunks.get('u')
    if not u:
        return ['$Globals']
    count, pos, names = struct.unpack_from('<I', u)[0], 4, []
    for _ in range(count):
        n = struct.unpack_from('<I', u, pos)[0]
        names.append(u[pos + 4:pos + 4 + n - 1].decode('ascii'))
        pos += 4 + n
    return names


RESOURCE_TABLE_MAPS = ('srv', 'sampler', 'uav', 'layout_hashes', 'texture', 'collection')   # FShaderResourceTable arrays after ResourceTableBits


def record_resource_table(shader_root, resource_hash):
    """Global uniform buffer resources from the record's FShaderResourceTable: [(map, uniform buffer, resource index, bind slot)] for the
    srv, sampler, uav and texture maps. Each map is a BuildResourceTableTokenStream: one start offset per cbuffer slot (0 = none), then
    (UB << 24 | Resource << 8 | Bind) tokens per buffer, closed by 0xFFFFFFFF."""
    record = _read_record(shader_root, resource_hash)
    if record is None or len(record[0]) < 4:
        return []
    header, names = record[0], record_uniform_buffers(shader_root, resource_hash)
    maps, off = {}, 4
    for name in RESOURCE_TABLE_MAPS:
        n = struct.unpack_from('<I', header, off)[0]
        maps[name] = struct.unpack_from('<%dI' % n, header, off + 4)
        off += 4 + 4 * n
    out = []
    for name in ('srv', 'sampler', 'uav', 'texture'):
        words = maps[name]
        for ub in range(min(len(names), len(words))):
            k = words[ub]
            while k and k < len(words) and words[k] != INDEX_NONE and words[k] >> 24 == ub:
                out.append((name, names[ub], (words[k] >> 8) & 0xFFFF, words[k] & 0xFF))
                k += 1
    return out


def scene_texture_bindings(table):
    """SceneTexturesStruct members a shader reads: [(member, 'srv' | 'sampler', slot)]; an unmapped resource index is an error."""
    out = []
    for kind, ub, res, slot in table:
        if ub != 'SceneTexturesStruct':
            continue
        member = SCENE_TEXTURES.get(res)
        if member is None or kind == 'uav' or (kind == 'sampler') != member.endswith('Sampler'):
            raise GpuError('gpu_scene_texture_unmapped:%s:%d' % (kind, res))
        out.append((member, 'sampler' if kind == 'sampler' else 'srv', slot))
    return out


def flat_ground_scene(cam, ground_z, base_color, size=None, near=None):
    """Synthetic SceneTextures of an infinite flat ground at world height ground_z (UE cm) seen by cam (the View UB camera, same tuple as
    niagara_view_ub.camera_view): per texel the camera ray through the texel centre (ScreenPositionScaleBias UV convention) meets the plane at
    view depth t; ground texels get reversed-Z device depth near / t and an encoded default-lit G-buffer (normal +Z, specular 0.5,
    roughness 1, base colour), sky texels depth 0 and a black G-buffer. Shaders only sample these with normalized UVs (sample_l, point
    clamp), so the texture size only quantizes the lookup. Returns member -> (format, texels [H, W, ...])."""
    import niagara_view_ub
    w, h = size or niagara_view_ub.VIEW_SIZE
    near = near or niagara_view_ub.NEAR
    cam = cam if cam is not None else niagara_view_ub.DEFAULT_CAMERA
    fwd, right, up = (np.array(v, dtype=np.float64) for v in niagara_di.rotation_axes(*cam[3:6]))
    half = np.tan(np.radians(cam[6] if cam[6] > 0 else 90.0) / 2.0)
    aspect = niagara_view_ub.VIEW_SIZE[0] / niagara_view_ub.VIEW_SIZE[1]
    ndc_x = (np.arange(w) + 0.5) / w * 2.0 - 1.0
    ndc_y = 1.0 - (np.arange(h) + 0.5) / h * 2.0
    # ray d = fwd + right * x * half + up * y * half / aspect has view-space z = 1, so its plane hit is at view depth t
    dz = np.float32(fwd[2]) + (right[2] * half * ndc_x).astype(np.float32)[None, :] + (up[2] * half / aspect * ndc_y).astype(np.float32)[:, None]
    with np.errstate(divide='ignore', invalid='ignore'):
        t = np.float32(float(ground_z) - float(cam[2])) / dz
        ground = np.isfinite(t) & (t >= near)
        depth = np.where(ground, np.float32(near) / t, np.float32(0))
    # one RGBA16F texel is 64 bits: select its bit pattern per texel (no float16 arithmetic, no 4-channel broadcast)
    texel = lambda value: (ground * np.asarray(value, np.float16).view(np.uint64)[0]).view(np.float16).reshape(h, w, 4)
    gbuffer_a = texel((0.5, 0.5, 1.0, 1.0))                                            # EncodeNormal(+Z) = N * 0.5 + 0.5, PerObjectGBufferData
    gbuffer_b = texel((0.0, 0.5, 1.0, SHADING_MODEL_DEFAULT_LIT / 255.0))              # metallic, specular, roughness, shading model | mask << 4
    gbuffer_c = texel(tuple(base_color[:3]) + (1.0,))                                  # linear base colour, AO
    scene = {name: ('float', depth) for name in SCENE_DEPTHS}
    scene.update(GBufferATexture=('half4', gbuffer_a), GBufferBTexture=('half4', gbuffer_b), GBufferCTexture=('half4', gbuffer_c))
    return scene


def scene_views(dev, scene, members):
    """SRVs / sampler for the SceneTexturesStruct members a shader reads. scene None (no scene) binds the engine's empty-scene
    fallbacks: black (0, 0, 0, 0) colour and G-buffers, DepthDummy device depth 0 (nothing drawn); the sampler is point clamp."""
    out = {}
    for member in set(members):
        if member == 'PointClampSampler':
            out[member] = dev.sampler(FILTER_POINT, ADDRESS_CLAMP)
            continue
        fmt, texels = (scene or {}).get(member) or (('float', np.zeros((1, 1), np.float32)) if member in SCENE_DEPTHS
                                                    else ('half4', np.zeros((1, 1, 4), np.float16)))
        out[member] = dev.texture2d(texels, (texels.shape[1], texels.shape[0]), fmt)
    return out


def find_dxbc(shader_root, resource_hash):
    hits = glob.glob(str(Path(shader_root) / '*SM5*' / resource_hash / '*.dxbc'))
    if not hits:
        raise GpuError('gpu_shader_missing:' + resource_hash)
    path = os.path.abspath(hits[0])
    if os.name == 'nt' and len(path) >= 260 and not path.startswith('\\\\?\\'):
        path = '\\\\?\\' + path   # extended-length path: record names exceed MAX_PATH (Blender's Python is not long-path aware)
    with open(path, 'rb') as f:
        return f.read()


def gpu_spawn_info(spawn_infos, active, cap=2000000):
    """Spawn info arrays and InstancesToSpawn (NiagaraEmitterInstanceImpl.cpp 1373-1453, spec N-O2 section 8)."""
    offsets = [INT32_MAX] * MAX_SPAWN_INFOS
    params = [(0.0, 0.0, 0, 0)] * MAX_SPAWN_INFOS
    total = 0
    k = 0
    if active:
        for count, start_dt, interval, group in spawn_infos:
            if count <= 0 or k >= MAX_SPAWN_INFOS:
                continue
            if total + count > cap:
                count = cap - total
                if count <= 0:
                    break
            params[k] = (interval, start_dt, group, total)
            total += count
            offsets[k] = total
            k += 1
    params = [p if i < k else (0.0, 0.0, 0, total) for i, p in enumerate(params)]
    return total, offsets, params


class GpuEmitterProgram:
    """One GPU emitter: shader, binding table, data-interface static data."""

    def __init__(self, device, doc, emitter_export, gpu_script_index, bindings_dir, shader_root, system_stem, vector_fields=None, ground=None):
        """ground: world height (UE cm) of the synthetic flat ground the Landscape DIs report; None is an empty world (no landscape)."""
        self.dev = device
        script = doc[gpu_script_index]
        self.script_name = script.get('Name')
        self.outer = emitter_export.get('Name')
        props = script.get('Properties') or {}
        vm = props.get('CachedScriptVM') or {}
        self.stages = vm.get('SimulationStageMetaData') or []
        if len(self.stages) != 1:
            raise GpuError('gpu_multi_stage:%d' % len(self.stages))
        self.resource_hash, self.bindings = find_binding(bindings_dir, system_stem, self.script_name, self.outer)
        self.cs = device.compute_shader(find_dxbc(shader_root, self.resource_hash))
        self.uniform_buffers = record_uniform_buffers(shader_root, self.resource_hash)
        self.scene_resources = scene_texture_bindings(record_resource_table(shader_root, self.resource_hash)) \
            if 'SceneTexturesStruct' in self.uniform_buffers else []
        self.resources = {(r['baseType'], r['byteOffset']): r['baseIndex'] for r in self.bindings['resources']}
        meta = (vm.get('ShaderScriptParametersMetadata') or {}).get('DataInterfaceParamInfo') or []
        infos = vm.get('DataInterfaceInfo') or []
        resolved = {r['CompileName']: r for r in props.get('ResolvedDataInterfaces') or []}
        self.di_values = {}
        self.di_views = {}          # C++ offset of a DI resource member -> SRV / sampler object
        self.particle_reads = []    # per-dispatch source bindings (dispatch_tick)
        static = []
        base = STATIC_PARAMETERS_SIZE
        gdf_owned = False
        for m, info in zip(meta, infos):
            cls = m['DIClassName']
            if cls not in DI_STRUCT_SIZE and cls not in (COLLISION, PARTICLE_READ):
                raise GpuError('gpu_di_unimplemented:' + cls)
            rd = resolved.get(info['Name'])
            if rd is None:
                raise GpuError('gpu_di_unresolved:' + info['Name'])
            exp = doc[_ref_index(rd['ResolvedDataInterface'])]
            di_props = exp.get('Properties') or {}
            if cls in CURVES:
                curve = niagara_di.curve_from_properties(exp['Type'], di_props)
                offset = sum(len(x) for x in static)
                static.append(curve.lut)
                self.di_values.update({base: f32w(curve.min_time), base + 4: f32w(curve.max_time), base + 8: f32w(curve.inv_time_range),
                                       base + 12: int(curve.num_samples_minus_one), base + 16: int(offset)})
            elif cls == VECTOR_FIELD:
                self._vector_field(base, di_props, vector_fields or {})
            elif cls == SPRITE_INFO:
                self._sprite_info(base, doc, di_props)
            elif cls == CAMERA:
                self.di_values[base] = 0        # ESplitScreenType::None
            elif cls in ARRAY_FORMATS:
                self._array(base, cls, di_props)
            elif cls == LANDSCAPE:
                if ground is None:
                    self._no_landscape(base)
                else:
                    self._flat_landscape(base, ground)
            elif cls == GBUFFER:
                self._gbuffer(base)
            elif cls == PARTICLE_READ:
                base += self._particle_read(base, di_props, m)
                continue
            elif cls == COLLISION:
                if gdf_owned:
                    continue                    # included struct already added by the first CollisionQuery DI
                gdf_owned = True
                self._empty_gdf(base)
                base += GDF_SIZE
                continue
            base += DI_STRUCT_SIZE[cls]
        lut = np.concatenate(static).astype(np.float32) if static else np.zeros(4, np.float32)
        _, self.static_srv, _ = device.typed_buffer(lut.view(np.uint32), 'float')
        self.cb_words = max(p['baseIndex'] + p['byteSize'] for p in self.bindings['parameters']) // 4 + 4 if self.bindings['parameters'] else 4

    def _vector_field(self, base, props, vector_fields):
        """UNiagaraDataInterfaceVectorField::SetShaderParameters: proxy dims/bounds, RGBA16F volume, bilinear sampler by tiling."""
        asset, field_class = niagara_di.field_asset(props.get('Field'))
        tiles = [bool(props.get('bTileX', False)), bool(props.get('bTileY', False)), bool(props.get('bTileZ', False))]
        vf = vector_fields.get(asset) if asset is not None and field_class == 'VectorFieldStatic' else None
        if asset is not None and field_class == 'VectorFieldStatic' and vf is None:
            raise GpuError('vector_field_data_missing:' + asset)
        if vf is not None:
            sx, sy, sz = vf.size
            tex = np.zeros((sz, sy, sx, 4), np.uint16)
            tex[..., :3] = vf.halves.reshape(sz, sy, sx, 3)
            dims, lo, hi = vf.size, vf.bounds_min, vf.bounds_max
        else:  # GBlackVolumeTexture, zero proxy
            sx = sy = sz = 1
            tex = np.zeros((1, 1, 1, 4), np.uint16)
            dims, lo, hi = (0, 0, 0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        for k, vec in enumerate((tuple(1.0 if t else 0.0 for t in tiles), dims, lo, hi)):
            for c in range(3):
                self.di_values[base + 16 * k + 4 * c] = f32w(vec[c])
        self.di_views[base + 64] = self.dev.texture3d(tex, (sx, sy, sz))
        self.di_views[base + 72] = self.dev.sampler_axes(
            FILTER_LINEAR, *[ADDRESS_WRAP if t else ADDRESS_CLAMP for t in tiles])

    def _array(self, base, cls, props):
        """Read-only array: ArrayBufferParams = (Num, max(0, Num - 1)); data in the helper's ReadPixelFormat (spec N-O4 section 5)."""
        fmt, per = ARRAY_FORMATS[cls]
        words = niagara_di.array_words(cls, props.get(niagara_di.ARRAY_TYPES[cls][0]))
        num = words.shape[0]
        if cls == 'NiagaraDataInterfaceArrayBool':
            data = bytes(1 if w else 0 for w in words.ravel())
            elements = num
        else:
            data = words.astype('<u4').tobytes()
            elements = num * per if per > 1 else num
        self.di_values.update({base: num, base + 4: max(0, num - 1)})
        _, srv = self.dev.format_buffer(data, fmt, elements)
        self.di_views[base + 8] = srv

    def _empty_gdf(self, base):
        """Global distance field with no clipmaps (empty scene): black volumes, every distance query reports invalid."""
        for off in range(GDF['CenterAndExtent'], GDF['MipWorldToUVBias'] + 96, 4):
            self.di_values[base + off] = 0
        for name in ('MipFactor', 'MipTransition', 'ClipmapSizeInPages', 'VolumeDimension', 'VolumeTexelSize', 'MaxDFAOConeDistance', 'NumClipmaps',
                     'CoveredExpandSurfaceScale', 'NotCoveredExpandSurfaceScale', 'NotCoveredMinStepScale', 'DitheredStepThreshold', 'DitheredTraceThreshold'):
            self.di_values[base + GDF[name]] = 0
        for name in ('InvPageAtlasSize', 'InvCoverageAtlasSize'):
            for c in range(3):
                self.di_values[base + GDF[name] + 4 * c] = 0
        black = self.dev.texture3d(np.zeros((1, 1, 1, 4), np.uint16), (1, 1, 1))
        for name in ('PageAtlasTexture', 'CoverageAtlasTexture', 'PageTableTexture', 'MipTexture'):
            self.di_views[base + GDF[name]] = black
        clamp = self.dev.sampler_axes(FILTER_LINEAR, ADDRESS_CLAMP, ADDRESS_CLAMP, ADDRESS_CLAMP)
        for name in ('CoverageSampler', 'PageAtlasSampler', 'MipSampler'):
            self.di_views[base + GDF[name]] = clamp

    def _no_landscape(self, base):
        """UNiagaraDataInterfaceLandscape::SetShaderParameters with no landscape: every *_Default setter (black textures, identity transforms,
        all sources disabled); queries report invalid and zero height."""
        for off in range(0, DI_STRUCT_SIZE[LANDSCAPE], 4):
            if off not in LANDSCAPE_TEXTURES and off not in LANDSCAPE_SAMPLERS:
                self.di_values[base + off] = 0
        for m in LANDSCAPE_IDENTITY:
            for c in range(4):
                self.di_values[base + m + 20 * c] = f32w(1.0)
        black = {'half4': self.dev.texture2d(np.array([0, 0, 0, 0x3C00], np.uint16), (1, 1), 'half4'),   # GBlackTexture(WithSRV)
                 'uint4': self.dev.texture2d(np.zeros(4, np.uint32), (1, 1), 'uint4')}                    # GBlackUintTexture
        samplers = {'bilinear': self.dev.sampler(FILTER_LINEAR, ADDRESS_CLAMP), 'point': self.dev.sampler(FILTER_POINT, ADDRESS_CLAMP)}
        for off, fmt in LANDSCAPE_TEXTURES.items():
            self.di_views[base + off] = black[fmt]
        for off, kind in LANDSCAPE_SAMPLERS.items():
            self.di_views[base + off] = samplers[kind]

    def _flat_landscape(self, base, height):
        """Landscape DI over an infinite flat ground at world height (UE cm), through the cached-height source (virtual textures stay
        disabled): a 4x4 R32F of the height whose UV is pinned to a texel centre (UvScaleBias (0, 0, 0.375, 0.375)), so GetHeight is valid
        and exact everywhere and the gathered GetWorldNormal is +Z through the identity UvToWorld; physical material index 0."""
        self._no_landscape(base)
        n = 4
        self.di_views[base + 552] = self.dev.texture2d(np.full((n, n), height, np.float32), (n, n), 'float')
        self.di_values.update({base + 568: 1, base + 720: f32w(0.0), base + 724: f32w(0.0), base + 728: f32w(0.375), base + 732: f32w(0.375),
                               base + 736: f32w(100.0), base + 740: f32w(100.0), base + 744: n, base + 748: n})

    def _gbuffer(self, base):
        """UNiagaraDataInterfaceGBuffer::SetShaderParameters: no velocity texture is produced for the bake view, so the black dummy with a
        point clamp sampler. The G-buffer itself is read through the SceneTexturesStruct uniform buffer (dispatch_tick, scene_views)."""
        self.di_views[base] = self.dev.texture2d(np.zeros((1, 1, 4), np.float16), (1, 1), 'half4')
        self.di_views[base + 8] = self.dev.sampler(FILTER_POINT, ADDRESS_CLAMP)

    def _particle_read(self, base, props, meta):
        """GPU particle read: CreateShaderStorage keeps one attribute slot per generated function (NAME_None for GetLocalSpace etc.);
        source data binds per dispatch. Returns the DI's parameter size (struct + both loose arrays)."""
        slots = []
        for f in meta.get('GeneratedFunctions') or []:
            spec = {s.get('Item1'): s.get('Item2') for s in f.get('Specifiers') or []}
            name = str(f.get('DefinitionName', ''))
            value = name[4:].rsplit(' By ', 1)[0] if name.startswith('Get ') and (name.endswith(' By Index') or name.endswith(' By ID')) else None
            slots.append((spec.get('Attribute'), PR_VALUE_TYPES.get(value)) if spec.get('Attribute') else (None, None))
        n4 = max(1, -(-len(slots) // 4))
        self.particle_reads.append(SimpleNamespace(base=base, source=str((props.get('EmitterBinding') or {}).get('EmitterName', '')), slots=slots, n4=n4))
        return 80 + 32 * n4

    def _sprite_info(self, base, doc, props):
        """FNDIProxy ShaderParameters_RT from the referenced sprite renderer (raw cooked enums; class defaults when absent)."""
        ref = props.get('SpriteRenderer')
        rp = (doc[_ref_index(ref)].get('Properties') or {}) if isinstance(ref, dict) else None
        r = rp or {}
        enum = lambda key, default: SPRITE_ENUMS[key].index(str(r.get(key, default)).rsplit('::', 1)[-1])
        size = r.get('SubImageSize') or {}
        self.di_values.update({base: 1 if rp is not None else 0, base + 4: enum('SourceMode', 'Particles'), base + 8: enum('Alignment', 'Automatic'),
                               base + 12: enum('FacingMode', 'Automatic'), base + 16: 1 if r.get('bSubImageBlend', True) else 0,
                               base + 24: f32w(size.get('X', 1.0)), base + 28: f32w(size.get('Y', 1.0))})

    def constant_buffer(self, values):
        cb = np.zeros(self.cb_words, np.uint32)
        for p in self.bindings['parameters']:
            for w in range(p['byteSize'] // 4):
                cpp = p['byteOffset'] + 4 * w
                v = values.get(cpp, self.di_values.get(cpp))
                if v is None:
                    raise GpuError('gpu_parameter_unset:%d' % cpp)
                cb[p['baseIndex'] // 4 + w] = v & 0xFFFFFFFF
        return cb

    def slot(self, kind, name):
        return self.resources.get((kind, OFF[name]))


class GpuIdState:
    """GPU persistent IDs (NiagaraDataSet.cpp AllocateGPUFreeIDs, NiagaraInitFreeIDBuffer.usf, NiagaraComputeFreeIDs.usf)."""
    CHUNK = 1024

    def __init__(self):
        self.allocated = 0
        self.free_list = np.zeros(0, np.int32)
        self.table = np.zeros(0, np.int32)      # last ID -> index table (particle read by ID)

    def allocate(self, instances):
        want = -(-max(1, instances) // self.CHUNK) * self.CHUNK
        if want <= self.allocated:
            return
        fresh = np.arange(self.allocated, want, dtype=np.int32)   # new IDs first, then the existing buffer
        self.free_list = np.concatenate([fresh, self.free_list])[:want]
        self.allocated = want

    def compute_free(self, id_to_index):
        """Free IDs (table entry -1) ascending, written from the start of the list; later entries keep their old values."""
        self.table = np.asarray(id_to_index, np.int32)[:self.allocated].copy()
        free = np.nonzero(id_to_index[:self.allocated] == -1)[0].astype(np.int32)
        self.free_list = self.free_list.copy()
        self.free_list[:len(free)] = free


def struct_words(packed):
    return np.frombuffer(packed, dtype='<u4')


def put_block(values, base, packed_cur, packed_prev):
    for k, w in enumerate(np.concatenate([struct_words(packed_cur), struct_words(packed_prev)])):
        values[base + 4 * k] = int(w)


def particle_read_attributes(pr, layout):
    """NDIParticleRead_FindAttributeIndices: register start per slot (-1 when absent or mistyped; no half attributes), AcquireTag register."""
    indices = [-1] * (4 * pr.n4)
    for k, (name, value) in enumerate(pr.slots):
        var = layout.find(name) if name and value else None
        if var is not None and var.type.key in value[1]:
            indices[k] = var.int_start if value[0] else var.float_start
    var = layout.find('ID')
    return indices, (var.int_start + 1 if var is not None else -1)


def _bind_particle_reads(prog, values, views, counts, sources):
    """Per-dispatch ParticleRead parameters (UNiagaraDataInterfaceParticleRead::SetShaderParameters). A GPU source binds its current data
    (ticked earlier this frame); its instance count goes into this dispatch's count buffer at InstanceCountOffset. CPU or missing sources get the
    engine's error parameters."""
    dev = prog.dev
    dummy_i = dummy_f = None
    for k, pr in enumerate(prog.particle_reads):
        src = sources(pr.source) if sources is not None else None
        b = pr.base
        values[b] = 1 if src is not None and src.local_space else 0
        indices, compressed = [-1] * (4 * pr.n4), [0] * (4 * pr.n4)
        if src is None or not src.gpu:
            if dummy_i is None:
                dummy_i = dev.typed_buffer(np.zeros(1, np.uint32), 'sint')[1]
                dummy_f = dev.typed_buffer(np.zeros(1, np.uint32), 'float')[1]
            values.update({b + 4: 0, b + 8: 0, b + 12: INDEX_NONE, b + 16: 0, b + 20: 0, b + 24: 0, b + 28: INDEX_NONE})
            bound = dict(SpawnedIDsBuffer=dummy_i, IDToIndexTable=dummy_i, InputFloatBuffer=dummy_f, InputIntBuffer=dummy_i, InputHalfBuffer=dummy_f)
        else:
            n, lay = src.count, src.layout
            indices, acquire = particle_read_attributes(pr, lay)
            counts[2 + k] = n
            fl = np.concatenate([np.asarray(src.buffer['float', j][:n], np.uint32) for j in range(lay.total_float)]) if n and lay.total_float else np.zeros(1, np.uint32)
            it = np.concatenate([np.asarray(src.buffer['int32', j][:n], np.uint32) for j in range(lay.total_int)]) if n and lay.total_int else np.zeros(1, np.uint32)
            ids = src.ids
            free = ids.free_list.view(np.uint32) if ids is not None and ids.free_list.size else np.zeros(1, np.uint32)
            table = ids.table.view(np.uint32) if ids is not None and ids.table.size else np.zeros(1, np.uint32)
            values.update({b + 4: src.spawned & 0xFFFFFFFF, b + 8: src.acquire_tag & 0xFFFFFFFF, b + 12: 2 + k, b + 16: n, b + 20: n, b + 24: 0,
                           b + 28: acquire & 0xFFFFFFFF})
            bound = dict(SpawnedIDsBuffer=dev.typed_buffer(free, 'sint')[1], IDToIndexTable=dev.typed_buffer(table, 'sint')[1],
                         InputFloatBuffer=dev.typed_buffer(fl, 'float')[1], InputIntBuffer=dev.typed_buffer(it, 'sint')[1],
                         InputHalfBuffer=dev.typed_buffer(np.zeros(1, np.uint32), 'float')[1])
        for name, off in PR_SRVS.items():
            views[b + off] = bound[name]
        for w in range(4 * pr.n4):
            values[b + 80 + 4 * w] = indices[w] & 0xFFFFFFFF
            values[b + 80 + 16 * pr.n4 + 4 * w] = compressed[w]


def dispatch_tick(prog, layout, inputs_f, inputs_i, count, alloc, spawn_infos, active, reset, tick_counter, blocks, ids=None, ubs=None, sources=None,
                  scene=None):
    """One spawn+update dispatch. inputs_*: dict component -> uint32 array (len count); sources: emitter name -> current particle data of a
    ParticleRead source (count, layout, buffer, ids, spawned, acquire_tag, local_space, gpu) or None; scene: SceneTexturesStruct texels
    (flat_ground_scene) or None for the empty-scene fallbacks. Returns (new_count, float cols, int cols)."""
    dev = prog.dev
    to_spawn, offs, params = gpu_spawn_info(spawn_infos, active, cap=max(0, alloc - count))
    total = count + to_spawn
    nf, ni = layout.total_float, layout.total_int
    if total == 0:
        return 0, to_spawn, {j: np.zeros(0, np.uint32) for j in range(nf)}, {j: np.zeros(0, np.uint32) for j in range(ni)}
    mark = dev.mark()
    groups = (total + THREADS - 1) // THREADS
    gx, gy = (groups, 1) if groups <= MAX_GROUPS_X else (MAX_GROUPS_X, (groups + MAX_GROUPS_X - 1) // MAX_GROUPS_X)
    values = {OFF['ComponentBufferSizeRead']: count, OFF['ComponentBufferSizeWrite']: total, OFF['SimStart']: 1 if reset else 0,
              OFF['ReadInstanceCountOffset']: 0 if count > 0 else INDEX_NONE, OFF['WriteInstanceCountOffset']: 1,
              OFF['EmitterTickCounter']: tick_counter, OFF['NumSpawnedInstances']: to_spawn,
              OFF['IndirectDispatchArgsOffset']: 0}
    for k, w in enumerate([0xFFFFFFFF, 0, 0x00010000, 0x00010000]):  # SimulationStageIterationInfo: (INDEX_NONE, 0, 1 iteration, 1 loop)
        values[OFF['SimulationStageIterationInfo'] + 4 * k] = w
    for k, w in enumerate([0, 0, 0]):
        values[OFF['ParticleIterationStateInfo'] + 4 * k] = w
    for k, w in enumerate(offs):
        values[OFF['EmitterSpawnInfoOffsets'] + 4 * k] = w & 0xFFFFFFFF
    for k, (interval, start_dt, group, start) in enumerate(params):
        b = OFF['EmitterSpawnInfoParams'] + 16 * k
        values.update({b: f32w(interval), b + 4: f32w(start_dt), b + 8: group & 0xFFFFFFFF, b + 12: start & 0xFFFFFFFF})
    for k, w in enumerate([gx * THREADS, gy, 1]):
        values[OFF['DispatchThreadIdBounds'] + 4 * k] = w
    for k, w in enumerate([1, gx * THREADS, 0]):
        values[OFF['DispatchThreadIdToLinear'] + 4 * k] = w
    for name, (cur, prev) in blocks.items():
        put_block(values, OFF[name], cur, prev)
    counts = [count, 0] + [0] * max(2, len(prog.particle_reads))
    views = {}
    _bind_particle_reads(prog, values, views, counts, sources)
    cbs = []
    for k, name in enumerate(prog.uniform_buffers):
        if name == '$Globals':
            cbs.append(dev.constant_buffer(prog.constant_buffer(values)))
        elif ubs is not None and name in ubs:
            data = np.frombuffer(ubs[name] + b'\0' * (-len(ubs[name]) % 16), dtype='<u4') if ubs[name] else np.zeros(4, np.uint32)
            cbs.append(dev.constant_buffer(data))
        elif name == 'SceneTexturesStruct':
            cbs.append(dev.constant_buffer(np.zeros(4, np.uint32)))   # resources only: bound through the resource table below
        else:
            raise GpuError('gpu_uniform_buffer_unsupported:' + name)
    in_f = np.concatenate([inputs_f[j] for j in range(nf)]) if count and nf else np.zeros(4, np.uint32)
    in_i = np.concatenate([inputs_i[j] for j in range(ni)]) if count and ni else np.zeros(4, np.uint32)
    _, f_srv, _ = dev.typed_buffer(in_f, 'float')
    _, i_srv, _ = dev.typed_buffer(in_i, 'sint')
    of_buf, _, of_uav = dev.typed_buffer(np.zeros(max(1, nf * total), np.uint32), 'float', uav=True)
    oi_buf, _, oi_uav = dev.typed_buffer(np.zeros(max(1, ni * total), np.uint32), 'sint', uav=True)
    cnt_buf, _, cnt_uav = dev.typed_buffer(np.array(counts, np.uint32), 'uint', uav=True)
    id_buf = None
    if ids is not None:
        ids.allocate(alloc)
        _, free_srv, _ = dev.typed_buffer(ids.free_list.view(np.uint32), 'sint')
        id_buf, _, id_uav = dev.typed_buffer(np.full(ids.allocated, 0xFFFFFFFF, np.uint32), 'sint', uav=True)
    srvs, uavs = [None] * 32, [None] * 8
    if ids is not None:
        s = prog.slot('UBMT_SRV', 'FreeIDList')
        if s is not None:
            srvs[s] = free_srv
        s = prog.slot('UBMT_UAV', 'RWIDToIndexTable')
        if s is not None:
            uavs[s] = id_uav
    for name, view in (('InputFloat', f_srv), ('InputInt', i_srv), ('StaticInputFloat', prog.static_srv)):
        s = prog.slot('UBMT_SRV', name)
        if s is not None:
            srvs[s] = view
    for name, view in (('RWOutputFloat', of_uav), ('RWOutputInt', oi_uav), ('RWInstanceCounts', cnt_uav)):
        s = prog.slot('UBMT_UAV', name)
        if s is not None:
            uavs[s] = view
    samplers = [None] * 16
    for (kind, off), slot in prog.resources.items():
        if off >= STATIC_PARAMETERS_SIZE:
            view = views.get(off, prog.di_views.get(off))
            if view is None:
                raise GpuError('gpu_resource_unsupported:%s@%d' % (kind, off))
            if kind == 'UBMT_SAMPLER':
                samplers[slot] = view
            else:
                srvs[slot] = view
        elif off in (OFF['FreeIDList'], OFF['RWIDToIndexTable']) and ids is None:
            raise GpuError('gpu_resource_unsupported:%s@%d' % (kind, off))
    if prog.scene_resources:
        tex = scene_views(dev, scene, [member for member, _, _ in prog.scene_resources])
        for member, kind, slot in prog.scene_resources:
            table = samplers if kind == 'sampler' else srvs
            if table[slot] is not None:
                raise GpuError('gpu_slot_conflict:%s:%d' % (kind, slot))
            table[slot] = tex[member]
    last_s = max([i for i, v in enumerate(srvs) if v is not None], default=-1) + 1
    last_u = max([i for i, v in enumerate(uavs) if v is not None], default=-1) + 1
    last_m = max([i for i, v in enumerate(samplers) if v is not None], default=-1) + 1
    dev.dispatch(prog.cs, (gx, gy, 1), cbs=cbs, srvs=srvs[:last_s], uavs=uavs[:last_u], samplers=samplers[:last_m])
    new_count = int(dev.read(cnt_buf, 16)[1])
    outf = dev.read(of_buf, max(1, nf * total) * 4)
    outi = dev.read(oi_buf, max(1, ni * total) * 4)
    fcols = {j: outf[j * total:j * total + new_count].copy() for j in range(nf)}
    icols = {j: outi[j * total:j * total + new_count].copy() for j in range(ni)}
    if id_buf is not None:
        ids.compute_free(dev.read(id_buf, ids.allocated * 4).view(np.int32))
    dev.release_since(mark)
    return new_count, to_spawn, fcols, icols


def gpu_script_index(doc, emitter_export):
    props = emitter_export.get('Properties') or {}
    sel, _ = ir.select_emitter_version(props)
    ref = props['VersionData'][sel].get('GPUComputeScript')
    if not isinstance(ref, dict):
        raise GpuError('gpu_script_missing')
    return _ref_index(ref)
