import json
import secrets
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from .auth import PlatformError, digest, password_hash, password_matches, password_valid

class Store:
    def __init__(self, settings, clock=time.time):
        self.settings, self.clock = settings, clock
        self.lock = threading.RLock()
        self.hash_slots = threading.BoundedSemaphore(1)
        self.rates = {}
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,device_name TEXT NOT NULL,created_at REAL NOT NULL,refresh_expires_at REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY,session_id TEXT NOT NULL,kind TEXT NOT NULL,expires_at REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL,tokens INTEGER NOT NULL,concurrency INTEGER NOT NULL,days INTEGER NOT NULL,models TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,plan_id TEXT NOT NULL,plan_version INTEGER NOT NULL,starts_at REAL NOT NULL,ends_at REAL NOT NULL,status TEXT NOT NULL,token_quota INTEGER NOT NULL,concurrency_limit INTEGER NOT NULL,models TEXT NOT NULL,idempotency_key TEXT UNIQUE NOT NULL);
            """)

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.settings.database, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def rate_limit(self, action, client):
        now = self.clock()
        with self.lock:
            self.rates = {k:v for k,v in self.rates.items() if v[0] > now-60}
            key = (action, client)
            start, count = self.rates.get(key, (now, 0))
            if count >= 10 or len(self.rates) >= 10000:
                raise PlatformError(429, "rate_limited")
            self.rates[key] = (start, count+1)

    @staticmethod
    def email(value):
        if not isinstance(value, str) or len(value)>254 or value.count("@")!=1 or any(c.isspace() or unicodedata.category(c).startswith("C") for c in value):
            raise PlatformError(400, "invalid_request")
        value = value.strip().casefold()
        if not all(value.split("@")):
            raise PlatformError(400, "invalid_request")
        return value

    @staticmethod
    def account(row):
        return {"id":row["id"], "email":row["email"], "email_verified":False}

    @contextmanager
    def hashing(self):
        if not self.hash_slots.acquire(blocking=False):
            raise PlatformError(429, "rate_limited")
        try: yield
        finally: self.hash_slots.release()

    def _issue(self, db, account, session):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = self.clock()+900
        for token, kind, end in ((access,"access",expires),(refresh,"refresh",session["refresh_expires_at"])):
            db.execute("INSERT INTO tokens VALUES(?,?,?,?,0)", (digest(token),session["id"],kind,end))
        return {"access_token":access,"refresh_token":refresh,"expires_at":expires,"refresh_expires_at":session["refresh_expires_at"],"account":self.account(account),"session_id":session["id"]}

    def _session(self, db, account, device_name):
        if not isinstance(device_name,str) or len(device_name)>128 or any(unicodedata.category(c).startswith("C") for c in device_name):
            raise PlatformError(400,"invalid_request")
        session = {"id":secrets.token_hex(16),"refresh_expires_at":self.clock()+30*86400}
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,0)",(session["id"],account["id"],device_name,self.clock(),session["refresh_expires_at"]))
        return self._issue(db,account,session)

    def register(self,email,password,device_name="device"):
        email = self.email(email)
        if not password_valid(password): raise PlatformError(400,"invalid_request")
        with self.hashing(): hashed = password_hash(password)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM accounts WHERE email=?",(email,)).fetchone(): raise PlatformError(409,"account_unavailable")
            account = {"id":secrets.token_hex(16),"email":email}
            db.execute("INSERT INTO accounts VALUES(?,?,?)",(account["id"],email,hashed))
            return self._session(db,account,device_name)

    def login(self,email,password,device_name="device"):
        try: email = self.email(email)
        except PlatformError: raise PlatformError(401,"invalid_authentication") from None
        if not password_valid(password): raise PlatformError(401,"invalid_authentication")
        with self.transaction() as db:
            account = db.execute("SELECT * FROM accounts WHERE email=?",(email,)).fetchone()
        with self.hashing():
            if account: valid = password_matches(password,account["password_hash"])
            else:
                password_hash(password, b"\0"*16)
                valid = False
        if not valid: raise PlatformError(401,"invalid_authentication")
        with self.transaction() as db: return self._session(db,account,device_name)

    def refresh(self,token):
        invalid = False
        with self.transaction() as db:
            row = db.execute("SELECT t.*,s.account_id,s.refresh_expires_at,s.revoked FROM tokens t JOIN sessions s ON s.id=t.session_id WHERE t.hash=? AND t.kind='refresh'",(digest(token),)).fetchone()
            if not row or row["revoked"] or row["expires_at"]<=self.clock(): invalid=True
            elif row["used"]:
                db.execute("UPDATE sessions SET revoked=1 WHERE id=?",(row["session_id"],))
                invalid=True
            else:
                db.execute("UPDATE tokens SET used=1 WHERE session_id=?",(row["session_id"],))
                account=db.execute("SELECT * FROM accounts WHERE id=?",(row["account_id"],)).fetchone()
                result=self._issue(db,account,{"id":row["session_id"],"refresh_expires_at":row["refresh_expires_at"]})
        if invalid: raise PlatformError(401,"invalid_authentication")
        return result

    def authenticate(self,token):
        with self.transaction() as db:
            row=db.execute("SELECT a.*,s.id AS session_id FROM tokens t JOIN sessions s ON t.session_id=s.id JOIN accounts a ON s.account_id=a.id WHERE t.hash=? AND t.kind='access' AND t.used=0 AND t.expires_at>? AND s.revoked=0 AND s.refresh_expires_at>?",(digest(token),self.clock(),self.clock())).fetchone()
            if not row: raise PlatformError(401,"invalid_authentication")
            return {"account":self.account(row),"account_id":row["id"],"session_id":row["session_id"]}

    def list_sessions(self,account_id):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT id,device_name,created_at,refresh_expires_at,revoked FROM sessions WHERE account_id=?",(account_id,))]

    def revoke_session(self,account_id,session_id):
        with self.transaction() as db:
            cursor=db.execute("UPDATE sessions SET revoked=1 WHERE id=? AND account_id=?",(session_id,account_id))
            if not cursor.rowcount: raise PlatformError(404,"not_found")

    def logout(self,account_id,session_id): self.revoke_session(account_id,session_id)

    def create_plan(self,id,name,tokens,concurrency,days,models):
        if not all(isinstance(v,int) and not isinstance(v,bool) and v>0 for v in (tokens,concurrency,days)) or not models or not all(isinstance(m,str) and 0<len(m)<=128 for m in models) or not isinstance(id,str) or not 0<len(id)<=128 or not isinstance(name,str) or not 0<len(name)<=256:
            raise PlatformError(400,"invalid_plan")
        with self.transaction() as db:
            try: db.execute("INSERT INTO plans VALUES(?,?,1,?,?,?,?)",(id,name,tokens,concurrency,days,json.dumps(models)))
            except sqlite3.IntegrityError: raise PlatformError(409,"plan_exists") from None
        return next(p for p in self.plans() if p["id"]==id)

    def plans(self):
        with self.transaction() as db:
            return [{**dict(r),"test_only":True,"models":json.loads(r["models"])} for r in db.execute("SELECT * FROM plans ORDER BY id")]

    def grant(self,email,plan_id,key):
        if not isinstance(key,str) or not 0<len(key)<=256: raise PlatformError(400,"invalid_request")
        with self.transaction() as db:
            account=db.execute("SELECT id FROM accounts WHERE email=?",(self.email(email),)).fetchone()
            plan=db.execute("SELECT * FROM plans WHERE id=?",(plan_id,)).fetchone()
            if not account or not plan: raise PlatformError(404,"not_found")
            old=db.execute("SELECT * FROM grants WHERE idempotency_key=?",(key,)).fetchone()
            if old:
                if old["account_id"]!=account["id"] or old["plan_id"]!=plan_id: raise PlatformError(409,"idempotency_conflict")
                return {"id":old["id"],"test_only":True}
            id=secrets.token_hex(16)
            db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?,'active',?,?,?,?)",(id,account["id"],plan_id,plan["version"],self.clock(),self.clock()+plan["days"]*86400,plan["tokens"],plan["concurrency"],plan["models"],key))
            return {"id":id,"test_only":True}

    def revoke_grant(self,grant_id):
        with self.transaction() as db: db.execute("UPDATE grants SET status='revoked' WHERE id=?",(grant_id,))

    def entitlements(self,account_id):
        with self.transaction() as db:
            rows=db.execute("SELECT id,plan_id,plan_version,starts_at,ends_at,status,token_quota,concurrency_limit,models FROM grants WHERE account_id=? ORDER BY starts_at,id",(account_id,)).fetchall()
        return [{**dict(r),"test_only":True,"models":json.loads(r["models"]),"status":"expired" if r["status"]=="active" and r["ends_at"]<=self.clock() else r["status"],**self._usage(account_id,r["id"])} for r in rows]

    def _usage(self,owner,grant):
        from .ledger import Ledger
        snapshot=Ledger(self).snapshot(owner,grant)
        return {k:snapshot[k] for k in ("used_tokens","reserved_tokens","remaining_tokens")}
