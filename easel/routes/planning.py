"""Trend radar, content calendar and idea bank."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

TREND_SOURCES: dict[str, tuple[str, str | None]] = {
    "weibo": ("https://v2.xxapi.cn/api/weibohot", "https://60s.viki.moe/v2/weibo"),
    "douyin": ("https://v2.xxapi.cn/api/douyinhot", "https://60s.viki.moe/v2/douyin"),
    "zhihu": ("https://api.zhihu.com/topstory/hot-list?limit=50", "https://60s.viki.moe/v2/zhihu"),
    "bilibili": ("https://v2.xxapi.cn/api/bilibilihot", "https://60s.viki.moe/v2/bili"),
    "baidu": ("https://v2.xxapi.cn/api/baiduhot", "https://60s.viki.moe/v2/baidu/hot"),
    "toutiao": ("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                "https://60s.viki.moe/v2/toutiao"),
}
TREND_LABELS = {
    "weibo": "微博",
    "douyin": "抖音",
    "zhihu": "知乎",
    "bilibili": "B站",
    "baidu": "百度",
    "toutiao": "头条",
}
_TREND_CACHE: dict[str, tuple[float, list]] = {}
_HOT_TITLE_KEYS = ("title", "Title", "word", "name", "keyword")
_HOT_HOT_KEYS = ("hot", "hot_value", "HotValue", "hotValue", "num", "detail_text")
_HOT_URL_KEYS = ("url", "Url", "link", "mobil_url")

SCHEDULE_STATUSES = {"idea", "draft", "scheduled", "published"}
SCHEDULE_KINDS = {"content", "event"}
IDEA_STATUSES = {"pending", "doing", "done"}


def _http_get_json(url: str, timeout: int = 8):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _pick_str(data: object, keys: tuple[str, ...]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _trend_web_url(url: str) -> str:
    match = re.fullmatch(r'https?://api\.zhihu\.com/questions/(\d+)', url)
    return f'https://www.zhihu.com/question/{match.group(1)}' if match else url


def _parse_hot(obj: object) -> list[dict]:
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict):
        data = data.get("data") or data.get("list") or []
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if isinstance(item, str):
            if item.strip():
                out.append({"title": item.strip(), "hot": "", "url": ""})
            continue
        if not isinstance(item, dict):
            continue
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        title = _pick_str(item, _HOT_TITLE_KEYS) or _pick_str(target, _HOT_TITLE_KEYS)
        if not title:
            continue
        hot = _pick_str(item, _HOT_HOT_KEYS) or _pick_str(target.get("metrics_area"), ("text",))
        url = _pick_str(item, _HOT_URL_KEYS) or _pick_str(target, _HOT_URL_KEYS)
        out.append({"title": title, "hot": hot, "url": _trend_web_url(url)})
    return out


def _fetch_platform(pf: str) -> list[dict]:
    primary, backup = TREND_SOURCES.get(pf, (None, None))
    for url in (primary, backup):
        if not url:
            continue
        try:
            items = _parse_hot(_http_get_json(url))
            if items:
                return items
        except Exception:
            continue
    return []


async def api_trends(platforms: str = "weibo,douyin,zhihu", limit: int = 12):
    pfs = [p.strip() for p in platforms.split(",") if p.strip() in TREND_SOURCES]
    now = time.time()
    loop = asyncio.get_event_loop()
    result = []
    for pf in pfs:
        c = _TREND_CACHE.get(pf)
        if c and now - c[0] < 300:
            items = c[1]
        else:
            items = await loop.run_in_executor(None, _fetch_platform, pf)
            if items:
                _TREND_CACHE[pf] = (now, items)
            elif c:
                items = c[1]
        result.append({
            "platform": pf,
            "label": TREND_LABELS.get(pf, pf),
            "items": items[:max(1, min(limit, 30))],
            "ok": bool(items),
        })
    return {"trends": result, "updated": int(now)}


def _read_list(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _write_list(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def schedule_file(outputs: Path) -> Path:
    return outputs / "_schedule.json"


def read_schedule(outputs: Path) -> list[dict]:
    return _read_list(schedule_file(outputs))


def write_schedule(outputs: Path, items: list[dict]) -> None:
    _write_list(schedule_file(outputs), items)


class ScheduleItem(BaseModel):
    title: str
    date: str
    platform: str = ""
    time: str = ""
    status: str = "idea"
    note: str = ""
    kind: str = "content"          # content（内容/发布）| event（平台活动/节日/特殊日期）
    url: str = ""                  # 已发布内容链接（可选）
    source: str = "manual"         # manual | publish-page | chat | scheduler
    event_type: str = ""           # event 专属：节日/电商/平台活动/行业
    end_date: str = ""             # event 专属：活动区间结束日


class IdeaItem(BaseModel):
    title: str
    note: str = ""
    source: str = ""
    status: str = "pending"


def create_router(core) -> APIRouter:
    router = APIRouter()
    router.add_api_route("/api/trends", api_trends, methods=["GET"])

    def ideas_file() -> Path:
        return core.OUTPUTS_DIR / "_ideas.json"

    @router.get("/api/schedule")
    async def api_schedule_list():
        return read_schedule(core.OUTPUTS_DIR)

    @router.post("/api/schedule")
    async def api_schedule_create(req: ScheduleItem):
        items = read_schedule(core.OUTPUTS_DIR)
        kind = req.kind if req.kind in SCHEDULE_KINDS else "content"
        st = req.status if req.status in SCHEDULE_STATUSES else "idea"
        item = {
            "id": uuid.uuid4().hex[:12],
            "title": req.title.strip() or ("未命名活动" if kind == "event" else "未命名"),
            "date": req.date,
            "platform": req.platform,
            "time": req.time,
            "status": st,
            "note": req.note,
            "kind": kind,
            "url": req.url,
            "source": req.source if req.source in {"manual", "publish-page", "chat", "scheduler"} else "manual",
            "event_type": req.event_type,
            "end_date": req.end_date,
        }
        items.append(item)
        write_schedule(core.OUTPUTS_DIR, items)
        return item

    @router.put("/api/schedule/{sid}")
    async def api_schedule_update(sid: str, req: ScheduleItem):
        items = read_schedule(core.OUTPUTS_DIR)
        for it in items:
            if it.get("id") == sid:
                kind = req.kind if req.kind in SCHEDULE_KINDS else it.get("kind", "content")
                it.update({
                    "title": req.title.strip() or it.get("title", "未命名"),
                    "date": req.date,
                    "platform": req.platform,
                    "time": req.time,
                    "status": req.status if req.status in SCHEDULE_STATUSES else it.get("status", "idea"),
                    "note": req.note,
                    "kind": kind,
                    "url": req.url,
                    "event_type": req.event_type,
                    "end_date": req.end_date,
                })
                write_schedule(core.OUTPUTS_DIR, items)
                return it
        raise HTTPException(404, "排期不存在")

    @router.delete("/api/schedule/{sid}")
    async def api_schedule_delete(sid: str):
        items = read_schedule(core.OUTPUTS_DIR)
        new = [it for it in items if it.get("id") != sid]
        if len(new) == len(items):
            raise HTTPException(404, "排期不存在")
        write_schedule(core.OUTPUTS_DIR, new)
        return {"ok": True, "deleted": sid}

    @router.get("/api/schedule/context")
    async def api_schedule_context(days: int = 14):
        """规划摘要（发布节奏/断更缺口 + 待发排期 + 临近节点 + 建议）——薄封装 calendar_ops，
        前端页头「近期节点/建议」与 Agent 读回共用同一逻辑。失败返回空摘要不抛错。"""
        cmd = [sys.executable, str(core.SHARED_SCRIPTS / "calendar_ops.py"),
               "--data", str(schedule_file(core.OUTPUTS_DIR)), "context", "--days", str(max(1, min(days, 90)))]
        try:
            proc = subprocess.run(cmd, cwd=str(core.PROJECT_ROOT), env=core._proxy_env(),
                                  capture_output=True, text=True, timeout=20)
            return json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
        except Exception:
            return {}

    @router.get("/api/ideas")
    async def api_ideas_list():
        return _read_list(ideas_file())

    @router.post("/api/ideas")
    async def api_ideas_create(req: IdeaItem):
        items = _read_list(ideas_file())
        st = req.status if req.status in IDEA_STATUSES else "pending"
        item = {
            "id": uuid.uuid4().hex[:12],
            "title": req.title.strip() or "未命名选题",
            "note": req.note,
            "source": req.source,
            "status": st,
            "created": int(time.time()),
        }
        items.insert(0, item)
        _write_list(ideas_file(), items)
        return item

    @router.put("/api/ideas/{iid}")
    async def api_ideas_update(iid: str, req: IdeaItem):
        items = _read_list(ideas_file())
        for it in items:
            if it.get("id") == iid:
                it.update({
                    "title": req.title.strip() or it.get("title", "未命名选题"),
                    "note": req.note,
                    "source": req.source,
                    "status": req.status if req.status in IDEA_STATUSES else it.get("status", "pending"),
                })
                _write_list(ideas_file(), items)
                return it
        raise HTTPException(404, "选题不存在")

    @router.delete("/api/ideas/{iid}")
    async def api_ideas_delete(iid: str):
        items = _read_list(ideas_file())
        new = [it for it in items if it.get("id") != iid]
        if len(new) == len(items):
            raise HTTPException(404, "选题不存在")
        _write_list(ideas_file(), new)
        return {"ok": True, "deleted": iid}

    return router
