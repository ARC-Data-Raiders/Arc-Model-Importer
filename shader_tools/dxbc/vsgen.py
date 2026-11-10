import json
import pathlib
import re
import subprocess
from .disasm import disassemble
from .parse import parse


def generate(shader, sha, directory, rng, fxc, winding='W0'):
    rows = sorted(shader['signatures']['input'], key=lambda r: (int(r['Register']), tuple('xyzw'.index(c) for c in r['Mask'])))
    rasterizer = ('FFACE', 'SAMPLE', 'COVERAGE', 'PRIMID', 'RTINDEX', 'VPINDEX')
    for row in rows:
        if row['SysValue'] in ('RTINDEX', 'SAMPLE'):
            return {'status': 'fixture_unsupported:input_sysvalue:'+row['SysValue']}
    produced = [row for row in rows if row['SysValue'] not in rasterizer]
    fields = []
    assignments = []
    coefficients = []
    for index, row in enumerate(produced):
        register = int(row['Register'])
        if row['SysValue'] == 'POS' or row['Name'].lower() == 'sv_position':
            fields.append('float4 p : SV_Position;')
            assignments.append('o.p=float4(ndc,0.5,1);')
            continue
        mask = row['Mask']
        width = len(mask)
        declaration = ' '.join(d['text'] for d in shader['declarations'] if d['op'].startswith('dcl_input_ps') and re.search(r'\bv'+str(register)+r'\b', d['text']))
        constant = 'constant' in declaration or row['Format'] in ('uint', 'int')
        interpolation = 'nointerpolation' if constant else 'centroid' if 'centroid' in declaration else 'noperspective' if 'noperspective' in declaration else 'linear'
        if row['Format'] not in ('float', 'uint', 'int'):
            return {'status':'fixture_unsupported:input_format:'+row['Format']}
        dtype = row['Format']+str(width)
        name = 'a'+str(index)
        fields.append(interpolation+' '+dtype+' '+name+' : '+row['Name']+row['Index']+';')
        expressions = []
        for component in mask:
            if row['Format'] == 'float':
                a0 = rng.uniform(-2,2)
                a1 = None if constant else rng.uniform(-1,1)
                a2 = None if constant else rng.uniform(-1,1)
                expression = format(a0,'.9g')
                if not constant:
                    expression += '+('+format(a1,'.9g')+')*ndc.x+('+format(a2,'.9g')+')*ndc.y'
            else:
                a0 = rng.randrange(16)
                a1 = a2 = None
                expression = str(a0)
            coefficients.append({'register':register,'component':component,'format':row['Format'],'interpolation':interpolation,'a0':a0,'a1':a1,'a2':a2})
            expressions.append(expression)
        assignments.append('o.'+name+'='+dtype+'('+','.join(expressions)+');')
    has_position = any(row['SysValue']=='POS' or row['Name'].lower()=='sv_position' for row in produced)
    if not has_position:
        fields.append('float4 p : SV_Position;')
        assignments.append('o.p=float4(ndc,0.5,1);')
    positions = 'float2(-1,-1),float2(-1,3),float2(3,-1)' if winding=='W0' else 'float2(-1,-1),float2(3,-1),float2(-1,3)'
    source='struct Output {'+' '.join(fields)+'};\nOutput main(uint vid : SV_VertexID) { float2 positions[3]={'+positions+'}; float2 ndc=positions[vid]; Output o; '+' '.join(assignments)+' return o; }\n'
    path=directory/'vs.hlsl';path.write_bytes(source.replace('\n','\r\n').encode('utf-8'));binary=directory/'vs.dxbc'
    with (directory/'fxc.log').open('w',encoding='utf-8') as log:
        result=subprocess.run([str(fxc),'/T','vs_5_0','/E','main','/O3','/Fo',str(binary),str(path)],stdout=log,stderr=subprocess.STDOUT,timeout=1800)
    if result.returncode:
        raise RuntimeError('FXC failed for '+sha+' exit '+str(result.returncode))
    assembly=disassemble(binary.read_bytes());(directory/'vs.asm').write_text(assembly,encoding='utf-8',newline='\n');actual=parse(assembly)['signatures']['output'];keys=['Name','Index','Mask','Register']
    expected=[{k:r[k] for k in keys} for r in produced];observed=[{k:r[k] for k in keys} for r in actual]
    extra=[row for row in observed if row not in expected]
    maximum=max((int(row['Register']) for row in observed),default=-1)
    extra_valid=not extra or (not has_position and len(extra)==1 and extra[0]['Name'].lower()=='sv_position' and int(extra[0]['Register'])==maximum)
    valid=all(row in observed for row in expected) and extra_valid and len(observed)==len(expected)+len(extra)
    result={'status':'supported' if valid else 'fixture_unsupported:signature_mismatch','expected_signature':expected,'actual_signature':observed,'coefficients':coefficients,'hlsl':str(path),'rasterizer_inputs':[row for row in rows if row['SysValue'] in rasterizer],'winding':winding}
    return result
