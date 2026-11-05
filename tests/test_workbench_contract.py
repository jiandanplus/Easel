"""Stable nonsecret workspace/source contracts for drafts and task actions."""
import json
from pathlib import Path

import pytest

import web.app as web
from tests.test_agent_harnesses import sandbox  # noqa: F401


def test_status_has_versioned_stable_contract_without_paths_or_keys(sandbox, monkeypatch):
    monkeypatch.setattr(web, "get_skills", lambda: [])
    monkeypatch.setattr(web, "list_personas", lambda: [])
    first = sandbox.get("/api/status").json()
    second = sandbox.get("/api/status").json()
    contract = first["workbench"]
    assert contract == second["workbench"]
    assert contract["version"] == 1
    for field in ("build_id", "workspace_scope", "source_scope"):
        assert len(contract[field]) == 64 and set(contract[field]) <= set("0123456789abcdef")
    assert str(web.PROJECT_ROOT) not in json.dumps(contract)


def test_scope_survives_new_store_and_changes_on_source_configuration(tmp_path):
    from easel.workbench import WorkbenchState
    first = WorkbenchState(tmp_path / "sessions", tmp_path)
    contract = first.contract({"source": "custom", "key": "private-one"}, "build")
    assert WorkbenchState(tmp_path / "sessions", tmp_path).contract({"source": "custom", "key": "private-one"}, "build") == contract
    changed = first.contract({"source": "custom", "key": "private-two"}, "build")
    assert changed["workspace_scope"] == contract["workspace_scope"]
    assert changed["source_scope"] != contract["source_scope"]
    assert "private" not in json.dumps(changed)


def test_source_scope_ignores_central_token_rotation(sandbox, monkeypatch):
    monkeypatch.setattr(web, "_harness_settings", lambda: {"runtime": "pi", "service_source": "subscription", "subscription_backend": "central", "execution_target": "local"})
    state = {"url": "https://central.test", "auth": {"access_token": "private-one", "refresh_token": "refresh-one", "expires_at": 1,
             "scope": {"user_id": "user", "tenant_id": "tenant", "billing_account_id": "wallet", "product_id": "easel", "session_id": "s1", "authorization_epoch": 0}}}
    class Store:
        def locked(self):
            from contextlib import nullcontext
            return nullcontext()
    class Client:
        def __init__(self, root):
            self.store = Store()
        def _state(self):
            return state
    import easel.platform.central
    monkeypatch.setattr(easel.platform.central, "CentralClient", Client)
    first = web._workbench_contract()["source_scope"]
    state["auth"].update(access_token="private-two", refresh_token="refresh-two", expires_at=9)
    assert web._workbench_contract()["source_scope"] == first
    state["auth"]["scope"]["user_id"] = "different"
    assert web._workbench_contract()["source_scope"] != first


def test_contract_refuses_symlinked_private_directory(tmp_path):
    from easel.workbench import WorkbenchState, WorkbenchError
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (root / "workbench").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkbenchError):
        WorkbenchState(root, tmp_path).contract({}, "build")


def test_attachment_restore_is_bounded_and_retains_missing_reference(sandbox):
    scope = web._attachment_scope("owned")
    path = f"_inbox/{scope}/upload/note.txt"
    full = web.OUTPUTS_DIR / path
    full.parent.mkdir(parents=True)
    full.write_text("fixture")
    attachment = {"id": web._attachment_id(scope, path), "name": "note.txt", "path": path}
    response = sandbox.post("/api/workbench/attachments/validate", json={"session_id": "owned", "attachments": [attachment]})
    assert response.status_code == 200
    assert response.json()["attachments"][0]["state"] == "available"
    full.unlink()
    missing = sandbox.post("/api/workbench/attachments/validate", json={"session_id": "owned", "attachments": [attachment]}).json()
    assert missing["attachments"][0]["path"] == path and missing["attachments"][0]["state"] == "missing"
    forbidden = sandbox.post("/api/workbench/attachments/validate", json={"session_id": "other", "attachments": [attachment]}).json()
    assert forbidden["attachments"][0]["state"] == "forbidden"


@pytest.mark.parametrize("path", ["../private", "/etc/passwd", "_harness/config.json"])
def test_attachment_restore_never_opens_foreign_or_private_paths(sandbox, path):
    response = sandbox.post("/api/workbench/attachments/validate", json={"session_id": "owned", "attachments": [{"id": "wrong", "name": "fixture", "path": path}]})
    assert response.status_code == 200 and response.json()["attachments"][0]["state"] == "forbidden"


def test_output_reference_is_explicit_signed_and_cannot_cross_source(sandbox, monkeypatch):
    file = web.OUTPUTS_DIR / "project" / "copy.md"
    file.parent.mkdir(parents=True)
    file.write_text("real document")
    response = sandbox.get("/api/artifacts/reference", params={"path": "project/copy.md", "session_id": "owned"})
    assert response.status_code == 200
    ref = response.json()
    assert ref["name"] == "copy.md" and ref["size"] == 13
    restored = sandbox.post("/api/workbench/attachments/validate", json={"session_id": "new-session", "attachments": [ref]})
    assert restored.json()["attachments"][0]["state"] == "available"
    context = web._attachment_context(web.ChatRequest(sessionId="new-session", message="revise", attachments=[ref]))
    assert "outputs/project/copy.md" in context
    contract = web._workbench_contract
    monkeypatch.setattr(web, "_workbench_contract", lambda *args: {**contract(), "source_scope": "b" * 64})
    rejected = sandbox.post("/api/workbench/attachments/validate", json={"session_id": "new-session", "attachments": [ref]})
    assert rejected.json()["attachments"][0]["state"] == "forbidden"


@pytest.mark.parametrize("path", ["_harness/config.json", "../.env", "_sessions/jobs/probe.jsonl"])
def test_output_reference_refuses_private_files(sandbox, path):
    if ".." not in Path(path).parts:
        full = web.OUTPUTS_DIR / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("private fixture")
    assert sandbox.get("/api/artifacts/reference", params={"path": path}).status_code in (403, 404)


def test_workbench_scope_key_is_not_downloadable_as_generic_output(sandbox):
    private = web.OUTPUTS_DIR / "_sessions" / "workbench" / "scope.key"
    private.parent.mkdir(parents=True)
    private.write_text("a" * 64)
    assert sandbox.get("/api/media/_sessions/workbench/scope.key").status_code == 403
    assert sandbox.get("/api/output/_sessions/workbench/scope.key").status_code == 403


def test_output_alias_cannot_issue_reference_to_private_runtime_file(sandbox):
    private = web.OUTPUTS_DIR / "_sessions" / "secret.txt"
    private.parent.mkdir(parents=True)
    private.write_text("private")
    public = web.OUTPUTS_DIR / "project" / "alias.txt"
    public.parent.mkdir(parents=True)
    public.symlink_to(private)
    assert sandbox.get("/api/artifacts/reference", params={"path": "project/alias.txt"}).status_code == 403
