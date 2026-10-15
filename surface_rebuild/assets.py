"""Path-aware FModel export indexing. No arbitrary basename collision winner."""
from pathlib import Path
from collections import defaultdict
import re
import os
import json

_BUNDLED_TEXTURES = None


class ResolutionError(ValueError):
    pass


def infer_export_root(source):
    """Find an export root from package-directory boundaries, on any computer."""
    path=Path(source).expanduser().resolve()
    parts=list(path.parts);lower=[p.casefold() for p in parts]
    if 'pioneergame' in lower:
        return Path(*parts[:lower.index('pioneergame')])
    for i,part in enumerate(lower[:-1]):
        if part=='content' and lower[i+1]=='pioneer':
            return Path(*parts[:i])
    directory=path if path.is_dir() else path.parent
    for candidate in [directory,*directory.parents]:
        if (candidate/'Content/Pioneer').is_dir() or (candidate/'PioneerGame/Content/Pioneer').is_dir():
            return candidate
    raise ResolutionError('Cannot locate the FModel export root; choose it in the ARC Texturer panel')


def package(value):
    if isinstance(value, dict):
        value = value.get('ObjectPath') or value.get('AssetPathName') or value.get('ObjectName')
    if not value or value == 'None':
        return ''
    value = str(value).replace('\\', '/')
    if "'" in value:
        value = value.split("'", 1)[1].rstrip("'")
    value = re.sub(r'\.(?:uasset|json|png|pskx?|\d+)$', '', value, flags=re.I)
    value = value.split('.', 1)[0]
    if value.startswith('/Game/'):
        value = 'PioneerGame/Content/' + value[6:]
    elif value.startswith('/Engine/'):
        value = 'Engine/Content/' + value[8:]
    elif value.startswith('/'):
        mount, _, rest = value[1:].partition('/')
        value = f'RemappedPlugins/{mount}/Content/{rest}'
    elif value.casefold().startswith('content/'):
        # FModel's newer game-only exports omit the PioneerGame directory.
        # Keep the same logical package identity as /Game and older exports.
        value = 'PioneerGame/Content/' + value[8:]
    return value.strip('/')


def relative_package(path, root):
    path = Path(path).resolve()
    try:
        return package(path.relative_to(Path(root).resolve()).as_posix())
    except ValueError:
        # Meshes can come from a different dump while textures come from the
        # selected export root. Use the package boundary, not a drive prefix.
        parts = path.parts
        for i, part in enumerate(parts[:-1]):
            if part.casefold() != 'content':
                continue
            if i and parts[i-1].casefold() in {'pioneergame', 'engine'}:
                return package('/'.join(parts[i-1:]))
            if parts[i+1].casefold() == 'pioneer':
                return package('Content/' + '/'.join(parts[i+1:]))
            if i >= 2 and parts[i-2].casefold() == 'remappedplugins':
                return package('/'.join(parts[i-2:]))
        raise


