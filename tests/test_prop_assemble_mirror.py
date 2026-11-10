"""Assembled prop children land where UE puts them on Y-negated mesh data (no Blender)."""
import importlib.util
import math
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_prop_assemble_probe', ROOT / 'Maps/prop_assemble.py')
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)
S = np.diag([1.0, -1.0, 1.0])


def ue_matrix(pitch, yaw, roll):
    """FRotationMatrix (row-vector) transposed to act on column vectors."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    return np.array([[cp * cy, cp * sy, sp],
                     [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
                     [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]]).T


def blender_xyz(euler):
    a, b, c = euler
    rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return rz @ ry @ rx


class MirroredChildPoseTests(unittest.TestCase):
    # SM_ExtractionElevator_01_A sockets (location cm, (pitch, yaw, roll), scale) from the cooked mesh JSON.
    SOCKETS = {
        'Tower': ((209.00307, -258.35767, 337.92072), (0.0, 0.0, 2.504478e-06), (1, 1, 1)),
        'Barrier_02_A': ((-908.78937, 0.0001, 8.5599785), (0.0, -89.999985, 2.5e-06), (1, 1, 1)),
        'Door_A_01': ((0.0, 710.06964, -8.00165), (39.999996, -90.000015, 0.0), (1, 1, 1)),
        'Engine_01_F': ((-353.7019, -430.25427, 34.473953), (-8.651422e-06, -179.99998, -180.0), (-1, -1, -1)),
        'DoorPiston_01_A': ((247.1062, 0.0, -29.499882), (9.5e-15, 89.99999, -40.00001), (1, 1, 1)),
    }

    def test_child_pose_is_the_ue_pose_seen_through_the_mesh_mirror(self):
        points = np.array([[0.0, -111.2, 0.0], [35.0, 12.0, -7.0], [-20.0, 64.0, 90.0]])
        for name, (loc, pyr, scale) in self.SOCKETS.items():
            socket = {'location': loc, 'rotation': pyr, 'scale': scale}
            bl_loc, euler, bl_scale = pa._compose_child_pose(socket, {})
            for p in points:
                ue_world = np.array(loc) + ue_matrix(*pyr) @ (np.array(scale) * p)
                imported = S @ p   # PSK/UEModel vertex data is the UE point with Y negated
                bl_world = np.array(bl_loc) + blender_xyz(euler) @ (np.array(bl_scale) * imported)
                np.testing.assert_allclose(bl_world, S @ ue_world, atol=1e-3, err_msg=name)

    def test_asymmetric_children_keep_their_authored_side(self):
        tower = pa._compose_child_pose({'location': self.SOCKETS['Tower'][0], 'rotation': (0, 0, 0), 'scale': (1, 1, 1)}, {})
        self.assertAlmostEqual(tower[0][1], 258.35767, places=3)
        # Barrier_02 spans local Y -111..0 and faces out of the -X side in UE (x to -1020), not inward.
        loc, pyr, scale = self.SOCKETS['Barrier_02_A']
        bl_loc, euler, _ = pa._compose_child_pose({'location': loc, 'rotation': pyr, 'scale': scale}, {})
        tip = np.array(bl_loc) + blender_xyz(euler) @ (S @ np.array([0.0, -111.2, 0.0]))
        self.assertLess(tip[0], -1019.0)


if __name__ == '__main__':
    unittest.main()
