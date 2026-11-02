"""The shared, unchanged crop used by packaged and runtime outfit previews."""

_OUTFIT_CROP_SRC = 512
_OUTFIT_CROP_SIDE = 326
_OUTFIT_CROP_OUT = 256
_OUTFIT_CROP_ALPHA = 8.0 / 255.0


def selected_game_icon(stems, identity, roots, cache, command=()):
    """Resolve only the selected authored metadata and icon, off Blender's thread."""
    import json
    from pathlib import Path
    import subprocess

    def extract(relative):
        if not command:
            return
        arguments = list(command)
        game_index = arguments.index("--game-dir") + 1
        game = Path(arguments[game_index])
        if game.name.casefold() != "paks":
            candidates = [game / "PioneerGame/Content/Paks", game / "Content/Paks", game / "Paks"]
            game = next((candidate for candidate in candidates if candidate.is_dir()), game)
        arguments[game_index] = str(game)
        result = subprocess.run(arguments + ["--path", "PioneerGame/Content/Pioneer/" + relative],
                                capture_output=True, text=True, timeout=120,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("Selected outfit preview extraction failed: " + (result.stderr or result.stdout)[-1000:])

    for stem in stems:
        relative = "Items/Characters/Skins/Outfit/" + stem + "/DA_UIMetaData_Outfit_" + stem
        metadata = next((Path(root) / (relative + ".json") for root in roots if (Path(root) / (relative + ".json")).is_file()), None)
        if metadata is None:
            extract(relative)
            candidate = Path(cache) / (relative + ".json")
            metadata = candidate if candidate.is_file() else None
        if metadata is None:
            continue
        records = json.loads(metadata.read_text(encoding="utf-8-sig"))
        if isinstance(records, dict):
            records = records.get("Exports") or [records]
        for record in records:
            properties = record.get("Properties") or {}
            if record.get("Type") != "UICharacterVisualSkinMetaDataItem" or properties.get("DisplayName", {}).get("Key") != identity:
                continue
            reference = properties.get("Image", {}).get("AssetPathName", "")
            package, separator, name = reference.rpartition(".")
            prefix = "/Game/Pioneer/Items/Characters/Skins/Outfit/"
            if not separator or not package.startswith(prefix) or package.rsplit("/", 1)[-1] != name or ".." in package.split("/"):
                continue
            icon_relative = package[len("/Game/Pioneer/"):]
            for root in roots:
                candidate = Path(root) / (icon_relative + ".png")
                if candidate.is_file():
                    return str(candidate)
            extract(icon_relative)
            candidate = Path(cache) / (icon_relative + ".png")
            if candidate.is_file():
                return str(candidate)
    return ""


def crop_outfit_preview_rgba(arr):
    """Top-center square of a top-down float RGBA icon, resized to 256.

    Matches the shipped outfit portraits. ``arr`` is height, width, 4.
    """
    import numpy as np

    src = np.asarray(arr, dtype=np.float32)
    if src.ndim != 3 or src.shape[2] < 4:
        return src
    height, width = src.shape[:2]
    side = int(round(min(height, width) * _OUTFIT_CROP_SIDE / _OUTFIT_CROP_SRC))
    side = max(1, min(side, height, width))
    alpha = src[:, :, 3]
    _ys, xs = np.nonzero(alpha > _OUTFIT_CROP_ALPHA)
    if len(xs) == 0:
        left = (width - side) // 2
    else:
        center = (float(xs.min()) + float(xs.max()) + 1.0) / 2.0
        left = int(round(center - side / 2.0))
        left = max(0, min(width - side, left))
    top = 0
    if top + side > height:
        top = height - side
    cropped = src[top : top + side, left : left + side]
    if cropped.shape[0] == _OUTFIT_CROP_OUT and cropped.shape[1] == _OUTFIT_CROP_OUT:
        return np.clip(cropped, 0.0, 1.0)
    return _resize_rgba(cropped, _OUTFIT_CROP_OUT, _OUTFIT_CROP_OUT)


def _resize_rgba(arr, out_w: int, out_h: int):
    """Bilinear resize. ``arr`` is top-down float RGBA."""
    import numpy as np

    src = np.asarray(arr, dtype=np.float32)
    in_h, in_w = src.shape[:2]
    if in_w == out_w and in_h == out_h:
        return np.clip(src, 0.0, 1.0)
    ys = (np.arange(out_h, dtype=np.float32) + 0.5) * (in_h / out_h) - 0.5
    xs = (np.arange(out_w, dtype=np.float32) + 0.5) * (in_w / out_w) - 0.5
    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    y1 = np.clip(y0 + 1, 0, in_h - 1)
    x1 = np.clip(x0 + 1, 0, in_w - 1)
    y0 = np.clip(y0, 0, in_h - 1)
    x0 = np.clip(x0, 0, in_w - 1)
    wy = (ys - y0.astype(np.float32)).reshape(out_h, 1, 1)
    wx = (xs - x0.astype(np.float32)).reshape(1, out_w, 1)
    top = src[y0][:, x0] * (1.0 - wx) + src[y0][:, x1] * wx
    bot = src[y1][:, x0] * (1.0 - wx) + src[y1][:, x1] * wx
    return np.clip(top * (1.0 - wy) + bot * wy, 0.0, 1.0)
