"""Small editable Blender graph compiler. All UV transforms operate in Unreal UV space."""
from pathlib import Path
import bpy
from contextlib import contextmanager
from math import tau
from .records import metadata
from .assets import package


def arrange(tree):
    """Place every dependency before its consumer, including inside groups."""
    nodes = [n for n in tree.nodes if n.type != 'FRAME']
    parents = {n: set() for n in nodes}
    for link in tree.links:
        if link.from_node in parents and link.to_node in parents:
            parents[link.to_node].add(link.from_node)
    depths = {}
    def depth(node, visiting=()):
        if node in depths: return depths[node]
        if node in visiting: return 0
        depths[node] = max((depth(p, visiting + (node,)) + 1 for p in parents[node]), default=0)
        return depths[node]
    columns = {}
    for n in nodes: columns.setdefault(depth(n), []).append(n)
    x = 0
    for d in sorted(columns):
        column = columns[d]
        y = 0
        for n in column:
            n.parent = None
            n.width = 250 if n.type == 'GROUP' else 230 if n.type == 'TEX_IMAGE' else 190
            n.location = (x, y)
            height = 130 + 24 * max(sum(not s.hide for s in n.inputs), sum(not s.hide for s in n.outputs))
            if n.type == 'TEX_IMAGE': height = max(height, 340)
            if n.type == 'BSDF_PRINCIPLED': height = 680
            y -= height + 90
        x += max(n.width for n in column) + 120
    # Semantic frames remain useful labels but never determine the wire order.
    for frame in [n for n in tree.nodes if n.type == 'FRAME']: tree.nodes.remove(frame)


