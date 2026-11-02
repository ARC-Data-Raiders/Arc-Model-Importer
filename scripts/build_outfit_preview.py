"""Package one exact game-authored outfit icon using the runtime crop."""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_preview(metadata, icon, output, build):
    import numpy as np
    from PIL import Image

    records = json.loads(metadata.read_text(encoding="utf-8-sig"))
    if isinstance(records, dict):
        records = records.get("Exports") or [records]
    entries = [entry["Properties"] for entry in records if entry.get("Type") == "UICharacterVisualSkinMetaDataItem"]
    if len(entries) != 1:
        raise ValueError("Expected one authored outfit metadata item")
    properties = entries[0]
    reference = properties["Image"]["AssetPathName"]
    package, object_name = reference.rsplit(".", 1)
    if not package.startswith("/Game/Pioneer/Items/Characters/Skins/Outfit/") or Path(package).name != object_name:
        raise ValueError("Not an exact outfit icon reference")
    relative = package[len("/Game/Pioneer/"):] + ".png"
    if not icon.as_posix().endswith("/" + relative):
        raise ValueError("Icon path does not match the authored metadata reference")
    key = properties["DisplayName"]["Key"]
    with (ROOT / "outfit_reference.csv").open(encoding="utf-8-sig") as stream:
        row = next(row for row in csv.DictReader(stream) if row["ST"] == key)
    stem = Path(package).parent.name
    if stem not in [part.strip() for part in row["Model Folder Name"].split(";")]:
        raise ValueError("Outfit mapping does not match the authored icon folder")
    spec = importlib.util.spec_from_file_location("outfit_preview_crop", ROOT / "outfit_preview.py")
    crop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crop)
    with Image.open(icon) as image:
        pixels = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    pixels = crop.crop_outfit_preview_rgba(pixels)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (stem + ".png")
    Image.fromarray(np.rint(pixels * 255.0).astype(np.uint8)).save(destination, optimize=True)
    receipt = {"build": build, "outfit": key, "image_reference": reference,
               "metadata": str(metadata), "metadata_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
               "icon": str(icon), "icon_sha256": hashlib.sha256(icon.read_bytes()).hexdigest(),
               "output": destination.name, "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
    (output / (stem + ".source.json")).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--icon", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "assets/outfit_previews")
    parser.add_argument("--build", required=True)
    args = parser.parse_args()
    print(json.dumps(build_preview(args.metadata, args.icon, args.output, args.build), indent=2))


if __name__ == "__main__":
    main()
