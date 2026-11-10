"""Original node lowering for native view vectors using Blender's actual UV TBN.

The caller owns semantic frames and node creation. Normal Map supplies Blender's
handed tangent frame; the native DirectX green axis is represented by negative G.
No inferred bitangent, transform approximation, or backend import is involved.
"""


def lower_world_view(node_factory, connect):
    """Return scalar XYZ sockets of Geometry.Incoming (surface toward viewer).

    node_factory(bl_idname, label='') creates a node in the caller's current
    DR.Coordinates stage; connect(output_socket, input_socket) links sockets.
    """
    geometry = node_factory('ShaderNodeNewGeometry', label='DR.View.Surface to viewer')
    split = node_factory('ShaderNodeSeparateXYZ', label='DR.View.World components')
    connect(geometry.outputs['Incoming'], split.inputs['Vector'])
    return tuple(split.outputs[axis] for axis in ('X', 'Y', 'Z'))


def lower_native_tangent_view(node_factory, connect, uv_name=''):
    """Return native DX tangent-space view XYZ scalar sockets.

    A Normal Map node transforms each unit tangent axis through the renderer's
    UV-handed basis, including mirrored UVs and object transforms. Projection
    of Incoming onto those unit axes gives native tangent coordinates. Empty
    uv_name follows Blender's active render UV, exactly as other Normal Maps.
    """
    if not isinstance(uv_name, str):
        raise TypeError('uv_name must be a UV layer name string')
    geometry = node_factory('ShaderNodeNewGeometry', label='DR.View.Surface to viewer')
    projections = []
    for axis, encoded in (
        ('Tangent X', (1.0, 0.5, 0.5, 1.0)),
        ('Native bitangent Y', (0.5, 0.0, 0.5, 1.0)),
        ('Normal Z', (0.5, 0.5, 1.0, 1.0)),
    ):
        basis = node_factory('ShaderNodeNormalMap', label='DR.View.' + axis + ' basis')
        basis.space = 'TANGENT'
        basis.uv_map = uv_name
        basis.inputs['Strength'].default_value = 1.0
        basis.inputs['Color'].default_value = encoded
        projection = node_factory('ShaderNodeVectorMath', label='DR.View.' + axis + ' projection')
        projection.operation = 'DOT_PRODUCT'
        connect(geometry.outputs['Incoming'], projection.inputs[0])
        connect(basis.outputs['Normal'], projection.inputs[1])
        projections.append(projection.outputs['Value'])
    return tuple(projections)

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
