"""Minimal D3D11 compute harness over ctypes (spec N-O4 G1): run the game's own DXBC compute shaders on WARP or hardware.

No third-party dependencies: d3d11.dll / d3dcompiler_47.dll are Windows system components. COM methods are called through their
vtable slots (d3d11.h order). Buffers are typed (R32 float/sint/uint, RGBA32 variants) or raw/structured, with SRV/UAV views,
constant buffers, 2D/3D textures for data-interface textures and vector fields, and staging read-back.
"""
import ctypes
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_size_t, c_uint, c_void_p, wintypes

import numpy as np

S_OK = 0
D3D_DRIVER_TYPE_HARDWARE, D3D_DRIVER_TYPE_WARP = 1, 5
D3D11_SDK_VERSION = 7
USAGE_DEFAULT, USAGE_DYNAMIC, USAGE_STAGING = 0, 2, 3
BIND_CONSTANT_BUFFER, BIND_SHADER_RESOURCE, BIND_UNORDERED_ACCESS = 0x4, 0x8, 0x80
CPU_ACCESS_WRITE, CPU_ACCESS_READ = 0x10000, 0x20000
MISC_BUFFER_ALLOW_RAW_VIEWS, MISC_BUFFER_STRUCTURED = 0x20, 0x40
MAP_READ, MAP_WRITE_DISCARD = 1, 4
FORMAT = {'float': 41, 'uint': 42, 'sint': 43, 'float4': 2, 'uint4': 3, 'sint4': 4, 'raw': 39, 'structured': 0, 'half4': 10, 'float2': 16, 'r8uint': 62}
SRV_BUFFER, SRV_BUFFEREX, SRV_TEXTURE2D, SRV_TEXTURE3D = 1, 11, 4, 8
UAV_BUFFER = 1
UAV_FLAG_RAW = 0x1

# vtable slots
DEV_CreateBuffer, DEV_CreateTexture2D, DEV_CreateTexture3D, DEV_CreateSRV, DEV_CreateUAV, DEV_CreateComputeShader, DEV_CreateSamplerState = 3, 5, 6, 7, 8, 18, 23
CTX_Map, CTX_Unmap, CTX_Dispatch, CTX_CopyResource, CTX_UpdateSubresource = 14, 15, 41, 47, 48
CTX_ClearUAVUint = 51
CTX_CSSetSRVs, CTX_CSSetUAVs, CTX_CSSetShader, CTX_CSSetSamplers, CTX_CSSetCBs = 67, 68, 69, 70, 71
CTX_ClearState, CTX_Flush = 110, 111
UNK_Release = 2


class BufferDesc(Structure):
    _fields_ = [('ByteWidth', c_uint), ('Usage', c_uint), ('BindFlags', c_uint), ('CPUAccessFlags', c_uint), ('MiscFlags', c_uint), ('StructureByteStride', c_uint)]


class SubresourceData(Structure):
    _fields_ = [('pSysMem', c_void_p), ('SysMemPitch', c_uint), ('SysMemSlicePitch', c_uint)]


class ViewDesc(Structure):
    """D3D11_SHADER_RESOURCE_VIEW_DESC / D3D11_UNORDERED_ACCESS_VIEW_DESC: format, dimension, 16-byte union."""
    _fields_ = [('Format', c_uint), ('ViewDimension', c_uint), ('u', c_uint * 4)]


class Texture2DDesc(Structure):
    _fields_ = [('Width', c_uint), ('Height', c_uint), ('MipLevels', c_uint), ('ArraySize', c_uint), ('Format', c_uint), ('SampleCount', c_uint),
                ('SampleQuality', c_uint), ('Usage', c_uint), ('BindFlags', c_uint), ('CPUAccessFlags', c_uint), ('MiscFlags', c_uint)]


class Texture3DDesc(Structure):
    _fields_ = [('Width', c_uint), ('Height', c_uint), ('Depth', c_uint), ('MipLevels', c_uint), ('Format', c_uint), ('Usage', c_uint),
                ('BindFlags', c_uint), ('CPUAccessFlags', c_uint), ('MiscFlags', c_uint)]


class SamplerDesc(Structure):
    _fields_ = [('Filter', c_uint), ('AddressU', c_uint), ('AddressV', c_uint), ('AddressW', c_uint), ('MipLODBias', ctypes.c_float),
                ('MaxAnisotropy', c_uint), ('ComparisonFunc', c_uint), ('BorderColor', ctypes.c_float * 4), ('MinLOD', ctypes.c_float),
                ('MaxLOD', ctypes.c_float)]


