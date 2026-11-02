"""View uniform buffer layout and contents (spec N-O4 §4): FViewUniformShaderParameters constants from VIEW_UNIFORM_BUFFER_MEMBER_TABLE.

The constant part of a global uniform buffer is its non-resource members in declaration order under UE shader-parameter
alignment (TShaderParameterTypeInfo): scalars 4, 2-vectors 8, 3-/4-vectors and matrices 16, array elements 16-byte strided.
The table is parsed from the engine header (the game renderer is the 5.3 fork).
"""
import re
from pathlib import Path

import numpy as np

DEFAULT_HEADER = r'Z:\Epic Games\UE_5.7\Engine\Source\Runtime\Engine\Public\SceneView.h'   # game View UB = 5.7 layout (spec N-O4 section 4)
TYPES = {  # name -> (size, alignment, component kind)
    'float': (4, 4, 'f'), 'int32': (4, 4, 'i'), 'uint32': (4, 4, 'u'), 'int': (4, 4, 'i'), 'uint': (4, 4, 'u'),
    'FVector2f': (8, 8, 'f'), 'FIntPoint': (8, 8, 'i'), 'FUintVector2': (8, 8, 'u'), 'FVector2D': (8, 8, 'f'),
    'FVector3f': (12, 16, 'f'), 'FIntVector': (12, 16, 'i'), 'FUintVector3': (12, 16, 'u'),
    'FVector4f': (16, 16, 'f'), 'FLinearColor': (16, 16, 'f'), 'FIntVector4': (16, 16, 'i'), 'FUintVector4': (16, 16, 'u'),
    'FIntRect': (16, 16, 'i'), 'FMatrix44f': (64, 16, 'f'),
}
DIMENSIONS = {'TVC_MAX': 2, 'NUM_ATMOSPHERE_LIGHTS': 2, 'SKY_IRRADIANCE_ENVIRONMENT_MAP_VEC4_COUNT': 8, 'GlobalDistanceField::MaxClipmaps': 6,
              'MAX_PHYSICS_FIELD_TARGETS': 32}   # 5.3 header constants
MEMBER = re.compile(r'VIEW_UNIFORM_BUFFER_MEMBER(_PER_VIEW)?(_EX|_ARRAY)?\(\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*(?:,\s*([^)]+))?\)')
VIEW_SIZE = (1920, 1080)   # bake view (and scene texture) resolution
NEAR = 10.0                # near plane, cm
DEFAULT_CAMERA = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 90.0)   # no player camera: origin, facing +X


def parse_table(scene_view_h):
    text = Path(scene_view_h).read_text(encoding='utf-8', errors='replace')
    start = text.index('#define VIEW_UNIFORM_BUFFER_MEMBER_TABLE')
    end = text.index('\n\n', start)
    body = text[start:end]
    members = []
    for m in MEMBER.finditer(body):
        _, kind, typ, name, extra = m.groups()
        count = 1
        if kind == '_ARRAY':
            dim = extra.strip()
            count = DIMENSIONS[dim] if dim in DIMENSIONS else int(dim)
        members.append((typ, name, count, kind == '_ARRAY'))
    return members


