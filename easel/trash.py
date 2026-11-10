"""Recycle bin for the content library: deleting moves an item here, it is purged after KEEP_DAYS."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

TRASH_NAME = "_trash"
KEEP_DAYS = 30
_ID = re.compile(r"\d{14}-[0-9a-f]{8}")


def trash_dir(outputs: Path) -> Path:
    return outputs / TRASH_NAME


def tree_size(path: Path, limit: int = 200_000) -> tuple[int, int]:
    """Bytes and file count without following links; stops counting after `limit` files."""
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size, 1
    except OSError:
        return 0, 0
    total = files = 0
    for root, dirs, names in os.walk(path, followlinks=False):
        for name in names:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
            files += 1
            if files >= limit:
                return total, files
    return total, files


def move_to_trash(outputs: Path, full: Path) -> dict:
    outputs = outputs.resolve()
    original = full.relative_to(outputs).as_posix()
    item_id = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    folder = trash_dir(outputs) / item_id
    (folder / "item").mkdir(parents=True)
    kind = "dir" if full.is_dir() and not full.is_symlink() else "file"
    size, count = tree_size(full)
    shutil.move(str(full), str(folder / "item" / full.name))
    meta = {"id": item_id, "name": full.name, "original": original, "kind": kind, "bytes": size, "files": count, "deleted_at": time.time()}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _folder(outputs: Path, item_id: str) -> Path:
    if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
        raise KeyError(item_id)
    folder = trash_dir(outputs) / item_id
    if not (folder / "meta.json").is_file():
        raise KeyError(item_id)
    return folder


def _meta(folder: Path) -> dict | None:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and isinstance(meta.get("original"), str) else None


def list_trash(outputs: Path, now: float | None = None) -> list[dict]:
    """Newest first. Items past KEEP_DAYS are deleted here, so the bin cleans itself when opened."""
    now = time.time() if now is None else now
    root = trash_dir(outputs)
    items = []
    if not root.is_dir():
        return items
    for folder in root.iterdir():
        if not _ID.fullmatch(folder.name):
            continue
        meta = _meta(folder)
        if meta is None:
            continue
        expires = float(meta.get("deleted_at", 0)) + KEEP_DAYS * 86400
        if expires <= now:
            shutil.rmtree(folder, ignore_errors=True)
            continue
        items.append({**meta, "expires_at": expires})
    return sorted(items, key=lambda item: item.get("deleted_at", 0), reverse=True)


def _free_target(target: Path) -> Path:
    if not target.exists() and not target.is_symlink():
        return target
    stem, suffix = (target.stem, target.suffix) if target.suffix and not target.is_dir() else (target.name, "")
    for n in range(1, 1000):
        candidate = target.with_name(f"{stem}（恢复 {n}）{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(str(target))


def restore(outputs: Path, item_id: str) -> dict:
    outputs = outputs.resolve()
    folder = _folder(outputs, item_id)
    meta = _meta(folder)
    if meta is None:
        raise KeyError(item_id)
    target = (outputs / meta["original"]).resolve()
    if target == outputs or outputs not in target.parents or target.relative_to(outputs).parts[0].startswith(("_", ".")):
        raise PermissionError(meta["original"])
    payload = folder / "item" / meta["name"]
    if not payload.exists() and not payload.is_symlink():
        raise KeyError(item_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _free_target(target)
    shutil.move(str(payload), str(target))
    shutil.rmtree(folder, ignore_errors=True)
    return {"id": item_id, "restored": target.relative_to(outputs).as_posix()}


def remove(outputs: Path, item_id: str) -> None:
    shutil.rmtree(_folder(outputs, item_id))


def empty(outputs: Path) -> int:
    count = 0
    root = trash_dir(outputs)
    if root.is_dir():
        for folder in root.iterdir():
            if _ID.fullmatch(folder.name):
                shutil.rmtree(folder, ignore_errors=True)
                count += 1
    return count
