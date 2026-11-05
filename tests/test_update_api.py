import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
import app as web
from easel import updater

LOCAL = "http://127.0.0.1:7860"
MANIFEST = {"format": updater.FORMAT, "version": 38, "url": "https://example.invalid/v38.zip",
            "sha256": "a" * 64, "bytes": 10, "notes": "修复若干问题"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "Applications/Easel"
    layout = updater.Layout(root, 37, root / "v37/Easel/Easel.app/Contents/Resources")
    root.mkdir(parents=True)
    monkeypatch.setenv("EASEL_PORTABLE", "1")
    monkeypatch.setenv("EASEL_RESOURCE_ROOT", str(layout.resource))
    monkeypatch.setenv("EASEL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("EASEL_PORT", "17868")
    monkeypatch.setattr(updater, "managed_layout", lambda resource: layout)
    monkeypatch.setattr(web, "_UPDATE", {"state": "idle", "manifest": None, "received": 0, "error": ""})
    monkeypatch.setattr(web, "_RUNNING_CHAT", {})
    with TestClient(web.app, base_url=LOCAL, headers={"Origin": LOCAL}, client=("127.0.0.1", 50000)) as c:
        c.layout = layout
        yield c


def test_status_reports_managed_version_and_last_result(client):
    (client.layout.root / "update-result.json").write_text('{"status": "updated", "from": 36, "to": 37}')
    body = client.get("/api/update/status").json()
    assert body["managed"] is True and body["version"] == 37 and body["state"] == "idle"
    assert body["last"]["status"] == "updated" and body["available"] is None


def test_unmanaged_install_cannot_check(client, monkeypatch):
    monkeypatch.setattr(updater, "managed_layout", lambda resource: None)
    assert client.get("/api/update/status").json()["managed"] is False
    assert client.post("/api/update/check").status_code == 409


def test_check_download_apply_flow(client, monkeypatch):
    monkeypatch.setattr(updater, "check", lambda resource, url: MANIFEST)
    body = client.post("/api/update/check").json()
    assert body["state"] == "available" and body["available"] == {"version": 38, "notes": "修复若干问题", "bytes": 10}

    installed = []
    monkeypatch.setattr(updater, "install", lambda layout, manifest, progress: installed.append(manifest["version"]))
    assert client.post("/api/update/download").status_code == 200
    for _ in range(50):
        if client.get("/api/update/status").json()["state"] == "ready":
            break
    assert installed == [38] and client.get("/api/update/status").json()["state"] == "ready"

    switches = []
    monkeypatch.setattr(updater, "start_switch", lambda layout, new, workspace, port: switches.append((new, workspace.name, port)))
    assert client.post("/api/update/apply").json() == {"state": "switching", "version": 38}
    assert switches == [(38, "workspace", 17868)]


def test_check_reports_latest_when_nothing_newer(client, monkeypatch):
    monkeypatch.setattr(updater, "check", lambda resource, url: None)
    assert client.post("/api/update/check").json()["state"] == "latest"
    assert client.post("/api/update/download").status_code == 409


def test_check_surfaces_signature_failure(client, monkeypatch):
    def reject(resource, url):
        raise updater.UpdateError("更新清单签名无效")
    monkeypatch.setattr(updater, "check", reject)
    response = client.post("/api/update/check")
    assert response.status_code == 502 and "签名" in response.json()["detail"]


def test_apply_refused_while_a_task_runs(client, monkeypatch):
    web._UPDATE.update(state="ready", manifest=MANIFEST)
    monkeypatch.setattr(web, "_RUNNING_CHAT", {"web:s": object()})
    monkeypatch.setattr(updater, "start_switch", lambda *a: pytest.fail("must not switch while busy"))
    response = client.post("/api/update/apply")
    assert response.status_code == 409 and "任务" in response.json()["detail"]


def test_apply_requires_downloaded_update(client, monkeypatch):
    web._UPDATE.update(state="available", manifest=MANIFEST)
    monkeypatch.setattr(updater, "start_switch", lambda *a: pytest.fail("not downloaded yet"))
    assert client.post("/api/update/apply").status_code == 409
