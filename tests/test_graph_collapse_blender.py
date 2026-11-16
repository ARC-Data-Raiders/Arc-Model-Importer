"""Private-Blender checks for graph_layout.collapse_into_groups: same maths, bounded trees, clean layout."""
import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native_runtime'))
from native_test_support import namespace
namespace()
from _native_runtime_regression_addon.Shared.materials import graph_layout as gl

_OPS = ('ADD', 'MULTIPLY', 'SUBTRACT', 'MAXIMUM', 'MINIMUM', 'POWER')


def _val(s):
    v = getattr(s, 'default_value', None)
    if v is None:
        return '-'
    try:
        return ','.join('%.4f' % float(x) for x in v)
    except TypeError:
        return '%.4f' % float(v) if isinstance(v, (int, float)) else str(v)


def _props(n):
    out = []
    for p in ('operation', 'blend_type', 'data_type', 'interpolation', 'use_clamp', 'clamp'):
        if hasattr(n, p):
            out.append('%s=%s' % (p, getattr(n, p)))
    img = getattr(n, 'image', None)
    if img is not None:
        out.append('img=' + img.name)
    ramp = getattr(n, 'color_ramp', None)
    if ramp is not None:
        out.append('ramp=' + ';'.join('%.3f:%s' % (e.position, _val(e)) for e in ramp.elements))
    return '|'.join(out)


def _link_into(tree, sock):
    for l in tree.links:
        if l.to_socket == sock and l.is_valid and not l.is_muted:
            return l
    return None