def layout(members):
    """name -> (byte offset, size, type, count); total constant size."""
    out, off = {}, 0
    for typ, name, count, is_array in members:
        if typ not in TYPES:
            raise ValueError('view_ub_type:' + typ)
        size, align, _ = TYPES[typ]
        if is_array:
            stride = max(16, -(-size // 16) * 16)
            off = -(-off // 16) * 16
            out[name] = (off, stride * count, typ, count)
            off += stride * count
        else:
            off = -(-off // align) * align
            out[name] = (off, size, typ, 1)
            off += size
    return out, -(-off // 16) * 16


SHIPPED_TABLE = Path(__file__).with_name('niagara_view_ub_57.json')


def load_members(scene_view_h=None):
    """Member table from the engine header when available, else the shipped 5.7 table (addon installs have no engine source)."""
    if scene_view_h and Path(scene_view_h).is_file():
        return parse_table(scene_view_h)
    import json
    return [tuple(m) for m in json.loads(SHIPPED_TABLE.read_text(encoding='utf-8'))['members']]


class ViewBuffer:
    """Byte image of the View UB constants with named setters (float32 / int32 words)."""

    def __init__(self, scene_view_h=None):
        self.members = load_members(scene_view_h)
        self.layout, self.size = layout(self.members)
        self.words = np.zeros(self.size // 4, np.uint32)

    def set(self, name, values):
        off, size, typ, _ = self.layout[name]
        kind = TYPES[typ][2]
        arr = np.asarray(values, dtype=np.float32 if kind == 'f' else (np.int32 if kind == 'i' else np.uint32)).ravel()
        self.words[off // 4:off // 4 + arr.size] = arr.view(np.uint32)

    def matrix(self, name, m):
        """UE row-vector matrix: HLSL mul(v, M); store row-major rows (UE FMatrix44f memory order)."""
        self.set(name, np.asarray(m, dtype=np.float64).reshape(4, 4))


def _df(v):
    """FDFVector3 high/low split (double -> float + float remainder)."""
    v = np.asarray(v, dtype=np.float64)
    hi = v.astype(np.float32).astype(np.float64)
    return hi, (v - hi)


def camera_view(vb, cam, width=VIEW_SIZE[0], height=VIEW_SIZE[1], near=NEAR):
    """Fill the camera-related View members (UE world units, LWC tile 0) for a perspective camera.

    cam: (x, y, z, pitch, yaw, roll, fov_deg); UE left-handed, reversed-Z infinite far projection (FReversedZPerspectiveMatrix).
    """
    import niagara_di
    if cam is None:
        cam = DEFAULT_CAMERA
    x, y, z = cam[0:3]
    fwd, right, up = (np.array(v, dtype=np.float64) for v in niagara_di.rotation_axes(*cam[3:6]))
    fov = np.radians(cam[6] if cam[6] > 0 else 90.0)
    # ViewMatrix (UE): world -> view with view axes (right, up, forward) -> columns X=right? UE view space: X right, Y up, Z forward.
    rot = np.stack([right, up, fwd], axis=1)                 # world-to-view rotation (row-vector convention: v_view = v_world @ rot)
    t2v = np.eye(4)
    t2v[:3, :3] = rot
    half = np.tan(fov / 2.0)
    aspect = width / height
    proj = np.zeros((4, 4))
    proj[0, 0] = 1.0 / half
    proj[1, 1] = aspect / half
    proj[2, 3] = 1.0
    proj[3, 2] = near                                         # reversed-Z infinite: z_clip = near, w = view z
    pre = -np.array([x, y, z])                                # PreViewTranslation: translated world = world + pre (camera at origin)
    t2c = t2v @ proj
    vb.matrix('TranslatedWorldToClip', t2c)
    vb.matrix('TranslatedWorldToView', t2v)
    vb.matrix('ViewToTranslatedWorld', np.linalg.inv(t2v))
    vb.matrix('TranslatedWorldToCameraView', t2v)
    vb.matrix('CameraViewToTranslatedWorld', np.linalg.inv(t2v))
    vb.matrix('ViewToClip', proj)
    vb.matrix('ViewToClipNoAA', proj)
    vb.matrix('ClipToView', np.linalg.inv(proj))
    vb.matrix('ClipToTranslatedWorld', np.linalg.inv(t2c))
    for prev, cur in (('PrevViewToClip', proj), ('PrevClipToView', np.linalg.inv(proj)), ('PrevTranslatedWorldToClip', t2c),
                      ('PrevTranslatedWorldToView', t2v), ('PrevViewToTranslatedWorld', np.linalg.inv(t2v))):
        if prev in vb.layout:
            vb.matrix(prev, cur)
    screen = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, proj[2, 2], 1], [0, 0, proj[3, 2], 0]], dtype=np.float64)
    s2t = screen @ np.linalg.inv(t2c)
    for name, m in (('ScreenToTranslatedWorld', s2t), ('PrevScreenToTranslatedWorld', s2t), ('SVPositionToTranslatedWorld', s2t),
                    ('ScreenToRelativeWorld', s2t), ('RelativeWorldToClip', t2c), ('ClipToRelativeWorld', np.linalg.inv(t2c)),
                    ('PrevClipToRelativeWorld', np.linalg.inv(t2c)), ('ClipToPrevClip', np.eye(4)), ('ClipToPrevClipWithAA', np.eye(4)),
                    ('PrevTranslatedWorldToCameraView', t2v), ('PrevCameraViewToTranslatedWorld', np.linalg.inv(t2v))):
        if name in vb.layout:
            vb.matrix(name, m)
    origin = np.array([x, y, z], dtype=np.float64)
    for prefix in ('ViewOrigin', 'WorldViewOrigin', 'PrevWorldCameraOrigin', 'PrevWorldViewOrigin'):
        hi, lo = _df(origin)
        if prefix + 'High' in vb.layout:
            vb.set(prefix + 'High', hi)
            vb.set(prefix + 'Low', lo)
    for prefix in ('PreViewTranslation', 'PrevPreViewTranslation'):
        hi, lo = _df(-origin)
        if prefix + 'High' in vb.layout:
            vb.set(prefix + 'High', hi)
            vb.set(prefix + 'Low', lo)
    for name, val in (('ViewTilePosition', (0.0, 0.0, 0.0)), ('RelativeWorldCameraOriginTO', origin), ('RelativeWorldViewOriginTO', origin),
                      ('RelativePreViewTranslationTO', -origin), ('PrevRelativeWorldCameraOriginTO', origin), ('PrevRelativeWorldViewOriginTO', origin),
                      ('RelativePrevPreViewTranslationTO', -origin), ('ViewRectMinAndSize', (0, 0, width, height))):
        if name in vb.layout:
            vb.set(name, val)
    vb.set('ViewForward', fwd)
    vb.set('ViewUp', up)
    vb.set('ViewRight', right)
    for name in ('HMDViewNoRollUp',):
        vb.set(name, up)
    vb.set('HMDViewNoRollRight', right)
    for name in ('RelativeWorldCameraOrigin', 'RelativeWorldViewOrigin', 'PrevRelativeWorldCameraOrigin', 'PrevRelativeWorldViewOrigin'):
        if name in vb.layout:
            vb.set(name, (x, y, z))
    for name in ('TranslatedWorldCameraOrigin', 'PrevTranslatedWorldCameraOrigin'):
        if name in vb.layout:
            vb.set(name, (0.0, 0.0, 0.0))
    for name in ('RelativePreViewTranslation', 'RelativePrevPreViewTranslation'):
        if name in vb.layout:
            vb.set(name, pre)
    for name, v in (('ViewSizeAndInvSize', (width, height, 1.0 / width, 1.0 / height)), ('BufferSizeAndInvSize', (width, height, 1.0 / width, 1.0 / height)),
                    ('ViewRectMin', (0.0, 0.0, 0.0, 0.0)), ('FieldOfViewWideAngles', (fov, 2 * np.arctan(half / aspect))),
                    ('PrevFieldOfViewWideAngles', (fov, 2 * np.arctan(half / aspect))), ('NearPlane', near), ('TemporalAAJitter', (0, 0, 0, 0)),
                    ('ScreenPositionScaleBias', (0.5, -0.5, 0.5, 0.5)), ('ScreenToViewSpace', (half, half / aspect, 0.0, 0.0)),
                    # CreateInvDeviceZToWorldZTransform: perspective (0, 0, 1 / M[3][2], M[2][2] / M[3][2]), so ConvertFromDeviceZ = near / DeviceZ
                    ('InvDeviceZToWorldZTransform', (0.0, 0.0, 1.0 / near, proj[2, 2] / near)), ('BufferToSceneTextureScale', (1.0, 1.0)),
                    ('PreExposure', 1.0), ('OneOverPreExposure', 1.0)):
        if name in vb.layout:
            vb.set(name, v)
    return vb
