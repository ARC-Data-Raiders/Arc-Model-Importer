"""Pure O5.5 property packing and coordinate conversions."""
import math
import numpy as np


def binding(name):
    parts = name.split("_")
    if name.startswith("dr_camneg_"):
        return ("VIEW_LAYER", "dr_cam", parts[-1], True)
    if name.startswith("dr_camfwd_"):
        return ("VIEW_LAYER", "dr_camfwd", parts[-1], False)
    if name.startswith("dr_cam_"):
        return ("VIEW_LAYER", "dr_cam", parts[-1], False)
    if name.startswith("dr_mpc_"):
        return ("VIEW_LAYER", name, "x", False)
    if name.startswith(("dr_l2w_", "dr_w2l_")):
        return ("OBJECT", name.rsplit("_", 1)[0], parts[-1], False)
    if name.startswith("dr_cpd_"):
        k = int(parts[-1])
        return ("OBJECT", "dr_cpd_" + str(k // 4), "xyzw"[k % 4], False)
    raise ValueError("binding:" + name)


def uv_name(layer_names, k):
    if layer_names is None:
        return "" if k == 0 else "UV1"
    if not layer_names:
        return ""
    j = min(k, len(layer_names) - 1)
    return "" if j == 0 else layer_names[j]


def ue_affine(m4):
    scale = np.diag([100.0, -100.0, 100.0, 1.0])
    a = scale @ np.asarray(m4, dtype=np.float64) @ np.diag([0.01, -0.01, 0.01, 1.0])
    inverse = np.linalg.inv(a)
    return a[:3].astype(np.float32).tolist(), inverse[:3].astype(np.float32).tolist()


def camera_props(m4):
    m = np.asarray(m4, dtype=np.float64)
    position = [100.0 * m[0, 3], -100.0 * m[1, 3], 100.0 * m[2, 3], 0.0]
    f = [-m[0, 2], m[1, 2], -m[2, 2]]
    length = math.sqrt(sum(v * v for v in f))
    return np.asarray(position, np.float32).tolist(), np.asarray([v / length for v in f] + [0.0], np.float32).tolist()


def cpd_vector(v, cpd_slots):
    values = {row["index"]: row["default"] for row in cpd_slots}
    return [float(np.float32(values.get(4 * v + i, 0.0))) for i in range(4)]
