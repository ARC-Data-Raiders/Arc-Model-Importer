"""Render native character material response probes without any game data.

Run in an isolated Blender profile, supplying a scratch directory explicitly:
  blender --background --factory-startup -t 4 --python-exit-code 1 \
    --python tests/test_native_character_materials_blender.py -- --output /scratch/characters

Alternatively set DATARAIDERS_TEST_OUTPUT. Output contains numeric reports,
small EXR probes and synthetic MI/texture fixtures. Existing files with the same
names are overwritten. This does not touch an open user Blender scene.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys

def main():
    repo=Path(__file__).resolve().parents[1]
    args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=os.environ.get('DATARAIDERS_TEST_OUTPUT'))
    parser.add_argument('--case',choices=['all','body','face','eye','aux'],default='all')
    options=parser.parse_args(args)
    if not options.output:
        parser.error('--output or DATARAIDERS_TEST_OUTPUT is required; no implicit C-drive scratch')
    output=Path(options.output).expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True)
    os.environ['TEMP']=str(output);os.environ['TMP']=str(output)
    sys.pycache_prefix=str(output/'pycache')
    sys.path.insert(0,str(repo.parent))
    cases={'body':'body_response.py','face':'face_response.py','eye':'eye_reflection.py','aux':'eye_auxiliary.py'}
    completed=[]
    for case,filename in cases.items():
        if options.case not in {'all',case}:continue
        destination=output/("eye_aux" if case == "aux" else case);destination.mkdir(exist_ok=True)
        runpy.run_path(str(Path(__file__).parent/'native_characters'/filename),
                       init_globals={'OUT':destination,'repo':repo})
        completed.append(case)
    (output/'native-character-suite.json').write_text(json.dumps({'passed':True,'cases':completed},indent=2))
    print('NATIVE_CHARACTER_SUITE_OK',','.join(completed))


if __name__ == "__main__":
    main()