class AssetIndex:
    def __init__(self, root):
        root = Path(root).expanduser().resolve()
        # Accept the export root, PioneerGame/Content, or Pioneer subtree.
        parts = list(root.parts)
        lower = [p.casefold() for p in parts]
        if 'pioneergame' in lower:
            root = Path(*parts[:lower.index('pioneergame')])
        elif 'content' in lower:
            i = lower.index('content')
            if i == len(parts)-1 or lower[i+1] == 'pioneer':
                root = Path(*parts[:i])
        self.root = root
        self.paths = {}
        self.names = defaultdict(list)
        self.arrays = defaultdict(dict)
        self.content = defaultdict(list)
        self.resolution_notes = {}
        self._build()

    def _build(self):
        if not self.root.is_dir():
            raise ResolutionError(f'Export directory does not exist: {self.root}')
        # os.walk already distinguishes files. Resolving every file and the root
        # again performed >120,000 filesystem lookups on the 1.42 export.
        for directory, _, files in os.walk(self.root):
            relative = Path(directory).relative_to(self.root)
            for name in files:
                stem, ext = os.path.splitext(name); ext = ext.lower()
                if ext not in {'.json', '.png', '.psk', '.pskx', '.hdr'}: continue
                p = Path(directory) / name
                key = package((relative / name).as_posix()).casefold()
                self.paths[(key, ext)] = p
                self.names[(stem.casefold(), ext)].append(p)
                if '/content/' in key:
                    self.content[(key.split('/content/', 1)[1], ext)].append(p)
                if ext == '.png':
                    # Texture2DArray slices: *_LAYER0 (tooling) or FModel TA_*_0.
                    match = re.match(r'(.+)_LAYER(\d+)$', stem, re.I)
                    from_layer = bool(match)
                    if not match and stem.upper().startswith('TA_'):
                        match = re.match(r'(.+)_(\d+)$', stem, re.I)
                    if match:
                        base = package((relative / match[1]).as_posix()).casefold()
                        idx = int(match[2])
                        # LAYER form wins when both naming styles are present.
                        if from_layer or idx not in self.arrays[base]:
                            self.arrays[base][idx] = p

    def resolve(self, reference, ext='.json', context=None):
        key = package(reference).casefold()
        exact = self.paths.get((key, ext))
        if exact:
            return exact
        # Mount layout differs in some FModel exports; match the full Content suffix.
        if '/content/' in key:
            suffix = key.split('/content/', 1)[1]
            matches = self.content.get((suffix, ext), [])
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return _prefer_duplicate_export(matches)
            return None
        stem = key.rsplit('/', 1)[-1]
        stem = re.sub(r'\.\d{3}$', '', stem)
        candidates = self.names.get((stem, ext), [])
        if len(candidates) == 1:
            return candidates[0]
        if context and candidates:
            try:
                context_parts = relative_package(context, self.root).rsplit('/', 1)[0].casefold().split('/')
            except ValueError:
                context_parts = []
            ranked = []
            for p in candidates:
                common = 0
                candidate_parts = relative_package(p, self.root).rsplit('/', 1)[0].casefold().split('/')
                for a, b in zip(context_parts, candidate_parts):
                    if a != b:
                        break
                    common += 1
                distance=len(context_parts)+len(candidate_parts)-2*common
                ranked.append((-common, distance, p.as_posix().casefold(), p))
            ranked.sort()
            if ranked and -ranked[0][0] >= 3:
                tied=[v for v in ranked if v[:2]==ranked[0][:2]]
                chosen=ranked[0][3]
                if len(tied)>1:
                    self.resolution_notes[(str(reference),str(context))]=('Equally close material paths; selected '+str(chosen))
                return chosen
        if candidates:
            return _prefer_duplicate_export(candidates)
        return None

    def texture(self, reference, layer=None):
        if layer is not None:
            key = package(reference).casefold()
            idx = int(layer)
            found = self.arrays.get(key, {}).get(idx)
            if found:
                return found
            # Same folder, alternate slice stems (LAYER vs bare index).
            for suffix in (f'_LAYER{idx}', f'_{idx}'):
                alt = self.paths.get((key + suffix.casefold(), '.png'))
                if alt:
                    self.arrays[key][idx] = alt
                    return alt
                # Content-suffix / stem fallbacks via resolve.
                alt = self.resolve(package(reference) + suffix, '.png')
                if alt:
                    self.arrays[key][idx] = alt
                    return alt
            global _BUNDLED_TEXTURES
            if _BUNDLED_TEXTURES is None:
                manifest=Path(__file__).parent/'data/bundled_textures_142.json'
                _BUNDLED_TEXTURES=json.loads(manifest.read_text()) if manifest.is_file() else {}
            relative=_BUNDLED_TEXTURES.get(key, {}).get(str(idx))
            if relative:
                bundled=Path(__file__).parent/'data'/relative
                if bundled.is_file():return bundled
            return None
        return self.resolve(reference, '.png') or self.resolve(reference, '.hdr')


_INDEX = {}


def _prefer_duplicate_export(candidates):
    """Same texture exported under MapPlacements or an old dump. Keep the game content copy."""
    def rank(path):
        text = path.as_posix().casefold()
        penalty = 0
        if "mapplacements" in text:
            penalty += 2
        if "blenderumap" in text:
            penalty += 4
        return (penalty, len(text), text)
    return sorted(candidates, key=rank)[0]


def get_index(root, refresh=False):
    key = str(Path(root).resolve()).casefold()
    if refresh or key not in _INDEX:
        _INDEX[key] = AssetIndex(root)
    return _INDEX[key]
