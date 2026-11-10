"""Install verified Wave 4A plans into the O5.5 runtime cache."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
from Outfit_Accessories.pak_catalog import canonical_path


class Unsupported(Exception):
    pass


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
    raise Unsupported("binding:" + name)


def cpd_slots(shadermaps):
    data = json.loads(gzip.decompress(Path(shadermaps).read_bytes()))
    base = data["chain"][-1]["json"]
    ced = base.get("CachedExpressionData")
    if ced is None:
        ced = base["Properties"]["CachedExpressionData"]
    if any(k >= 0 for k in ced["VectorPrimitiveDataIndexValues"]):
        raise Unsupported("vector_cpd")
    return sorted(
        [{"index": k, "name": ced["RuntimeEntries"]["ParameterInfoSet"][i]["Name"],
          "default": ced["ScalarValues"][i]}
         for i, k in enumerate(ced["ScalarPrimitiveDataIndexValues"]) if k >= 0],
        key=lambda row: row["index"])


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=1) + "\n",
                    encoding="utf-8", newline="\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def install(wave4a, w1, sup, out):
    wave4a, w1, sup, out = map(Path, (wave4a, w1, sup, out))
    if out.exists():
        raise FileExistsError(out)
    statuses = json.loads((wave4a / "out/mi_status.json").read_text())
    expected = json.loads((sup / "w4/expected_material_ub.json").read_text())
    by_short = {k.rsplit("/", 1)[-1]: v for k, v in expected.items()}
    out.mkdir(parents=True)
    index = {"build_id": "26159202", "plans": {}, "runtime": "parity-1",
             "schema": "dr.parity-index/1"}
    textures = set()
    for short in sorted(k for k, v in statuses.items() if v == "pass"):
        source = wave4a / "out/D6/plans" / short
        plan = json.loads((source / "plan.json").read_text())
        plan["schema"] = "dr.material-plan/2"
        for row in plan["params"]:
            typ, attribute, component, negate = binding(row["name"])
            row.update(comp="x", attribute_type=typ, attribute=attribute,
                       component=component, negate=negate)
        for tex in plan["textures"]:
            original = source / tex["image"]
            relative = "textures/" + tex["object_path"].lstrip("/") + ".exr"
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != original.read_bytes():
                    raise Unsupported("texture_collision:" + relative)
            else:
                shutil.copyfile(original, target)
            tex["image"] = "../../" + relative
            textures.add(relative)
        directory = "plans/" + short
        target = out / directory
        write_json(target / "plan.json", plan)
        shutil.copyfile(source / "material.dag.json", target / "material.dag.json")
        canonical = canonical_path(plan["material_instance"])
        index["plans"][canonical] = {
            "base_material": plan["base_material"],
            "cpd_slots": cpd_slots(w1 / "out/W2/full" / by_short[short]["shadermaps"]),
            "dag_sha256": sha(target / "material.dag.json"), "dir": directory,
            "plan_sha256": sha(target / "plan.json")}
    mpc_path = sup / "o3/mpc_export/PioneerGame/Content/Pioneer/MaterialLibrary/MaterialCollections/MC_WeatherStates.json"
    parameters = json.loads(mpc_path.read_text())["Exports"][0]["Properties"]["ScalarParameters"]
    mpc = {"labels": {}, "params": {}, "schema": "dr.parity-mpc/1"}
    for p in parameters:
        name = "dr_mpc_MC_WeatherStates_" + re.sub(r"[^A-Za-z0-9_]", "_", p["ParameterName"])
        mpc["labels"][name] = p["ParameterName"]
        mpc["params"][name] = p["DefaultValue"]
    write_json(out / "mpc_defaults.json", mpc)
    write_json(out / "index.json", index)
    return {"plan_count": len(index["plans"]), "texture_count": len(textures),
            "plans": {k: v["dir"] for k, v in index["plans"].items()}}


def main():
    parser = argparse.ArgumentParser()
    for name in ("wave4a", "w1", "sup", "out"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.wave4a, args.w1, args.sup, args.out), sort_keys=True))


if __name__ == "__main__":
    main()
