"""In-app update endpoints (managed macOS layout only: <root>/vN + current link)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from easel import updater

_UPDATE: dict = {"state": "idle", "manifest": None, "received": 0, "error": ""}


def _layout():
    resource = os.environ.get("EASEL_RESOURCE_ROOT")
    if os.environ.get("EASEL_PORTABLE") != "1" or not resource:
        return None
    return updater.managed_layout(Path(resource))


def _manifest_url() -> str:
    return os.environ.get("EASEL_UPDATE_MANIFEST_URL") or updater.DEFAULT_MANIFEST_URL


def create_router(core) -> APIRouter:
    router = APIRouter()

    @router.get("/api/update/status")
    async def api_update_status():
        layout = _layout()
        last = None
        if layout is not None:
            try:
                last = json.loads((layout.root / "update-result.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        manifest = _UPDATE["manifest"]
        return {"managed": layout is not None, "version": layout.version if layout else None,
                "state": _UPDATE["state"], "error": _UPDATE["error"], "received": _UPDATE["received"],
                "available": {k: manifest[k] for k in ("version", "notes", "bytes")} if manifest else None,
                "busy": core._busy(), "last": last}

    @router.post("/api/update/check")
    async def api_update_check():
        layout = _layout()
        if layout is None:
            raise HTTPException(409, "当前安装方式不支持应用内更新")
        if _UPDATE["state"] in ("downloading", "switching"):
            raise HTTPException(409, "更新正在进行")
        try:
            manifest = await asyncio.to_thread(updater.check, layout.resource, _manifest_url())
        except updater.UpdateError as error:
            _UPDATE.update(state="idle", error=str(error))
            raise HTTPException(502, str(error)) from None
        _UPDATE.update(state="available" if manifest else "latest", manifest=manifest, received=0, error="")
        return await api_update_status()

    @router.post("/api/update/download")
    async def api_update_download():
        layout = _layout()
        manifest = _UPDATE["manifest"]
        if layout is None or manifest is None or _UPDATE["state"] not in ("available", "failed"):
            raise HTTPException(409, "没有可下载的更新")

        def progress(received):
            _UPDATE["received"] = received

        async def run():
            try:
                await asyncio.to_thread(updater.install, layout, manifest, progress=progress)
                _UPDATE.update(state="ready", error="")
            except updater.UpdateError as error:
                _UPDATE.update(state="failed", error=str(error))

        _UPDATE.update(state="downloading", received=0, error="")
        task = asyncio.create_task(run())
        core._BG_TASKS.add(task)
        task.add_done_callback(core._BG_TASKS.discard)
        return await api_update_status()

    @router.post("/api/update/apply")
    async def api_update_apply():
        layout = _layout()
        manifest = _UPDATE["manifest"]
        if layout is None or manifest is None or _UPDATE["state"] != "ready":
            raise HTTPException(409, "更新尚未下载完成")
        if core._busy():
            raise HTTPException(409, "请等待当前任务完成或停止后再更新")
        workspace, port = os.environ.get("EASEL_WORKSPACE_ROOT"), os.environ.get("EASEL_PORT")
        if not workspace or not port:
            raise HTTPException(409, "当前安装方式不支持应用内更新")
        updater.start_switch(layout, manifest["version"], Path(workspace), int(port))
        _UPDATE.update(state="switching", error="")
        return {"state": "switching", "version": manifest["version"]}

    return router
