import os,json
from pathlib import Path
from dotenv import load_dotenv
import psycopg
load_dotenv('.env',override=True)
with psycopg.connect(os.environ['DATABASE_URI']) as c:
 row=c.execute("SELECT r.id::text,r.thread_id,r.tenant_id::text FROM agent_requests r "
    "JOIN agent_content_refs f ON f.request_id=r.id AND f.tenant_id=r.tenant_id "
    "JOIN users u ON u.id=r.user_id WHERE u.account=%s AND f.content_snapshot=%s "
    "ORDER BY r.created_at DESC LIMIT 1",('acc_alice','接管验收：只回复数字2。')).fetchone()
 assert row
 rid,tid,tenant=row
 out={'request_id':rid,'thread_id':tid,'tenant_id':tenant,'browser':'CUA IAB localhost:3000',
      'ui_checks':['normal submission answered 2','regenerate answered 2','reload and reopen both answers visible']}
 for label,table in [('requests','agent_requests'),('attempts','agent_run_attempts'),('answers','agent_answers'),('content_refs','agent_content_refs')]:
  key='id' if table=='agent_requests' else 'request_id'
  if table=='agent_answers':
   n=c.execute('SELECT count(*) FROM agent_answers WHERE attempt_id IN '
       '(SELECT id FROM agent_run_attempts WHERE request_id=%s)',(rid,)).fetchone()[0]
  else:n=c.execute('SELECT count(*) FROM '+table+' WHERE '+key+'=%s',(rid,)).fetchone()[0]
  out[label]=n
 assert (out['requests'],out['attempts'],out['answers'],out['content_refs'])==(1,2,2,1),out
Path('docs/reviews/evidence/flash-02-repair-02-independent/browser-counts.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False,indent=2))