class MappedSubresource(Structure):
    _fields_ = [('pData', c_void_p), ('RowPitch', c_uint), ('DepthPitch', c_uint)]


class D3D11Error(RuntimeError):
    pass


def _method(obj, slot, restype, *argtypes):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[slot])


def _check(hr, what):
    if hr != S_OK:
        raise D3D11Error('%s failed: 0x%08X' % (what, hr & 0xffffffff))


def release(obj):
    if obj:
        _method(obj, UNK_Release, c_uint)(obj)


class Device:
    def __init__(self, warp=True):
        d3d11 = ctypes.WinDLL('d3d11')
        dev, ctx, fl = c_void_p(), c_void_p(), c_uint()
        hr = d3d11.D3D11CreateDevice(None, D3D_DRIVER_TYPE_WARP if warp else D3D_DRIVER_TYPE_HARDWARE, None, 0, None, 0, D3D11_SDK_VERSION,
                                     byref(dev), byref(fl), byref(ctx))
        _check(hr, 'D3D11CreateDevice')
        self.dev, self.ctx, self.feature_level = dev, ctx, fl.value
        self._owned = []

    def close(self):
        for obj in reversed(self._owned):
            release(obj)
        self._owned = []
        release(self.ctx)
        release(self.dev)
        self.dev = self.ctx = None

    def _own(self, obj):
        self._owned.append(obj)
        return obj

    def mark(self):
        return len(self._owned)

    def release_since(self, mark):
        """Release every object created after mark (per-tick buffers and views)."""
        for obj in reversed(self._owned[mark:]):
            release(obj)
        del self._owned[mark:]

    # ------------------------------------------------------------------ resources
    def buffer(self, data, bind, usage=USAGE_DEFAULT, cpu=0, misc=0, stride=0):
        data = np.ascontiguousarray(data)
        size = max(16, (data.nbytes + 15) // 16 * 16) if bind & BIND_CONSTANT_BUFFER else max(4, data.nbytes)
        desc = BufferDesc(size, usage, bind, cpu, misc, stride)
        init = None
        if data.nbytes:
            padded = np.zeros(size, dtype=np.uint8)
            padded[:data.nbytes] = data.view(np.uint8).ravel()
            self._keep = padded
            init = SubresourceData(padded.ctypes.data, 0, 0)
        buf = c_void_p()
        _check(_method(self.dev, DEV_CreateBuffer, c_int, POINTER(BufferDesc), POINTER(SubresourceData), POINTER(c_void_p))(
            self.dev, byref(desc), byref(init) if init else None, byref(buf)), 'CreateBuffer')
        return self._own(buf)

    def constant_buffer(self, words):
        return self.buffer(np.asarray(words, dtype=np.uint32), BIND_CONSTANT_BUFFER)

    def typed_buffer(self, data, fmt, uav=False):
        """Typed buffer + SRV (and UAV); data is a flat array of 32-bit words (or 4-word elements for *4 formats)."""
        data = np.asarray(data)
        bind = BIND_SHADER_RESOURCE | (BIND_UNORDERED_ACCESS if uav else 0)
        buf = self.buffer(data.view(np.uint32) if data.size else np.zeros(1, np.uint32), bind)
        n = max(1, data.size // (4 if fmt.endswith('4') else 1))
        srv = self.view(buf, fmt, n, uav=False)
        return buf, srv, (self.view(buf, fmt, n, uav=True) if uav else None)

    def format_buffer(self, data_bytes, fmt, elements):
        """Read-only typed buffer + SRV over raw bytes with an explicit element count (array DIs: RG32F, R8_UINT, ...)."""
        raw = np.frombuffer(bytes(data_bytes) + b'\0' * (-len(data_bytes) % 4), dtype=np.uint32) if data_bytes else np.zeros(1, np.uint32)
        buf = self.buffer(raw, BIND_SHADER_RESOURCE)
        return buf, self.view(buf, fmt, max(1, elements), uav=False)

    def raw_buffer(self, data, uav=False, structured_stride=0):
        data = np.asarray(data, dtype=np.uint32)
        bind = BIND_SHADER_RESOURCE | (BIND_UNORDERED_ACCESS if uav else 0)
        misc = MISC_BUFFER_STRUCTURED if structured_stride else MISC_BUFFER_ALLOW_RAW_VIEWS
        buf = self.buffer(data if data.size else np.zeros(1, np.uint32), bind, misc=misc, stride=structured_stride)
        n = max(1, data.size * 4 // (structured_stride or 4))
        kind = 'structured' if structured_stride else 'raw'
        return buf, self.view(buf, kind, n, uav=False), (self.view(buf, kind, n, uav=True) if uav else None)

    def view(self, buf, fmt, n, uav):
        if uav:
            desc = ViewDesc(FORMAT[fmt], UAV_BUFFER, (c_uint * 4)(0, n, UAV_FLAG_RAW if fmt == 'raw' else 0, 0))
            out = c_void_p()
            _check(_method(self.dev, DEV_CreateUAV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, buf, byref(desc), byref(out)), 'CreateUAV')
        else:
            if fmt == 'raw':
                desc = ViewDesc(FORMAT[fmt], SRV_BUFFEREX, (c_uint * 4)(0, n, 1, 0))
            else:
                desc = ViewDesc(FORMAT[fmt], SRV_BUFFER, (c_uint * 4)(0, n, 0, 0))
            out = c_void_p()
            _check(_method(self.dev, DEV_CreateSRV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, buf, byref(desc), byref(out)), 'CreateSRV')
        return self._own(out)

    def texture2d(self, data, size, fmt):
        """Single-mip 2D texture + SRV in a FORMAT key ('half4', 'float', 'uint4', ...); data holds the packed texels, row-major [H, W, ...]."""
        w, h = size
        desc = Texture2DDesc(w, h, 1, 1, FORMAT[fmt], 1, 0, USAGE_DEFAULT, BIND_SHADER_RESOURCE, 0, 0)
        data = np.ascontiguousarray(data)
        self._keep_tex = data
        init = SubresourceData(data.ctypes.data, data.nbytes // h, data.nbytes)
        tex = c_void_p()
        _check(_method(self.dev, DEV_CreateTexture2D, c_int, POINTER(Texture2DDesc), POINTER(SubresourceData), POINTER(c_void_p))(
            self.dev, byref(desc), byref(init), byref(tex)), 'CreateTexture2D')
        self._own(tex)
        vd = ViewDesc(FORMAT[fmt], SRV_TEXTURE2D, (c_uint * 4)(0, 1, 0, 0))
        srv = c_void_p()
        _check(_method(self.dev, DEV_CreateSRV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, tex, byref(vd), byref(srv)), 'CreateSRV2D')
        return self._own(srv)

    def texture3d(self, data_rgba16f, size):
        """RGBA16F 3D texture (vector fields): data uint16 [D,H,W,4]."""
        w, h, d = size
        desc = Texture3DDesc(w, h, d, 1, FORMAT['half4'], USAGE_DEFAULT, BIND_SHADER_RESOURCE, 0, 0)
        data = np.ascontiguousarray(data_rgba16f, dtype=np.uint16)
        self._keep_tex = data
        init = SubresourceData(data.ctypes.data, w * 8, w * h * 8)
        tex = c_void_p()
        _check(_method(self.dev, DEV_CreateTexture3D, c_int, POINTER(Texture3DDesc), POINTER(SubresourceData), POINTER(c_void_p))(
            self.dev, byref(desc), byref(init), byref(tex)), 'CreateTexture3D')
        self._own(tex)
        vd = ViewDesc(FORMAT['half4'], SRV_TEXTURE3D, (c_uint * 4)(0, 1, 0, 0))
        srv = c_void_p()
        _check(_method(self.dev, DEV_CreateSRV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, tex, byref(vd), byref(srv)), 'CreateSRV3D')
        return self._own(srv)

    def sampler(self, filter_=0x15, address=1):
        """Default: MIN_MAG_MIP_LINEAR (0x15), WRAP (1)."""
        desc = SamplerDesc(filter_, address, address, address, 0.0, 1, 8, (ctypes.c_float * 4)(0, 0, 0, 0), 0.0, 3.402823466e38)
        out = c_void_p()
        _check(_method(self.dev, DEV_CreateSamplerState, c_int, POINTER(SamplerDesc), POINTER(c_void_p))(self.dev, byref(desc), byref(out)), 'CreateSampler')
        return self._own(out)

    def sampler_axes(self, filter_, u, v, w):
        desc = SamplerDesc(filter_, u, v, w, 0.0, 1, 8, (ctypes.c_float * 4)(0, 0, 0, 0), 0.0, 3.402823466e38)
        out = c_void_p()
        _check(_method(self.dev, DEV_CreateSamplerState, c_int, POINTER(SamplerDesc), POINTER(c_void_p))(self.dev, byref(desc), byref(out)), 'CreateSampler')
        return self._own(out)

    def compute_shader(self, dxbc):
        code = ctypes.create_string_buffer(bytes(dxbc), len(dxbc))
        out = c_void_p()
        _check(_method(self.dev, DEV_CreateComputeShader, c_int, c_void_p, c_size_t, c_void_p, POINTER(c_void_p))(
            self.dev, code, len(dxbc), None, byref(out)), 'CreateComputeShader')
        return self._own(out)

    # ------------------------------------------------------------------ execution
    def dispatch(self, cs, groups, cbs=(), srvs=(), uavs=(), samplers=()):
        ctx = self.ctx
        arr = lambda items: (c_void_p * max(1, len(items)))(*[i if i else None for i in items])
        _method(ctx, CTX_CSSetShader, None, c_void_p, c_void_p, c_uint)(ctx, cs, None, 0)
        if cbs:
            _method(ctx, CTX_CSSetCBs, None, c_uint, c_uint, c_void_p)(ctx, 0, len(cbs), arr(cbs))
        if srvs:
            _method(ctx, CTX_CSSetSRVs, None, c_uint, c_uint, c_void_p)(ctx, 0, len(srvs), arr(srvs))
        if samplers:
            _method(ctx, CTX_CSSetSamplers, None, c_uint, c_uint, c_void_p)(ctx, 0, len(samplers), arr(samplers))
        if uavs:
            _method(ctx, CTX_CSSetUAVs, None, c_uint, c_uint, c_void_p, c_void_p)(ctx, 0, len(uavs), arr(uavs), None)
        _method(ctx, CTX_Dispatch, None, c_uint, c_uint, c_uint)(ctx, *groups)
        # unbind so the same buffers can be read back / rebound as SRVs
        nulls = (c_void_p * 8)()
        _method(ctx, CTX_CSSetUAVs, None, c_uint, c_uint, c_void_p, c_void_p)(ctx, 0, 8, nulls, None)
        _method(ctx, CTX_CSSetSRVs, None, c_uint, c_uint, c_void_p)(ctx, 0, 8, nulls)

    def read(self, buf, nbytes):
        staging = self.buffer(np.zeros(nbytes // 4 or 1, np.uint32), 0, usage=USAGE_STAGING, cpu=CPU_ACCESS_READ)
        _method(self.ctx, CTX_CopyResource, None, c_void_p, c_void_p)(self.ctx, staging, buf)
        mapped = MappedSubresource()
        _check(_method(self.ctx, CTX_Map, c_int, c_void_p, c_uint, c_uint, c_uint, POINTER(MappedSubresource))(
            self.ctx, staging, 0, MAP_READ, 0, byref(mapped)), 'Map')
        out = np.frombuffer(ctypes.string_at(mapped.pData, nbytes), dtype=np.uint32).copy()
        _method(self.ctx, CTX_Unmap, None, c_void_p, c_uint)(self.ctx, staging, 0)
        return out


def compile_hlsl(source, entry='main', target='cs_5_0'):
    """D3DCompile via the system d3dcompiler_47.dll (tests and lab fixtures only)."""
    lib = ctypes.WinDLL('d3dcompiler_47')
    src = source.encode('utf-8')
    code, err = c_void_p(), c_void_p()
    hr = lib.D3DCompile(src, len(src), None, None, None, entry.encode(), target.encode(), 0, 0, byref(code), byref(err))
    if hr != S_OK:
        msg = ''
        if err:
            ptr = _method(err, 3, c_void_p)(err)
            n = _method(err, 4, c_size_t)(err)
            msg = ctypes.string_at(ptr, n).decode('utf-8', 'replace')
        raise D3D11Error('D3DCompile failed: ' + msg)
    ptr = _method(code, 3, c_void_p)(code)
    n = _method(code, 4, c_size_t)(code)
    blob = ctypes.string_at(ptr, n)
    release(code)
    return blob


def disassemble(dxbc):
    """D3DDisassemble via the system d3dcompiler_47.dll."""
    lib = ctypes.WinDLL('d3dcompiler_47')
    out = c_void_p()
    code = ctypes.create_string_buffer(bytes(dxbc), len(dxbc))
    hr = lib.D3DDisassemble(code, c_size_t(len(dxbc)), c_uint(0), None, byref(out))
    _check(hr, 'D3DDisassemble')
    ptr = _method(out, 3, c_void_p)(out)
    n = _method(out, 4, c_size_t)(out)
    text = ctypes.string_at(ptr, n).decode('latin1')
    release(out)
    return text
