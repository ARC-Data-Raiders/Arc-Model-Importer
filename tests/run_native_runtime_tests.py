"""Run portable regressions; staging verification can supply --repository."""
import argparse,os,sys,unittest
from pathlib import Path
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--repository',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--temp-root',type=Path);args=parser.parse_args()
os.environ['DATARAIDERS_TEST_REPOSITORY']=str(args.repository.resolve())
if args.temp_root:
    args.temp_root.mkdir(parents=True,exist_ok=True);os.environ['DATARAIDERS_TEST_TEMP']=str(args.temp_root.resolve());os.environ['TEMP']=str(args.temp_root.resolve());os.environ['TMP']=str(args.temp_root.resolve())
suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent/'native_runtime'),pattern='test_native*.py',top_level_dir=str(Path(__file__).parent))
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
