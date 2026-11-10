"""Settings pages for this machine: device facts, where data lives, and the recycle bin."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from easel import trash as _trash


class _OpenRequest(BaseModel):
    key: str


def _os_name() -> str:
    if sys.platform == "darwin":
        version = platform.mac_ver()[0]
        return f"macOS {version}" if version else "macOS"
    return platform.platform(terse=True)


def create_router(core) -> APIRouter:
    router = APIRouter()

    def places() -> list[tuple[str, str, str, Path]]:
        outputs = core.OUTPUTS_DIR
        sessions = core.OPENCLAW_SESSIONS_DIR  # <state>/agents/main/sessions
        agent = sessions.parents[2] if sessions.parts[-3:] == ("agents", "main", "sessions") else sessions
        return [
            ("outputs", "内容库", "生成的文案、图片、视频等成品", outputs),
            ("sessions", "对话记录", "最近一轮对话的结果，用于断线后取回", outputs / "_sessions"),
            ("uploads", "上传的附件", "对话里上传的文件", outputs / "_inbox"),
            ("trash", "回收站", f"删除的内容，保留 {_trash.KEEP_DAYS} 天", _trash.trash_dir(outputs)),
            ("profiles", "账号画像", "运营账号的画像和人设", core.PROFILES_DIR),
            ("agent", "智能体数据", "智能体的配置、会话和缓存", agent),
        ]

    @router.get("/api/system")
    def api_system():
        storage = []
        for key, label, desc, path in places():
            if key == "outputs":  # The library itself, without the hidden system folders listed separately.
                size = files = 0
                if path.is_dir():
                    for entry in path.iterdir():
                        if not entry.name.startswith(("_", ".")):
                            b, n = _trash.tree_size(entry)
                            size, files = size + b, files + n
            else:
                size, files = _trash.tree_size(path) if path.exists() else (0, 0)
            storage.append({"key": key, "label": label, "desc": desc, "path": str(path), "exists": path.exists(), "bytes": size, "files": files})
        return {
            "device": {"name": platform.node(), "os": _os_name(), "arch": platform.machine(), "python": platform.python_version()},
            "workspace": str(core.PROJECT_ROOT),
            "storage": storage,
        }

    @router.post("/api/system/open")
    def api_system_open(req: _OpenRequest):
        path = next((p for key, _, _, p in places() if key == req.key), None)
        if path is None:
            raise HTTPException(404, "没有这个位置")
        if not path.exists():
            raise HTTPException(404, "这个位置还没有数据")
        if sys.platform != "darwin":
            raise HTTPException(400, "目前只支持在 macOS 上打开文件夹")
        subprocess.run(["open", str(path)], check=False, timeout=10)
        return {"ok": True}

    @router.get("/api/trash")
    def api_trash():
        return {"items": _trash.list_trash(core.OUTPUTS_DIR), "keep_days": _trash.KEEP_DAYS}

    @router.post("/api/trash/{item_id}/restore")
    def api_trash_restore(item_id: str):
        try:
            return _trash.restore(core.OUTPUTS_DIR, item_id)
        except KeyError:
            raise HTTPException(404, "回收站里没有这一项") from None
        except PermissionError:
            raise HTTPException(403, "原位置是系统数据，不能恢复到那里") from None
        except (OSError, FileExistsError) as error:
            raise HTTPException(500, f"恢复失败：{error}") from None

    @router.delete("/api/trash/{item_id}")
    def api_trash_remove(item_id: str):
        try:
            _trash.remove(core.OUTPUTS_DIR, item_id)
        except KeyError:
            raise HTTPException(404, "回收站里没有这一项") from None
        return {"ok": True}

    @router.delete("/api/trash")
    def api_trash_empty():
        return {"ok": True, "removed": _trash.empty(core.OUTPUTS_DIR)}

    return router
