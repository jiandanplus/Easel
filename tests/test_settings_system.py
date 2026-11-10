"""Settings pages backed by this machine: recycle bin, storage and device facts (synthetic paths only)."""
import time

import web.app as web
from easel import trash
from tests import test_agent_harnesses as harness_tests

sandbox = harness_tests.sandbox


def test_deleting_from_the_library_moves_into_the_bin_and_restores(sandbox):
    project = web.OUTPUTS_DIR / "城市夜景"
    project.mkdir(parents=True)
    (project / "文案.md").write_text("夜景", encoding="utf-8")
    deleted = sandbox.delete("/api/output/城市夜景")
    assert deleted.status_code == 200 and deleted.json()["kind"] == "dir"
    assert not project.exists()
    assert all(item["name"] != "城市夜景" for item in sandbox.get("/api/outputs").json())
    items = sandbox.get("/api/trash").json()["items"]
    assert [(i["name"], i["original"], i["kind"], i["files"]) for i in items] == [("城市夜景", "城市夜景", "dir", 1)]
    assert sandbox.delete("/api/output/_trash").status_code == 403  # The bin is system data.
    restored = sandbox.post(f"/api/trash/{items[0]['id']}/restore").json()
    assert restored["restored"] == "城市夜景" and (project / "文案.md").read_text(encoding="utf-8") == "夜景"
    assert sandbox.get("/api/trash").json()["items"] == []


def test_restore_never_overwrites_and_the_bin_can_be_emptied(sandbox):
    web.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    note = web.OUTPUTS_DIR / "笔记.txt"
    note.write_text("旧", encoding="utf-8")
    first = sandbox.delete("/api/output/笔记.txt").json()["trash_id"]
    note.write_text("新", encoding="utf-8")
    assert sandbox.post(f"/api/trash/{first}/restore").json()["restored"] == "笔记（恢复 1）.txt"
    assert note.read_text(encoding="utf-8") == "新"
    for name in ("a.txt", "b.txt"):
        (web.OUTPUTS_DIR / name).write_text(name, encoding="utf-8")
        sandbox.delete(f"/api/output/{name}")
    one = sandbox.get("/api/trash").json()["items"][0]["id"]
    assert sandbox.delete(f"/api/trash/{one}").status_code == 200
    assert len(sandbox.get("/api/trash").json()["items"]) == 1
    assert sandbox.delete("/api/trash").json()["removed"] == 1
    assert sandbox.get("/api/trash").json()["items"] == []
    for bad in ("../x", "20260101000000-zzzzzzzz", "nope"):
        assert sandbox.post(f"/api/trash/{bad}/restore").status_code == 404


def test_old_items_are_purged_when_the_bin_is_listed(sandbox):
    web.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    (web.OUTPUTS_DIR / "old.txt").write_text("x", encoding="utf-8")
    meta = trash.move_to_trash(web.OUTPUTS_DIR, web.OUTPUTS_DIR / "old.txt")
    assert trash.list_trash(web.OUTPUTS_DIR, now=meta["deleted_at"] + 29 * 86400)
    assert trash.list_trash(web.OUTPUTS_DIR, now=meta["deleted_at"] + 31 * 86400) == []
    assert not (trash.trash_dir(web.OUTPUTS_DIR) / meta["id"]).exists()


def test_system_reports_device_and_where_data_lives(sandbox):
    (web.OUTPUTS_DIR / "成品").mkdir(parents=True, exist_ok=True)
    (web.OUTPUTS_DIR / "成品" / "图.png").write_bytes(b"x" * 10)
    (web.OUTPUTS_DIR / "_inbox").mkdir(exist_ok=True)
    (web.OUTPUTS_DIR / "_inbox" / "a.bin").write_bytes(b"y" * 7)
    body = sandbox.get("/api/system").json()
    assert body["device"]["name"] and body["device"]["os"]
    sizes = {item["key"]: item["bytes"] for item in body["storage"]}
    assert sizes["outputs"] == 10 and sizes["uploads"] == 7 and sizes["trash"] == 0
    assert sandbox.post("/api/system/open", json={"key": "nowhere"}).status_code == 404
    assert sandbox.post("/api/system/open", json={"key": "trash"}).status_code == 404  # Nothing there yet.
    assert time.time() > 0


def test_avatar_is_stored_on_this_machine_and_checked(sandbox):
    assert sandbox.get("/api/profile/avatar").status_code == 404
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert sandbox.post("/api/profile/avatar", files={"file": ("a.png", png, "image/png")}).json()["type"] == "image/png"
    got = sandbox.get("/api/profile/avatar")
    assert got.status_code == 200 and got.content == png and got.headers["content-type"] == "image/png"
    jpg = b"\xff\xd8\xff" + b"1" * 32
    sandbox.post("/api/profile/avatar", files={"file": ("b.jpg", jpg, "image/jpeg")})
    assert sandbox.get("/api/profile/avatar").content == jpg and not (web.OUTPUTS_DIR / "_profile" / "avatar.png").exists()
    assert sandbox.post("/api/profile/avatar", files={"file": ("x.png", b"<svg/>", "image/png")}).status_code == 415
    assert sandbox.post("/api/profile/avatar", files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024), "image/png")}).status_code == 413
    assert sandbox.delete("/api/profile/avatar").status_code == 200
    assert sandbox.get("/api/profile/avatar").status_code == 404
    assert sandbox.get("/api/output/_profile/avatar.jpg").status_code in (403, 404)  # Not exposed as library content.