class Graph:
    def __init__(self, material, obj, index, report, tree=None, parent=None):
        self.mat, self.obj, self.index, self.report = material, obj, index, report
        if tree is None:
            material.use_nodes = True
            tree = material.node_tree
        self.tree, self.parent = tree, parent
        self.tree.nodes.clear()
        self.n, self.l = self.tree.nodes, self.tree.links
        self.x, self.y = 0, 0
        self.frame = None
        self.uv_cache = {}
        self.images = {}
        self.sample_cache, self.transform_cache, self.orientation_cache = {}, {}, {}
        self.external = {}
        self.group_node = None

    def node(self, typ, label='', **attrs):
        n = self.n.new(typ)
        n.label = label
        if label:
            n.name = label
        if self.frame:
            n.parent = self.frame
        n.location = (self.x, self.y)
        self.x += 210
        if self.x > 1260:
            self.x = 0; self.y -= 300
        for k, v in attrs.items():
            setattr(n, k, v)
        return n

    def section(self, label):
        self.frame = None
        frame = self.n.new('NodeFrame')
        frame.label = label; frame.name = label
        frame.location = (0, self.y - 220)
        self.frame = frame; self.x = self.y = 0
        return frame

    def set(self, socket, v):
        if isinstance(v, bpy.types.NodeSocket):
            if v.id_data != self.tree:
                if self.parent is None: raise ValueError('Shader socket belongs to a different node tree')
                key = v.as_pointer()
                if key not in self.external:
                    name = (v.node.label or v.node.name) + ' · ' + v.name
                    typ = 'NodeSocketColor' if v.type == 'RGBA' else 'NodeSocketVector' if v.type == 'VECTOR' else 'NodeSocketFloat'
                    self.tree.interface.new_socket(name=name, in_out='INPUT', socket_type=typ)
                    self.parent.set(self.group_node.inputs[-1], v)
                    self.external[key] = self.group_input.outputs[-2]
                v = self.external[key]
            self.l.new(v, socket)
        elif isinstance(v, (tuple, list)):
            if hasattr(socket.default_value, '__len__'):
                size = len(socket.default_value)
                socket.default_value = tuple(v[:size]) + (1,) * max(0, size - len(v))
            else:
                socket.default_value = float(v[0])
        elif hasattr(socket.default_value, '__len__'):
            size = len(socket.default_value)
            socket.default_value = (float(v),) * min(size, 3) + ((1,) if size == 4 else ())
        else:
            socket.default_value = v

    def value(self, v, label=''):
        n = self.node('ShaderNodeValue', label)
        n.outputs[0].default_value = float(v)
        return n.outputs[0]

    def color(self, v, label=''):
        n = self.node('ShaderNodeRGB', label)
        n.outputs[0].default_value = tuple(v[:3]) + (v[3] if len(v) > 3 else 1,)
        return n.outputs[0]

    def math(self, op, a, b=0, c=0, label=''):
        n = self.node('ShaderNodeMath', label or op, operation=op)
        for s, v in zip(n.inputs, [a, b, c]):
            self.set(s, v)
        return n.outputs[0]

    def add(self, a, b):
        if isinstance(b, (int, float)) and b == 0: return a
        if isinstance(a, (int, float)) and a == 0: return b
        return self.math('ADD', a, b)
    def sub(self, a, b):
        if isinstance(b, (int, float)) and b == 0: return a
        return self.math('SUBTRACT', a, b)
    def mul(self, a, b):
        if isinstance(b, (int, float)) and b == 1: return a
        if isinstance(a, (int, float)) and a == 1: return b
        return self.math('MULTIPLY', a, b)
    def div(self, a, b):
        if isinstance(b, (int, float)) and b == 1: return a
        return self.math('DIVIDE', a, b)
    def clamp(self, a): return self.math('MINIMUM', self.math('MAXIMUM', a, 0), 1)

    def mix(self, a, b, f, label='lerp'):
        if isinstance(f, (int, float)) and f == 0: return a
        if isinstance(f, (int, float)) and f == 1: return b
        n = self.node('ShaderNodeMixRGB', label, blend_type='MIX')
        self.set(n.inputs[0], f); self.set(n.inputs[1], a); self.set(n.inputs[2], b)
        return n.outputs[0]

    def cmul(self, a, b, label='multiply'):
        n = self.node('ShaderNodeMixRGB', label, blend_type='MULTIPLY')
        n.inputs[0].default_value = 1
        self.set(n.inputs[1], a); self.set(n.inputs[2], b)
        return n.outputs[0]

    def split(self, v):
        n = self.node('ShaderNodeSeparateXYZ')
        self.set(n.inputs[0], v)
        return tuple(n.outputs)

    def combine(self, x, y, z=0):
        n = self.node('ShaderNodeCombineXYZ')
        for s, v in zip(n.inputs, [x, y, z]): self.set(s, v)
        return n.outputs[0]

    def vector(self, op, a, b=(0, 0, 0), scale=1):
        n = self.node('ShaderNodeVectorMath', op, operation=op)
        self.set(n.inputs[0], a); self.set(n.inputs[1], b)
        if op == 'SCALE': self.set(n.inputs[3], scale)
        return n.outputs['Value'] if op in {'DOT_PRODUCT', 'LENGTH', 'DISTANCE'} else n.outputs['Vector']

    def uv(self, channel=0):
        if self.parent is not None: return self.parent.uv(channel)
        if channel in self.uv_cache: return self.uv_cache[channel]
        names = [u.name for u in self.obj.data.uv_layers]
        if channel >= len(names):
            self.report['warnings'].append(f'Missing UV channel {channel}; UV0 substituted')
            channel = 0
        n = self.node('ShaderNodeUVMap', f'UV{channel} (Unreal coordinates)')
        n.uv_map = names[channel] if names else ''
        uv = self.orient_uv(n.outputs['UV'])
        self.uv_cache[channel] = uv
        return uv

    def transform_uv(self, channel=0, scale=1, offset=(0, 0), rotation=0):
        key = (channel, scale, tuple(offset), rotation)
        if key in self.transform_cache: return self.transform_cache[key]
        uv = self.uv(channel)
        if scale != 1 or offset != (0, 0) or rotation != 0:
            n = self.helper('UV transform', {'UV': uv, 'Tiling': scale, 'Offset U': offset[0], 'Offset V': offset[1], 'Rotation turns': rotation})
            uv = n.outputs['UV']
        self.transform_cache[key] = uv
        return uv

    def orient_uv(self, uv):
        key = uv.as_pointer() if isinstance(uv, bpy.types.NodeSocket) else tuple(uv)
        if key not in self.orientation_cache:
            self.orientation_cache[key] = self.helper('UV orientation', {'UV': uv}).outputs['UV']
        return self.orientation_cache[key]

    def helper(self, kind, values, uv_name=''):
        name = 'ARC · ' + kind + (' · ' + uv_name if uv_name else '') + ' · 1.2'
        tree = bpy.data.node_groups.get(name)
        if tree is None or tree.get('arc_helper') != kind:
            tree = bpy.data.node_groups.new(name, 'ShaderNodeTree')
            tree['arc_helper'] = kind
            specs = {
                'UV orientation': [('UV', 'Vector', (0,0,0))],
                'UV transform': [('UV','Vector',(0,0,0)), ('Tiling','Float',1), ('Offset U','Float',0), ('Offset V','Float',0), ('Rotation turns','Float',0)],
                'DX normal decode': [('Red','Float',.5), ('Green','Float',.5), ('Strength','Float',1)],
                'RNM normals': [('Base','Vector',(0,0,1)), ('Detail','Vector',(0,0,1))],
                'DX tangent to world': [('Normal','Vector',(0,0,1))],
            }[kind]
            output = 'UV' if kind.startswith('UV') else 'Normal'
            for label, typ, default in specs:
                item = tree.interface.new_socket(name=label, in_out='INPUT', socket_type='NodeSocket'+typ)
                item.default_value = default
                if typ == 'Float': item.min_value = -100000; item.max_value = 100000
            tree.interface.new_socket(name=output, in_out='OUTPUT', socket_type='NodeSocketVector')
            b = Graph(self.mat, self.obj, self.index, self.report, tree=tree)
            inp = b.node('NodeGroupInput'); out = b.node('NodeGroupOutput')
            v = inp.outputs
            if kind == 'UV orientation':
                x,y,z = b.split(v['UV']); result = b.combine(x, b.sub(1,y), z)
            elif kind == 'UV transform':
                scaled = b.vector('SCALE', v['UV'], scale=v['Tiling'])
                rotate = b.node('ShaderNodeVectorRotate', 'Rotate in Unreal UV space', rotation_type='AXIS_ANGLE')
                b.set(rotate.inputs['Vector'], scaled); rotate.inputs['Center'].default_value = (.5,.5,0)
                rotate.inputs['Axis'].default_value = (0,0,1)
                b.set(rotate.inputs['Angle'], b.mul(v['Rotation turns'], tau))
                result = b.vector('ADD', rotate.outputs[0], b.combine(v['Offset U'], v['Offset V']))
            elif kind == 'DX normal decode':
                x = b.mul(b.sub(b.mul(v['Red'],2),1),v['Strength'])
                y = b.mul(b.sub(b.mul(v['Green'],2),1),v['Strength'])
                z = b.math('SQRT',b.math('MAXIMUM',b.sub(1,b.add(b.mul(x,x),b.mul(y,y))),0))
                result = b.combine(x,y,z)
            elif kind == 'RNM normals':
                t = b.vector('ADD',v['Base'],(0,0,1)); u = b.vector('MULTIPLY',v['Detail'],(-1,-1,1))
                result = b.vector('NORMALIZE',b.vector('SUBTRACT',b.vector('SCALE',t,scale=b.div(b.vector('DOT_PRODUCT',t,u),b.split(t)[2])),u))
            else:
                x,y,z = b.split(v['Normal'])
                encoded = b.combine(b.add(b.mul(x,.5),.5),b.sub(.5,b.mul(y,.5)),b.add(b.mul(z,.5),.5))
                normal = b.node('ShaderNodeNormalMap', 'Blender tangent basis', space='TANGENT')
                normal.uv_map = uv_name; b.set(normal.inputs['Color'],encoded)
                result = normal.outputs['Normal']
            b.set(out.inputs[output], result); arrange(tree)
        node = self.node('ShaderNodeGroup', kind); node.node_tree = tree
        for label, value in values.items(): self.set(node.inputs[label], value)
        return node

    @contextmanager
    def block(self, label):
        tree = bpy.data.node_groups.new(self.mat.name + ' · ' + label, 'ShaderNodeTree')
        tree['arc_stage'] = label
        group = self.node('ShaderNodeGroup', label); group.node_tree = tree
        child = Graph(self.mat, self.obj, self.index, self.report, tree=tree, parent=self)
        child.group_node = group
        child.group_input = child.node('NodeGroupInput')
        child.group_output = child.node('NodeGroupOutput')
        try: yield child
        finally: arrange(tree)

    def export(self, value, label, typ=None):
        if typ is None:
            typ = 'Color' if isinstance(value, (tuple,list)) and len(value)==4 or isinstance(value,bpy.types.NodeSocket) and value.type=='RGBA' else 'Vector' if isinstance(value,(tuple,list)) or isinstance(value,bpy.types.NodeSocket) and value.type=='VECTOR' else 'Float'
        self.tree.interface.new_socket(name=label, in_out='OUTPUT', socket_type='NodeSocket'+typ)
        self.set(self.group_output.inputs[label], value)
        return self.group_node.outputs[label]

    def parameter(self, label, value, typ='Float'):
        """A visible stage-group default, editable per material instance."""
        if self.parent is None: return self.color(value,label) if typ=='Color' else self.value(value,label)
        item = self.tree.interface.new_socket(name=label, in_out='INPUT', socket_type='NodeSocket'+typ)
        item.default_value = value
        if typ=='Float': item.min_value=-100000; item.max_value=100000
        self.set(self.group_node.inputs[label], value)
        return self.group_input.outputs[label]

    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        path = self.index.texture(ref, layer)
        if path is None:
            if required:
                item = package(ref) + (f'_LAYER{layer}' if layer is not None else '')
                if item not in self.report['missing_textures']: self.report['missing_textures'].append(item)
            return None
        info = metadata().get('textures', {}).get(package(ref).casefold(), {})
        if srgb is None: srgb = info.get('SRGB', True)
        colorspace = 'sRGB' if srgb else 'Non-Color'
        image_key = str(path).casefold() + '|' + colorspace
        img = self.images.get(image_key)
        if img is None:
            img = next((i for i in bpy.data.images if i.get('arc_image_key') == image_key), None)
            if img is None:
                img = bpy.data.images.load(str(path), check_existing=True)
                try:
                    if int(img.size[0]) <= 0 or int(img.size[1]) <= 0:
                        img.reload()
                except Exception:
                    pass
                img.colorspace_settings.name = colorspace
                img.alpha_mode = 'CHANNEL_PACKED'
                img['arc_image_key'] = image_key
            self.images[image_key] = img
        return self.sample_image(img, uv, label or Path(path).stem, info)

    def sample_image(self, img, uv=None, label='', info=None):
        """Sample an existing image, including lossless character mask channel packs."""
        info = info or {}
        image_key = img.get('arc_image_key', str(img.as_pointer()))
        n = self.node('ShaderNodeTexImage', label or img.name)
        n.image = img
        n.extension = 'REPEAT'
        if info.get('AddressX', '').endswith('TA_Clamp') and info.get('AddressY', '').endswith('TA_Clamp'):
            n.extension = 'EXTEND'
        n.interpolation = 'Closest' if str(info.get('Filter', '')).endswith('TF_Nearest') else 'Linear'
        uv = uv if uv is not None else self.uv(0)
        sample_key = (image_key, uv.as_pointer() if isinstance(uv,bpy.types.NodeSocket) else tuple(uv))
        if sample_key in self.sample_cache:
            self.n.remove(n); return self.sample_cache[sample_key]
        self.set(n.inputs['Vector'], self.orient_uv(uv))
        c = self.node('ShaderNodeSeparateColor', mode='RGB')
        self.l.new(n.outputs['Color'], c.inputs[0])
        result = {'color': n.outputs['Color'], 'r': c.outputs['Red'], 'g': c.outputs['Green'],
                  'b': c.outputs['Blue'], 'a': n.outputs['Alpha'], 'node': n}
        self.sample_cache[sample_key] = result
        return result

    def decode_normal(self, tex, strength=1):
        if not tex: return self.combine(0, 0, 1)
        return self.helper('DX normal decode', {'Red':tex['r'], 'Green':tex['g'], 'Strength':strength}).outputs['Normal']

    def rnm(self, a, b):
        return self.helper('RNM normals', {'Base':a, 'Detail':b}).outputs['Normal']

    def triplanar(self, ref, size=100, srgb=None, label='World projection'):
        geo = self.node('ShaderNodeNewGeometry')
        pos = self.vector('MULTIPLY', geo.outputs['Position'], (100 / size, -100 / size, 100 / size))
        x, y, z = self.split(pos)
        normal = self.vector('ABSOLUTE', geo.outputs['Normal'])
        a, b, c = self.split(normal)
        denom = self.math('MAXIMUM', self.add(self.add(a, b), c), 1e-6)
        weights = [self.div(v, denom) for v in [a, b, c]]
        samples = [self.image(ref, uv, srgb=srgb, label=label + axis) for axis, uv in
                   [(' X', self.combine(y, z)), (' Y', self.combine(x, z)), (' Z', self.combine(x, y))]]
        if not all(samples): return None
        result = {}
        for key in ['color', 'r', 'g', 'b', 'a']:
            v = (0, 0, 0) if key == 'color' else 0
            for sample, weight in zip(samples, weights):
                if key == 'color':
                    v = self.vector('ADD', v, self.vector('SCALE', sample[key], scale=weight))
                else:
                    v = self.add(v, self.mul(sample[key], weight))
            result[key] = v
        return result

    def normal_to_world(self, normal, uv_channel=0):
        names = [u.name for u in self.obj.data.uv_layers]
        uv_name = names[uv_channel] if len(names) > uv_channel else names[0] if names else ''
        n = self.helper('DX tangent to world', {'Normal':normal}, uv_name=uv_name)
        return n.outputs['Normal']

    def finish(self, color, roughness=.5, metallic=0, normal=None, specular=.5, emission=None, cloth=0, alpha=1, emission_strength=1, response=None):
        response=response or {}
        if self.parent is not None:
            values = [self.export(v,label,typ) for v,label,typ in [
                (color,'Base colour','Color'),(roughness,'Roughness','Float'),(metallic,'Metallic','Float'),
                (specular,'Specular','Float'),(alpha,'Alpha','Float'),(cloth,'Cloth','Float')]]
            normal = self.export(normal,'Tangent normal','Vector') if normal is not None else None
            if emission is not None:
                emission = self.export(emission,'Emission','Color')
                emission_strength = self.export(emission_strength,'Emission strength','Float')
            response={name:self.export(value,name) for name,value in response.items()}
            return self.parent.finish(values[0],values[1],values[2],normal,values[3],emission,values[5],values[4],emission_strength,response)
        self.frame = None; self.x = 1800; self.y = 0
        n = self.node('ShaderNodeBsdfPrincipled', 'ARC material response')
        for name, v in [('Base Color', color), ('Roughness', roughness), ('Metallic', metallic),
                        ('Specular IOR Level', specular), ('Alpha', alpha), ('Sheen Weight', cloth)]:
            self.set(n.inputs[name], v)
        # UE dielectric F0 = 0.08*Specular; at IOR 1.5 Blender's scale maps directly.
        n.inputs['IOR'].default_value = 1.5
        for name,value in response.items():self.set(n.inputs[name],value)
        if normal is not None: self.set(n.inputs['Normal'], self.normal_to_world(normal))
        if emission is not None:
            self.set(n.inputs['Emission Color'], emission)
            self.set(n.inputs['Emission Strength'], emission_strength)
        out = self.node('ShaderNodeOutputMaterial')
        self.l.new(n.outputs[0], out.inputs['Surface'])
        self.mat['arc_albedo_node'] = n.name
        self.report['node_count'] = len(self.n)
        seen = set()
        def count(tree):
            if tree.as_pointer() in seen: return 0
            seen.add(tree.as_pointer())
            return len(tree.nodes) + sum(count(n.node_tree) for n in tree.nodes if n.type=='GROUP' and n.node_tree)
        self.report['unique_graph_nodes'] = count(self.tree)
        arrange(self.tree)
        return n
