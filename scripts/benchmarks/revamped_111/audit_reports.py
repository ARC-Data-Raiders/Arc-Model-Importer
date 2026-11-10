def _benchmark_root():
    import os
    from pathlib import Path
    value = os.environ.get("DATARAIDERS_BENCHMARK_ROOT", r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/benchmark-111-20261028")
    root = Path(value).resolve()
    boundary = Path(r"Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local").resolve()
    if not root.is_relative_to(boundary):
        raise ValueError("Benchmark output must remain under the designated Z: scratch root")
    return root

from pathlib import Path
import json,csv,collections,math
T=_benchmark_root()
A=json.loads((T/'report-A.json').read_text());B=json.loads((T/'report-B.json').read_text());b={g['id']:g for g in B['groups']}
rows=[];details=[]
for a in A['groups']:
 bb=b[a['id']];ma=[o for o in a['objects'] if o['type']=='MESH'];mb=[o for o in bb['objects'] if o['type']=='MESH']
 slotsA=[s for o in ma for s in o['slots']];slotsB=[s for o in mb for s in o['slots']]
 failA=[{'object':o['name'],'report':o['properties'].get('arc_surface_report',''),'status':o['properties'].get('arc_material_status','')} for o in ma if o['properties'].get('arc_surface_rebuild')=='FAIL']
 allB=bb['material_reports'];fallbackB=[{'object':r.get('object'),'slot':r.get('slot'),'material':r.get('material'),'family':r.get('family'),'warnings':r.get('warnings'),'default_color':r.get('default_color')} for r in allB if r.get('family')=='fallback' or r.get('default_color')]
 missingA=[{'object':o['name'],'slot':s['slot'],'image':im} for o in ma for s in o['slots'] for im in s.get('images',[]) if not im.get('exists',False)]
 missingB=[{'object':o['name'],'slot':s['slot'],'image':im} for o in mb for s in o['slots'] for im in s.get('images',[]) if not im.get('exists',False)]
 errorsB=[r for r in allB if r.get('error')]
 row={'id':a['id'],'label':a['label'],'A_meshes':len(ma),'B_meshes':len(mb),'A_triangles':sum(o['triangles'] for o in ma),'B_triangles':sum(o['triangles'] for o in mb),'A_slots':len(slotsA),'B_slots':len(slotsB),'A_missing_bound_images':len(missingA),'B_missing_bound_images':len(missingB),'A_surface_rebuild_FAIL_objects':len(failA),'B_material_errors':len(errorsB),'B_fallback_slots':len(fallbackB),'A_import_seconds':round(a['seconds'],3),'B_import_seconds':round(bb['seconds'],3)}
 rows.append(row);details.append(dict(row,A_partial_surface_reports=failA,B_fallbacks=fallbackB,A_missing_images=missingA,B_missing_images=missingB,B_errors=errorsB,A_nonconnected=[s for s in slotsA if not s.get('surface_connected')],B_nonconnected=[s for s in slotsB if not s.get('surface_connected')]))
with (T/'benchmark-cases.csv').open('w',newline='',encoding='utf-8') as f:
 writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
summary={'source_build':'26159202','mapping_sha256':'CA12D5239EB91C21983A0105BD6CF7052D0CCA83032B9EC3E1C903965A3299EC','groups':details,'geometry_import_errors':{'A':A['all_errors'],'B':B['all_errors']},'A_missing_bound_images':sum(r['A_missing_bound_images'] for r in rows),'B_missing_bound_images':sum(r['B_missing_bound_images'] for r in rows),'A_surface_rebuild_FAIL_objects':sum(r['A_surface_rebuild_FAIL_objects'] for r in rows),'B_material_errors':sum(r['B_material_errors'] for r in rows),'B_fallback_slots':sum(r['B_fallback_slots'] for r in rows),'triangle_differences':[r for r in rows if r['A_triangles']!=r['B_triangles']]}
(T/'benchmark-audit.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k not in ['groups','triangle_differences']},indent=2));print('triangle differences',[(r['id'],r['A_triangles'],r['B_triangles']) for r in summary['triangle_differences']])
