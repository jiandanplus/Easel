"""Content library: output tree, safe output paths, previews, media, deletion and uploads.

Moved out of web/app.py. Shared paths and helpers are read from the core app
module (`_core`) at call time; state owned by this domain lives here.
"""
from __future__ import annotations

from pathlib import Path
import asyncio
import json
import shutil
import time
import uuid
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from easel.harnesses.base import HarnessError

router = APIRouter()
_core = None

def _file_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _core.IMAGE_EXTS:
        return 'image'
    if ext in _core.VIDEO_EXTS:
        return 'video'
    if ext in _core.AUDIO_EXTS:
        return 'audio'
    if ext in _core.TEXT_EXTS:
        return 'text'
    return 'binary'


def _file_meta(f: Path, rel: str) -> dict:
    try:
        st = f.stat()
        mtime, size = int(st.st_mtime), st.st_size
    except OSError:
        mtime, size = 0, 0
    return {'name': f.name, 'path': rel, 'kind': _file_kind(f.name), 'mtime': mtime, 'size': size}


def _private_output_path(path: Path) -> bool:
    """Keep managed runtime credentials out of generic output browsing/downloads."""
    root = _core.OUTPUTS_DIR.resolve()
    from easel.platform.privacy import sensitive_values
    try:
        if any(value in str(path) for value in sensitive_values(root.parent)):
            return True
    except HarnessError:
        raise HTTPException(403, '敏感输出保护不可用，请检查本地账号状态') from None
    for candidate in (path.absolute(), path.resolve()):
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0].casefold() in {'_harness', '_sessions'}:
            return True
    return False


def _build_output_node(path: Path, rel: str) -> dict:
    """递归构建产物树节点：文件→file 节点；目录→dir 节点带 children + 递归 fileCount。"""
    if path.is_dir():
        children = []
        for c in sorted(path.iterdir()):
            if c.name.startswith('.') or _private_output_path(c):   # 嵌套层只跳隐藏文件；_base.mp4 等以 _ 开头的产物要保留
                continue
            children.append(_build_output_node(c, f'{rel}/{c.name}'))
        mtime = max((x['mtime'] for x in children), default=int(path.stat().st_mtime))
        file_count = sum(x.get('fileCount', 1) if x['type'] == 'dir' else 1 for x in children)
        return {'name': path.name, 'type': 'dir', 'path': rel, 'mtime': mtime,
                'children': children, 'fileCount': file_count}
    m = _file_meta(path, rel)
    m['type'] = 'file'
    return m


def _read_project_meta(proj: Path) -> dict:
    """读项目目录的 .easel.json 展示头，附封面/成品的解析路径供前端富展示。

    只取展示相关字段（不含编排 steps）。cover 解析优先级：
    展示头声明的 cover → 首个成品媒体 → 目录内首张图/视频（兜底）。
    """
    mf = proj / ".easel.json"
    if _private_output_path(mf) or not mf.is_file():
        return {}
    try:
        from easel.platform.privacy import safe_snapshot
        with safe_snapshot(mf, _core.OUTPUTS_DIR) as snapshot:
            raw = snapshot.read(65537)
            if len(raw) > 65536:
                return {}
            data = json.loads(raw)
    except Exception:
        return {}
    meta = {k: data.get(k) for k in
            ("title", "summary", "platform", "kind", "status", "tags", "deliverables")
            if data.get(k) not in (None, "", [])}
    if not meta:
        return {}

    def _rel_if_exists(name: str) -> str:
        return f"{proj.name}/{name}" if name and (proj / name).is_file() else ""

    # 封面解析
    cover_rel = ""
    declared = data.get("cover")
    if declared and (proj / declared).is_file():
        cover_rel = f"{proj.name}/{declared}"
    if not cover_rel:
        for d in (data.get("deliverables") or []):
            if _file_kind(d) in ("image", "video") and (proj / d).is_file():
                cover_rel = f"{proj.name}/{d}"
                break
    if cover_rel:
        meta["cover"] = cover_rel
    # 成品路径（前端「成品区」高亮用）：解析为 outputs 相对路径，只留真实存在的
    meta["deliverablePaths"] = [f"{proj.name}/{d}" for d in (data.get("deliverables") or [])
                                if (proj / d).is_file()]
    return meta


def get_output_tree() -> list[dict]:
    if not _core.OUTPUTS_DIR.is_dir():
        return []
    items = []
    for e in sorted(_core.OUTPUTS_DIR.iterdir()):
        # 内容库只展示「项目目录」：跳过系统目录(_login/_publish/...)、隐藏项、
        # 以及根目录散文件（按新规约产物必在项目目录内，根散文件=系统状态/残渣）。
        if e.name.startswith('.') or e.name.startswith('_') or _private_output_path(e):
            continue
        if not e.is_dir() or e.name in _core.SYSTEM_TOPLEVEL_DIRS:
            continue
        node = _build_output_node(e, e.name)
        meta = _read_project_meta(e)
        if meta:
            node['meta'] = meta
        items.append(node)
    # 按最后修改时间倒序：最近产物排最前（供工作台「最近产物」与内容库时间排序）
    return sorted(items, key=lambda x: x.get('mtime', 0), reverse=True)


