"""Every cached texture PNG must be as large as its .arc-texture.json SizeX/SizeY.

A PNG smaller than its sidecar means ArcPak decoded a lower mip because the top mips'
bulk data did not load (see ArcPak/TextureMipGuard.cs). Point DR_ARCPAK_CACHE at another
cache root to check it instead of the installed addon's cache.
"""
import json
import os
import struct
import unittest

DEFAULT_CACHE = os.path.join(
    os.environ.get('APPDATA', ''), 'Blender Foundation', 'Blender', '5.1', 'scripts',
    'addons', 'DataRaiders-Outfits', 'ArcPak', 'cache')


def png_size(path):
    with open(path, 'rb') as fh:
        head = fh.read(24)
    if head[:8] != b'\x89PNG\r\n\x1a\n' or head[12:16] != b'IHDR':
        raise ValueError('not a PNG: ' + path)
    return struct.unpack('>II', head[16:24])


def undersized_textures(root):
    bad = []
    for folder, _, files in os.walk(root):
        for name in files:
            if not name.endswith('.arc-texture.json'):
                continue
            png = os.path.join(folder, name[:-len('.arc-texture.json')] + '.png')
            if not os.path.isfile(png):
                continue
            with open(os.path.join(folder, name), encoding='utf8') as fh:
                meta = json.load(fh)
            want_x, want_y = meta.get('SizeX'), meta.get('SizeY')
            if not isinstance(want_x, int) or not isinstance(want_y, int):
                continue
            got_x, got_y = png_size(png)
            if got_x < want_x or got_y < want_y:
                bad.append(f'{os.path.relpath(png, root)}: png {got_x}x{got_y}, json {want_x}x{want_y}')
    return sorted(bad)


class TextureCacheMipSize(unittest.TestCase):
    def test_cached_png_matches_sidecar_size(self):
        root = os.environ.get('DR_ARCPAK_CACHE', DEFAULT_CACHE)
        if not os.path.isdir(root):
            self.skipTest('no ArcPak cache at ' + root)
        bad = undersized_textures(root)
        self.assertEqual(bad, [], f'{len(bad)} cached PNG(s) smaller than their sidecar:\n' + '\n'.join(bad))


if __name__ == '__main__':
    unittest.main()