def flat_signature(tree):
    """Expression strings of every output, read through groups and reroutes; names and frames ignored."""
    memo = {}

    def from_socket(stack, tree, node, sock):
        key = (tuple(g.as_pointer() for g, _t in stack), node.as_pointer(), sock.identifier)
        if key in memo:
            return memo[key]
        if node.type == 'REROUTE':
            l = _link_into(tree, node.inputs[0])
            r = from_socket(stack, tree, l.from_node, l.from_socket) if l else 'none'
        elif node.type == 'GROUP' and node.node_tree is not None:
            sub = node.node_tree
            idx = list(node.outputs).index(sock)
            out = next(n for n in sub.nodes if n.type == 'GROUP_OUTPUT' and n.is_active_output)
            r = from_input(stack + ((node, tree),), sub, out.inputs[idx])
        elif node.type == 'GROUP_INPUT':
            parent, ptree = stack[-1]
            idx = list(node.outputs).index(sock)
            r = from_input(stack[:-1], ptree, parent.inputs[idx])
        else:
            args = [from_input(stack, tree, s) for s in node.inputs if s.enabled]
            r = '%s.%s(%s)[%s]' % (node.bl_idname, sock.identifier, ','.join(args), _props(node))
        memo[key] = r
        return r

    def from_input(stack, tree, sock):
        l = _link_into(tree, sock)
        if l is None:
            return _val(sock)
        return from_socket(stack, tree, l.from_node, l.from_socket)

    outs = sorted((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL'), key=lambda n: n.target)
    return [from_input((), tree, s) for o in outs for s in o.inputs if s.enabled]


def build_tree(mat_name, *, zones=5, per_zone=58, tagged=True):
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    uv = nt.nodes.new('ShaderNodeTexCoord')
    tails = []
    for z in range(zones):
        prev = None
        for i in range(per_zone):
            if i == 0:
                n = nt.nodes.new('ShaderNodeSeparateXYZ')
                nt.links.new(uv.outputs['UV'], n.inputs[0])
                sock = n.outputs[z % 3]
            elif i == per_zone // 2:
                n = nt.nodes.new('ShaderNodeValToRGB')
                n.color_ramp.elements[0].position = 0.1 * (z + 1) / zones
                n.color_ramp.elements.new(0.5).color = (0.2 * z / zones, 0.3, 0.4, 1.0)
                nt.links.new(prev, n.inputs[0])
                sock = n.outputs[1]
            else:
                n = nt.nodes.new('ShaderNodeMath')
                n.operation = _OPS[(i + z) % len(_OPS)]
                n.inputs[1].default_value = 0.01 * (i + 1) + z
                nt.links.new(prev, n.inputs[0])
                if i > 3 and i % 7 == 0:
                    nt.links.new(tails_src[i - 3], n.inputs[1])
                sock = n.outputs[0]
            if i == 0:
                tails_src = {}
            tails_src[i] = sock
            if tagged:
                gl.tag(n, stage='Wear' if i < per_zone // 2 else 'Surface', zone='Zone %d' % (z + 1))
            prev = sock
        tails.append(prev)
    acc = tails[0]
    for z, t in enumerate(tails[1:]):
        m = nt.nodes.new('ShaderNodeMath')
        m.operation = 'ADD'
        nt.links.new(acc, m.inputs[0])
        nt.links.new(t, m.inputs[1])
        if tagged:
            gl.tag(m, stage='Combine')
        acc = m.outputs[0]
    nt.links.new(acc, bsdf.inputs['Roughness'])
    nt.links.new(tails[-1], bsdf.inputs['Metallic'])
    return mat


def _trees(tree):
    return gl._walk_trees(tree, True)


@unittest.skipUnless(bpy is not None, 'Requires factory-startup Blender')
class GraphCollapseTests(unittest.TestCase):
    def setUp(self):
        self.mats = []
        self.groups_before = set(g.name for g in bpy.data.node_groups)

    def tearDown(self):
        for m in self.mats:
            bpy.data.materials.remove(m)
        for g in list(bpy.data.node_groups):
            if g.name not in self.groups_before:
                bpy.data.node_groups.remove(g)

    def _mat(self, name, **kw):
        m = build_tree(name, **kw)
        self.mats.append(m)
        return m

    def _check_limits(self, nt):
        top = [n for n in nt.nodes if n.type not in ('FRAME', 'REROUTE')]
        self.assertLessEqual(len(top), gl.TOP_MAX, [n.name for n in top])
        for t in _trees(nt):
            if t is nt:
                continue
            body = [n for n in t.nodes if n.type not in ('FRAME', 'REROUTE', 'GROUP_INPUT', 'GROUP_OUTPUT')]
            self.assertLessEqual(len(body), gl.GROUP_HARD, t.name)
        self.assertLessEqual(gl.tree_depth(nt), gl.DEPTH_MAX)

    def _round_trip(self, tagged):
        m = self._mat('Collapse %s' % tagged, tagged=tagged)
        nt = m.node_tree
        self.assertGreaterEqual(len(nt.nodes), 250)
        before = flat_signature(nt)
        cs = gl.collapse_into_groups(nt)   # direct call so a failure raises here
        self.assertTrue(cs['changed'], cs)
        gl.finalize_tree(nt, 'test')
        self.assertTrue(flat_signature(nt) == before, 'flattened signature differs')
        self._check_limits(nt)
        for t in _trees(nt):
            self.assertEqual(gl.assert_clean(t), [], t.name)
        names = [n.label or n.node_tree.name for n in nt.nodes if n.type == 'GROUP']
        self.assertFalse([x for x in names if 'Part ' in x], names)
        return m

    def test_tagged_round_trip_keeps_signature(self):
        m = self._round_trip(True)
        labels = ' '.join(n.label for n in m.node_tree.nodes if n.type == 'GROUP')
        self.assertIn('Zone', labels)

    def test_untagged_round_trip_keeps_signature(self):
        self._round_trip(False)

    def test_second_finalize_is_noop(self):
        m = self._mat('Collapse idem')
        nt = m.node_tree
        gl.finalize_tree(nt, 'test')
        count = len(bpy.data.node_groups)
        sig = gl.content_signature(nt)
        again = gl.finalize_tree(nt, 'test')
        self.assertEqual(again['collapse']['skipped'], 'stamped')
        self.assertEqual(len(bpy.data.node_groups), count)
        self.assertEqual(gl.content_signature(nt), sig)

    def test_identical_content_reuses_groups(self):
        a = self._mat('Collapse reuse A')
        b = self._mat('Collapse reuse B')
        sa = gl.finalize_tree(a.node_tree, 'test')['collapse']
        sb = gl.finalize_tree(b.node_tree, 'test')['collapse']
        self.assertGreater(sa['groups_made'], 0)
        self.assertGreater(sb['groups_reused'], 0, sb)
        self.assertTrue(flat_signature(a.node_tree) == flat_signature(b.node_tree), 'signatures differ')

    def test_user_edited_tree_is_left_alone(self):
        m = self._mat('Collapse edited', zones=2, per_zone=10)
        nt = m.node_tree
        gl.finalize_tree(nt, 'test', collapse=False)
        nt.nodes[3].location.x += 500
        stats = gl.collapse_into_groups(nt)
        self.assertEqual(stats['skipped'], 'user edited')

    def test_finalize_objects_hook(self):
        m = self._mat('Collapse hook')
        mesh = bpy.data.meshes.new('Collapse hook mesh')
        obj = bpy.data.objects.new('Collapse hook obj', mesh)
        mesh.materials.append(m)
        try:
            before = flat_signature(m.node_tree)
            totals = gl.finalize_objects([obj])
            self.assertEqual(totals['errors'], 0)
            self.assertEqual(totals['trees'], 1)
            self.assertTrue(flat_signature(m.node_tree) == before, 'flattened signature differs')
            self._check_limits(m.node_tree)
        finally:
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)


if __name__ == '__main__':
    unittest.main()
