import argparse
import json
import os
import pathlib
import struct
import subprocess
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    run = pathlib.Path(args.run)
    os.environ.update(TEMP=str(run / 'tmp'), TMP=str(run / 'tmp'), DOTNET_PROCESSOR_COUNT='4', OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
    root = pathlib.Path(__file__).resolve().parents[1]
    fixtures = run / 'out/W6/fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    fxc = r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\fxc.exe'
    exe = run / 'build/dr_oracle/dr_oracle.exe'
    shaders = {}
    for path in sorted((root / 'tests/shaders').glob('*.hlsl')):
        stage = 'vs' if path.name.startswith('vs') else 'ps'
        target = fixtures / (path.stem + '.dxbc')
        with (run / 'logs' / ('W6-fxc-' + path.stem + '.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run([fxc, '/T', stage + '_5_0', '/E', 'main', '/O3', '/Fo', str(target), str(path)], stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        if result.returncode:
            raise RuntimeError('FXC failed: ' + path.name)
        shaders[path.stem] = target

    def binary(name, values):
        path = fixtures / name
        path.write_bytes(np.asarray(values, dtype='<f4').tobytes())
        return path

    def dds(name, width, height, fmt, mip_payloads, block_bytes=None):
        flags = 1 | 2 | 4 | 0x1000 | 0x20000 | (0x80000 if block_bytes else 8)
        bpp = {28: 4, 61: 1}.get(fmt)
        pitch = len(mip_payloads[0]) if block_bytes else width * bpp
        header = [124, flags, height, width, pitch, 0, len(mip_payloads)] + [0] * 11 + [32, 4, 0x30315844, 0, 0, 0, 0, 0] + [0x1000 | (0x400008 if len(mip_payloads) > 1 else 0), 0, 0, 0, 0] + [fmt, 3, 0, 1, 0]
        path = fixtures / name
        path.write_bytes(b'DDS ' + struct.pack('<36I', *header) + b''.join(mip_payloads))
        return path

    cb = binary('cb.bin', [.25, .5, .75, 1])
    address_cb = binary('address_cb.bin', [-.5625, .25, 0, 0])
    rgba = np.array([[[64*x, 64*y, 32*(x+y), 255] for x in range(4)] for y in range(4)], dtype=np.uint8)
    tex = dds('rgba.dds', 4, 4, 28, [rgba.tobytes()])
    ramp = dds('ramp.dds', 4, 1, 61, [bytes([0, 64, 128, 255])])
    sb_values = np.array([[.25, .5, .75, 1], [2, 3, 4, 5], [-1, -2, -3, -4]], dtype='<f4')
    sb = binary('sbuf.bin', sb_values)
    indices = sum((0 if y < 2 else 1) << (2*(4*y+x)) for y in range(4) for x in range(4))
    bc = dds('bc1.dds', 4, 4, 71, [struct.pack('<HHI', 0xffff, 0, indices)], 8)
    mip = dds('mips.dds', 2, 2, 28, [bytes([255, 0, 0, 255])*4, bytes([0, 255, 0, 255])])
    vertex = binary('vertices.bin', [[*p, .1, .2, .3, .4, .5, .6] for p in [(-1, -1, 0, 1), (-1, 3, 0, 1), (3, -1, 0, 1)]])
    reverse_source = fixtures / 'vs_frontface_w1.hlsl'
    reverse_source.write_bytes(b'float4 main(uint id:SV_VertexID):SV_Position { float4 p[3]={float4(-1,-1,0,1),float4(3,-1,0,1),float4(-1,3,0,1)};return p[id];}\r\n')
    reverse_binary = fixtures / 'vs_frontface_w1.dxbc'
    with (run / 'logs/W6-fxc-vs_frontface_w1.log').open('w', encoding='utf-8') as log:
        reverse_rc = subprocess.run([fxc, '/T', 'vs_5_0', '/E', 'main', '/O3', '/Fo', str(reverse_binary), str(reverse_source)], stdout=log, stderr=subprocess.STDOUT, timeout=1800).returncode
    if reverse_rc:
        raise RuntimeError('T11 reverse VS compilation failed')
    result = {}
    warp_address = {}
    for device in ['warp', 'hardware']:
        result[device] = {}

        def execute(test, ps, width, height, directives, expected, tolerance=0, vs='vs_main', hlsl=False):
            outputs = expected if test == "T2" else [expected]
            paths = [fixtures / (device + '_' + test + '_' + str(i) + '.f32') for i in range(len(outputs))]
            lines = [['DRJOB', 1], ['device', device]]
            for stage, shader_name in [('vs', vs), ('ps', ps)]:
                lines.append([stage, 'hlsl', root / 'tests/shaders' / (shader_name + '.hlsl'), 'main', stage + '_5_0'] if hlsl else [stage, 'dxbc', shaders[shader_name]])
            lines.extend(directives)
            lines.append(['draw', 3])
            lines.extend(['rt', i, width, height, path] for i, path in enumerate(paths))
            job = fixtures / (device + '_' + test + '.txt')
            job.write_text('\n'.join('\t'.join(map(str, line)) for line in lines) + '\n', encoding='utf-8', newline='\n')
            with (run / 'logs' / ('W6-' + device + '-' + test + '.log')).open('w', encoding='utf-8') as log:
                rc = subprocess.run([str(exe), str(job)], stdout=log, stderr=subprocess.STDOUT, timeout=1800).returncode
            if rc:
                result[device][test] = {'pass': False, 'max_abs_err': None, 'exit': rc}
                if device == 'warp':
                    (run / 'out/W6/selftest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
                    raise RuntimeError('WARP test ' + test + ' exited ' + str(rc))
                return None
            arrays = [np.fromfile(path, dtype='<f4').reshape(height, width, 4) for path in paths]
            error = max(float(np.max(np.abs(a.astype(np.float64) - np.broadcast_to(np.asarray(b, dtype=np.float64), a.shape)))) for a, b in zip(arrays, outputs))
            passed = error <= tolerance
            result[device][test] = {'pass': passed, 'max_abs_err': error}
            (run / 'out/W6/selftest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
            if not passed and device == 'warp':
                raise RuntimeError('WARP test ' + test + ' max_abs_err ' + str(error))
            return arrays[0]

        def sampler(mode=3, linear=False):
            return ['samp', 'ps', 0, 21 if linear else 0, mode, 3, 3, 0, 1, 0, 3.402823466e38]

        execute('T1', 'ps_cb', 2, 2, [['cb', 'ps', 0, cb]], [.25, .5, .75, 1])
        execute('T1_hlsl', 'ps_cb', 2, 2, [['cb', 'ps', 0, cb]], [.25, .5, .75, 1], hlsl=True)
        execute('T2', 'ps_mrt', 2, 2, [], [[1, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 1], [.5, .25, .125, 1]])
        expected = rgba.astype(np.float64) / 255
        execute('T3', 'ps_sample', 4, 4, [['tex', 'ps', 0, tex, 28], sampler()], expected, 1e-7)
        linear = expected.copy()
        c = linear[:, :, :3]
        linear[:, :, :3] = np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)
        execute('T4', 'ps_sample', 4, 4, [['tex', 'ps', 0, tex, 29], sampler()], linear, 5e-4)
        for mode, name in [(1, 'WRAP'), (2, 'MIRROR'), (3, 'CLAMP')]:
            u = -.5625 + .25*np.arange(9)
            x = 4*u - .5
            i0 = np.floor(x).astype(int)
            w = x - i0
            def address(i):
                if mode == 1:
                    return i % 4
                if mode == 3:
                    return np.clip(i, 0, 3)
                m = i % 8
                return np.where(m < 4, m, 7 - m)
            t = np.array([0, 64, 128, 255])
            values = ((1-w)*t[address(i0)] + w*t[address(i0+1)]) / 255
            expect = np.zeros((1, 9, 4))
            expect[:, :, :3] = values[None, :, None]
            expect[:, :, 3] = 1
            observed = execute('T5_' + name, 'ps_address', 9, 1, [['tex', 'ps', 0, ramp, 61], sampler(mode, True), ['cb', 'ps', 0, address_cb]], expect, 1e-6)
            if device == 'warp' and observed is not None:
                warp_address[name] = observed[0, :, 0].tolist()
                (run / 'out/W6/t5_address.json').write_text(json.dumps(warp_address, indent=2) + '\n', encoding='utf-8', newline='\n')
        execute('T6', 'ps_structured', 3, 1, [['sbuf', 'ps', 0, 16, sb]], sb_values[None, :, :])
        expected_bc = np.ones((4, 4, 4))
        expected_bc[2:, :, :3] = 0
        for view in [71, 72]:
            execute('T7_' + str(view), 'ps_sample', 4, 4, [['tex', 'ps', 0, bc, view], sampler()], expected_bc)
        execute('T8', 'ps_mip', 2, 1, [['tex', 'ps', 0, mip, 28], sampler()], np.array([[[0, 1, 0, 1], [1, 0, 0, 1]]]))
        execute('T9', 'ps_discard', 2, 1, [['clear', -1, -1, -1, -1]], np.array([[[-1]*4, [1]*4]]))
        execute('T10', 'ps_attributes', 2, 2, [['il', 'POSITION', 0, 2, 0, 0], ['il', 'ATTRIBUTE', 0, 2, 0, 16], ['il', 'ATTRIBUTE', 1, 16, 0, 32], ['vb', 0, 40, vertex]], [.1, .2, .5, .6], 1e-6, vs='vs_attributes')
        faces = {}
        for winding, vertex_shader in [('W0', shaders['vs_main']), ('W1', reverse_binary)]:
            output = fixtures / (device + '_T11_' + winding + '.f32')
            job = fixtures / (device + '_T11_' + winding + '.txt')
            lines = [['DRJOB', 1], ['device', device], ['vs', 'dxbc', vertex_shader], ['ps', 'dxbc', shaders['ps_frontface']], ['draw', 3], ['rt', 0, 2, 2, output]]
            job.write_text('\n'.join('\t'.join(map(str, line)) for line in lines) + '\n', encoding='utf-8', newline='\n')
            with (run / 'logs' / ('W6-' + device + '-T11-' + winding + '.log')).open('w', encoding='utf-8') as log:
                face_rc = subprocess.run([str(exe), str(job)], stdout=log, stderr=subprocess.STDOUT, timeout=1800).returncode
            if face_rc:
                raise RuntimeError('T11 oracle exit ' + str(face_rc))
            values = np.fromfile(output, dtype='<f4')
            if values.size != 16 or not np.all(values == values[0]) or float(values[0]) not in (0.0, 1.0):
                raise RuntimeError('T11 result is not a uniform 0 or 1')
            faces[winding] = int(values[0])
        face_pass = faces['W0'] != faces['W1']
        result[device]['T11'] = {'pass': face_pass, 'max_abs_err': 0.0}
        (run / 'out/W6/selftest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
        if device == 'warp':
            (run / 'out/X2b').mkdir(parents=True, exist_ok=True)
            (run / 'out/X2b/frontface.json').write_text(json.dumps(faces, indent=2) + '\n', encoding='utf-8', newline='\n')
        if not face_pass:
            raise RuntimeError('T11 windings have identical frontface values')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
