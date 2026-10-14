import os,subprocess,tarfile,io,shutil
from pathlib import Path
wt=Path('/Users/acerola/.codex/worktrees/feedback-03/air_agent');dst=Path('/tmp/aa-review03-clean-build');dst.mkdir(exist_ok=True)
base=dst/'base';candidate=dst/'candidate';base.mkdir(exist_ok=True)
raw=subprocess.check_output(['git','-C',str(wt),'archive','0f2cd02','frontend'])
with tarfile.open(fileobj=io.BytesIO(raw)) as archive:archive.extractall(base,filter='data')
if not candidate.exists():shutil.copytree(wt/'frontend',candidate/'frontend',ignore=shutil.ignore_patterns('node_modules','.next','.env','.env.local','.env.*.local','*.tsbuildinfo'))
env=os.environ.copy();env.pop('NODE_ENV',None);env['NEXT_TELEMETRY_DISABLED']='1'
for label,d in [('base',base/'frontend'),('candidate',candidate/'frontend')]:
 p=d/'node_modules'
 if not p.exists():p.symlink_to(wt/'frontend/node_modules',target_is_directory=True)
 log=Path('/Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-03-independent')/(label+'-clean-build.txt')
 with log.open('w') as f:r=subprocess.run(['npm','run','build'],cwd=d,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=60)
 print(label,'exit',r.returncode,flush=True)
 print('\n'.join(log.read_text().splitlines()[-9:]),flush=True)
