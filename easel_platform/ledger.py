"""Transactional admission and measured usage. Never stores request or response text."""
import json
import secrets
from .auth import PlatformError

class Ledger:
    def __init__(self,store):
        self.store=store
        with store.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,owner TEXT NOT NULL,grant_id TEXT NOT NULL,model TEXT NOT NULL,idempotency_key TEXT NOT NULL,body_hash TEXT NOT NULL,reserved INTEGER NOT NULL,state TEXT NOT NULL,active INTEGER NOT NULL,input_tokens INTEGER,output_tokens INTEGER,created_at REAL NOT NULL,rule_version TEXT NOT NULL,UNIQUE(owner,idempotency_key));
            CREATE TABLE IF NOT EXISTS reconciliations(key TEXT PRIMARY KEY,request_id TEXT NOT NULL,input_tokens INTEGER NOT NULL,output_tokens INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS task_tokens(hash TEXT PRIMARY KEY,owner TEXT NOT NULL,session_id TEXT NOT NULL,grant_id TEXT NOT NULL,model TEXT NOT NULL,expires_at REAL NOT NULL);
            """)

    def recover(self):
        """Call once on single-instance server startup, never from operator constructors."""
        with self.store.transaction() as db:
            db.execute("UPDATE requests SET state=CASE WHEN state='reserved' THEN 'released' ELSE 'pending_reconciliation' END,active=0 WHERE active=1")

    def _grant(self,db,owner,grant,model=None):
        r=db.execute("SELECT * FROM grants WHERE id=? AND account_id=?",(grant,owner)).fetchone()
        now=self.store.clock()
        if not r or r['status']!='active' or not r['starts_at']<=now<r['ends_at'] or (model and model not in json.loads(r['models'])):
            raise PlatformError(403,'entitlement_required')
        return r

    def reserve(self,*,owner,grant,key,body_hash,tokens,model='',session_id=None):
        if type(tokens) is not int or tokens<=0:raise PlatformError(400,'invalid_request')
        with self.store.transaction() as db:
            g=self._grant(db,owner,grant,model)
            if session_id:
                s=db.execute("SELECT * FROM sessions WHERE id=? AND account_id=? AND revoked=0 AND refresh_expires_at>?",(session_id,owner,self.store.clock())).fetchone()
                if not s:raise PlatformError(401,'invalid_authentication')
            old=db.execute('SELECT * FROM requests WHERE owner=? AND idempotency_key=?',(owner,key)).fetchone()
            if old:raise PlatformError(409,'idempotency_conflict' if old['body_hash']!=body_hash else 'request_already_exists')
            used,reserved,active=db.execute("SELECT COALESCE(SUM(CASE WHEN state='settled' THEN input_tokens+output_tokens ELSE 0 END),0),COALESCE(SUM(CASE WHEN state IN ('reserved','dispatched','pending_reconciliation') THEN reserved ELSE 0 END),0),COALESCE(SUM(active),0) FROM requests WHERE grant_id=?",(grant,)).fetchone()
            if used+reserved+tokens>g['token_quota']:raise PlatformError(429,'quota_exceeded')
            if active>=g['concurrency_limit']:raise PlatformError(429,'concurrency_exceeded')
            id=secrets.token_hex(16)
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,'reserved',1,NULL,NULL,?,'token-v1')",(id,owner,grant,model,key,body_hash,tokens,self.store.clock()))
            return {'id':id,'state':'reserved','reserved_tokens':tokens}

    def dispatch(self,id):
        with self.store.transaction() as db:db.execute("UPDATE requests SET state='dispatched' WHERE id=? AND state='reserved'",(id,))

    def pending(self,id):
        with self.store.transaction() as db:db.execute("UPDATE requests SET state='pending_reconciliation',active=0 WHERE id=? AND state IN ('reserved','dispatched')",(id,))

    def release(self,id):
        with self.store.transaction() as db:db.execute("UPDATE requests SET state='released',active=0 WHERE id=? AND state='reserved'",(id,))

    @staticmethod
    def validate_usage(input_tokens,output_tokens):
        if any(type(n) is not int or n<0 or n>2**53 for n in (input_tokens,output_tokens)):raise PlatformError(502,'invalid_upstream_usage')

    def _settle(self,db,id,input_tokens,output_tokens):
        self.validate_usage(input_tokens,output_tokens)
        r=db.execute('SELECT * FROM requests WHERE id=?',(id,)).fetchone()
        if not r:raise PlatformError(404,'not_found')
        if r['state']=='settled':
            if (r['input_tokens'],r['output_tokens'])!=(input_tokens,output_tokens):raise PlatformError(409,'reconciliation_conflict')
            return
        if r['state'] not in ('dispatched','pending_reconciliation'):raise PlatformError(409,'reconciliation_conflict')
        db.execute("UPDATE requests SET state='settled',active=0,input_tokens=?,output_tokens=? WHERE id=?",(input_tokens,output_tokens,id))

    def settle(self,id,input_tokens,output_tokens):
        with self.store.transaction() as db:self._settle(db,id,input_tokens,output_tokens)

    def reconcile(self,id,input_tokens,output_tokens,key):
        if not isinstance(key,str) or not 0<len(key)<=256:raise PlatformError(400,'invalid_request')
        self.validate_usage(input_tokens,output_tokens)
        with self.store.transaction() as db:
            old=db.execute('SELECT * FROM reconciliations WHERE key=?',(key,)).fetchone()
            if old and (old['request_id'],old['input_tokens'],old['output_tokens'])!=(id,input_tokens,output_tokens):raise PlatformError(409,'reconciliation_conflict')
            r=db.execute('SELECT state FROM requests WHERE id=?',(id,)).fetchone()
            if not r:raise PlatformError(404,'not_found')
            if r['state'] not in ('pending_reconciliation','settled'):raise PlatformError(409,'reconciliation_conflict')
            self._settle(db,id,input_tokens,output_tokens)
            if not old:db.execute('INSERT INTO reconciliations VALUES(?,?,?,?)',(key,id,input_tokens,output_tokens))
        return {'id':id,'state':'settled','input_tokens':input_tokens,'output_tokens':output_tokens}

    def snapshot(self,owner,grant=None):
        with self.store.transaction() as db:
            rows=db.execute('SELECT id,grant_id,model,state,reserved AS reserved_tokens,input_tokens,output_tokens,created_at,rule_version FROM requests WHERE owner=?'+(' AND grant_id=?' if grant else '')+' ORDER BY created_at,id',((owner,grant) if grant else (owner,))).fetchall()
            quota=db.execute('SELECT COALESCE(SUM(token_quota),0) FROM grants WHERE account_id=?'+(' AND id=?' if grant else ''),((owner,grant) if grant else (owner,))).fetchone()[0]
        usage=[dict(r) for r in rows]
        used=sum((r['input_tokens'] or 0)+(r['output_tokens'] or 0) for r in usage if r['state']=='settled')
        reserved=sum(r['reserved_tokens'] for r in usage if r['state'] in ('reserved','dispatched','pending_reconciliation'))
        return {'usage':usage,'used_tokens':used,'reserved_tokens':reserved,'remaining_tokens':max(0,quota-used-reserved),'pending_reconciliation':sum(r['state']=='pending_reconciliation' for r in usage)}
