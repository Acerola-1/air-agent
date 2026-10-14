import json,os,sys,subprocess
from pathlib import Path
wt=Path('/Users/acerola/.codex/worktrees/feedback-03/air_agent');cfg=json.loads(Path('/tmp/aa-review03-private.json').read_text())
env=os.environ.copy();env.update({k:v for k,v in cfg['env'].items() if v is not None});env['PYTHONPATH']=str(wt/'admin/api')+':'+str(wt/'admin/api/tests');env['PYTHONDONTWRITEBYTECODE']='1'
log=Path('/Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-03-independent/admin-and-probes.txt')
with log.open('w') as f:
 r=subprocess.run([str(wt/'admin/api/.venv/bin/python'),'-m','pytest','tests/test_feedback_api.py','/tmp/test_feedback_review03.py','-q','-p','no:cacheprovider','--tb=short'],cwd=wt/'admin/api',env=env,stdout=f,stderr=subprocess.STDOUT)
print('exit',r.returncode)
print('\n'.join(log.read_text().splitlines()[-22:]))