def _safe_output_path(rel: str) -> Path:
    '把相对路径解析到 outputs/ 内，防路径穿越。'
    if _private_output_path(_core.OUTPUTS_DIR / rel):
        raise HTTPException(403, '运行时私有数据不可通过产物接口访问')
    full = (_core.OUTPUTS_DIR / rel).resolve()
    root = _core.OUTPUTS_DIR.resolve()
    if root != full and root not in full.parents:
        raise HTTPException(403, '非法路径')
    if not full.is_file():
        raise HTTPException(404, '文件不存在')
    return full



@router.get("/api/outputs")
def api_outputs():
    # 产物树是全量递归扫描（文件量大时单次可达数十秒）。必须是同步 handler：
    # FastAPI 会自动放入线程池执行；若写成 async def 直调，扫描期间会阻塞事件循环，
    # 全站所有请求（含 /api/status）一起挂起等它。
    return get_output_tree()


@router.get("/api/artifacts/archive")
def api_artifact_archive(path: str):
    full = _safe_output_path(path)
    if full.suffix.lower() != '.zip':
        raise HTTPException(400, '仅支持 ZIP 产物索引')
    artifacts = []
    seen = set()
    extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.avif', '.bmp',
                  '.ico', '.html', '.htm', '.mp4', '.webm', '.mov', '.m4v',
                  '.mp3', '.wav', '.ogg', '.m4a', '.flac', '.pdf'}
    try:
        from easel.platform.privacy import safe_snapshot
        with safe_snapshot(full, _core.OUTPUTS_DIR) as snapshot, zipfile.ZipFile(snapshot) as archive:
            entries = archive.infolist()
            if len(entries) > 1000:
                raise HTTPException(400, '压缩包文件过多，暂不自动预览')
            for entry in entries:
                name = entry.filename
                parts = name.split('/')
                if (entry.is_dir() or name.startswith('/') or '\\' in name
                        or any(not part or part.startswith(('.', '_')) for part in parts)
                        or any(ord(character) < 32 for character in name)
                        or Path(name).suffix.lower() not in extensions):
                    continue
                relative = (full.parent / name).relative_to(_core.OUTPUTS_DIR.resolve()).as_posix()
                try:
                    original = _safe_output_path(relative)
                except HTTPException:
                    continue
                if original.stat().st_size != entry.file_size or relative in seen:
                    continue
                seen.add(relative)
                artifacts.append(relative)
                if len(artifacts) >= 100:
                    break
    except (OSError, zipfile.BadZipFile):
        raise HTTPException(400, '无法读取压缩包索引')
    return {'paths': artifacts}


async def _output_snapshot(full):
    """Await an owned worker result; cancelled callers still close its spool."""
    from easel.platform.privacy import safe_snapshot
    scan = asyncio.create_task(asyncio.to_thread(safe_snapshot, full, _core.OUTPUTS_DIR))
    try:
        snapshot = await asyncio.shield(scan)
    except asyncio.CancelledError:
        def close_scanned(task):
            try:
                task.result().close()
            except Exception:
                pass  # retrieve failed scan too; it already closed its own spool
        scan.add_done_callback(close_scanned)
        raise
    return snapshot


@router.get("/api/output/{path:path}")
async def api_output(path: str):
    """文本产物内容。二进制/媒体返回 isBinary=true，前端改用 /api/media。"""
    full = _safe_output_path(path)
    with await _output_snapshot(full) as snapshot:
        kind = _file_kind(full.name)
        if kind != "text":
            return {"path": path, "content": "", "kind": kind, "isBinary": True}
        # Text responses must fit a bounded browser JSON allocation; media/download
        # continues to stream arbitrarily large clean files with bounded memory.
        if snapshot.seek(0, 2) > 8 * 1024 * 1024:
            raise HTTPException(413, '文本产物过大，请下载查看')
        snapshot.seek(0)
        try:
            return {"path": path, "content": snapshot.read().decode("utf-8"), "kind": "text", "isBinary": False}
        except UnicodeDecodeError:
            return {"path": path, "content": "", "kind": "binary", "isBinary": True}


@router.get("/api/media/{path:path}")
async def api_media(path: str, request: Request):
    """Serve a scanned immutable snapshot, including Range requests."""
    from easel.platform.privacy import snapshot_response
    full = _safe_output_path(path)
    snapshot = await _output_snapshot(full)
    return snapshot_response(snapshot, full.name, request.headers.get("range", ""))


# 系统数据目录/文件——不允许从内容库删除（删了会丢登录态/日历/发布记录）
PROTECTED_OUTPUTS = {"_login", "_analytics", "_schedule.json", "_ideas.json",
                     "_publish", "_publish.log", "_sessions", "_profile_build", "_debug", "_inbox"}
