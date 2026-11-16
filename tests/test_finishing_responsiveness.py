"""Finishing phase stays responsive: material graph layout runs in slices, no block reaches 0.5 s.

Builds a synthetic outfit-sized node group (~720 nodes, long links that get a bus) shared by
several materials, then compares the old single synchronous finalize_objects block with the
sliced iter_finalize_objects / StagedRun "material graphs" phase on identical fresh copies.
"""
import sys
import time
import types
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native_runtime'))
from native_test_support import namespace, PACKAGE, ROOT
namespace()
if PACKAGE + '.Maps' not in sys.modules:
    _m = types.ModuleType(PACKAGE + '.Maps')
    _m.__path__ = [str(ROOT / 'Maps')]
    sys.modules[PACKAGE + '.Maps'] = _m
from _native_runtime_regression_addon.Shared.materials import graph_layout as gl

MAX_BLOCK_S = 0.5
CHAINS, DEPTH, MATS = 40, 18, 4


def _big_group(tag):
    g = bpy.data.node_groups.new('W3 Big ' + tag, 'ShaderNodeTree')
    g.interface.new_socket('Out', in_out='OUTPUT', socket_type='NodeSocketFloat')
    nodes, links = g.nodes, g.links
    gin = nodes.new('NodeGroupInput')
    gout = nodes.new('NodeGroupOutput')
    grid = []
    for i in range(CHAINS):
        row = []
        for j in range(DEPTH):
            n = nodes.new('ShaderNodeMath')
            n.name = 'm_%02d_%02d' % (i, j)
            n.operation = 'ADD'
            n.location = (j * 10.0, -i * 10.0)
            if j:
                links.new(row[-1].outputs[0], n.inputs[0])
            row.append(n)
        grid.append(row)
    # Long links across most columns: these route through the bus.
    for i in range(CHAINS):
        links.new(grid[i][0].outputs[0], grid[(i + 7) % CHAINS][DEPTH - 3].inputs[1])
        links.new(grid[i][1].outputs[0], grid[(i + 13) % CHAINS][DEPTH - 2].inputs[1])
    acc = grid[0][-1]
    for i in range(1, CHAINS):
        n = nodes.new('ShaderNodeMath')
        n.name = 'sum_%02d' % i
        links.new(acc.outputs[0], n.inputs[0])
        links.new(grid[i][-1].outputs[0], n.inputs[1])
        acc = n
    links.new(acc.outputs[0], gout.inputs[0])
    return g


def _scene(tag):
    """One object, MATS materials sharing one big group; nothing hooked or laid out yet."""
    g = _big_group(tag)
    me = bpy.data.meshes.new('W3 mesh ' + tag)
    ob = bpy.data.objects.new('W3 obj ' + tag, me)
    bpy.context.scene.collection.objects.link(ob)
    for k in range(MATS):
        m = bpy.data.materials.new('W3 mat %s %d' % (tag, k))
        m.use_nodes = True
        nt = m.node_tree
        grp = nt.nodes.new('ShaderNodeGroup')
        grp.node_tree = g
        bsdf = nt.nodes.get('Principled BSDF')
        nt.links.new(grp.outputs[0], bsdf.inputs['Roughness'])
        me.materials.append(m)
    return ob, g


def _wiring(tree):
    """(source node, socket, target node, socket) with our bus reroutes read through."""
    def src(sock, seen=()):
        n = sock.node
        if n.bl_idname == 'NodeReroute' and n.as_pointer() not in seen:
            ls = n.inputs[0].links
            return src(ls[0].from_socket, seen + (n.as_pointer(),)) if ls else None
        return (n.name, sock.identifier)
    out = set()
    for l in tree.links:
        if l.to_node.bl_idname == 'NodeReroute':
            continue
        out.add((src(l.from_socket), l.to_node.name, l.to_socket.identifier))
    return out


def _locations(tree):
    return {n.name: (round(n.location[0], 2), round(n.location[1], 2)) for n in tree.nodes
            if n.bl_idname not in ('NodeReroute', 'NodeFrame')}


@unittest.skipIf(bpy is None, 'needs Blender')
class FinishingResponsiveness(unittest.TestCase):
    def test_sliced_finish_matches_sync_and_never_blocks(self):
        scene = bpy.context.scene
        ob_a, g_a = _scene('A')
        ob_b, g_b = _scene('B')
        wiring0 = _wiring(g_a)
        self.assertEqual(wiring0, _wiring(g_b))

        # Before: the old finish, one synchronous call (one main-thread block).
        t = time.perf_counter()
        sync = gl.finalize_objects([ob_a], scene)
        before = time.perf_counter() - t

        # After: the sliced phase driven by StagedRun like the modal timer does.
        from _native_runtime_regression_addon.Maps import staged_import as si

        def build():
            yield ('build', 1, 1, 'fixture')
            return 'RESULT'
        run = si.StagedRun(build())
        run.prewarm = False
        ticks, max_tick, total, started = 0, 0.0, 0.0, False
        while True:
            t = time.perf_counter()
            ended = run.advance()
            dt = time.perf_counter() - t
            ticks += 1
            max_tick, total = max(max_tick, dt), total + dt
            if ended:
                if started or not run.begin_graphs([ob_b], scene):
                    break
                started = True
            self.assertLess(ticks, 200000)
        self.assertTrue(started)
        self.assertIsNone(run.error)
        self.assertEqual(run.result, 'RESULT')
        self.assertTrue(all(m.node_tree.get(gl.HOOK_KEY) for m in ob_b.data.materials))

        # Cached second pass (stamp / folder finish still call finalize_objects synchronously).
        t = time.perf_counter()
        again = gl.finalize_objects([ob_b], scene)
        cached = time.perf_counter() - t

        # __stdout__: the test runner swallows captured output of passing files.
        print('W3FINISH before_block_s=%.3f after_max_block_s=%.3f after_total_s=%.3f ticks=%d '
              'cached_pass_s=%.3f laid_out_sync=%d laid_out_again=%d bus=%d'
              % (before, max_tick, total, ticks, cached, sync['laid_out'], again['laid_out'],
                 sum(1 for n in g_b.nodes if n.get(gl.BUS_TAG))), file=sys.__stdout__, flush=True)
        self.assertGreater(sum(1 for n in g_b.nodes if n.get(gl.BUS_TAG)), 0, 'fixture must build a bus')
        self.assertEqual(again['laid_out'], 0)
        self.assertLess(max_tick, MAX_BLOCK_S)
        self.assertLess(cached, MAX_BLOCK_S)
        # Same layout and same wiring from both paths; no link lost or doubled by the bus.
        self.assertEqual(_locations(g_a), _locations(g_b))
        self.assertEqual(_wiring(g_a), wiring0)
        self.assertEqual(_wiring(g_b), wiring0)
        for n in g_b.nodes:
            for s in n.inputs:
                if not s.is_multi_input:
                    self.assertLessEqual(len(s.links), 1, (n.name, s.identifier))


if __name__ == '__main__':
    unittest.main()
