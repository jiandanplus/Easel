import json
from fastapi.testclient import TestClient
from easel_platform import Settings, Store, create_app
def register(c):
    r=c.post("/v1/auth/register", json={"email":"a@example.test","password":"test-password-123456"})
    assert r.status_code == 201
    return r.json()

def headers(s): return {"Authorization":"Bearer "+s["access_token"]}

def test_operator_grants_idempotence_expiry_revocation(tmp_path):
    now = [1000.0]
    settings = Settings(tmp_path / "db")
    app = create_app(settings, clock=lambda:now[0])
    store = app.state.store
    store.create_plan("trial", "Test trial", 10000, 2, 1, ["public-model"])
    with TestClient(app) as c:
        a = register(c)
        first = store.grant("a@example.test", "trial", "key-1")
        assert store.grant("a@example.test", "trial", "key-1") == first
        assert c.get("/v1/plans").json()["plans"][0]["test_only"] is True
        ent = c.get("/v1/entitlements", headers=headers(a)).json()["entitlements"]
        assert len(ent) == 1 and ent[0]["status"] == "active"
        assert ent[0]["remaining_tokens"] == 10000
        assert c.post("/v1/grant", json={}).status_code == 404
        now[0] += 86401
        login = c.post("/v1/auth/login", json={"email":"a@example.test", "password":"test-password-123456"}).json()
        assert c.get("/v1/entitlements", headers=headers(login)).json()["entitlements"][0]["status"] == "expired"
        store.revoke_grant(first["id"])
        assert store.entitlements(a["account"]["id"])[0]["status"] == "revoked"

def test_cli_operator_database_and_parser(tmp_path, capsys, monkeypatch):
    from easel_platform.__main__ import main
    db = str(tmp_path / "db")
    with TestClient(create_app(Settings(tmp_path / "db"))) as c: register(c)
    main(["plan", "create", "--database", db, "--id", "trial", "--name", "Test", "--tokens", "1000", "--concurrency", "1", "--days", "1", "--model", "alias"])
    main(["grant", "--database", db, "--email", "a@example.test", "--plan", "trial", "--key", "once"])
    assert Store(Settings(tmp_path / "db")).plans()[0]["test_only"] is True
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw:calls.append(kw))
    main(["serve", "--database", db, "--host", "127.0.0.1", "--port", "9876"])
    assert calls == [{"host":"127.0.0.1", "port":9876, "proxy_headers":False}]


def test_cli_subprocess_operator_commands(tmp_path):
    import os
    import subprocess
    import sys
    db=tmp_path/"db"
    with TestClient(create_app(Settings(db))) as c: register(c)
    env={**os.environ,"HOME":str(tmp_path),"PYTHONPATH":os.getcwd()}
    base=[sys.executable,"-m","easel_platform"]
    for args in (["plan","create","--database",str(db),"--id","trial","--name","Test","--tokens","1000","--concurrency","1","--days","1","--model","alias"], ["grant","--database",str(db),"--email","a@example.test","--plan","trial","--key","once"]):
        result=subprocess.run(base+args,env=env,cwd=tmp_path,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)["test_only"] is True
    assert len(Store(Settings(db)).entitlements(Store(Settings(db)).login("a@example.test","test-password-123456")["account"]["id"]))==1