MAX_UPLOAD_MB = 50


def _unique_upload_path(dest: Path, filename: str) -> Path:
    """同一上传批次内保留所有同名文件，不让后一个静默覆盖前一个。"""
    target = dest / filename
    if not target.exists():
        return target
    source = Path(filename)
    index = 2
    while True:
        target = dest / f"{source.stem} ({index}){source.suffix}"
        if not target.exists():
            return target
        index += 1


def _safe_output_target(rel: str, *, must_exist: bool = True) -> Path:
    """解析到 outputs/ 内的文件或目录（防穿越）。与 _safe_output_path 不同：允许目录、
    可要求不必已存在（上传新文件时）。永远拒绝 outputs/ 根本身。"""
    if _private_output_path(_core.OUTPUTS_DIR / rel):
        raise HTTPException(403, '运行时私有数据不可通过产物接口访问')
    full = (_core.OUTPUTS_DIR / rel).resolve()
    root = _core.OUTPUTS_DIR.resolve()
    if full == root or root not in full.parents:
        raise HTTPException(403, '非法路径')
    if must_exist and not full.exists():
        raise HTTPException(404, '不存在')
    return full


def _is_protected(full: Path) -> bool:
    """路径的顶层段是否属于受保护的系统项。"""
    try:
        rel = full.relative_to(_core.OUTPUTS_DIR.resolve())
    except ValueError:
        return True
    if not rel.parts:
        return True
    first = rel.parts[0]
    return (
        first.startswith('_')
        or first.startswith('.')
        or first in PROTECTED_OUTPUTS
        or first.lower() in {'analytics', 'wechat'}
    )


@router.delete("/api/output/{path:path}")
async def api_output_delete(path: str):
    """把内容库里的文件或项目目录移进回收站（30 天后自动清除）。系统数据（_login/_analytics/日历/发布记录）受保护。"""
    from easel import trash as _trash
    full = _safe_output_target(path)
    if _is_protected(full):
        raise HTTPException(403, '系统数据受保护，不可从内容库删除')
    is_dir = full.is_dir()
    try:
        moved = _trash.move_to_trash(_core.OUTPUTS_DIR, full)
    except OSError as e:
        raise HTTPException(500, f'删除失败：{e}')
    return {"ok": True, "deleted": path, "kind": "dir" if is_dir else "file", "trash_id": moved["id"]}


@router.post("/api/upload")
async def api_upload(
    files: list[UploadFile] = File(...),
    sessionId: str = Form(...),
):
    """Store chat attachments in a session-scoped inbox and return opaque refs."""
    scope = _core._attachment_scope(sessionId)
    batch = time.strftime('%Y%m%d-') + uuid.uuid4().hex[:6]
    dest = _core.OUTPUTS_DIR / "_inbox" / scope / batch
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXTS:
            raise HTTPException(400, f'不支持的文件类型：{ext or name}')
        data = await f.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f'{name} 超过 {MAX_UPLOAD_MB}MB 上限')
        target = _unique_upload_path(dest, name)
        target.write_bytes(data)
        rel = f"_inbox/{scope}/{batch}/{target.name}"
        saved.append({"id": _core._attachment_id(scope, rel), "name": target.name, "path": rel})
    if not saved:
        raise HTTPException(400, '没有文件')
    return {"ok": True, "files": saved}


@router.get("/api/upload/limits")
async def api_upload_limits():
    """当前上传上限（MB）——前端在文件超限时据此切换到本地复制通道。"""
    return {"ok": True, "max_mb": MAX_UPLOAD_MB}


@router.post("/api/upload/local")
async def api_upload_local(
    files: list[UploadFile] = File(...),
    sessionId: str = Form(...),
):
    """超过上传上限的文件复制通道：1MB 分块流式落盘（不整读进内存）、无大小上限；
    产出与 /api/upload 同构的附件引用（id/name/path），附件校验与清单链路零改动。"""
    scope = _core._attachment_scope(sessionId)
    batch = time.strftime('%Y%m%d-') + uuid.uuid4().hex[:6]
    dest = _core.OUTPUTS_DIR / "_inbox" / scope / batch
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXTS:
            raise HTTPException(400, f'不支持的文件类型：{ext or name}')
        target = _unique_upload_path(dest, name)
        with open(target, 'wb') as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        rel = f"_inbox/{scope}/{batch}/{target.name}"
        saved.append({"id": _core._attachment_id(scope, rel), "name": target.name, "path": rel})
    if not saved:
        raise HTTPException(400, '没有文件')
    return {"ok": True, "files": saved}


def create_router(core):
    """Bind the core app module and return this domain's routes."""
    global _core, UPLOAD_EXTS
    _core = core
    UPLOAD_EXTS = _core.IMAGE_EXTS | _core.VIDEO_EXTS | {
        ".pdf", ".txt", ".md", ".markdown", ".csv", ".json", ".srt", ".vtt",
        ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".mp3", ".wav", ".m4a"}
    return router
