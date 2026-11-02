"""Path-aware FModel export indexing. No arbitrary basename collision winner."""
from pathlib import Path
from collections import defaultdict
import re
import os
import json



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
        self.compatibility_mode=not any((self.root/rel).is_dir() for rel in ('PioneerGame/Content','RemappedPlugins/Content','Content/Pioneer'))
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
                previous = self.paths.get((key, ext))
                self.paths[(key, ext)] = _prefer_duplicate_export([previous, p]) if previous else p
                self.names[(stem.casefold(), ext)].append(p)
                if name.casefold().endswith('.arc-material.json'):
                    # Native named sidecars and converted material JSON share a
                    # logical package. Keep the native form discoverable by the
                    # original material stem as well as its explicit filename.
                    native_stem = name[:-len('.arc-material.json')]
                    self.names[(native_stem.casefold(), ext)].append(p)
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
            requested_mount=_logical_mount(key)
            matches = [p for p in self.content.get((suffix, ext), []) if _logical_mount(package(p.relative_to(self.root).as_posix()).casefold())==requested_mount]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return _prefer_duplicate_export(matches)
            if not self.compatibility_mode:return None
        stem = key.rsplit('/', 1)[-1]
        stem = re.sub(r'\.\d{3}$', '', stem)
        candidates = self.names.get((stem, ext), [])
        requested_mount=_logical_mount(key)
        if requested_mount is not None:candidates=[p for p in candidates if _logical_mount(package(p.relative_to(self.root).as_posix()).casefold()) in (requested_mount,None)]
        if len(candidates) == 1:
            return candidates[0]
        mounts={_logical_mount(package(p.relative_to(self.root).as_posix()).casefold()) for p in candidates}
        if len(candidates)>1 and len(mounts)>1:raise ResolutionError('Ambiguous basename crosses declared namespaces: '+str(reference))
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
                    # A converted JSON and its native .arc-material.json sidecar are one logical package.
                    def logical(path):
                        text=path.as_posix().casefold()
                        return text[:-len('.arc-material.json')]+'.json' if text.endswith('.arc-material.json') else text
                    if len({logical(v[3]) for v in tied})!=1:raise ResolutionError('Equally close material identities require an explicit package: '+str(reference))
                    chosen=_prefer_duplicate_export([v[3] for v in tied])
                self.resolution_notes[(str(reference),str(context))]='Explicit nearest-folder FModel compatibility binding; namespace association unverified'
                return chosen
        if candidates:
            from ..material_runtime.repository import digest
            if len({digest(path) for path in candidates})>1:raise ResolutionError('Ambiguous basename requires exact reference: '+str(reference))
            return _prefer_duplicate_export(candidates)
        return None

    def texture(self, reference, layer=None):
        if not reference:return None
        info=self.texture_info(reference)
        kind=info.get('NativeTextureKind') or info.get('Type')
        if info.get('NativeResourceKind') or kind in ('VolumeTexture','TextureCube','TextureCubeArray','RuntimeVirtualTexture','Font'):
            raise ResolutionError('Unsupported active native resource in FModel compatibility: '+str(reference)+' ('+str(kind)+')')
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
            return None
        return self.resolve(reference, '.png') or self.resolve(reference, '.hdr')


    def texture_info(self,reference):
        """Current local typed metadata, otherwise reviewed native facts with provenance."""
        from ..material_runtime.assets import native_texture_kind,native_resource_kind
        from ..material_runtime.repository import _exports,package_ref,object_name,digest
        leaf=object_name(reference);candidates=self.names.get((leaf.casefold()+'.arc-texture','.json'),[])
        requested=_logical_mount(package(reference).casefold())
        candidates=[p for p in candidates if _logical_mount(package(p.relative_to(self.root).as_posix()).casefold()) in (requested,None)]
        exact=[p for p in candidates if package(p.relative_to(self.root).as_posix()).casefold()==package(reference).casefold()]
        if len(candidates)>1 and len(exact)==1:candidates=exact   # same leaf in the gun folder and a skin folder
        if len(candidates)>1:raise ResolutionError('Ambiguous exact texture sidecars: '+str(reference))
        ordinary=self.resolve(reference,'.json')
        sources=candidates+([ordinary] if ordinary is not None and ordinary not in candidates else [])
        for path in sources:
            raw=json.loads(path.read_text(encoding='utf-8-sig'))
            for entry in _exports(raw):
                if not isinstance(entry,dict) or (native_texture_kind(entry) is None and native_resource_kind(entry) is None):continue
                if str(entry.get('Name','')).casefold()!=leaf.casefold():raise ResolutionError('Texture metadata object mismatch: '+str(reference))
                if entry.get('Package') and '/' in str(reference) and package_ref(entry['Package']).casefold()!=package_ref(reference).casefold():raise ResolutionError('Texture metadata namespace mismatch: '+str(reference))
                return {**entry.get('Properties',{}),'Type':entry['Type'],'NativeTextureKind':native_texture_kind(entry),'NativeResourceKind':native_resource_kind(entry),'Mips':entry.get('Mips',[]),'source':{'path':str(path),'sha256':digest(path)},'association':'local FModel metadata observation; build association uncertain'}
        from ..material_runtime.native_data import current_metadata
        source=getattr(self,'_native_catalog_compat_source',None)
        return dict(current_metadata(source).get('textures',{}).get(package(reference).casefold(),{}))


def _logical_mount(key):
    key=str(key).replace('\\','/').casefold().strip('/')
    if key.startswith(('pioneergame/content/','remappedplugins/content/','content/')):return 'game'
    if key.startswith('engine/content/'):return 'engine'
    match=re.match(r'remappedplugins/([^/]+)/content/',key)
    return match[1] if match else None


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
        # ArcPak's raw sidecar retains Parent, named defaults and scopes that
        # conversion JSON drops. Prefer it independently of os.walk file order.
        converted = not text.endswith('.arc-material.json')
        return (penalty, converted, len(text), text)
    return sorted(candidates, key=rank)[0]


def get_index(root, refresh=False):
    key = str(Path(root).resolve()).casefold()
    if refresh or key not in _INDEX:
        _INDEX[key] = AssetIndex(root)
    return _INDEX[key]


def invalidate_index(root=None):
    """Drop stale file inventories after extraction adds or replaces assets."""
    if root is None:
        _INDEX.clear()
        return
    target = Path(root).expanduser().resolve()
    parts = list(target.parts)
    lower = [part.casefold() for part in parts]
    if 'pioneergame' in lower:
        target = Path(*parts[:lower.index('pioneergame')])
    elif 'content' in lower:
        i = lower.index('content')
        if i == len(parts)-1 or lower[i+1] == 'pioneer':
            target = Path(*parts[:i])
    identity = str(target).casefold()
    for key, index in list(_INDEX.items()):
        if key == identity or str(index.root).casefold() == identity:
            del _INDEX[key]
