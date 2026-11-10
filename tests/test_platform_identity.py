import hashlib
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from easel_platform import Settings, Store, create_app

PASSWORD = "test-password-123456"

def register(c, email="a@example.test"):
    r = c.post("/v1/auth/register", json={"email": email, "password": PASSWORD, "device_name": "test"})
    assert r.status_code == 201, r.text
    return r.json()

def headers(s):
    return {"Authorization": "Bearer " + s["access_token"]}

def test_registration_has_no_entitlement(tmp_path):
    with TestClient(create_app(Settings(tmp_path / "db"))) as c:
        s = register(c)
        assert c.get("/v1/entitlements", headers=headers(s)).json()["entitlements"] == []
        assert s["account"]["email_verified"] is False
        assert c.get("/v1/models", headers=headers(s)).json()["ready"] is False

def test_rotation_replay_expiry_logout_and_restart(tmp_path):
    now = [1000.0]
    settings = Settings(tmp_path / "db")
    with TestClient(create_app(settings, clock=lambda: now[0])) as c:
        s = register(c)
        now[0] += 901
        assert c.get("/v1/account", headers=headers(s)).status_code == 401
        r = c.post("/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
        assert r.status_code == 200
        rotated = r.json()
    with TestClient(create_app(settings, clock=lambda: now[0])) as c:
        assert c.get("/v1/account", headers=headers(rotated)).status_code == 200
        assert c.post("/v1/auth/refresh", json={"refresh_token": s["refresh_token"]}).status_code == 401
        assert c.get("/v1/account", headers=headers(rotated)).status_code == 401
        login = c.post("/v1/auth/login", json={"email":"A@EXAMPLE.TEST", "password":PASSWORD}).json()
        assert c.post("/v1/auth/logout", headers=headers(login)).status_code == 200
        assert c.get("/v1/account", headers=headers(login)).status_code == 401

def test_ownership_and_hash_only_persistence(tmp_path):
    settings = Settings(tmp_path / "db")
    with TestClient(create_app(settings)) as c:
        a, b = register(c), register(c, "b@example.test")
        assert c.delete("/v1/sessions/" + b["session_id"], headers=headers(a)).status_code == 404
        assert len(c.get("/v1/sessions", headers=headers(a)).json()["sessions"]) == 1
        assert c.delete("/v1/sessions/" + a["session_id"], headers=headers(a)).status_code == 200
    raw = settings.database.read_bytes()
    for secret in (PASSWORD, a["access_token"], a["refresh_token"]):
        assert secret.encode() not in raw
    db = sqlite3.connect(settings.database)
    hashes = [r[0] for r in db.execute("select password_hash from accounts")]
    assert hashes[0] != hashes[1]
    assert all(h.startswith("scrypt$131072$8$1$") for h in hashes)

@pytest.mark.parametrize("password", ["short", "x" * 129, "x" * 14 + "\n", "😀" * 129])
def test_safe_validation(tmp_path, password):
    with TestClient(create_app(Settings(tmp_path / "db"))) as c:
        r = c.post("/v1/auth/register", json={"email":"a@example.test", "password":password})
        assert r.status_code == 400
        assert password not in r.text
        r = c.post("/v1/auth/login", content="{bad-secret")
        assert r.status_code == 400 and "bad-secret" not in r.text
        assert c.post("/v1/auth/login", json={}, headers={"Origin":"https://evil.test"}).status_code == 403

def test_generic_login_rate_limit_and_hash_bound(tmp_path):
    with TestClient(create_app(Settings(tmp_path / "db"))) as c:
        s = register(c)
        missing = c.post("/v1/auth/login", json={"email":"none@example.test", "password":PASSWORD})
        wrong = c.post("/v1/auth/login", json={"email":"a@example.test", "password":"wrong-password-123"})
        assert missing.status_code == wrong.status_code == 401
        assert missing.json() == wrong.json()
        for _ in range(12):
            r = c.post("/v1/auth/login", json={"email":"a@example.test", "password":"wrong-password-123"})
        assert r.status_code == 429
        store = c.app.state.store
        assert store.hash_slots.acquire(blocking=False)
        try:
            r = c.post("/v1/auth/register", json={"email":"c@example.test", "password":PASSWORD})
            assert r.status_code == 429
        finally:
            store.hash_slots.release()

def test_refresh_family_absolute_expiry(tmp_path):
    now = [1000.0]
    with TestClient(create_app(Settings(tmp_path / "db"), clock=lambda:now[0])) as c:
        s = register(c)
        now[0] += 30 * 86400 + 1
        assert c.post("/v1/auth/refresh", json={"refresh_token":s["refresh_token"]}).status_code == 401

def test_settings_validation(tmp_path):
    with pytest.raises(ValueError): Settings(tmp_path / "db", upstream_url="http://example.test")
    assert "super-secret" not in repr(Settings(tmp_path / "db", upstream_key="super-secret"))


def test_malformed_unicode_and_invalid_login_are_safe(tmp_path):
    with TestClient(create_app(Settings(tmp_path / "db"))) as c:
        r=c.post("/v1/auth/register",content=json.dumps({"email":"a@example.test","password":"x"*15+"\ud800"}))
        assert r.status_code==400
        r=c.post("/v1/auth/login",json={"email":"invalid", "password":PASSWORD})
        assert r.status_code==401 and r.json()=={"error":{"code":"invalid_authentication"}}
        r=c.post("/v1/auth/refresh",content=json.dumps({"refresh_token":"\ud800"}))
        assert r.status_code==400

def test_corrupt_database_is_not_replaced(tmp_path):
    p=tmp_path/"db"
    p.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError): Store(Settings(p))
    assert p.read_bytes()==b"not a sqlite database"
