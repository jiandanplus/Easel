"""Easel Web — FastAPI 后端（含 SSE 流式输出）."""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
if os.name == "nt":
    import msvcrt
else:
    import fcntl
import hashlib
import ipaddress
import json
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
import ipaddress
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool
from sse_starlette.sse import EventSourceResponse

RESOURCE_ROOT = Path(os.environ.get("EASEL_RESOURCE_ROOT") or Path(__file__).resolve().parents[1]).expanduser().resolve()
PROJECT_ROOT = Path(os.environ.get("EASEL_WORKSPACE_ROOT") or Path(__file__).resolve().parents[1]).expanduser().resolve()

if str(RESOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(RESOURCE_ROOT))
if str(RESOURCE_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(RESOURCE_ROOT / "scripts"))

from easel.gateway_endpoint import chat_completions_url, healthz_url, port_source
from easel.openclaw_cmd import openclaw_base_cmd
from easel.persona import (load_profile_text, persona_prefix, chat_turn_message,
                           profile_exists, valid_persona_name, _FILE_ORDER)
from easel.timeouts import TIMEOUT_CHAT, TIMEOUT_DIRECT, TIMEOUT_PRODUCE
from easel.direct_api import (DirectAPIError, SYSTEM_PROMPT, api_config, api_mode,
                              history_path, read_settings, stream_chat)
from easel import harnesses
from easel.harnesses import store as harness_store
from easel.harnesses.base import HarnessError, RunRequest
from easel.service_contracts import ServiceRoute, TaskContext, availability
from easel.workbench import WorkbenchState, WorkbenchError, identifier as _workbench_identifier, digest as _workbench_digest
try:
    from easel.gateway_questions import (
        GatewayClient, GatewayQuestionError, GatewayUnsupportedError,
        question_bridge_supported)
except Exception:  # 兼容缺失依赖：问答题桥接降级为关闭
    GatewayClient = None  # type: ignore
    GatewayQuestionError = None  # type: ignore
    GatewayUnsupportedError = None  # type: ignore
    question_bridge_supported = None  # type: ignore

# 问答题桥接的一次性诊断标记：连接失败/旧版本无 question RPC 的告警每进程只打一次，
# 避免每开一个新会话就在后端刷一行同样的错（用户反馈的噪音）。
_QBRIDGE_WARNED: set[str] = set()
# 进程级熔断：一旦确认桥接不可用（旧版本无 question RPC、或 connect 持续失败如
# NOT_PAIRED/scope-upgrade），就彻底停掉桥接，后续每轮直接跳过——不再连接，也就不再
# 每轮在网关上触发新的配对/权限申请。恢复需重启 easel web。
_QBRIDGE_DISABLED = False


def _qbridge_warn_once(key: str, message: str) -> None:
    if key in _QBRIDGE_WARNED:
        return
    _QBRIDGE_WARNED.add(key)
    print(message, file=sys.stderr, flush=True)

PROFILES_DIR = PROJECT_ROOT / "profiles"
SKILLS_DIR = PROJECT_ROOT / "skills"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

STATIC_DIR = Path(__file__).resolve().parent / "static"
REACT_DIR = RESOURCE_ROOT / "web" / "frontend" / "dist"
OPENCLAW_PROFILE = "easel"
# 对话传输层：http＝直连常驻 gateway 的 OpenAI 兼容端点，agent 在 gateway 进程里直接跑，
# 省掉每轮 spawn `openclaw agent` 瘦客户端的冷启动（本机实测同一句话：CLI 7.1-7.6s/轮，
# HTTP 4.1-4.5s/轮，差值就是客户端冷启动）；cli＝每轮 spawn 的老路径。
#
# 默认 http，但**只对新会话生效**：见 _resolve_transport。同一会话绝不中途换边——两条路径
# 写的是不同的 transcript，换边等于静默清空整段对话记忆（实测拿 CLI 去续一个 HTTP 起的会话，
# agent 直接答"无历史"）。设 EASEL_CHAT_TRANSPORT=cli 可整机回到老路径。
CHAT_TRANSPORT = os.environ.get("EASEL_CHAT_TRANSPORT", "http").strip().lower()
# 这里曾有个 OPENCLAW_WORKSPACE 常量，写死的是 2026.6.x 布局（~/.openclaw/workspace-<profile>）。
# 全仓无人引用，但留着迟早会被新代码拿去用，而 OpenClaw 的布局 2026.9.x 起已经变成
# <state 目录>/workspace（issue #19）。要用 workspace 路径请走 easel.openclaw_workspace.workspace_dir()，
# 它直接问 openclaw 要运行时真值，不猜版本。
# OpenClaw 会话历史（transcript）目录：<profile 配置目录>/agents/main/sessions/<session-id>.jsonl
# 配置/状态目录统一走 easel.openclaw_workspace：支持 EASEL_OPENCLAW_STATE_DIR 覆盖（#62）。
from easel.openclaw_workspace import config_path as _oc_config_path, state_dir as _oc_state_dir
OPENCLAW_SESSIONS_DIR = _oc_state_dir() / "agents" / "main" / "sessions"

# 思考档位（每轮 --thinking）。前后端已完整支持展示思考：后端把 thinking_delta 转成 SSE
# `thinking` 事件，前端 MessageBubble 渲染「💭 思考过程」并在流式结束后持久保留。面板里有没有
# 内容取决于网关——支持 extended-thinking 的网关会按档位回传思考流；不支持的（如内网 codewiz，
# 实测 transcript 里 assistant 只有 text 块、thinking 恒为 0）面板留空，调高档位也不会有内容。
# 默认 medium：让支持思考的部署直接显示较完整思考；EASEL_THINKING_LEVEL 可覆盖（low 提速 / high 更详尽）。
THINKING_LEVEL = (os.environ.get("EASEL_THINKING_LEVEL", "").strip() or "medium")

# gateway 进程把原始事件流（token/thinking/收尾）写到的**单个共享文件**。
# 关键：`openclaw agent` 只是瘦客户端，没有 --raw-stream 标志——只有常驻 gateway 按它自己
# 的 OPENCLAW_RAW_STREAM/OPENCLAW_RAW_STREAM_PATH 写这个文件（见 scripts/gateway.sh）。
# web 侧 tail 它做流式；默认值必须与 gateway.sh 里 EASEL_RAW_STREAM_PATH 的默认一致。
SHARED_RAW_STREAM = Path(os.environ.get("EASEL_RAW_STREAM_PATH", "/tmp/easel-raw-stream.jsonl"))


class _GatewayHttpProc:
    """HTTP 直连模式下的「伪进程」：给主循环 / 停止逻辑提供 poll/wait/kill 兼容面。"""

    def __init__(self) -> None:
        self._done = False
        self._task = None

    def finish(self) -> None:
        self._done = True

    def poll(self):
        return 0 if self._done else None

    def terminate(self) -> None:
        self._done = True
        if self._task is not None:
            try:
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):  # 兼容 await asyncio.to_thread(proc.wait, ...)
        # 必须真的等：主循环拿它来决定「能不能放掉会话双锁」。提前返回会让下一轮在
        # 上一轮可能还在写同一份 transcript 时就启动 —— 正是双锁要防的并发写。
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._done:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("gateway-http-turn", timeout)
            time.sleep(0.05)
        return 0


_HTTP_READY_CACHE: dict = {"at": -1e9, "ok": False}
_HTTP_READY_TTL = 60.0          # 探针要打一次真端点，别每轮都付这个 RTT


def _gateway_http_ready(force: bool = False) -> bool:
    """探测 gateway 的 /v1/chat/completions 到底挂没挂上。

    **不能探 /v1/models**：那个路径会被 gateway 控制台 SPA 的 catch-all 接走，端点没开也照样
    返回 200（body 是 OpenClaw Control 的 HTML 首页）—— 探了等于没探，只要网关活着就恒为 True。
    这里直接打真端点：openclaw 要求 gateway.http.endpoints.chatCompletions.enabled === true
    才挂这条路由（默认 false），没开就是 404；开了则因为 body 缺 messages 返回 400。用 400 当
    "端点在"的证据，既走完整条路由又不消耗一次模型调用。
    """
    now = time.monotonic()
    if not force and now - _HTTP_READY_CACHE["at"] < _HTTP_READY_TTL:
        return bool(_HTTP_READY_CACHE["ok"])
    ok = False
    try:
        import httpx  # noqa: F401  HTTP 路径全靠它做 SSE；没装就当端点不可用，回退 CLI
        rq = urllib.request.Request(
            chat_completions_url(), data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(rq, timeout=3):
                ok = False       # 空 body 还给 200 说明这不是我们要的端点，不敢用
        except urllib.error.HTTPError as e:
            ok = e.code == 400   # 400 Missing user message ⇒ 端点在；404 ⇒ 没开
    except Exception:  # noqa: BLE001
        ok = False
    _HTTP_READY_CACHE.update(at=now, ok=ok)
    return ok


def _heal_openclaw_session(sk: str) -> None:
    """每轮 spawn openclaw 前，清洗该会话历史里的无签名 thinking 块 + 空消息（自愈防回放失效）。

    best-effort：任何异常都不阻断对话（清洗失败大不了退回原样，仍可 /new）。
    """
    try:
        import session_heal  # scripts/session_heal.py（已加入 sys.path）
        p = OPENCLAW_SESSIONS_DIR / f"{_openclaw_session_id(sk)}.jsonl"
        if p.is_file():
            st = session_heal.sanitize_history_file(p)
            if st.get("changed"):
                print(f"[session-heal] {p.name}: -{st['thinking_removed']} thinking / "
                      f"-{st['msgs_dropped']} empty", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[session-heal] 跳过（{e}）", file=sys.stderr, flush=True)


# 制作层/直接执行层/chat 超时统一走 easel/timeouts.py（CLI/Web/skill 三入口单一真相源）

SHARED_SCRIPTS = (PROJECT_ROOT if os.environ.get("EASEL_PORTABLE") == "1" else RESOURCE_ROOT) / "skills" / "shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from model_registry import model_group
from media_gateway import (GATEWAY_ENV_KEYS, gateway_managed,
                           gateway_readiness, gateway_settings, gateway_updates,
                           refresh_gateway_environment, validate_gateway_env)

BROWSER_PROFILES = Path.home() / ".easel-browser-profiles"
LOGIN_DIR = OUTPUTS_DIR / "_login"
PUBLISH_DIR = OUTPUTS_DIR / "_publish"   # 异步发布的状态/验证码文件（抖音发布可能触发短信墙）
PROFILE_BUILD_DIR = OUTPUTS_DIR / "_profile_build"   # 异步画像构建的状态文件（避免长请求被代理超时）
DEBUG_DIR = OUTPUTS_DIR / "_debug"   # 诊断日志（对话流收尾情况等），_ 前缀不进内容库
SESSIONS_DIR = OUTPUTS_DIR / "_sessions"   # 每会话最近一轮的完整结果，供 SSE 连接中断后前端取回
# 非 _ 前缀的历史系统目录（归因层数据），内容库不展示（真产物一律在项目目录内）
SYSTEM_TOPLEVEL_DIRS = {"analytics"}
LOGIN_TIMEOUT = 240
LOGIN_PROCESSES: dict[str, subprocess.Popen] = {}

# whoami 真校验（起 headless 浏览器，数秒）的进程内缓存：避免账号页 + 工作台重复起浏览器。
WHOAMI_TTL = 600  # 秒
_WHOAMI_CACHE: dict[str, tuple[float, dict]] = {}
_WHOAMI_LOCK = threading.Lock()
_WHOAMI_TASKS: dict[str, asyncio.Task] = {}
_LOGIN_START_LOCKS: dict[str, asyncio.Lock] = {}

LOGIN_RUNNERS: dict[str, dict] = {
    "xiaohongshu": {"name": "小红书", "backend": "xhs", "profile": "XiaohongshuProfile"},
    "kuaishou": {"name": "快手", "backend": "web", "wp": "kuaishou", "profile": "KuaishouProfile"},
    "weixin-channels": {"name": "微信视频号", "backend": "web", "wp": "weixin-channels", "profile": "ChannelsProfile"},
    "zhihu": {"name": "知乎", "backend": "web", "wp": "zhihu", "profile": "ZhihuProfile"},
    "bilibili": {"name": "B站", "backend": "biliup"},
    "douyin": {"name": "抖音", "backend": "douyin", "profile": "DouyinProfile"},
    # 微信公众号：扫码登录后台会话（发布+数据都走它），backend=='wechat-oa' 在各处单独分支处理。
    "wechat-oa": {"name": "微信公众号", "backend": "wechat-oa"},
}

# ---- 微信公众号（wechat-oa）凭证式接入 ----
# 复用 skill-wechat-publisher 的发布引擎与配置：凭证存在其 wechat-publisher.yaml，
# 发布/取数脚本都从这里读账号。web 侧统一用账号 key "web"。
WECHAT_SKILL_DIR = PROJECT_ROOT / "skills" / "builtin" / "skill-wechat-publisher"
WECHAT_SKILL_SCRIPTS = WECHAT_SKILL_DIR / "scripts"
WECHAT_PUBLISH_SCRIPT = WECHAT_SKILL_SCRIPTS / "publish.py"
WECHAT_CONFIG_YAML = WECHAT_SKILL_DIR / "wechat-publisher.yaml"
WECHAT_WEB_ACCOUNT = "web"   # web 端配置写入/读取的账号 key


def _wechat_load_yaml() -> dict:
    """读 wechat-publisher.yaml（不存在或损坏则返回空 dict）。"""
    if not WECHAT_CONFIG_YAML.is_file():
        return {}
    try:
        import yaml
        data = yaml.safe_load(WECHAT_CONFIG_YAML.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _wechat_web_account() -> dict:
    """返回 web 端配置的公众号账号（accounts.web），无则空 dict。"""
    cfg = _wechat_load_yaml()
    accts = cfg.get("accounts") if isinstance(cfg.get("accounts"), dict) else {}
    acc = accts.get(WECHAT_WEB_ACCOUNT)
    return acc if isinstance(acc, dict) else {}


def _wechat_has_credentials() -> bool:
    acc = _wechat_web_account()
    return bool(acc.get("app_id") and acc.get("app_secret"))


def _wechat_save_credentials(app_id: str, app_secret: str, name: str = "", author: str = "") -> None:
    """把 AppID/AppSecret 写入 wechat-publisher.yaml 的 accounts.web（原子写，保留其它账号）。"""
    import yaml
    cfg = _wechat_load_yaml()
    if not isinstance(cfg.get("accounts"), dict):
        cfg["accounts"] = {}
    acc = cfg["accounts"].get(WECHAT_WEB_ACCOUNT)
    if not isinstance(acc, dict):
        acc = {}
    acc["name"] = name or acc.get("name") or "微信公众号"
    acc["app_id"] = app_id
    acc["app_secret"] = app_secret
    if author:
        acc["author"] = author
    acc.setdefault("author", "")
    cfg["accounts"][WECHAT_WEB_ACCOUNT] = acc
    # web 账号存在即设为默认，方便 CLI 直接用
    cfg.setdefault("default", WECHAT_WEB_ACCOUNT)
    WECHAT_CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
    tmp = WECHAT_CONFIG_YAML.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, WECHAT_CONFIG_YAML)


def _wechat_clear_credentials() -> None:
    """删除 accounts.web 及其 token 缓存。"""
    import yaml
    cfg = _wechat_load_yaml()
    accts = cfg.get("accounts")
    if isinstance(accts, dict) and WECHAT_WEB_ACCOUNT in accts:
        accts.pop(WECHAT_WEB_ACCOUNT, None)
        if cfg.get("default") == WECHAT_WEB_ACCOUNT:
            cfg["default"] = next(iter(accts), "") if accts else ""
        try:
            tmp = WECHAT_CONFIG_YAML.with_suffix(".yaml.tmp")
            tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            os.replace(tmp, WECHAT_CONFIG_YAML)
        except Exception:
            pass
    for cache in (WECHAT_SKILL_SCRIPTS / f".token_cache_{WECHAT_WEB_ACCOUNT}.json",
                  WECHAT_SKILL_SCRIPTS / ".token_cache.json"):
        try:
            cache.unlink()
        except OSError:
            pass


def _wechat_verify_token() -> tuple[bool, str]:
    """用当前 accounts.web 凭证调官方 token 接口验证。返回 (ok, message)。
    在子进程里跑，避免把 skill 的 import 副作用带进 web 进程。"""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from config import set_account\n"
        "from wechat_token import get_access_token\n"
        "set_account(%r)\n"
        "try:\n"
        "    t = get_access_token(force_refresh=True)\n"
        "    print('OK' if t else 'EMPTY')\n"
        "except Exception as e:\n"
        "    print('ERR:' + str(e))\n"
    ) % (str(WECHAT_SKILL_SCRIPTS), WECHAT_WEB_ACCOUNT)
    try:
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(WECHAT_SKILL_SCRIPTS),
                              env=_wechat_env(), capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "验证超时（网络或 IP 白名单问题）"
    out = (proc.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if last == "OK":
        return True, ""
    if last.startswith("ERR:"):
        return False, last[4:].strip()[:200]
    err = (proc.stderr or "").strip().splitlines()[-1:] or ["验证失败"]
    return False, err[0][:200]


def _k(env, label, required=True, secret=True, aliases=None):
    return {"env": env, "label": label, "required": required, "secret": secret, "aliases": aliases or []}


def _model_spec(group: str, label: str | None = None) -> dict:
    spec = model_group(group)
    return {
        "label": label or spec["label"],
        "settings": spec.get("settings", []),
        "providers": spec["providers"],
    }


def _short_drama_spec() -> dict:
    """Image is required; video and cloud voice settings remain optional enhancements."""
    image = model_group("image")
    optional = []
    for group_name in ("video", "voice"):
        group = model_group(group_name)
        optional.extend({**key, "required": False} for key in group.get("settings", []))
        for provider in group["providers"]:
            optional.extend({**key, "required": False} for key in provider["keys"])
    seen = set()
    optional = [key for key in optional if not (key["env"] in seen or seen.add(key["env"]))]
    return {
        "label": "AI 短剧（生图必需 + 生视频/云配音可选）",
        "settings": [],
        "providers": [{
            "id": "drama",
            "name": "关键帧生图（必需）+ 视频生成与闭源配音（可选）",
            "keys": [*image["providers"][0]["keys"], *optional],
        }],
    }

SKILL_API_REQUIREMENTS: dict[str, dict] = {
    "ai-image-gen": _model_spec("image"),
    "ecom-details-image": _model_spec("image", "电商配图（AI 生图）"),
    "ai-video-gen": _model_spec("video"),
    "ai-music": _model_spec("music"),
    "voice-clone": _model_spec("voice", "声音克隆 / 云端 TTS"),
    # AI 短剧：编排 ai-image-gen(关键帧,必需) + ai-video-gen(生视频,可选,缺则退化图片短剧)。
    # 以生图为「已配置」基线（缺生图无法出关键帧）；生视频 key 同框可选填，也可在 ai-video-gen 卡片配。
    "short-drama": _short_drama_spec(),
    # 论文解读：MinerU 与生图均为可选（缺 MinerU 用 pdfplumber 兜底、缺生图用信息图/图表）。
    # 全 key 可选 → 不误报感叹号；但仍进注册表以便就地填 MINERU_API_TOKEN（无其它叶子 skill 承载它）。
    "paper-explainer": {
        "label": "论文解读（MinerU / 生图 均可选）",
        "providers": [
            {
                "id": "paper",
                "name": "MinerU 解析(可选, 缺则 pdfplumber) + 封面/概念生图(可选)",
                "keys": [
                    _k("MINERU_API_TOKEN", "MinerU API Token（可选，缺则用 pdfplumber 兜底）",
                       required=False),
                    _k("IMG_API_KEY", "生图 API Key（可选，用于封面/概念图）", required=False,
                       aliases=["OPENAI_API_KEY", "API_KEY"]),
                    _k("IMG_BASE_URL", "生图 API 根地址（可选）", required=False, secret=False,
                       aliases=["OPENAI_BASE_URL", "OPENAI_API_BASE", "BASE_URL"]),
                ],
            },
        ],
    },
}

_ENV_ALLOWLIST: set[str] = {
    "EASEL_DIRECT_API_BASE_URL", "EASEL_DIRECT_API_KEY", "EASEL_DIRECT_API_MODEL",
}
_ENV_ALLOWLIST.update(GATEWAY_ENV_KEYS)
for _spec in SKILL_API_REQUIREMENTS.values():
    for _key in _spec.get("settings", []):
        _ENV_ALLOWLIST.add(_key["env"])
        _ENV_ALLOWLIST.update(_key.get("aliases", []))
    for _prov in _spec["providers"]:
        for _key in _prov["keys"]:
            _ENV_ALLOWLIST.add(_key["env"])
            _ENV_ALLOWLIST.update(_key.get("aliases", []))

ENV_FILE = PROJECT_ROOT / ".env"
_HARNESS_LOOP = None
_PLACEHOLDER_RE = re.compile(r"replace_me|your[-_]?api[-_]?key|xxx|^\.{3}$|^<.*>$", re.I)

TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts", ".html", ".htm", ".css", ".xml", ".yaml", ".yml", ".srt", ".vtt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """应用生命周期：关机时回收公众号扫码进程（替代已弃用的 on_event）。"""
    global _HARNESS_LOOP
    _HARNESS_LOOP = asyncio.get_running_loop()
    yield
    await _central_platform_router.shutdown()
    tasks = list(_HARNESS_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    _HARNESS_LOOP = None
    _stop_mp_login_on_shutdown()


app = FastAPI(title="Easel", docs_url=None, redoc_url=None, lifespan=_lifespan)

def _local_ports(env_port: str) -> set[str]:
    """本机写守卫放行的端口集合：固定的 7860/7870/5173，加上 `--port`/EASEL_PORT
    实际配置的端口——否则自定义端口跑 Easel 时，前端自己发的写请求（包括发消息
    的 /api/chat/stream）会被 local_write_guard 当跨站请求 403 掉。"""
    return {'7860', '7870', '5173', (env_port or '').strip() or '7860'}


_LOCAL_PORTS = _local_ports(os.environ.get('EASEL_PORT', ''))
_LOCAL_ORIGINS = [f'http://{host}:{port}'
                  for host in ('127.0.0.1', 'localhost') for port in _LOCAL_PORTS]
_LOCAL_ORIGINS += [o.strip() for o in os.environ.get('EASEL_EXTRA_ORIGINS', '').split(',') if o.strip()]
_EXTRA_HOSTS = [h.strip() for h in os.environ.get('EASEL_EXTRA_HOSTS', '').split(',') if h.strip()]

# VSCode/code-server 等远程开发环境常通过 VSCODE_PROXY_URI 这个反代域名访问 Easel
# （真实连接仍是本机回环，代理在同一台机器上转发）；不识别这个域名会让通过代理
# 打开的页面从第一个请求起就被下面的 TrustedHostMiddleware/CORS 拒绝——收紧前
# allow_origins=["*"] 时这条路是通的，别让这次收紧顺带堵死它。
def _proxy_uri_origin(uri: str) -> tuple[str, str] | None:
    """从 VSCODE_PROXY_URI 里取反代域名，返回 (host, origin)；解析不出 host 则 None。"""
    if not uri:
        return None
    parsed = urllib.parse.urlparse(uri)
    if not parsed.hostname:
        return None
    return parsed.hostname, f'{parsed.scheme}://{parsed.hostname}'


_proxy_origin = _proxy_uri_origin(os.environ.get('VSCODE_PROXY_URI', ''))
if _proxy_origin:
    _EXTRA_HOSTS.append(_proxy_origin[0])
    _LOCAL_ORIGINS.append(_proxy_origin[1])


def _loopback_peer(request: Request) -> bool:
    """对端是否来自回环地址。只有「没有 Origin 可判」时才用作判据。"""
    client = request.client
    if client is None:
        return False
    host = client.host or ''
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


app.add_middleware(CORSMiddleware, allow_origins=_LOCAL_ORIGINS, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1'] + _EXTRA_HOSTS)


from easel.platform.transport import http_peer as _platform_http_peer, require_local_http


@app.middleware("http")
async def platform_peer_context(request: Request, call_next):
    token = _platform_http_peer.set(request.client.host if request.client else "")
    try:
        return await call_next(request)
    finally:
        _platform_http_peer.reset(token)


@app.middleware('http')
async def local_write_guard(request: Request, call_next):
    """拦住「别的网站让浏览器替我改本机状态」的写请求，同时不误伤本机调用。

    两条判据，按请求实际形态二选一：
    ① 带 Origin（浏览器跨站发起）：Origin 必须在 _LOCAL_ORIGINS 里，否则 403。
    ② 不带 Origin（curl / 本机脚本 / 测试客户端）：要求对端来自回环地址。

    局限（重要）：这套防护假定 Origin/Host 头不可伪造——对浏览器成立，对裸
    HTTP 客户端不成立。本进程仍监听 0.0.0.0（见文件底部 uvicorn.run，未随
    此中间件收紧，兼容 Docker/远程容器端口转发场景），因此局域网内能直连到
    这个端口的攻击者，只要手写 curl 一起伪造 `Origin`/`Host` 头就能绕过本
    中间件与下面的 TrustedHostMiddleware。这里防的是「浏览器打开恶意网页」
    这一类跨站攻击，不是「网络可达即视为可信」的边界——不要把本服务暴露在
    不受信任的网络（公共 WiFi、无防火墙的公网主机）上。
    """
    origin = request.headers.get('origin')
    if request.url.path == '/api/clipper' and origin and re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
        headers = {
            'Access-Control-Allow-Origin': origin,
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
            'Vary': 'Origin',
        }
        if request.method == 'OPTIONS':
            return JSONResponse({}, headers=headers)
        response = await call_next(request)
        response.headers.update(headers)
        return response
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        allowed = origin in _LOCAL_ORIGINS if origin else _loopback_peer(request)
        if not allowed:
            return JSONResponse({'detail': '仅允许从本机工作台操作。'}, status_code=403)
    return await call_next(request)


def list_personas() -> list[dict]:
    if not PROFILES_DIR.is_dir():
        return []
    result = []
    for d in sorted(PROFILES_DIR.iterdir()):
        if not d.is_dir() or not valid_persona_name(d.name):
            continue
        desc = ''
        identity = d / 'identity.md'
        if identity.is_file():
            for line in identity.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith('#') and not line.startswith('<!--'):
                    desc = line[:80]
                    break
        result.append({'name': d.name, 'description': desc})
    return result


def find_skill(name: str) -> str | None:
    """查找 SKILL，返回完整名或 None。与 CLI skill.py 一致。"""
    cands = [name, f'skill-{name}'] if not name.startswith('skill-') else [name]
    for cand in cands:
        if (SKILLS_DIR / 'builtin' / cand / 'SKILL.md').is_file():
            return cand
    return None


def _parse_skill_md(path: Path) -> tuple[str, str, str]:
    """解析 SKILL.md → (description, layer, body)。body 为去掉 frontmatter 的正文。
    正确处理 YAML 块标量 description（`>-` / `>` / `|` 后跟缩进多行）。"""
    text = path.read_text(encoding='utf-8')
    desc, layer, body = '', '', text
    lines = text.splitlines()
    if not (lines and lines[0].strip() == '---'):
        return desc, layer, body
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end = i
            break
    if end is None:
        return desc, layer, body
    body = '\n'.join(lines[end + 1:]).strip()
    fm = lines[1:end]
    i = 0
    while i < len(fm):
        st = fm[i].strip()
        if st.startswith('layer:'):
            layer = st.split(':', 1)[1].strip().strip('"').strip("'")
            i += 1
        elif st.startswith('description:'):
            val = st.split(':', 1)[1].strip()
            if val and val[0] in '|>':
                block = []
                j = i + 1
                while j < len(fm):
                    if fm[j].strip() == '':
                        block.append('')
                        j += 1
                        continue
                    indent = len(fm[j]) - len(fm[j].lstrip())
                    if indent == 0:
                        break
                    block.append(fm[j].strip())
                    j += 1
                desc = ' '.join(x for x in block if x).strip()
                i = j
            else:
                desc = val.strip('"').strip("'")
                i += 1
        else:
            i += 1
    return desc, layer, body


def get_skills() -> list[dict]:
    env = _read_env()
    result = []
    sd = SKILLS_DIR / 'builtin'
    if sd.is_dir():
        for d in sorted(sd.iterdir()):
            if d.is_dir() and (d / 'SKILL.md').is_file():
                desc, layer, _ = _parse_skill_md(d / 'SKILL.md')
                needs_api = d.name in SKILL_API_REQUIREMENTS
                result.append({
                    'name': d.name,
                    'description': desc,
                    'layer': layer,
                    'needsApi': needs_api,
                    'apiConfigured': _skill_api_configured(d.name, env) if needs_api else True,
                })
    return result


def clean_agent_output(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        c = re.sub(r'\x1b\[[0-9;]*m', '', line)
        if c.startswith('[') and any(t in c[:40] for t in ('[provider-', '[agents/', '[agent/', '[plugins]', '[tools]', '[diagnostic]', '[fetch-', '[heartbeat]', '[health-', '[gateway]')):
            continue
        if c.strip():
            lines.append(c)
    return '\n'.join(lines).strip()


def _proxy_env() -> dict[str, str]:
    """返回带外网代理的环境变量（保护内网直连）。"""
    env = os.environ.copy()
    env.setdefault('EASEL_ROOT', str(PROJECT_ROOT))
    env.setdefault('http_proxy', os.environ.get('EASEL_PROXY', ''))
    env.setdefault('https_proxy', os.environ.get('EASEL_PROXY', ''))
    env.setdefault('no_proxy', 'localhost,127.0.0.1,10.*,*.xiaohongshu.com,*.devops.xiaohongshu.com,*.douyin.com,*.kuaishou.com,*.zhihu.com,*.bilibili.com,*.weixin.qq.com,*.qq.com')
    return env


def _fresh_media_gateway_env(env: dict[str, str]) -> dict[str, str]:
    """Refresh only gateway settings for a new task; vendor aliases stay per-script."""
    return refresh_gateway_environment(env, _read_env())


def _publish_env() -> dict[str, str]:
    """发布子进程 env：在 _proxy_env 基础上禁用脚本侧日历自动记录——
    发布页由 web 自己回流 _schedule.json，脚本再记一次会重复。对话页 Agent 直跑
    脚本时不经过这里，flag 未设 → 脚本自动记录（见 calendar_ops.record_publish）。"""
    env = _proxy_env()
    env['EASEL_CALENDAR_AUTORECORD'] = '0'
    return env


def _wechat_env() -> dict[str, str]:
    """公众号 API 子进程 env，决定微信 API 从哪个 IP 出网（公众号白名单要求固定出口 IP）。

    两种模式：
    - 设了 WECHAT_EGRESS_PROXY（如反向隧道到你本机/固定 IP 中转）→ 让微信 API **走这个代理**出网，
      微信看到的是该代理的公网 IP，白名单加它即可。其它外网仍走公司代理。
    - 未设 → 微信 API 走**直连**（api.weixin.qq.com 进 no_proxy）。注意本机直连出口也是共享 NAT
      轮询池（见 WECHAT_OA_INTEGRATION.md §4），直连仅在出口 IP 恰好固定的机器上可靠。"""
    env = _publish_env()
    wx_hosts = ("api.weixin.qq.com", "mp.weixin.qq.com")
    egress = os.environ.get("WECHAT_EGRESS_PROXY", "").strip()
    if egress:
        # 微信 API 走指定固定出口代理；从 no_proxy 里去掉微信域名，确保不被旁路成直连。
        env["http_proxy"] = env["https_proxy"] = egress
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = egress
        kept = [h for h in env.get("no_proxy", "").split(",") if h and not any(w in h for w in wx_hosts)]
        env["no_proxy"] = ",".join(kept)
        env["NO_PROXY"] = env["no_proxy"]
    else:
        existing = env.get("no_proxy", "")
        env["no_proxy"] = (existing + "," + ",".join(wx_hosts)) if existing else ",".join(wx_hosts)
        env["NO_PROXY"] = env["no_proxy"]
    return env


def _persona_prefix(persona: str | None) -> str:
    """把画像作为消息前缀内联（复用 easel.persona，与 CLI/skill 同源）。"""
    return persona_prefix(persona)


def _read_env() -> dict[str, str]:
    """宽松解析项目根 .env → {KEY: value}。跳过注释与非 KEY=value 行（容忍多行值残行）。"""
    result = {}
    if not ENV_FILE.is_file():
        return result
    for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        key, val = s.split('=', 1)
        key = key.strip()
        if key.isidentifier() or key.replace('-', '_').isidentifier():
            result[key] = val.strip()
    return result


def _is_set(val: str | None) -> bool:
    """非空且非占位符才算真正配置了。"""
    if not val or not val.strip():
        return False
    return not _PLACEHOLDER_RE.search(val.strip())


def _mask(val: str) -> str:
    """脱敏：只留尾 4 位（短值全遮）。"""
    v = val.strip()
    if len(v) <= 4:
        return '••••'
    return '••••' + v[-4:]


# 要从回显文本里抹掉的 .env 键：命中这些词的一律当机密处理
_SECRETISH_RE = re.compile(r'KEY|TOKEN|SECRET|PASSWORD|COOKIE', re.I)


def _web_output_redactor(emit):
    """One transient boundary before Web queues, persisted events and recovery."""
    from easel.harnesses.secrets import StreamRedactor
    from easel.platform.privacy import sensitive_values
    values = [value.strip().strip('"').strip("'")
              for key, value in _read_env().items()
              if _SECRETISH_RE.search(key) and len(value.strip()) >= 8]
    return StreamRedactor(values, emit, additional=lambda: sensitive_values(PROJECT_ROOT))


def _scrub_secrets(text: str) -> str:
    """把 .env 里的机密值从要回显给前端的文本里抹掉。

    子进程 stderr 会被原样带到浏览器，而 openclaw 的报错可能回显配置值；
    按「已知机密的字面量」替换最稳妥，不依赖猜正则能否匹配各家 key 的格式。
    """
    from easel.platform.privacy import sensitive_values
    from easel.harnesses.secrets import StreamRedactor
    out = StreamRedactor(sensitive_values(PROJECT_ROOT), lambda *_: None).text(text)
    for key, val in _read_env().items():
        v = val.strip().strip('"').strip("'")
        # 太短的值（占位符、枚举值如 none/http）替换会误伤正常文本
        if len(v) >= 8 and _SECRETISH_RE.search(key):
            out = out.replace(v, _mask(v))
    return out


def _key_configured(key: dict, env: dict[str, str]) -> bool:
    '某个 key（含别名）是否已配置。'
    if _is_set(env.get(key['env'])):
        return True
    return any(_is_set(env.get(a)) for a in key.get('aliases', []))


def _skill_api_configured(skill: str, env: dict[str, str] | None = None) -> bool:
    'SKILL 是否已具备可用配置：任一 provider 的全部 required key 齐全。'
    spec = SKILL_API_REQUIREMENTS.get(skill)
    if not spec:
        return True
    env = _read_env() if env is None else env
    group = {"ai-image-gen": "image", "ecom-details-image": "image",
             "short-drama": "image", "ai-video-gen": "video",
             "ai-music": "music", "voice-clone": "voice"}.get(skill)
    if group and gateway_managed(group, env):
        return gateway_readiness(group, env)["configured"]
    for prov in spec['providers']:
        if all(_key_configured(k, env) for k in prov['keys'] if k['required']):
            return True
    return False


# .env 的值是裸写 `KEY=value` 的，而 setup.sh:310/450 会 `source .env`。bash 在赋值右侧
# 不做分词和通配，但**照做**命令替换，且元字符会截断赋值另起一条命令 —— 所以 `$(…)`、反引号、
# `;`/`&`/`|`/`(`/`)`/`<`/`>`/空白 都等于任意命令执行。加引号写入不行：scripts/gateway.sh:63
# 用 `sed -n 's/^KEY=//p'` 裸取值，引号会跟着漏进端口号。只能在入口把值的字符集收死。
# 这里能写的全是 API Key / Base URL / 模型 id / 端口，没有一个需要空格或上面那些字符。
_ENV_VALUE_OK = re.compile(r'[A-Za-z0-9_.:/@+\-=~,%?#\[\]]*')


def _guard_env_values(updates: dict[str, str]) -> None:
    """集中拦截 .env 值注入：值里带换行能往 .env 追加任意行，带 `$(…)` 能直接执行命令
    —— 两者都等于任何能调到写 .env 接口的人都能拿到命令执行。键名同理。两个写入口都必须先过这道。"""
    for k, v in updates.items():
        if any(c in (k or '') for c in '\r\n=') or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k or ''):
            raise HTTPException(400, f'非法的配置键名：{(k or "")[:40]!r}')
        v = v or ''
        if any(c in v for c in '\r\n\x00'):
            raise HTTPException(400, f'配置值不能包含换行符：{k}')
        if not _ENV_VALUE_OK.fullmatch(v):
            bad = ''.join(sorted({c for c in v if not _ENV_VALUE_OK.fullmatch(c)}))
            raise HTTPException(400, f'配置值含不允许的字符（{bad!r}）：{k}')


def _is_local_gateway_base(url: str) -> bool:
    """这个供应商当前是不是挂在本地模型网关上。

    网关模式下 openclaw.json 里存的 baseUrl 是 127.0.0.1:8890，真实上游在 easel-models.yaml，
    面板显示/回传的是上游地址 —— 两边天生不相等。凡是拿「baseUrl 变了」当判据的逻辑都得先问过这里，
    否则网关用户每次保存都被判成「换了地址」。
    """
    return str(url or '').startswith('http://127.0.0.1:8890')


# 设置面板里「改了 Base URL 就必须重填 Key」要比对的 .env 键位（槽位 → (base 键, key 键)）。
_SLOT_ENV_KEYS = {
    'direct-api': ('EASEL_DIRECT_API_BASE_URL', 'EASEL_DIRECT_API_KEY'),
    'openai': ('OPENAI_BASE_URL', 'OPENAI_API_KEY'),
    'relay': ('EASEL_LLM_BASE_URL', 'EASEL_LLM_API_KEY'),
    'anthropic': ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY'),
    'siliconflow': ('SILICONFLOW_BASE_URL', 'SILICONFLOW_API_KEY'),
}


def _valid_base_url(u: str) -> bool:
    '整串校验（不是只看开头）：必须是 http(s)://host，且不带控制字符与 URL 内嵌凭据。'
    if any(c in u for c in '\r\n\t\x00') or '@' in u:
        return False
    try:
        p = urllib.parse.urlparse(u)
    except ValueError:
        return False
    return p.scheme in ('http', 'https') and bool(p.hostname)


def _ssrf_safe(u: str) -> bool:
    """自测会带着真 Key 去打这个地址，所以不许指向本机/内网/云元数据——
    这些地址上的服务通常无鉴权，一旦被当成「模型端点」就成了打内网的跳板。"""
    try:
        host = urllib.parse.urlparse(u).hostname or ''
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001  解析不了就当不安全
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _direct_api_enabled() -> bool:
    return _harness_settings()["runtime"] == "api"


def _harness_settings() -> dict:
    try:
        return harness_store.settings(OUTPUTS_DIR, read_settings(ENV_FILE))
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error


def _workbench_state():
    return WorkbenchState(SESSIONS_DIR, PROJECT_ROOT)


def _workbench_contract(config=None):
    """A stable owner/configuration boundary; never a model availability probe."""
    config = dict(config or _harness_settings())
    source = {"config": config}
    runtime = config.get("runtime", "openclaw")
    try:
        if config.get("service_source", "custom") == "subscription":
            if config.get("subscription_backend", "legacy") == "central":
                from easel.platform.central import CentralClient
                client = CentralClient(PROJECT_ROOT)
                with client.store.locked():
                    state = client._state()
            else:
                from easel.platform.client import PlatformClient
                client = PlatformClient(PROJECT_ROOT)
                with client.store.locked():
                    state = client.store.read()
            auth = json.loads(json.dumps(state.get("auth") or {}))
            for field in ("access_token", "refresh_token", "expires_at", "refresh_expires_at"):
                auth.pop(field, None)
            source["identity"] = {"url": state.get("url"), "auth": auth}
        elif runtime == "pi":
            from easel.harnesses.provider import read_provider
            source["provider"] = read_provider(PROJECT_ROOT)
        elif runtime == "api":
            source["provider"] = {key: value for key, value in read_settings(ENV_FILE).items()
                                  if key.startswith("EASEL_DIRECT_API_")}
        elif runtime == "codex":
            if harnesses.describe(runtime).get("origin") == "bundled":
                from easel.harnesses.provider import read_provider
                source["provider"] = read_provider(PROJECT_ROOT)
            else:
                from easel.chat_models import native_config_binding
                source["native"] = native_config_binding({**os.environ, **read_settings(ENV_FILE)})
        elif runtime == "openclaw":
            path = _oc_config_path()
            source["native"] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        build = hashlib.sha256(Path(__file__).read_bytes()
                               + ((REACT_DIR / "index.html").read_bytes() if (REACT_DIR / "index.html").is_file() else b"")).hexdigest()
        return _workbench_state().contract(source, build)
    except (WorkbenchError, HarnessError, OSError):
        raise HTTPException(503, "工作台状态暂不可用") from None


def _chat_backend(session_id: str | None) -> str:
    selected = "api" if _direct_api_enabled() else _harness_settings()["runtime"]
    if not session_id:
        return selected
    try:
        record = harness_store.binding(SESSIONS_DIR, session_id)
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error
    owner = record.get("runtime")
    if not owner:
        if history_path(SESSIONS_DIR, session_id).is_file():
            owner = "api"
        elif ((OPENCLAW_SESSIONS_DIR / f"{_openclaw_session_id(session_id)}.jsonl").is_file()
              or (OPENCLAW_SESSIONS_DIR / f"{_openclaw_session_id(f'web:{session_id}')}.jsonl").is_file()
              or _transport_pin_file(session_id).is_file()
              or (selected != "api" and _turn_file(f"web:{session_id}").is_file())):
            owner = "openclaw"
    if selected == "api" and owner == "openclaw":
        return selected
    if owner and owner != selected:
        raise HTTPException(409, f"此会话属于 {owner}，当前选择 {selected}；请新建会话，原历史不变")
    return selected


def _model_target_allowed(base: str, slot: str = "") -> bool:
    # Trust only the exact gateway configured by the user, not arbitrary private URLs.
    settings = read_settings(ENV_FILE)
    configured = settings.get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/")
    if slot == "direct-api" and _direct_api_enabled() and base.rstrip("/") == configured:
        try:
            api_config(settings, require_model=False)
            return True
        except DirectAPIError:
            return False
    return _ssrf_safe(base)


def _write_env(updates: dict[str, str]) -> None:
    '就地更新命中的 KEY、其余行原样保留，未命中的追加末尾；空串则删除该行。原子写。'
    updates = {k: v for k, v in updates.items() if k in _ENV_ALLOWLIST}
    _guard_env_values(updates)
    if set(updates).intersection(GATEWAY_ENV_KEYS):
        current = _read_env()
        try:
            # An explicitly re-entered key authorizes retaining the same key at a
            # new address. Without one, validate against the previous endpoint.
            previous = {**current, "NEW_API_KEY": ""} if updates.get("NEW_API_KEY", "").strip() else current
            validate_gateway_env({**current, **updates}, previous=previous)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
    if not updates:
        return
    lines = ENV_FILE.read_text(encoding='utf-8').splitlines() if ENV_FILE.is_file() else []
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        matched = None
        if s and not s.startswith('#') and '=' in s:
            k = s.split('=', 1)[0].strip()
            if k in updates:
                matched = k
        if matched is not None:
            seen.add(matched)
            val = updates[matched]
            if val.strip() == '':
                continue
            out.append(f'{matched}={val}')
            continue
        out.append(line)
    appended = [f'{k}={v}' for k, v in updates.items() if k not in seen and v.strip() != '']
    if appended:
        if out and out[-1].strip() != '':
            out.append('')
        out.append('# ---- Easel API keys (added via Web) ----')
        out.extend(appended)
    tmp = ENV_FILE.with_suffix('.env.tmp')
    tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
    tmp.replace(ENV_FILE)


def _api_spec_status(skill: str, env: dict[str, str]) -> dict:
    '返回注册表项 + 每个 key 当前配置状态与脱敏值（不回传明文）。'
    spec = SKILL_API_REQUIREMENTS[skill]

    def key_status(k: dict) -> dict:
        raw = env.get(k['env'], '')
        return {
            'env': k['env'],
            'label': k['label'],
            'required': k['required'],
            'secret': k['secret'],
            'choices': list(k.get('choices', [])),
            'configured': _key_configured(k, env),
            'masked': _mask(raw) if k['secret'] and _is_set(raw) else (raw if not k['secret'] else ''),
        }

    providers = []
    for prov in spec['providers']:
        keys = [key_status(k) for k in prov['keys']]
        providers.append({'id': prov['id'], 'name': prov['name'], 'keys': keys})
    return {
        'label': spec['label'],
        'settings': [key_status(k) for k in spec.get('settings', [])],
        'providers': providers,
    }


def _agent_thinking_level() -> str:
    try:
        data = json.loads(_oc_config_path().read_text(encoding="utf-8"))
        agents = data.get("agents", {})
        reference = agents.get("defaults", {}).get("model", {})
        for agent in agents.get("list", []):
            if isinstance(agent, dict) and agent.get("id") == "main" and agent.get("model"):
                reference = agent["model"]
                break
        primary = reference.get("primary", "") if isinstance(reference, dict) else reference
        if not isinstance(primary, str) or "/" not in primary:
            return THINKING_LEVEL
        provider, model_id = primary.split("/", 1)
        models = data.get("models", {}).get("providers", {}).get(provider, {}).get("models", [])
        for model in models:
            if isinstance(model, dict) and model.get("id") == model_id:
                return "off" if model.get("reasoning") is False else THINKING_LEVEL
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return THINKING_LEVEL


class _SyncOutcome(str):
    """Public string compatibility with explicit internal execution truth."""
    def __new__(cls, text, *, clean_end=True, stop_reason="completed"):
        result = super().__new__(cls, text)
        result.clean_end = clean_end
        result.stop_reason = stop_reason
        return result


def run_agent_sync(msg: str, timeout: int = TIMEOUT_DIRECT, session_id: str | None = None,
                   context: TaskContext | None = None) -> str:
    config = _harness_settings() if context is None else None
    if context is None:
        runtime = _chat_backend(session_id)
        context = _task_context(ChatRequest(message=msg, sessionId=session_id), runtime, config)
    try:
        context.require_available()
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error
    direct = context.runtime == "api"
    if direct:
        raise HTTPException(409, "API 直连模式支持聊天；此操作需要具备文件和技能执行工具的 Agent 运行时")
    sk = session_id or f'web-{int(time.time() * 1000)}'
    runtime = context.runtime
    if runtime not in ("api", "openclaw"):
        async def execute():
            request = ChatRequest(message=msg, sessionId=sk)
            errors = []
            def emit(kind, text):
                if kind == "error":
                    errors.append(text)
            result = await _run_harness_chat(request, sk, msg, runtime, _harness_settings(), emit,
                                             timeout, interactive=False, context=context)
            if errors:
                error = HTTPException(502, errors[0])
                error.stop_reason = getattr(result, "stop_reason", "error")
                raise error
            return result
        if _HARNESS_LOOP is not None and _HARNESS_LOOP.is_running():
            return asyncio.run_coroutine_threadsafe(execute(), _HARNESS_LOOP).result(timeout + 15)
        return asyncio.run(execute())
    _scrub_secrets("")  # register retained account values before the child can read/rotate them
    _heal_openclaw_session(sk)   # 清洗历史里无签名 thinking 块，防回放失效
    # 钉死 --session-id 让 OpenClaw 每轮续同一 transcript（防跨天空闲后新起空会话丢历史，见 _openclaw_session_id）
    cmd = openclaw_base_cmd() + ['--profile', OPENCLAW_PROFILE, 'agent', '--agent', 'main',
           '--session-key', f'agent:main:{sk}', '--session-id', _openclaw_session_id(sk),
           '--thinking', _agent_thinking_level(),
           '--timeout', str(timeout), '--message', msg]
    # 跨进程锁：同一会话同时刻只跑一个 openclaw，防并发 takeover 崩溃（rc=1）
    xlock = _CrossProcLock(sk)
    if not xlock.acquire(timeout=min(timeout, 300)):
        return _SyncOutcome('⏳ 这个会话正在另一个窗口运行，请稍候再试', clean_end=False, stop_reason='session_lock_timeout')
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=timeout + 30, env=_proxy_env())
        out = _scrub_secrets(clean_agent_output(r.stdout or ''))
        # 以前这里直接 `or '（无输出）'`：returncode 与 stderr 全部丢弃，于是子进程的
        # 任何失败（参数不被当前 OpenClaw 版本支持、配置校验不过、provider 报错）都长
        # 成同一句「（无输出）」，排查只能靠手动把整条命令行复现一遍。
        # 实测 `--session-key` 在老版 OpenClaw 上不存在，就是被这里藏掉一整轮的。
        err = _scrub_secrets(clean_agent_output(r.stderr or ''))
        if r.returncode != 0:
            detail = _scrub_secrets(err)[-400:].strip() or f'退出码 {r.returncode}'
            return _SyncOutcome(f'❌ agent 执行失败（rc={r.returncode}）：{detail}', clean_end=False, stop_reason='error')
        if out:
            return _SyncOutcome(out)
        # rc=0 但没有任何产出：stderr 里通常有线索（如模型拒答、工具被拦）
        if err:
            return f'（无输出）\n\n{_scrub_secrets(err)[-400:].strip()}'
        return '（无输出）'
    except subprocess.TimeoutExpired:
        return _SyncOutcome('⏱️ 请求超时', clean_end=False, stop_reason='timeout')
    except Exception as e:
        return _SyncOutcome(f'❌ {_scrub_secrets(str(e))}', clean_end=False, stop_reason='error')
    finally:
        xlock.release()


def check_gateway() -> bool:
    try:
        # 端口不能写死：Easel 用 --profile easel，OpenClaw 对非默认 profile 会走
        # 20000 + fnv1a32(profile) % 40000（easel → 37289），见 easel/gateway_endpoint.py。
        with urllib.request.urlopen(healthz_url(), timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _file_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTS:
        return 'image'
    if ext in VIDEO_EXTS:
        return 'video'
    if ext in AUDIO_EXTS:
        return 'audio'
    if ext in TEXT_EXTS:
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
    root = OUTPUTS_DIR.resolve()
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
        with safe_snapshot(mf, OUTPUTS_DIR) as snapshot:
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
    if not OUTPUTS_DIR.is_dir():
        return []
    items = []
    for e in sorted(OUTPUTS_DIR.iterdir()):
        # 内容库只展示「项目目录」：跳过系统目录(_login/_publish/...)、隐藏项、
        # 以及根目录散文件（按新规约产物必在项目目录内，根散文件=系统状态/残渣）。
        if e.name.startswith('.') or e.name.startswith('_') or _private_output_path(e):
            continue
        if not e.is_dir() or e.name in SYSTEM_TOPLEVEL_DIRS:
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
    if _private_output_path(OUTPUTS_DIR / rel):
        raise HTTPException(403, '运行时私有数据不可通过产物接口访问')
    full = (OUTPUTS_DIR / rel).resolve()
    root = OUTPUTS_DIR.resolve()
    if root != full and root not in full.parents:
        raise HTTPException(403, '非法路径')
    if not full.is_file():
        raise HTTPException(404, '文件不存在')
    return full


@app.get("/")
async def index():
    no_cache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
    react_index = REACT_DIR / "index.html"
    if react_index.is_file():
        return FileResponse(react_index, media_type="text/html", headers=no_cache)
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers=no_cache)


@app.get("/onepage")
async def onepage():
    return FileResponse(STATIC_DIR / "onepage.html", media_type="text/html")


@app.get("/assets/{path:path}")
async def react_assets(path: str):
    base = (REACT_DIR / "assets").resolve()
    fp = (REACT_DIR / "assets" / path).resolve()
    if base != fp and base not in fp.parents:
        raise HTTPException(403, "非法路径")
    if not fp.is_file():
        raise HTTPException(404)
    return FileResponse(fp, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/static/{path:path}")
async def static_file(path: str):
    base = STATIC_DIR.resolve()
    fp = (STATIC_DIR / path).resolve()
    if base != fp and base not in fp.parents:
        raise HTTPException(403, "非法路径")
    if not fp.is_file():
        raise HTTPException(404)
    # HTML entrypoints must not be cached: the intro page is edited in-place during local development.
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"} if fp.suffix.lower() in {".html", ".htm"} else {}
    return FileResponse(fp, headers=headers)


@app.get("/api/status")
async def api_status():
    direct = _direct_api_enabled()
    config = _harness_settings()
    runtime = config["runtime"]
    workbench = _workbench_contract(config)
    if config.get("service_source", "custom") == "subscription":
        state = await _subscription_readiness(config)
        return {"gateway": state["ready"], "transport": runtime, "runtime": runtime,
                "runtime_detail": state.get("detail", "订阅配置待完善"),
                "skills": get_skills(), "personas": list_personas(), "authVerified": False,
                "workbench": workbench}
    ready = False
    runtime_detail = ""
    if direct:
        try:
            api_config(read_settings(ENV_FILE))
            ready = True
        except DirectAPIError:
            pass
    elif runtime == "openclaw":
        ready = await asyncio.to_thread(check_gateway)
    else:
        descriptor = harnesses.describe(runtime)
        ready = descriptor["installed"]
        if runtime == "pi" and not ready:
            runtime_detail = "内置 Pi 运行时不可用，请重新安装完整应用"
        if runtime == "pi" or (runtime == "codex" and descriptor.get("origin") == "bundled"):
            from easel.harnesses.provider import provider_status
            ready = bool(ready and provider_status(PROJECT_ROOT)["configured"])
            if runtime == "pi" and descriptor["installed"]:
                runtime_detail = "内置 Pi 供应商已配置；认证与模型可用性待验证" if ready else "内置 Pi 供应商未配置"
    return {"gateway": ready, "transport": "api" if direct else (
                CHAT_TRANSPORT if runtime == "openclaw" else runtime), "runtime": runtime,
            "runtime_detail": runtime_detail, "skills": get_skills(), "personas": list_personas(),
            "workbench": workbench}


@app.get("/api/settings/bootstrap")
async def api_settings_bootstrap():
    """首次打开时的自检：模型配好了吗、网关活着吗、上次安装留了什么没做完。

    安装脚本不再在终端问 API Key（改为引导到这里配），所以前端需要一个可靠的
    「还缺什么」判据。modelConfigured 必须同时满足两件事，这正是 doctor 里那条
    「全绿却对话报错」防线：
      - .env 里有可用的认证（_env_key_valid，带占位符识别）；
      - primary 指向的 provider 在 openclaw.json 里真的配了认证
        （_primary_model_routable）—— 只查 .env 查不出 provider 一个字没写的情况。
    """
    direct = _direct_api_enabled()
    config = _harness_settings()
    runtime = config["runtime"]
    if config.get("service_source", "custom") == "subscription":
        state = await _subscription_readiness(config)
        ready = state["ready"]
        return {"modelConfigured": state["configured"], "envKeyOk": state["authenticated"], "routeOk": ready,
                "routeDetail": state["detail"], "gatewayUp": ready, "openclawConfigExists": False,
                "installWarnings": [], "runtime": runtime, "authVerified": False}
    if runtime not in ("api", "openclaw"):
        descriptor = harnesses.describe(runtime)
        installed = descriptor["installed"]
        if runtime == "pi" or (runtime == "codex" and descriptor.get("origin") == "bundled"):
            from easel.harnesses.provider import provider_status
            configured = provider_status(PROJECT_ROOT)["configured"]
            ready = bool(installed and configured)
            detail = ("内置供应商本地设置已完成；尚未验证上游认证，请发送消息验证模型可用性"
                      if ready else "请在设置中配置内置供应商的地址、模型与 API Key")
            if not installed:
                detail += "；内置运行时不可用，请重新安装完整 Easel 应用"
            return {"modelConfigured": ready, "envKeyOk": configured, "routeOk": ready,
                    "routeDetail": detail, "gatewayUp": ready, "openclawConfigExists": False,
                    "installWarnings": [], "runtime": runtime, "authVerified": False}
        return {"modelConfigured": installed, "envKeyOk": installed, "routeOk": installed,
                "routeDetail": "CLI 已安装；请在终端完成原生登录，并发送消息验证模型可用性",
                "gatewayUp": installed, "openclawConfigExists": False, "installWarnings": [],
                "runtime": runtime, "authVerified": False}
    env_ok = False
    route_ok = False
    route_detail = ''
    try:
        if direct:
            api_config(read_settings(ENV_FILE))
            env_ok = route_ok = True
        else:
            from easel.commands.doctor import _env_key_valid, _primary_model_routable
            env_ok = bool(_env_key_valid())
            route_ok, route_detail = _primary_model_routable()
    except DirectAPIError as error:
        route_detail = str(error)
    except Exception as e:  # noqa: BLE001
        route_detail = f'自检失败：{e}'

    warnings: list[dict] = []
    try:
        rep = PROJECT_ROOT / 'outputs' / '_install' / 'last-install.json'
        if rep.is_file():
            warnings = json.loads(rep.read_text(encoding='utf-8')).get('warnings') or []
    except Exception:  # noqa: BLE001
        warnings = []

    return {
        'modelConfigured': bool(env_ok and route_ok),
        'envKeyOk': env_ok,
        'routeOk': bool(route_ok),
        'routeDetail': route_detail,
        'gatewayUp': bool(route_ok) if direct else bool(check_gateway()),
        'openclawConfigExists': _oc_config_path().is_file(),
        'installWarnings': warnings,
    }


@app.post("/api/gateway/restart")
async def api_gateway_restart():
    """重启本机 gateway，让刚保存的模型配置立刻生效。

    为什么需要：默认传输层是 http（见 CHAT_TRANSPORT），对话走的是一个常驻 gateway，
    由 setup.sh 启动一次。配置改了之后它是否重新读 openclaw.json 取决于 OpenClaw 版本，
    不是一条该押在安装流程上的假设。

    安全性：web/app.py 绑 0.0.0.0 且无鉴权，所以这个端点刻意做成**无参数**，argv
    完全固定（复用 easel.commands.gateway 的同一条分发），不接受任何外部输入拼进命令。
    """
    if _direct_api_enabled():
        return {'ok': True, 'note': 'API 直连配置下一轮生效，无需重启 OpenClaw'}
    if _harness_settings()["runtime"] != "openclaw":
        raise HTTPException(409, "当前 Agent 使用独立按轮启动的进程，不需要重启 OpenClaw 网关")
    script = PROJECT_ROOT / 'scripts' / ('gateway.ps1' if os.name == 'nt' else 'gateway.sh')
    if not script.is_file():
        raise HTTPException(500, '找不到 gateway 启动脚本')
    cmd = (['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), 'restart']
           if os.name == 'nt' else ['bash', str(script), 'restart'])
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True,
                                   text=True, timeout=120, env=_proxy_env()),
        )
    except subprocess.TimeoutExpired:
        # 超时返回提示而不是 500：网关可能只是起得慢，用户刷新一下就好。
        return {'ok': False, 'note': '重启超时；可稍后刷新，或手动运行 bash scripts/gateway.sh restart'}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'note': f'重启失败：{e}'}
    up = check_gateway()
    tail = clean_agent_output(r.stdout or '')[-200:]
    return {'ok': bool(up), 'gatewayUp': bool(up),
            'note': tail or ('网关已重启' if up else '网关未就绪，请查看 /tmp/easel-gateway.log')}


@app.get("/api/personas")
async def api_personas():
    return list_personas()


@app.get("/api/persona/{name}")
async def api_persona(name: str):
    text = load_profile_text(name)
    if not text:
        raise HTTPException(404, "画像不存在")
    return {"name": name, "content": text}


def _valid_persona_name(name: str) -> bool:
    return valid_persona_name(name)


def _persona_file_path(name: str, filename: str) -> Path:
    """校验画像名/文件名，返回 profiles/<name>/<filename> 的安全路径。"""
    if not _valid_persona_name(name):
        raise HTTPException(400, "画像名非法")
    if not filename.endswith(".md") or "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, "文件名非法")
    pd = (PROFILES_DIR / name).resolve()
    fp = (pd / filename).resolve()
    if pd != fp.parent or PROFILES_DIR.resolve() not in pd.parents:
        raise HTTPException(403, "非法路径")
    return fp


@app.get("/api/persona/{name}/files")
async def api_persona_files(name: str):
    """返回画像六维文件原文（按固定顺序 + 其余 .md），供在线编辑。"""
    if not profile_exists(name):
        raise HTTPException(404, "画像不存在")
    pd = PROFILES_DIR / name
    ordered = list(_FILE_ORDER) + sorted(f.name for f in pd.glob("*.md") if f.name not in _FILE_ORDER)
    files = []
    for fn in ordered:
        fp = pd / fn
        files.append({"filename": fn, "content": fp.read_text(encoding="utf-8") if fp.is_file() else ""})
    return {"name": name, "files": files}


class PersonaFileRequest(BaseModel):
    filename: str
    content: str


@app.put("/api/persona/{name}/file")
async def api_persona_file_save(name: str, req: PersonaFileRequest):
    """保存画像单个维度文件（原子写）。"""
    if not profile_exists(name):
        raise HTTPException(404, "画像不存在")
    fp = _persona_file_path(name, req.filename)
    tmp = fp.with_suffix(".md.tmp")
    tmp.write_text(req.content, encoding="utf-8")
    tmp.replace(fp)
    return {"ok": True, "filename": req.filename}


@app.delete("/api/persona/{name}")
async def api_persona_delete(name: str):
    """删除整个画像目录。"""
    if not _valid_persona_name(name):
        raise HTTPException(400, "画像名非法")
    pd = (PROFILES_DIR / name).resolve()
    if PROFILES_DIR.resolve() not in pd.parents or not pd.is_dir():
        raise HTTPException(404, "画像不存在")
    import shutil
    shutil.rmtree(pd)
    return {"ok": True, "deleted": name}


@app.get("/api/skills")
async def api_skills():
    return get_skills()


@app.get("/api/skill/{name}")
async def api_skill_detail(name: str):
    """单个 SKILL 详情：描述 + 正文 + API 需求与当前配置状态（脱敏）。"""
    full = find_skill(name)
    if full is None:
        raise HTTPException(404, f"SKILL '{name}' 不存在")
    desc, layer, body = _parse_skill_md(SKILLS_DIR / "builtin" / full / "SKILL.md")
    needs_api = full in SKILL_API_REQUIREMENTS
    env = _read_env()
    return {
        "name": full,
        "layer": layer,
        "description": desc,
        "body": body,
        "needsApi": needs_api,
        "apiConfigured": _skill_api_configured(full, env) if needs_api else True,
        "apiSpec": _api_spec_status(full, env) if needs_api else None,
    }


class EnvUpdateRequest(BaseModel):
    updates: dict[str, str]


@app.get("/api/settings/media-gateway")
async def api_media_gateway_settings():
    """Saved media routes and configuration readiness; never an inference test."""
    return gateway_settings(_read_env())


@app.put("/api/settings/media-gateway")
async def api_media_gateway_save(req: Request):
    try:
        payload = await req.json()
        updates = gateway_updates(payload, _read_env())
    except (ValueError, TypeError):
        # Keep malformed input and credentials out of diagnostics.
        raise HTTPException(400, "统一媒体网关设置无效，请检查模式、地址、模型与密钥；更换地址需重新填写密钥，清除密钥前须全部切回自定义") from None
    try:
        _guard_env_values(updates)
    except HTTPException:
        raise HTTPException(400, "统一媒体网关设置包含不允许的字符") from None
    _write_env(updates)
    return {"ok": True, **gateway_settings(_read_env())}


@app.post("/api/env")
async def api_env_save(req: EnvUpdateRequest):
    """写 API key 到项目根 .env（仅允许注册表内 env 名）。返回更新后各 skill 的配置状态。"""
    bad = [k for k in (req.updates or {}) if k not in _ENV_ALLOWLIST]
    if bad:
        raise HTTPException(400, f"不允许写入的变量：{', '.join(bad)}")
    _write_env(req.updates or {})
    env = _read_env()
    return {
        "ok": True,
        "skills": {s: _skill_api_configured(s, env) for s in SKILL_API_REQUIREMENTS},
    }


# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 环境安装（skills/shared/scripts/install_tool.py 引擎桥）
# 体检走引擎 check --json；安装后台跑、前端轮询 /api/env/job/{id}。
# ═══════════════════════════════════════════════════════════════════════

INSTALL_TOOL = SHARED_SCRIPTS / "install_tool.py"
_ENV_TOOLS_CACHE: dict = {"ts": 0.0, "data": None}
_ENV_JOBS: dict[str, dict] = {}


@app.get("/api/env/tools")
async def api_env_tools(refresh: bool = False):
    """环境体检：引擎 check --json（15 秒缓存；refresh=1 强制重测）。"""
    if not refresh and _ENV_TOOLS_CACHE["data"] and time.time() - _ENV_TOOLS_CACHE["ts"] < 15:
        return _ENV_TOOLS_CACHE["data"]
    try:
        proc = await asyncio.to_thread(lambda: subprocess.run(
            [sys.executable, str(INSTALL_TOOL), "--json", "check"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=240, cwd=str(PROJECT_ROOT)))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "环境体检超时，请稍后再试")
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise HTTPException(500, f"环境体检失败：{(proc.stderr or '').strip()[-300:] or '无输出'}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"体检输出解析失败：{e}")
    data["cachedAt"] = int(time.time())
    _ENV_TOOLS_CACHE.update({"ts": time.time(), "data": data})
    return data


class EnvInstallRequest(BaseModel):
    id: str


_INSTALL_IDS_CACHE: dict = {"ts": 0.0, "ids": frozenset()}


def _install_tool_ids() -> frozenset[str]:
    """引擎里真实存在的配方 id（缓存 5 分钟）。安装接口只认这张表里的 id——
    只按正则放行的话，`--help` 这类带横线的串会被 argparse 当选项吃掉。"""
    if _INSTALL_IDS_CACHE["ids"] and time.time() - _INSTALL_IDS_CACHE["ts"] < 300:
        return _INSTALL_IDS_CACHE["ids"]
    try:
        p = subprocess.run([sys.executable, str(INSTALL_TOOL), "--json", "list"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, cwd=str(PROJECT_ROOT))
        ids = frozenset(t["id"] for t in (json.loads(p.stdout).get("tools") or []) if t.get("id"))
    except Exception:  # noqa: BLE001
        return _INSTALL_IDS_CACHE["ids"]
    if ids:
        _INSTALL_IDS_CACHE.update({"ts": time.time(), "ids": ids})
    return ids


@app.post("/api/env/install")
async def api_env_install(req: EnvInstallRequest):
    """后台安装（引擎 install）：立即返回 jobId，前端轮询进度。"""
    if os.environ.get("EASEL_PORTABLE") == "1":
        raise HTTPException(409, "内置核心试验包不支持自动安装扩展依赖；请使用源码环境配置媒体、浏览器或发布工具")
    tid = (req.id or "").strip()
    known = await asyncio.to_thread(_install_tool_ids)
    if tid not in known:
        raise HTTPException(400, f"无效的工具 id：{tid[:40]!r}")
    job_id = uuid.uuid4().hex[:16]
    if len(_ENV_JOBS) >= 200:                   # 任务表不能无限长：清掉最老的已结束任务
        for k, _ in sorted((kv for kv in _ENV_JOBS.items() if kv[1].get("ended")),
                           key=lambda kv: kv[1]["ended"])[:100]:
            _ENV_JOBS.pop(k, None)
    job = {"jobId": job_id, "id": tid, "state": "running", "lines": [],
           "result": None, "started": int(time.time()), "ended": None}
    _ENV_JOBS[job_id] = job

    def _run() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                [sys.executable, str(INSTALL_TOOL), "--json", "install", tid],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT))
            # stdout 必须**并发**抽干：串行地先读完 stderr 再读 stdout，子进程一旦往 stdout
            # 写满管道缓冲（64K）就会阻塞，而我们还堵在 stderr 上——双向死锁。
            _out: list[str] = []
            t_out = threading.Thread(target=lambda: _out.append(proc.stdout.read() or ""), daemon=True)
            t_out.start()
            for line in proc.stderr:            # 引擎进度日志走 stderr
                line = line.rstrip()
                if line:
                    job["lines"] = (job["lines"] + [line])[-40:]   # 只留最近 40 行（换列表，避免读端正在序列化时被就地改）
            try:
                proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:   # 卡死的安装进程必须真的杀掉，否则线程与子进程永久泄漏
                proc.kill()
                proc.wait(timeout=30)
                raise
            t_out.join(timeout=30)
            out = ("".join(_out)).strip()
            if out:
                job["result"] = (json.loads(out).get("results") or [None])[0]
            job["state"] = "ok" if (job["result"] or {}).get("state") == "ok" else "fail"
        except Exception as e:  # noqa: BLE001
            job["state"] = "fail"
            job["result"] = {"id": tid, "state": "fail", "detail": f"{type(e).__name__}: {e}"}
        finally:
            job["ended"] = int(time.time())
            _ENV_TOOLS_CACHE["ts"] = 0.0        # 装完让下一次体检不吃旧缓存

    threading.Thread(target=_run, daemon=True).start()
    return {"jobId": job_id, "id": tid, "state": "running"}


@app.get("/api/env/job/{job_id}")
async def api_env_job(job_id: str):
    """查安装进度：running / ok / fail + 最近日志行 + 最终结果。"""
    job = _ENV_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在（服务可能重启过）")
    return job


# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 模型配置（v1：按真实 .env / openclaw.json 只读展示 + 真自测）
# ═══════════════════════════════════════════════════════════════════════


def _mask_key(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    return f"«{v[:5]}…{v[-4:]}»" if len(v) > 14 else "«已配置»"


def _model_channels(runtime: str | None = None) -> dict:
    env = _read_env()
    primary = ""
    direct = runtime == "api" if runtime else _direct_api_enabled()
    try:
        oc = _oc_config_path()
        if not direct and oc.is_file():
            primary = str(json.loads(oc.read_text(encoding="utf-8"))
                          .get("agents", {}).get("defaults", {}).get("model", {}).get("primary", ""))
    except Exception:  # noqa: BLE001
        pass
    if direct:
        env = read_settings(ENV_FILE)
        primary = f"direct-api/{env.get('EASEL_DIRECT_API_MODEL', '')}"
    direct_ready = False
    if direct:
        try:
            api_config(env)
            direct_ready = True
        except DirectAPIError:
            pass

    chat_rows = []
    ob = (env.get("OPENAI_BASE_URL") or "").strip()
    om = (env.get("OPENAI_MODEL") or "").strip()
    ok_key = _is_set(env.get("OPENAI_API_KEY"))
    if direct:
        chat_rows.append({
            "slot": "direct-api", "order": 1, "name": "本地 / 内网 API 直连",
            "sub": "独立直连配置", "type": "openai",
            "model": env.get("EASEL_DIRECT_API_MODEL", "").strip(),
            "baseUrl": env.get("EASEL_DIRECT_API_BASE_URL", "").strip(),
            "keyMasked": _mask_key(env.get("EASEL_DIRECT_API_KEY", "")),
            "role": "主", "result": "已配置" if direct_ready else "缺配置",
        })
    elif ob or ok_key:
        chat_rows.append({
            "slot": "openai", "order": 1, "name": "deepseek",
            "sub": "官方直连",
            "type": "openai", "model": om or "deepseek-chat",
            "baseUrl": ob, "keyMasked": _mask_key(env.get("OPENAI_API_KEY", "") if ok_key else ""),
            "role": "主" if primary.startswith("openai/") else "备",
            "result": "已配置" if ok_key else "缺 key",
        })
    def _is_primary(pkey: str, model: str) -> bool:
        """这一行是不是当前的主模型。

        不能像 openai 行那样只比 provider 前缀：anthropic 与 relay 两个槽位现在都落在
        同一个 anthropic provider 上（见 _sync_anthropic_provider），只比前缀会让两行
        同时显示「主」。所以连模型一起比；model 本身可能已是 provider/model 形式
        （CLAUDE_MODEL 按约定就这么写），两种写法都要认。
        """
        if not primary or not model:
            return False
        return primary == model or primary == f"{pkey}/{model}"

    ab = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    ak = (env.get("ANTHROPIC_API_KEY") or "").strip()
    ak_ok = _is_set(ak)
    if not direct and (ab or ak):
        chat_rows.append({
            "slot": "anthropic", "order": len(chat_rows) + 1, "name": "anthropic", "sub": "官方直连",
            "type": "anthropic", "model": (env.get("CLAUDE_MODEL") or "claude-sonnet-4-6").strip(),
            "baseUrl": ab or "官方", "keyMasked": _mask_key(ak if ak_ok else ""),
            # 原来写死「备」：主模型明明是 anthropic/... 时面板也显示备用，和 openai 行
            # 的处理不一致，用户据此判断「哪个在生效」会判错。
            "role": "主" if _is_primary("anthropic", (env.get("CLAUDE_MODEL") or "").strip()) else "备",
            "result": "已配置" if ak_ok else "缺 key",
        })
    lb = (env.get("EASEL_LLM_BASE_URL") or "").strip()
    lk = (env.get("EASEL_LLM_API_KEY") or "").strip()
    lk_ok = _is_set(lk)
    if not direct and (lb or lk):
        chat_rows.append({
            "slot": "relay", "order": len(chat_rows) + 1, "name": "relay", "sub": "中转站",
            "type": "openai", "model": (env.get("CLAUDE_MODEL") or "deepseek-chat").strip(),
            "baseUrl": lb or "（未配置）", "keyMasked": _mask_key(lk if lk_ok else ""),
            # relay 槽位落的也是 anthropic provider（与 setup.sh 的 EASEL_LLM_* 分支一致）
            "role": "主" if _is_primary("anthropic", (env.get("CLAUDE_MODEL") or "").strip()) else "备",
            "result": "已配置" if lk_ok else "缺 key",
        })

    custom_rows = []
    try:
        oc = _oc_config_path()
        if not direct and oc.is_file():
            provs = (json.loads(oc.read_text(encoding="utf-8"))
                     .get("models", {}).get("providers", {})) or {}
            for pkey, pv in provs.items():
                if pkey in ("openai", "anthropic", "relay") or not isinstance(pv, dict):
                    continue
                models = pv.get("models") if isinstance(pv.get("models"), list) else []
                mid = models[0].get("id", "") if models and isinstance(models[0], dict) else ""
                model_entry = models[0] if models and isinstance(models[0], dict) else {}
                compat = model_entry.get("compat")
                compat = compat if isinstance(compat, dict) else {}
                custom_rows.append({
                    "slot": "custom", "order": 0, "name": pkey, "sub": "自定义",
                    "type": "openai",
                    "protocol": "anthropic" if pv.get("api") == "anthropic-messages" else "openai",
                    "thinking": model_entry.get("reasoning") is True,
                    "thinkingFormat": compat.get("thinkingFormat", ""),
                    "model": mid or "", "baseUrl": pv.get("baseUrl") or "",
                    "keyMasked": _mask_key(str(pv.get("apiKey") or "")
                                           if _is_set(str(pv.get("apiKey") or "")) else ""),
                    "role": "主" if primary == f"{pkey}/{mid}" else "备",
                    "result": "已配置" if _is_set(str(pv.get("apiKey") or "")) else "缺 key",
                    "deletable": True,
                })
    except Exception:  # noqa: BLE001
        pass
    chat_rows.extend(custom_rows)

    sf = bool((env.get("SILICONFLOW_API_KEY") or "").strip())
    trans_rows = [
        {"order": 0, "name": "自带字幕", "sub": "视频自带 SRT/VTT 时直接读", "type": "脚本层",
         "model": "—", "baseUrl": "—", "keyMasked": "—", "role": "免配", "result": "优先"},
        {"slot": "siliconflow", "order": 1, "name": "siliconflow", "sub": "硅基流动", "type": "openai",
         "model": "SenseVoiceSmall", "baseUrl": "https://api.siliconflow.cn/v1",
         "keyMasked": _mask_key(env.get("SILICONFLOW_API_KEY", "")), "role": "主",
         "result": "已配置" if sf else "缺 key"},
    ]
    channels: dict = {"chat": {"rows": chat_rows}, "transcribe": {"rows": trans_rows}}
    env = _fresh_media_gateway_env(env)
    try:
        import model_registry as _mr  # skills/shared/scripts 已在 sys.path 上
        _setting_env = {"video": "VIDEO_PROVIDER", "music": "MUSIC_PROVIDER", "voice": "VOICE_PROVIDER"}
        for _gid, _ch in (("image", "image"), ("video", "video"), ("music", "music"), ("voice", "speech")):
            _spec = _mr.MODEL_GROUPS[_gid]
            _chosen = (env.get(_setting_env.get(_gid, ""), "") or "").strip()
            _rows = []
            for _p in _spec["providers"]:
                _req = [k for k in _p["keys"] if k["required"]]
                _bk = next((k for k in _p["keys"] if not k["secret"]
                            and ("BASE" in k["env"] or "URL" in k["env"])), None)
                _mk = next((k for k in _p["keys"] if not k["secret"] and "MODEL" in k["env"]), None)
                _edit_keys = [k for k in _req if k is not _bk and k is not _mk]
                _k1 = _edit_keys[0] if _edit_keys else None
                _k2 = _edit_keys[1] if len(_edit_keys) > 1 else None
                _ok = all(_is_set(env.get(k["env"])) or any(_is_set(env.get(a)) for a in k.get("aliases", []))
                          for k in _req)
                _masked = ""
                if _k1:
                    _raw = env.get(_k1["env"], "")
                    if not _raw:
                        for _a in _k1.get("aliases", []):
                            if env.get(_a):
                                _raw = env[_a]
                                break
                    _masked = _mask_key(_raw)
                _rows.append({
                    "slot": _p["id"], "order": 0, "name": _p["name"], "sub": _gid,
                    "type": _p["id"], "model": (env.get(_mk["env"]) or "").strip() if _mk else "",
                    "baseUrl": (env.get(_bk["env"]) or "").strip() if _bk else "",
                    "keyMasked": _masked, "role": "主" if (_gid == "image" or _chosen == _p["id"]) else "备",
                    "result": "已配置" if _ok else "未配置",
                    "key2Label": _k2["label"] if _k2 else "",
                    "key2Masked": _mask_key(env.get(_k2["env"], "")) if _k2 and _k2["secret"] else "",
                    "modelEditable": _mk is not None,
                    "baseEditable": _bk is not None,
                    "baseOptional": (_bk is None) or (not _bk["required"]),
                })
            if gateway_managed(_gid, env):
                _ready = gateway_readiness(_gid, env)
                _gateway = gateway_settings(env)
                for _row in _rows:
                    _row["role"] = "保留"
                _rows.insert(0, {
                    "slot": "new-api", "order": 0, "name": "统一媒体网关",
                    "sub": "配置完整度；尚未验证模型调用", "type": _ready["provider"],
                    "model": _ready["model"], "baseUrl": _gateway["baseUrl"],
                    "keyMasked": _gateway["keyMasked"], "role": "主",
                    "result": "已配置" if _ready["configured"] else "缺配置",
                    "modelEditable": False, "baseEditable": False, "baseOptional": False,
                })
            channels[_ch] = {"rows": _rows}
    except Exception:  # noqa: BLE001
        pass
    return {"channels": channels, "primary": primary,
            "transport": "api" if direct else "openclaw"}


class ChatTransportRequest(BaseModel):
    transport: str


class HarnessSettingsRequest(BaseModel):
    service_source: str | None = None
    execution_target: str | None = None
    runtime: str
    model: str = Field(default="", max_length=240)
    sandbox: str = "workspace-write"
    codex_auto_approve: StrictBool | None = None
    subscription_model: str | None = Field(default=None, max_length=240)
    subscription_backend: str | None = None


class BundledProviderRequest(BaseModel):
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=240)
    api_key: str = Field(default="", max_length=16384)
    secret_action: str = "preserve"


def _require_bundled_provider():
    from easel.harnesses.runtime import resolve_codex
    if harnesses.describe("pi").get("installed"):
        return
    if resolve_codex().origin != "bundled":
        raise HTTPException(409, "此设置适用于完整安装包的内置 Pi 或 Codex；请检查运行时安装")


@app.get("/api/settings/harness/provider")
async def api_bundled_provider():
    from easel.harnesses.provider import provider_status
    try:
        _require_bundled_provider()
        return provider_status(PROJECT_ROOT)
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/settings/harness/provider")
async def api_bundled_provider_save(request: Request):
    from easel.harnesses.provider import save_provider
    from pydantic import ValidationError
    try:
        req = BundledProviderRequest.model_validate(await request.json())
    except (ValueError, ValidationError) as error:
        # Pydantic default validation details include the submitted raw secret.
        raise HTTPException(400, "供应商请求格式无效，请检查地址、模型和 Key") from error
    try:
        _require_bundled_provider()
        if _RUNNING_CHAT or _BG_TASKS or _DIRECT_CHAT_TASKS or _HARNESS_TASKS or _SYNC_SESSIONS or _PLATFORM_PREFLIGHTS or _platform_router.platform_changes or _central_platform_router.platform_changes:
            raise HTTPException(409, "请等待当前任务完成或停止后修改供应商")
        result = save_provider(PROJECT_ROOT, **req.model_dump())
    except HarnessError as error:
        raise HTTPException(400, str(error)) from error
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    return result


@app.get("/api/settings/harness")
async def api_harness_settings():
    config = _harness_settings()
    agents = await asyncio.to_thread(lambda: [harnesses.describe(runtime) for runtime in harness_store.IDS])
    return {**config, "agents": agents, "availability": availability(),
            "locked": "EASEL_AGENT_RUNTIME" in read_settings(ENV_FILE)}


@app.post("/api/settings/harness")
async def api_harness_settings_save(req: HarnessSettingsRequest):
    config = _harness_settings()
    try:
        route = ServiceRoute(req.service_source if "service_source" in req.model_fields_set else config["service_source"],
                             req.execution_target if "execution_target" in req.model_fields_set else config["execution_target"])
        route.require_available()
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error
    from easel.harnesses.capabilities import supports_subscription
    backend = req.subscription_backend if req.subscription_backend is not None else config.get("subscription_backend", "legacy")
    if backend not in ("legacy", "central"):
        raise HTTPException(400, "无效的订阅后端")
    if route.service_source == "subscription" and not supports_subscription(req.runtime, backend):
        raise HTTPException(409, "此引擎不支持当前订阅渠道；内置 Pi 支持中央订阅，Codex 保留旧版订阅")
    if req.runtime not in harness_store.IDS:
        raise HTTPException(400, "未知 Agent 底座")
    if req.sandbox not in ("read-only", "workspace-write"):
        raise HTTPException(400, "请选择只读或工作区写入权限")
    if any(ord(character) < 32 for character in req.model):
        raise HTTPException(400, "模型名称不能包含控制字符")
    if "EASEL_AGENT_RUNTIME" in read_settings(ENV_FILE):
        raise HTTPException(409, "启动环境固定了底座，请修改 EASEL_AGENT_RUNTIME 后重启")
    if _RUNNING_CHAT or _BG_TASKS or _DIRECT_CHAT_TASKS or _HARNESS_TASKS or _SYNC_SESSIONS or _PLATFORM_PREFLIGHTS or _platform_router.platform_changes or _central_platform_router.platform_changes:
        raise HTTPException(409, "请等待当前任务完成或停止后切换底座")
    if req.runtime not in ("api", "openclaw") and not harnesses.describe(req.runtime)["installed"]:
        raise HTTPException(409, "内置 Pi 请重新安装完整 Easel 应用；外部引擎请先安装并配置后再切换" if req.runtime == "pi" else "请先在终端安装并配置对应 Agent CLI，再切换")
    if req.runtime == "openclaw" and api_mode(read_settings(ENV_FILE)):
        if "EASEL_CHAT_TRANSPORT" in os.environ:
            raise HTTPException(409, "运行环境已固定仅聊天模式，请修改 EASEL_CHAT_TRANSPORT 后重启")
        _write_env_direct({"EASEL_CHAT_TRANSPORT": "http"})
    config["subscription_backend"] = backend
    config["service_source"] = route.service_source
    config["execution_target"] = route.execution_target
    config["runtime"] = req.runtime
    config["sandbox"] = req.sandbox
    if req.runtime == "codex" and req.codex_auto_approve is not None:
        config["codex_auto_approve"] = req.codex_auto_approve
    original = harness_store.read_json(OUTPUTS_DIR / "_harness" / "config.json")
    config["models"] = original.get("models", config["models"])
    config["subscription_model"] = original.get("subscription_model", config.get("subscription_model", ""))
    model = req.model.strip() if "model" in req.model_fields_set else config["models"].get(req.runtime, "")
    if route.service_source == "custom" and req.runtime == "codex" and "model" in req.model_fields_set:
        from easel.harnesses.runtime import codex_model
        try:
            model = codex_model(PROJECT_ROOT, model, require_configured=False)
        except HarnessError as error:
            raise HTTPException(400, str(error)) from error
    elif route.service_source == "custom" and req.runtime == "pi":
        from easel.harnesses.provider import pi_model
        try:
            model = pi_model(PROJECT_ROOT, model, require_configured=False)
        except HarnessError as error:
            raise HTTPException(400, str(error)) from error
    if route.service_source == "subscription":
        selected = (req.subscription_model if req.subscription_model is not None else
                    req.model.strip() if "model" in req.model_fields_set else
                    original.get("subscription_model", config.get("subscription_model", "")))
        if any(ord(c) < 32 for c in selected):
            raise HTTPException(400, "模型名称不能包含控制字符")
        config["subscription_model"] = selected
    else:
        config["models"] = {**config["models"], req.runtime: model}
    harness_store.write_json(OUTPUTS_DIR / "_harness" / "config.json", config)
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    return await api_harness_settings()


@app.post("/api/settings/chat/transport")
async def api_chat_transport_save(req: ChatTransportRequest):
    if req.transport not in ("api", "openclaw"):
        raise HTTPException(400, "请选择 api 或 openclaw")
    if "EASEL_AGENT_RUNTIME" in read_settings(ENV_FILE):
        raise HTTPException(409, "启动环境固定了执行引擎，请修改 EASEL_AGENT_RUNTIME 后重启")
    if "EASEL_CHAT_TRANSPORT" in os.environ:
        raise HTTPException(409, "运行环境已指定 EASEL_CHAT_TRANSPORT，请修改启动环境后重启")
    if _RUNNING_CHAT or _BG_TASKS or _DIRECT_CHAT_TASKS or _HARNESS_TASKS or _SYNC_SESSIONS or _PLATFORM_PREFLIGHTS or _platform_router.platform_changes or _central_platform_router.platform_changes:
        raise HTTPException(409, "请等待当前对话完成或停止后切换模式")
    config = _harness_settings()
    _write_env_direct({"EASEL_CHAT_TRANSPORT": "api" if req.transport == "api" else "http"})
    config["runtime"] = "api" if req.transport == "api" else "openclaw"
    harness_store.write_json(OUTPUTS_DIR / "_harness" / "config.json", config)
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    return _model_channels()


@app.get("/api/settings/models")
async def api_settings_models(runtime: str | None = None):
    """模型通道：chat / transcribe（含可编辑的原值；key 只回脱敏）。"""
    if runtime is not None and runtime not in ("api", "openclaw"):
        raise HTTPException(400, "仅聊天和 OpenClaw 才有 API 供应商配置")
    if runtime is None:
        return await asyncio.to_thread(_model_channels)
    return await asyncio.to_thread(_model_channels, runtime)


@app.post("/api/settings/harness/models/{runtime}")
async def api_harness_models(runtime: str):
    if runtime not in ("pi", "codex", "opencode"):
        raise HTTPException(400, "此底座不支持原生模型列表")
    from easel.harnesses.base import RunRequest
    from easel.harnesses.models import available_models
    request = RunRequest("", PROJECT_ROOT, {
        **_proxy_env(), "EASEL_ROOT": str(PROJECT_ROOT), "EASEL_AGENT_RUNTIME": runtime},
        service_source=_harness_settings()["service_source"],
        subscription_backend=_harness_settings().get("subscription_backend", "legacy"))
    try:
        models = await asyncio.wait_for(available_models(runtime, request), 25)
    except asyncio.TimeoutError as error:
        raise HTTPException(504, "原生模型列表读取超时，请稍后重试") from error
    except (HarnessError, OSError, UnicodeError) as error:
        raise HTTPException(409, "无法读取原生模型列表，请检查 CLI 安装与原生配置") from error
    return {"runtime": runtime, "models": models}


def _write_env_direct(updates: dict[str, str]) -> None:
    """后端受控键位专用：就地更新/追加 .env（不做 allowlist 过滤，仅服务端固定映射调用）。原子写。"""
    updates = {k: v for k, v in updates.items() if k and v.strip() != ''}
    if not updates:
        return
    _guard_env_values(updates)
    lines = ENV_FILE.read_text(encoding='utf-8').splitlines() if ENV_FILE.is_file() else []
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        matched = None
        if s and not s.startswith('#') and '=' in s:
            k = s.split('=', 1)[0].strip()
            if k in updates:
                matched = k
        if matched is not None:
            seen.add(matched)
            out.append(f'{matched}={updates[matched]}')
            continue
        out.append(line)
    appended = [f'{k}={v}' for k, v in updates.items() if k not in seen]
    if appended:
        if out and out[-1].strip() != '':
            out.append('')
        out.append('# ---- Easel 模型配置（Web 设置面板写入）----')
        out.extend(appended)
    tmp = ENV_FILE.with_suffix('.env.tmp')
    tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
    tmp.replace(ENV_FILE)


RESERVED_PROVIDER_KEYS = {"openai", "anthropic", "relay"}
THINKING_FORMATS = {
    "openai", "openrouter", "deepseek", "together", "qwen", "qwen-chat-template", "zai",
}
THINKING_COMPAT_KEYS = {
    "thinkingFormat", "supportsReasoningEffort", "supportedReasoningEfforts", "reasoningEffortMap",
}


def _openclaw_provider_creds() -> dict[str, tuple[str, str]]:
    """读 openclaw.json 里每个 chat 供应商现存的 (baseUrl, apiKey)。读不到就当空表（不阻断保存）。"""
    try:
        oc = _oc_config_path()
        provs = json.loads(oc.read_text(encoding='utf-8')).get('models', {}).get('providers', {})
        return {k: (str(v.get('baseUrl') or ''), str(v.get('apiKey') or ''))
                for k, v in provs.items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001
        return {}


def _sync_openclaw_chat(provider_updates: dict[str, dict], keep_custom: set[str], primary_ref: str) -> str:
    """同步 chat 供应商到 ~/.openclaw-easel/openclaw.json：更新/新增 + 删除多余自定义 + 主模型。

    provider_updates: {pkey: {"model","base","key"}}；keep_custom: 保留的自定义键；primary_ref: 目标主模型（空=不改）。
    只有确有差异才落盘（改前备份 .bak-web）。
    """
    try:
        oc = _oc_config_path()
        if not oc.is_file():
            return ''
        data = json.loads(oc.read_text(encoding='utf-8'))
        providers = data.setdefault('models', {}).setdefault('providers', {})
        changed = False
        for pkey in [k for k in list(providers.keys())
                     if k not in RESERVED_PROVIDER_KEYS and k not in keep_custom]:
            providers.pop(pkey, None)
            changed = True
        for pkey, vals in provider_updates.items():
            prov = providers.setdefault(pkey, {})
            base, key, model = vals.get('base', ''), vals.get('key', ''), vals.get('model', '')
            # 协议只在用户显式选择时落盘：anthropic → api=anthropic-messages；
            # openai/空 → 不写 api 字段（openclaw 默认 openai-completions，与既有配置零差异）。
            proto = (vals.get('protocol') or '').strip().lower()
            if proto == 'anthropic' and prov.get('api') != 'anthropic-messages':
                prov['api'] = 'anthropic-messages'
                changed = True
            elif proto == 'openai' and prov.get('api') == 'anthropic-messages':
                prov.pop('api', None)
                changed = True
            if base and prov.get('baseUrl') != base:
                if _is_local_gateway_base(prov.get('baseUrl')):
                    pass  # 本地模型网关模式：保留网关地址（真实上游在 easel-models.yaml），勿改回直连
                else:
                    prov['baseUrl'] = base
                    changed = True
            if key and prov.get('apiKey') != key:
                prov['apiKey'] = key
                changed = True
            if model:
                models = prov.get('models') if isinstance(prov.get('models'), list) and prov.get('models') else [{}]
                if not isinstance(models[0], dict):
                    models = [{}]
                if models[0].get('id') != model:
                    models[0]['id'] = model
                    changed = True
                model_name = models[0].get('name')
                if not isinstance(model_name, str) or not model_name.strip():
                    models[0]['name'] = model
                    changed = True
                prov['models'] = models
                thinking = vals.get('thinking')
                if thinking is not None:
                    before = json.dumps(models[0], sort_keys=True)
                    models[0]['reasoning'] = thinking
                    compat = models[0].get('compat')
                    compat = dict(compat) if isinstance(compat, dict) else {}
                    if thinking and prov.get('api') != 'anthropic-messages':
                        compat.setdefault('supportsReasoningEffort', True)
                        if vals.get('thinkingFormat'):
                            compat['thinkingFormat'] = vals['thinkingFormat']
                        models[0]['compat'] = compat
                    elif not thinking:
                        for compat_key in THINKING_COMPAT_KEYS:
                            compat.pop(compat_key, None)
                        if compat:
                            models[0]['compat'] = compat
                        else:
                            models[0].pop('compat', None)
                    if json.dumps(models[0], sort_keys=True) != before:
                        changed = True
        if primary_ref:
            ref = data.setdefault('agents', {}).setdefault('defaults', {}).setdefault('model', {})
            if ref.get('primary') != primary_ref:
                ref['primary'] = primary_ref
                changed = True
        if not changed:
            return ''
        shutil.copy2(oc, oc.parent / (oc.name + '.bak-web'))
        tmp = oc.parent / (oc.name + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(oc)
        return 'openclaw 已同步（下一条消息生效）'
    except Exception as e:  # noqa: BLE001
        return f'openclaw 同步失败：{e}'


def _sync_anthropic_provider(base: str, key: str) -> str:
    """把 Web 面板保存的 Anthropic 通道同步成 openclaw.json 里的 anthropic provider。

    bash setup.sh 用 oc_write_anthropic 写过同一块配置，但 Web 面板此前只更新 .env ——
    openclaw.json 里没有 anthropic provider，对话永远走不到新配的 Claude（问题：保存后
    界面显示「已配置」，实际不生效，且没有任何报错）。这里复刻 setup.sh 的写入：
    api=anthropic-messages、models 留空（agent 侧走 anthropic 扩展的内置 Claude 目录），
    原子写 + .bak-web 备份，风格与 _sync_openclaw_chat 一致。返回给用户的提示语。
    """
    try:
        oc = _oc_config_path()
        if not oc.is_file():
            return ''
        data = json.loads(oc.read_text(encoding='utf-8'))
        providers = data.setdefault('models', {}).setdefault('providers', {})
        prov = providers.get('anthropic')
        target_base = base or 'https://api.anthropic.com'
        # 早退判据必须把 api 一起算上：否则一个「baseUrl/apiKey 都对、但缺 api」的
        # 残缺 provider（旧版本 Web 面板写出来的就是这样）永远修不回来。
        if isinstance(prov, dict) and prov.get('baseUrl') == target_base \
                and prov.get('api') == 'anthropic-messages' \
                and (not key or prov.get('apiKey') == key):
            return ''
        new_prov = dict(prov) if isinstance(prov, dict) else {'models': []}
        new_prov['baseUrl'] = target_base
        # api 必须显式写死。上面的 docstring 一直这么说，但代码此前漏了这一行 ——
        # 不写时 OpenClaw 2026.2.x 会把该 provider 当成 openai-responses、请求打到
        # /responses，上游的报错被 gateway 当成正常回复塞进 choices[0].message.content，
        # 于是 Web 对话静默显示「（无输出）」。与 setup.sh 的 oc_write_anthropic 对齐。
        new_prov['api'] = 'anthropic-messages'
        # setup.sh 同样会设这个；两边都写，provider 才不会因为「谁后写」而缺字段。
        new_prov.setdefault('timeoutSeconds', 600)
        if key:
            new_prov['apiKey'] = key
        new_prov.setdefault('models', [])
        # 一次性迁移：更早的 Web 面板会把中转站写成一个名叫 relay 的 provider，但从来
        # 不给它填内容，primary 却指过去。本次若没拿到 key，就把 relay 里的捡回来，
        # 免得老用户升级后 key 变成孤儿；迁移后删掉 relay，避免两处并存各写一半。
        legacy = providers.get('relay')
        if isinstance(legacy, dict):
            if not key and not base and not _is_set(new_prov.get('apiKey')) \
                    and _is_set(legacy.get('apiKey')) and legacy.get('baseUrl'):
                new_prov['apiKey'] = legacy['apiKey']
                new_prov['baseUrl'] = legacy['baseUrl']
            providers.pop('relay', None)
        providers['anthropic'] = new_prov
        shutil.copy2(oc, oc.parent / (oc.name + '.bak-web'))
        tmp = oc.parent / (oc.name + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(oc)
        return 'anthropic provider 已同步到 openclaw（下一条消息生效）'
    except Exception as e:  # noqa: BLE001
        return f'anthropic provider 同步失败：{e}'


class ModelSaveRow(BaseModel):
    slot: str = ""
    name: str = ""
    model: str = ""
    baseUrl: str = ""
    key: str = ""
    key2: str = ""
    primary: bool = False
    # 自定义供应商的上游协议：openai（默认，/chat/completions）或 anthropic
    # （原生 /v1/messages；中转站卖原生 Claude 格式时选它）。
    protocol: str = ""
    thinking: StrictBool | None = None
    thinkingFormat: str | None = None


class ModelSaveRequest(BaseModel):
    channel: str = "chat"
    rows: list[ModelSaveRow] = Field(default_factory=list)


def _guard_chat_model_mutation():
    if _RUNNING_CHAT or _BG_TASKS or _DIRECT_CHAT_TASKS or _HARNESS_TASKS or _SYNC_SESSIONS or _PLATFORM_PREFLIGHTS or _platform_router.platform_changes or _central_platform_router.platform_changes:
        raise HTTPException(409, "请等待当前任务完成或停止后修改聊天模型")


@app.post("/api/settings/models/save")
async def api_settings_models_save(req: ModelSaveRequest):
    """保存模型通道：.env 就地更新（key 留空=不改）；chat 同步 openclaw；媒体通道写 provider 配置。"""
    ch0 = (req.channel or "").strip()
    if ch0 == "chat":
        _guard_chat_model_mutation()
    if ch0 == "chat" and _harness_settings()["runtime"] not in ("api", "openclaw"):
        raise HTTPException(409, "当前底座使用原生认证；模型覆盖请在 Agent 底座设置中保存")
    direct = _direct_api_enabled()
    if direct and ch0 == "chat" and any(row.slot != "direct-api" for row in req.rows):
        raise HTTPException(400, "API 直连模式请使用独立的 API 直连通道")
    if any(row.slot == "direct-api" for row in req.rows) and not (direct and ch0 == "chat"):
        raise HTTPException(400, "请先切换到 API 直连模式再保存直连配置")
    if direct and ch0 == "chat" and any(
            name in os.environ for name in (
                "EASEL_DIRECT_API_BASE_URL", "EASEL_DIRECT_API_MODEL", "EASEL_DIRECT_API_KEY")):
        raise HTTPException(409, "运行环境已指定直连配置，请修改启动环境后重启")
    if ch0 in ("speech", "image", "video", "music"):
        import model_registry as _mr2
        _gid0 = {"speech": "voice", "image": "image", "video": "video", "music": "music"}[ch0]
        _spec0 = _mr2.MODEL_GROUPS[_gid0]
        _by_id = {p["id"]: p for p in _spec0["providers"]}
        _setting0 = {"video": "VIDEO_PROVIDER", "music": "MUSIC_PROVIDER", "voice": "VOICE_PROVIDER"}.get(_gid0)
        _mupd: dict[str, str] = {}
        _primary0 = ""
        _env0 = _read_env()
        for _row in req.rows:
            _pid = (_row.slot or "").strip()
            if _pid == "new-api":
                raise HTTPException(400, "统一媒体网关请使用独立设置卡片保存")
            _p0 = _by_id.get(_pid)
            if not _p0:
                raise HTTPException(400, f"不认识的服务商：{_pid or '（空）'}")
            _base0 = (_row.baseUrl or "").strip().rstrip("/")
            _key0 = (_row.key or "").strip()
            _key20 = (_row.key2 or "").strip()
            _model0 = (_row.model or "").strip()
            if _base0 and not _valid_base_url(_base0):
                raise HTTPException(400, f'{_p0["name"]} 的根地址需是合法的 http(s):// 地址')
            if (_key0 and any(x.isspace() for x in _key0)) or (_key20 and any(x.isspace() for x in _key20)):
                raise HTTPException(400, f'{_p0["name"]} 的 Key 不能包含空白字符')
            _bk0 = next((k for k in _p0["keys"] if not k["secret"]
                         and ("BASE" in k["env"] or "URL" in k["env"])), None)
            _mk0 = next((k for k in _p0["keys"] if not k["secret"] and "MODEL" in k["env"]), None)
            _req0 = [k for k in _p0["keys"] if k["required"] and k is not _bk0 and k is not _mk0]
            # 与对话通道同一条规矩：只改根地址、Key 留空（留空=沿用旧 Key），等于把已存的真 Key
            # 指到新地址去——SILICONFLOW 这类通道还有「连通性自测」会把它当 Bearer 发过去。
            if _base0 and _bk0 and not _key0 and _base0 != (_env0.get(_bk0["env"], "") or "").strip().rstrip("/") \
                    and _req0 and (_env0.get(_req0[0]["env"], "") or "").strip():
                raise HTTPException(400, f'更换{_p0["name"]}的根地址时必须重新填写 Key')
            if _key0 and _req0:
                _mupd[_req0[0]["env"]] = _key0
            if _key20 and len(_req0) > 1:
                _mupd[_req0[1]["env"]] = _key20
            if _model0 and _mk0:
                _mupd[_mk0["env"]] = _model0
            if _base0 and _bk0:
                _mupd[_bk0["env"]] = _base0
            if getattr(_row, "primary", False):
                _primary0 = _pid
        if _primary0 and _setting0:
            _mupd[_setting0] = _primary0
        if not _mupd:
            raise HTTPException(400, "没有可保存的改动（key 留空表示不改）")
        _write_env_direct(_mupd)
        _resp0 = {"ok": True, "note": ""}
        _resp0.update(_model_channels())
        return _resp0

    updates: dict[str, str] = {}
    provider_updates: dict[str, dict] = {}
    keep_custom: set[str] = set()
    primary_ref = ''
    is_chat = (req.channel or '').strip() == 'chat'
    _cur_env = read_settings(ENV_FILE) if direct else _read_env()
    _cur_prov = _openclaw_provider_creds() if is_chat and not direct else {}
    for row in req.rows:
        slot = (row.slot or '').strip()
        name = (row.name or '').strip().lower()
        model = (row.model or '').strip()
        base = (row.baseUrl or '').strip().rstrip('/')
        key = (row.key or '').strip()
        if base and not _valid_base_url(base):
            raise HTTPException(400, f'Base URL 需是合法的 http(s):// 地址：{base[:60]}')
        if key and any(ch.isspace() for ch in key):
            raise HTTPException(400, 'API Key 不能包含空白字符')
        if len(model) > 120 or len(base) > 300 or len(key) > 400:
            raise HTTPException(400, '字段过长，请检查')
        # 改 Base URL 但把 Key 留空（留空=沿用旧 Key）＝ 把已存的真 Key 指向新地址，
        # 之后一次「连通性自测」就会把它当 Bearer 送到新地址去。换地址必须重填 Key。
        _be, _ke = _SLOT_ENV_KEYS.get(slot, ('', ''))
        if base and _be and not key and base != (_cur_env.get(_be, '') or '').strip().rstrip('/') \
                and (_cur_env.get(_ke, '') or '').strip():
            raise HTTPException(400, f'更换 Base URL 时必须重新填写 API Key（{slot}）')
        pkey = ''
        if slot == 'direct-api':
            if model:
                updates['EASEL_DIRECT_API_MODEL'] = model
            if base:
                updates['EASEL_DIRECT_API_BASE_URL'] = base
            if key:
                updates['EASEL_DIRECT_API_KEY'] = key
        elif slot == 'openai':
            if model:
                updates['OPENAI_MODEL'] = model
            if base:
                updates['OPENAI_BASE_URL'] = base
            if key:
                updates['OPENAI_API_KEY'] = key
            if is_chat:
                provider_updates['openai'] = {'model': model, 'base': base, 'key': key}
                pkey = 'openai'
        elif slot == 'relay':
            if base:
                updates['EASEL_LLM_BASE_URL'] = base
            if model:
                updates['CLAUDE_MODEL'] = model
            if key:
                updates['EASEL_LLM_API_KEY'] = key
            if is_chat:
                # 落进 anthropic provider，而不是造一个名叫 relay 的 provider。
                # setup.sh 的 EASEL_LLM_* 分支写的就是 models.providers.anthropic
                # （见 oc_write_anthropic）。此前这里只写 .env、从不创建 provider，
                # 却把 primary 指向 relay/<model> —— 对话直接报
                # 「No route-compatible authentication source is configured for relay」。
                pkey = 'anthropic'
        elif slot == 'anthropic':
            if model:
                updates['CLAUDE_MODEL'] = model
            if key:
                updates['ANTHROPIC_API_KEY'] = key
            if base:
                updates['ANTHROPIC_BASE_URL'] = base
            if is_chat:
                pkey = 'anthropic'
        elif slot == 'siliconflow':
            if base:
                updates['SILICONFLOW_BASE_URL'] = base
            if key:
                updates['SILICONFLOW_API_KEY'] = key
        elif slot == 'custom' and is_chat:
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,23}', name):
                raise HTTPException(400, f'供应商名只能用小写字母/数字/横线（最长24位）：{name[:30] or "（空）"}')
            if name in RESERVED_PROVIDER_KEYS:
                raise HTTPException(400, f'「{name}」是内置槽位名，请换一个')
            if not model or not base:
                raise HTTPException(400, f'自定义供应商「{name}」需要同时填模型和 Base URL')
            proto = (getattr(row, 'protocol', '') or '').strip().lower()
            if proto not in ('', 'openai', 'anthropic'):
                raise HTTPException(400, f'协议只支持 openai / anthropic：{proto}')
            thinking_format = (row.thinkingFormat or '').strip().lower()
            if thinking_format and thinking_format not in THINKING_FORMATS:
                raise HTTPException(400, f'不支持的思考格式：{thinking_format}')
            if thinking_format and row.thinking is None:
                raise HTTPException(400, '选择思考格式时需要显式声明是否启用思考')
            if proto == 'anthropic' and thinking_format:
                raise HTTPException(400, 'Anthropic 原生协议无需选择 OpenAI 兼容思考格式')
            provider_updates[name] = {'model': model, 'base': base, 'key': key, 'protocol': proto}
            if row.thinking is not None:
                provider_updates[name]['thinking'] = row.thinking
                if thinking_format:
                    provider_updates[name]['thinkingFormat'] = thinking_format
            keep_custom.add(name)
            pkey = name
        # 同一条规矩也得覆盖 openclaw.json 这一侧：_sync_openclaw_chat 只在 key 非空时改
        # apiKey，却无条件改 baseUrl —— 只换地址、Key 留空，下一轮对话就会拿着原 Key 去打新地址。
        # 自定义供应商压根不写 .env，前面那道 _SLOT_ENV_KEYS 闸拦不到它。
        # 但网关模式要放行：那边存的是 127.0.0.1:8890、面板回传的是真实上游，永远不相等，
        # 这道闸会把网关用户彻底锁死（连只改模型都保存不了）；而且下面 _sync_openclaw_chat
        # 本来就不会改网关的 baseUrl，没有「拿旧 Key 打新地址」这个风险。
        if is_chat and pkey and base and not key:
            # 换址校验的「现值」要跟配置的权威来源比：openai/relay/anthropic 的权威在 .env
            # （面板与 setup.sh 都写它），openclaw.json 只是保存时的镜像——setup.sh 配置后
            # 用户从没用面板保存过的话，镜像可能停在初始值（如 api.openai.com），拿它比对
            # 会把「原样回传真值」误判成「换址没填 key」，整个面板都保存不了（真机踩中）。
            # custom 供应商权威就在 openclaw.json，维持原比对。
            _be, _ke = _SLOT_ENV_KEYS.get(slot, ('', ''))
            if _be and _is_set(_cur_env.get(_ke)):
                _pb = (_cur_env.get(_be, '') or '').strip().rstrip('/')
                _pk = (_cur_env.get(_ke, '') or '').strip()
                if slot == 'anthropic' and not _pb:
                    _pb = 'https://api.anthropic.com'
            else:
                _pb, _pk = _cur_prov.get(pkey, ('', ''))
                _pb = _pb.strip().rstrip('/')
            if _pk and base != _pb and not _is_local_gateway_base(_pb):
                raise HTTPException(400, f'更换 Base URL 时必须重新填写 API Key（{pkey}）')
        if is_chat and slot == 'openai' and not key and _is_set(_cur_env.get('OPENAI_API_KEY')) \
                and (_cur_env.get('OPENAI_BASE_URL') or '').strip() \
                and not _is_local_gateway_base(_cur_prov.get('openai', ('', ''))[0]):
            provider_updates['openai']['key'] = _cur_env['OPENAI_API_KEY'].strip()
            provider_updates['openai']['base'] = (
                base or _cur_env['OPENAI_BASE_URL'].strip().rstrip('/'))
        if is_chat and pkey and getattr(row, 'primary', False) and model:
            # model 可能本来就是 provider/model 形式：anthropic 行的 model 直接取自
            # .env 的 CLAUDE_MODEL，而那个值按约定就写成 anthropic/claude-opus-4-7。
            # 无脑拼前缀会写出 anthropic/anthropic/claude-opus-4-7，主模型随即不可路由。
            primary_ref = model if '/' in model else f'{pkey}/{model}'
    if not updates and not provider_updates and not primary_ref:
        raise HTTPException(400, '没有可保存的改动（key 留空表示不改）')
    if is_chat and direct:
        try:
            api_config({**_cur_env, **updates})
        except DirectAPIError as error:
            raise HTTPException(400, str(error)) from None
    if updates:
        _write_env_direct(updates)
    note = ''
    if is_chat and direct:
        note = "API 直连配置已保存，下一轮生效；无需同步 OpenClaw"
    elif is_chat:
        note = _sync_openclaw_chat(provider_updates, keep_custom, primary_ref)
        # anthropic / relay 两个槽位都不在 provider_updates 里（它们不是自定义供应商），
        # 但同样要落到 openclaw.json 才真正生效 —— setup.sh 写的 provider 可能已过时。
        # 两者共用同一个 anthropic provider，和 setup.sh 的映射保持一致。
        _slots = {(r.slot or '').strip() for r in req.rows}
        effective_env = {**_cur_env, **updates}
        sync_slot = ''
        for candidate in ('relay', 'anthropic'):
            base_var, key_var = _SLOT_ENV_KEYS[candidate]
            if candidate in _slots and _is_set(effective_env.get(key_var)) \
                    and (candidate == 'anthropic' or effective_env.get(base_var, '').strip()):
                sync_slot = candidate
                break
        if not sync_slot:
            sync_slot = next((candidate for candidate in ('relay', 'anthropic')
                              if candidate in _slots), '')
        if sync_slot:
            base_var, key_var = _SLOT_ENV_KEYS[sync_slot]
            if _is_set(effective_env.get(key_var)):
                sync_base = effective_env.get(base_var, '').strip().rstrip('/')
                sync_key = effective_env[key_var].strip()
            else:
                sync_base = _cur_prov.get('anthropic', ('', ''))[0]
                sync_key = ''
            _an = _sync_anthropic_provider(sync_base, sync_key)
            if _an:
                note = f'{note}；{_an}' if note else _an
    resp = {"ok": True, "note": note}
    resp.update(_model_channels())
    return resp


class LocalAgentEnableRequest(BaseModel):
    id: str
    # 可选：从插件模型目录里选的模型 id（前端下拉列表）；不传用各家默认。
    model: str = ""


@app.get("/api/settings/local-agents")
async def api_local_agents():
    """探测本机已装的 agent CLI（Claude Code / Gemini CLI / Codex…）。

    目的：装了 Claude Code / Gemini CLI 且已登录的用户**不需要再填 API Key** ——
    OpenClaw 底座有对应的 CLI 后端，直接复用 CLI 自己的登录态。返回值同时如实
    标出哪些 CLI 暂无底座后端（installed 但 supported=false），不假装支持。
    """
    from easel.local_agents import summarize_local_agents
    return await asyncio.to_thread(summarize_local_agents)


@app.post("/api/settings/local-agents/enable")
async def api_local_agent_enable(req: LocalAgentEnableRequest):
    """把一个本机 CLI agent（目前支持 claude-code / gemini-cli）接入 Easel。

    实现是把对应 provider 写进 openclaw.json（复用 _sync_anthropic_provider 的
    原子写套路）。CLI 登录态本身由 openclaw 的 auth store 管理 —— 这里只负责
    「声明 provider 并把主模型切过去」；未登录的 CLI 会在下一轮对话时暴露
    认证错误，返回体里用 hint 提前告知用户先去终端登录。

    req.model 可选：从插件模型目录里选一个（前端下拉列表），不传用各家默认。
    传了但不在目录里 → 400 拒绝，绝不静默写入一个不存在的模型引用。
    """
    _guard_chat_model_mutation()
    from easel.local_agents import detect_local_agents, catalog_for_provider
    agents = {a["id"]: a for a in detect_local_agents()}
    agent = agents.get(req.id)
    if not agent:
        raise HTTPException(404, f"未知的本机 agent：{req.id}")
    if not agent["installed"]:
        raise HTTPException(400, f"本机没有找到 {agent['label']}（PATH 上没有 {agent['command'] or '可执行文件'}）")
    provider = agent["openclawProvider"]
    if not provider:
        raise HTTPException(400, f"{agent['label']} 暂无底座后端，无法免 key 接入")
    chosen = (req.model or "").strip()
    catalog = catalog_for_provider(str(agent["openclawProvider"]))
    if provider == "claude-cli":
        # base 必须读 .env（不是 os.environ）：面板/.env 里配的中转站或自建网关值
        # 只在项目 .env 里，不在进程环境里 —— 用 os.environ 会永远拿到空，然后把
        # 用户已配好的 baseUrl 静默覆盖成官方端点。
        note = _declare_anthropic_provider(
            (_read_env().get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
            or "https://api.anthropic.com")
        model_id = chosen or "claude-sonnet-4-6"
        # claude-cli 后端的模型走 provider=claude-cli；把主模型指过去
        primary_ref = f"claude-cli/{model_id}"
    elif provider == "google-gemini-cli":
        # google 插件的 CLI 后端默认启用（enabledByDefault），无需写 provider 块；
        # 接入 = 把主模型指到 CLI 后端的一个真实模型上。
        model_id = chosen or "gemini-3.1-pro-preview"
        primary_ref = f"google-gemini-cli/{model_id}"
        note = ""
    else:
        # 不该到这：supported 的 CLI 都应在上面有明确分支。新后端接入时必须补写，
        # 否则会像 gemini 最初那样返回「已接入」却什么都没写（假成功）。
        raise HTTPException(500, f"{agent['label']} 的接入流程未实现（openclawProvider={provider}）")
    if chosen and catalog and model_id not in {m["id"] for m in catalog}:
        raise HTTPException(400, f"{model_id} 不在 {agent['label']} 的可选模型里（可选：{', '.join(str(m['id']) for m in catalog)}）")
    if primary_ref:
        _oc_note = _set_openclaw_primary(primary_ref)
        note = f"{note}；{_oc_note}" if note else _oc_note
        if not _oc_note:
            note = f"已接入（主模型此前已是 {primary_ref}）"
    return {"ok": True, "agent": agent, "note": note or "已接入"}


def _set_openclaw_primary(primary_ref: str) -> str:
    """把 agents.defaults.model.primary 指到给定 provider/model（原子写 + 备份）。"""
    try:
        oc = _oc_config_path()
        if not oc.is_file():
            return 'openclaw.json 不存在，请先运行 bash setup.sh'
        data = json.loads(oc.read_text(encoding='utf-8'))
        ref = data.setdefault('agents', {}).setdefault('defaults', {}).setdefault('model', {})
        if ref.get('primary') == primary_ref:
            return ''
        ref['primary'] = primary_ref
        shutil.copy2(oc, oc.parent / (oc.name + '.bak-web'))
        tmp = oc.parent / (oc.name + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(oc)
        return f'主模型已切到 {primary_ref}（下一条消息生效）'
    except Exception as e:  # noqa: BLE001
        return f'主模型切换失败：{e}'


def _declare_anthropic_provider(base: str) -> str:
    """在 openclaw.json 里声明 anthropic provider（apiKey 缺省走 CLI 登录态/auth store）。

    与 setup.sh 的 oc_write_anthropic 同构；不写 key 时 agent 侧会回落到 claude-cli
    后端复用本机 Claude Code 的登录。原子写 + .bak-web 备份。
    """
    try:
        oc = _oc_config_path()
        if not oc.is_file():
            return 'openclaw.json 不存在，请先运行 bash setup.sh'
        data = json.loads(oc.read_text(encoding='utf-8'))
        providers = data.setdefault('models', {}).setdefault('providers', {})
        prov = providers.get('anthropic')
        if isinstance(prov, dict) and prov.get('baseUrl') == base:
            return ''
        new_prov = dict(prov) if isinstance(prov, dict) else {'models': []}
        new_prov['baseUrl'] = base
        new_prov.setdefault('models', [])
        providers['anthropic'] = new_prov
        shutil.copy2(oc, oc.parent / (oc.name + '.bak-web'))
        tmp = oc.parent / (oc.name + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(oc)
        return 'anthropic provider 已声明'
    except Exception as e:  # noqa: BLE001
        return f'anthropic provider 声明失败：{e}'


class SelftestRequest(BaseModel):
    channel: str = "chat"


class ModelsFetchRequest(BaseModel):
    """「拉取模型列表」请求。

    key 优先用前端传来的草稿值（供应商还没落盘时也能拉）；留空则按 slot 从已存配置里取
    —— 用户已经存过 Key，不该为了拉一次列表再贴一遍明文。服务端全程不落盘、不写日志。

    baseUrl 允许留空由 slot 回落，否则「只用已存配置拉列表」这条路径就进不来。
    """
    baseUrl: str = ""
    key: str = ""
    protocol: str = "openai"  # openai | anthropic
    slot: str = ""
    name: str = ""  # 自定义供应商的 provider 名（slot='custom' 时用它查已存凭据）


# slot → (base 键, key 键)；与 _SLOT_ENV_KEYS 同源，外加自定义供应商。
_FETCH_KEY_BY_SLOT = {
    'direct-api': ('EASEL_DIRECT_API_BASE_URL', 'EASEL_DIRECT_API_KEY'),
    'openai': ('OPENAI_BASE_URL', 'OPENAI_API_KEY'),
    'relay': ('EASEL_LLM_BASE_URL', 'EASEL_LLM_API_KEY'),
    'anthropic': ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY'),
    'siliconflow': ('SILICONFLOW_BASE_URL', 'SILICONFLOW_API_KEY'),
}


@app.post("/api/settings/models/available")
async def api_models_available(req: ModelsFetchRequest):
    """代前端拉一次 GET {base}/models，返回模型 id 列表（自定义供应商不用再手打模型名）。

    与 selftest 同一条安全线：带着用户 Key 出去的请求，目标必须过 _valid_base_url +
    _ssrf_safe；不落盘、不写日志。
    """
    base = (req.baseUrl or "").strip().rstrip("/")
    key = (req.key or "").strip()
    slot = (req.slot or "").strip()
    # 草稿没填 key 时，回落到该槽位已存的值（只在内存里用，不回显、不记日志）。
    if not key and slot:
        _env = read_settings(ENV_FILE) if _direct_api_enabled() else _read_env()
        if slot == 'custom':
            # 自定义供应商的凭据在 openclaw.json 里（provider 名 = 用户填的名字）
            _b, _k = _openclaw_provider_creds().get((req.name or '').strip().lower(), ("", ""))
        else:
            _bk, _kk = _FETCH_KEY_BY_SLOT.get(slot, ("", ""))
            _b, _k = _env.get(_bk, ""), _env.get(_kk, "")
        key = (_k or "").strip()
        if slot == "direct-api" and key and base and base != (_b or "").strip().rstrip("/"):
            raise HTTPException(400, "更换根地址时必须重新填写 Key，不能把已存 Key 发给其他地址")
        if not base and _b:
            base = _b.strip().rstrip("/")
    if not base:
        raise HTTPException(400, "Base URL 不能为空")
    if not _valid_base_url(base):
        raise HTTPException(400, "Base URL 不合法")
    if not _model_target_allowed(base, slot):
        raise HTTPException(400, "目标指向本机/内网地址，已拒绝（避免把 API Key 发给内网服务）")

    anthropic = slot != "direct-api" and (req.protocol or "").strip().lower() == "anthropic"
    if anthropic:
        url = base + "/v1/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                   "Authorization": f"Bearer {key}"}
    else:
        url = base + "/models"
        headers = {"Authorization": f"Bearer {key}"} if key else {}

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    handlers = [_NoRedirect]
    if slot == "direct-api" and _direct_api_enabled() and base == read_settings(ENV_FILE).get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/"):
        handlers.append(urllib.request.ProxyHandler({}))
    _opener = urllib.request.build_opener(*handlers)

    def _fetch() -> list[str]:
        rq = urllib.request.Request(url, headers=headers)
        with _opener.open(rq, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        # OpenAI / Anthropic 形状都是 {"data":[{"id":...}]}；有的网关直接给 ["id",...]。
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("models") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        ids = []
        for it in items:
            mid = it.get("id") if isinstance(it, dict) else it
            if isinstance(mid, str) and mid.strip():
                ids.append(mid.strip())
        return sorted(set(ids))

    try:
        models = await asyncio.to_thread(_fetch)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"拉取失败：{type(e).__name__}: {e}"[:180]) from e
    return {"baseUrl": base, "models": models, "fetchedAt": int(time.time())}


@app.post("/api/settings/models/selftest")
async def api_models_selftest(req: SelftestRequest):
    """真自测：对已配置的 OpenAI 兼容通道发 GET {base}/models 并计耗时。"""
    channel = (req.channel or "all").strip()
    _group = {"image": "image", "video": "video", "music": "music", "speech": "voice"}.get(channel)
    if _group and gateway_managed(_group, _read_env()):
        raise HTTPException(409, "统一媒体网关显示配置完整度；此旧接口不验证媒体调用")
    if channel in ("chat", "all") and _harness_settings()["runtime"] not in ("api", "openclaw"):
        raise HTTPException(409, "此底座使用原生模型配置，请新建对话或运行 easel ping 验证")
    direct = _direct_api_enabled()
    env = read_settings(ENV_FILE) if direct else _read_env()
    # (base, key, 是否为 Anthropic Messages 协议, slot)。协议决定鉴权头与路径：
    # Anthropic 是 x-api-key + /v1/models，OpenAI 兼容是 Bearer + /models。
    targets: list[tuple[str, str, bool, str]] = []
    if channel in ("chat", "all"):
        if direct:
            chat_targets = ((env.get("EASEL_DIRECT_API_BASE_URL", ""),
                             env.get("EASEL_DIRECT_API_KEY", ""), False, "direct-api"),)
        else:
            chat_targets = (
                (env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_API_KEY", ""), True, "anthropic"),
                (env.get("OPENAI_BASE_URL", ""), env.get("OPENAI_API_KEY", ""), False, "openai"),
                (env.get("EASEL_LLM_BASE_URL", ""), env.get("EASEL_LLM_API_KEY", ""), False, "relay"),
            )
        for base, key, is_anthropic, slot in chat_targets:
            if base.strip() and (key.strip() or direct):
                targets.append((base.strip().rstrip("/"), key.strip(), is_anthropic, slot))
    if channel in ("transcribe", "all") and (env.get("SILICONFLOW_API_KEY") or "").strip():
        targets.append(((env.get("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1").strip().rstrip("/"),
                        env["SILICONFLOW_API_KEY"].strip(), False, "siliconflow"))

    # 这里会把真实 API Key 当 Bearer 发出去，所以目标地址必须先过闸：
    # 合法 http(s)、且不指向本机/内网/云元数据；跳转也不跟（跟了等于绕过前面的判断）。
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    _opener = urllib.request.build_opener(_NoRedirect)

    def _probe(base: str, key: str, *, anthropic: bool = False, slot: str = "") -> dict:
        t0 = time.time()
        if not _valid_base_url(base):
            return {"baseUrl": base, "ok": False, "ms": 0, "detail": "Base URL 不合法，未发起请求"}
        if not _model_target_allowed(base, slot):
            return {"baseUrl": base, "ok": False, "ms": 0,
                    "detail": "目标指向本机/内网地址，已拒绝（避免把 API Key 发给内网服务）"}
        try:
            if anthropic:
                # Anthropic Messages 协议的鉴权头是 x-api-key（不是 Authorization: Bearer），
                # 且模型列表在 /v1/models。按 OpenAI 兼容方式探测只会得到 401/404，
                # 让用户误以为 Key 无效。同时带上两种头：兼容把 /v1/models 反代成
                # OpenAI 风格的中转站（它们只认 Bearer）。
                url = base + "/v1/models"
                rq = urllib.request.Request(url, headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "Authorization": f"Bearer {key}",
                })
            else:
                headers = {"Authorization": f"Bearer {key}"} if key else {}
                rq = urllib.request.Request(base + "/models", headers=headers)
            opener = _opener
            if slot == "direct-api" and base == env.get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/"):
                opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
            with opener.open(rq, timeout=15) as resp:
                return {"baseUrl": base, "ok": resp.status == 200, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            return {"baseUrl": base, "ok": False, "ms": int((time.time() - t0) * 1000),
                    "detail": f"{type(e).__name__}: {e}"[:140]}

    def _run_probes() -> list[dict]:
        out: list[dict] = []
        for base, key, is_anthropic, slot in targets:
            out.append(_probe(base, key, anthropic=is_anthropic, slot=slot))
        return out

    results = await asyncio.to_thread(_run_probes)
    return {"channel": channel, "results": results, "testedAt": int(time.time())}


class AttachmentRef(BaseModel):
    id: str
    name: str
    path: str


class ChatRequest(BaseModel):
    service_source: str | None = None
    execution_target: str | None = None
    message: str
    persona: str | None = None
    sessionId: str | None = None
    turnId: str | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=240)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    catalog_revision: str | None = Field(default=None, max_length=160)


class AttachmentValidationRequest(BaseModel):
    session_id: str = Field(max_length=160)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=32)


def _validated_attachment(attachment, session_id):
    """Uploads belong to one session; explicit output references to one source."""
    rel = Path(attachment.path)
    if rel.is_absolute() or ".." in rel.parts or "\\" in attachment.path:
        raise HTTPException(403, "附件不可用")
    normalized = rel.as_posix()
    if normalized.startswith("_inbox/"):
        scope = _attachment_scope(session_id)
        if (len(rel.parts) != 4 or rel.parts[1] != scope
                or attachment.id != _attachment_id(scope, normalized)):
            raise HTTPException(403, "附件不属于当前会话")
        full = _safe_output_target(normalized)
        if not full.is_file():
            raise HTTPException(404, "附件不存在")
        return full
    source = _workbench_contract()["source_scope"]
    expected = _workbench_state().reference_id(normalized, source)
    import hmac
    if not hmac.compare_digest(attachment.id, expected):
        raise HTTPException(403, "附件不可用")
    return _safe_output_path(normalized)


@app.post("/api/workbench/attachments/validate")
async def api_workbench_attachments(req: AttachmentValidationRequest):
    require_local_http()
    try:
        _workbench_identifier(req.session_id)
    except WorkbenchError:
        raise HTTPException(400, "无效的会话标识") from None
    items = []
    for attachment in req.attachments:
        state = "available"
        try:
            _validated_attachment(attachment, req.session_id)
        except HTTPException as error:
            state = "missing" if error.status_code == 404 else "forbidden"
        items.append({**attachment.model_dump(), "state": state})
    return {"version": 1, "session_id": req.session_id, "attachments": items}


@app.get("/api/artifacts/reference")
async def api_artifact_reference(path: str, session_id: str | None = None):
    require_local_http()
    if len(path) > 2048 or "\\" in path:
        raise HTTPException(400, "无效的文件标识")
    if session_id is not None:
        try:
            _workbench_identifier(session_id)
        except WorkbenchError:
            raise HTTPException(400, "无效的会话标识") from None
    full = _safe_output_path(path)
    if _is_protected(full):
        raise HTTPException(403, "文件不可用")
    normalized = full.relative_to(OUTPUTS_DIR.resolve()).as_posix()
    source = _workbench_contract()["source_scope"]
    return JSONResponse({"id": _workbench_state().reference_id(normalized, source), "path": normalized,
                         "name": full.name, "size": full.stat().st_size,
                         "kind": "text" if full.suffix.lower() in TEXT_EXTS else "file", "state": "available"},
                        headers={"Cache-Control": "no-store"})


@app.get("/api/chat/models")
async def api_chat_models(refresh: bool = False, session_id: str | None = None):
    from easel.chat_models import catalogs
    config = _harness_settings()
    try:
        if session_id:
            if len(session_id) > 256 or any(ord(c) < 32 for c in session_id):
                raise HarnessError("无效的会话标识")
            config = {**config, "runtime": _chat_backend(session_id)}
            record = harness_store.binding(SESSIONS_DIR, session_id)
            if record and (record["service_source"] != config["service_source"]
                           or record["execution_target"] != config["execution_target"]):
                raise HarnessError("此对话属于其他服务来源或执行位置，请新建对话；原历史保持不变")
            if record and config["service_source"] == "subscription":
                backend = config.get("subscription_backend", "legacy")
                if record.get("subscription_backend", "legacy") != backend:
                    raise HarnessError("此对话属于其他订阅渠道，请新建对话；原历史保持不变")
                if backend == "central":
                    from easel.platform.central import CentralClient
                    client = CentralClient(PROJECT_ROOT)
                    with client.store.locked():
                        state = client._state()
                    auth = state.get("auth") or {}
                    if (record.get("platform_url") != state.get("url")
                            or record.get("central_scope") != auth.get("scope")
                            or record.get("account_id") != auth.get("scope", {}).get("user_id")):
                        raise HarnessError("此对话属于其他订阅账号或平台，请新建对话；原历史保持不变")
                else:
                    from easel.platform.client import PlatformClient
                    client = PlatformClient(PROJECT_ROOT)
                    with client.store.locked():
                        state = client.store.read()
                    if record.get("account_id") != (state.get("auth") or {}).get("account", {}).get("id"):
                        raise HarnessError("此对话属于其他订阅账号，请新建对话；原历史保持不变")
        result = await asyncio.to_thread(catalogs.catalog, PROJECT_ROOT, config, read_settings(ENV_FILE), refresh=refresh)
    except (HarnessError, HTTPException) as error:
        result = {"revision": "", "source_revision": "", "expires_in": 0,
                  "runtime": config["runtime"], "service_source": config["service_source"],
                  "subscription_backend": config.get("subscription_backend", "legacy"),
                  "selected_model": "", "switchable": False, "ready": False, "models": [],
                  "reason": _scrub_secrets(str(error.detail if isinstance(error, HTTPException) else error))}
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def _task_context(req: ChatRequest, runtime: str, config: dict, env: dict | None = None) -> TaskContext:
    try:
        from easel.service_contracts import task_context
        selected = {**config, "runtime": runtime,
                    "service_source": req.service_source if "service_source" in req.model_fields_set else config["service_source"],
                    "execution_target": req.execution_target if "execution_target" in req.model_fields_set else config["execution_target"]}
        selection = None
        has_model = "model" in req.model_fields_set
        has_revision = "catalog_revision" in req.model_fields_set
        has_effort = "reasoning_effort" in req.model_fields_set
        if has_model or has_revision or has_effort:
            if not has_model or not has_revision:
                raise HarnessError("请选择当前模型目录中的模型后重试")
            from easel.chat_models import catalogs
            selection = catalogs.select(PROJECT_ROOT, selected, env if env is not None else read_settings(ENV_FILE),
                                        req.model, req.catalog_revision, req.reasoning_effort or "")
        if selected["service_source"] == "subscription":
            require_local_http()
        context = task_context(selected, root=PROJECT_ROOT, selection=selection)
        if selection is None and context.service_source == "custom" and runtime in ("api", "openclaw"):
            from dataclasses import replace
            model = ((env if env is not None else read_settings(ENV_FILE)).get("EASEL_DIRECT_API_MODEL", "").strip()
                     if runtime == "api" else "openclaw/default")
            context = replace(context, model=model)
        if req.sessionId:
            record = harness_store.binding(SESSIONS_DIR, req.sessionId)
            if record and (record["service_source"] != context.service_source or record["execution_target"] != context.execution_target):
                raise HarnessError("此会话属于其他服务来源或执行位置，请新建对话")
            if record and context.service_source == "subscription" and (
                    record.get("account_id") != context.account_id
                    or record.get("subscription_backend", "legacy") != context.subscription_backend
                    or (context.subscription_backend == "central" and (
                        record.get("platform_url") != context.platform_url
                        or record.get("central_scope") != context.central_scope))):
                raise HarnessError("此会话属于其他平台账号，请新建对话")
        return context
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error


def _bind_task(sk: str, context: TaskContext, native_id: str = ""):
    # Preserve the pre-existing API/OpenClaw compatibility exception in _chat_backend.
    record = harness_store.binding(SESSIONS_DIR, sk)
    if record.get("runtime") == "openclaw" and context.runtime == "api":
        return
    harness_store.bind(SESSIONS_DIR, sk, context.runtime, native_id,
                       service_source=context.service_source, execution_target=context.execution_target, account_id=context.account_id,
                       subscription_backend=context.subscription_backend, platform_url=context.platform_url,
                       central_scope=context.central_scope)


def _attachment_scope(session_id: str) -> str:
    """Map a browser session to a filesystem-safe, non-reversible inbox scope."""
    value = session_id.strip()
    if not value or len(value) > 256:
        raise HTTPException(400, "无效的会话标识")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _attachment_id(scope: str, path: str) -> str:
    return hashlib.sha256(f"{scope}\0{path}".encode("utf-8")).hexdigest()[:24]


def _attachment_context(req: ChatRequest) -> str:
    """Validate attachment ownership and build an Agent-only attachment manifest."""
    if not req.attachments:
        return ""
    if not req.sessionId:
        raise HTTPException(400, "附件必须绑定到会话")

    scope = _attachment_scope(req.sessionId)
    rows: list[str] = []
    seen: set[str] = set()
    for attachment in req.attachments:
        full = _validated_attachment(attachment, req.sessionId)
        normalized = full.relative_to(OUTPUTS_DIR.resolve()).as_posix()
        if normalized in seen:
            continue
        seen.add(normalized)
        rows.append(f"- outputs/{normalized}")

    return (
        "〔系统附件清单，仅供本轮执行，不要向用户复述文件上传过程或内部路径〕\n"
        "只允许使用下列当前会话附件；禁止扫描、枚举或猜测 outputs/_inbox 中的其他文件：\n"
        + "\n".join(rows)
        + "\n需要纳入内容项目时，将清单内文件复制到 outputs/<项目>/assets/ 后再使用；"
          "保留 inbox 原件，确保重试仍可复现。"
    )


def _chat_message(req: ChatRequest) -> str:
    context = _attachment_context(req)
    message = req.message.strip()
    if context:
        message = f"{message}\n\n{context}" if message else context
    if not message:
        raise HTTPException(400, "消息不能为空")
    return chat_turn_message(message, req.persona)


def _direct_chat_content(req: ChatRequest) -> tuple[str | list, str]:
    _attachment_context(req)  # Reuse ownership/path validation before reading any attachment.
    message = req.message.strip()
    if not message and not req.attachments:
        raise HTTPException(400, "消息不能为空")
    system = SYSTEM_PROMPT
    if req.persona:
        if not _valid_persona_name(req.persona) or not profile_exists(req.persona):
            raise HTTPException(400, "画像不存在或名称无效")
        system += "\n\n当前画像：\n" + load_profile_text(req.persona)
    content = [{"type": "text", "text": message or "请分析这些附件"}]
    for attachment in req.attachments:
        path = _safe_output_target(attachment.path)
        if path.suffix.lower() in TEXT_EXTS:
            if path.stat().st_size > 65536:
                raise HTTPException(413, "API 直连模式文本附件不能超过 64 KiB")
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                raise HTTPException(400, "文本附件需要 UTF-8 编码") from None
            content.append({"type": "text", "text": f"附件 {attachment.name}：\n{text}"})
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise HTTPException(413, "API 直连模式图片附件不能超过 4 MiB")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        else:
            raise HTTPException(400, "API 直连模式支持文本和图片附件；此附件需要文件处理工具")
    return (content if req.attachments else message), system


# 每个会话（session-key）一把锁：防止同一会话被两个并发的 openclaw agent 进程同时处理。
# 并发跑同一 session 文件会触发 openclaw 的 EmbeddedAttemptSessionTakeoverError（进程 rc=1、
# 表现为「答一半停在冒号」），以及会话串味（一个会话读到另一个的 session 文件内容）。
# 不同会话 key 不同锁 → 不同对话仍可并行；只序列化「同一会话」的重叠请求。
_session_locks: dict[str, asyncio.Lock] = {}


def _session_lock(sk: str) -> asyncio.Lock:
    lk = _session_locks.get(sk)
    if lk is None:
        lk = asyncio.Lock()
        _session_locks[sk] = lk
    return lk


# 会话续接：把 web 的 sessionId 确定性映射成一个稳定的 OpenClaw --session-id（transcript 文件名）。
# 背景（实测根因）：OpenClaw 靠 --session-key 解析 transcript，但空闲超过约 24h（threadBindings
# 默认 idleHours:24）后该绑定过期，下一条消息会新起一个空 transcript → 历史全丢（用户「关页两天
# 后再问就忘了」）。同一天内没事，隔天就断。解法：我们自己钉死 --session-id（对同一 web 会话恒定），
# 让 OpenClaw 每轮都续同一个 transcript 文件，绕开 key→绑定的过期/轮换逻辑。
_EASEL_SESSION_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # 固定命名空间（uuid5 确定性）


def _openclaw_session_id(sk: str) -> str:
    """web sessionId → 稳定的 OpenClaw session-id（transcript）。同 sk 永远同 id，无需落盘映射。"""
    return str(uuid.uuid5(_EASEL_SESSION_NS, sk))


def _session_flock_path(sk: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sk)[:120]
    return SESSIONS_DIR / f"{safe}.lock"


def _transport_pin_file(sk: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sk)[:120]
    return SESSIONS_DIR / f"{safe}.transport"


def _resolve_transport(sk: str) -> str:
    """决定这一轮走哪条传输层，并保证同一会话**永不中途换边**。

    换边的代价是静默丢光上下文，不是慢一点：CLI 路径用 `--session-id`（uuid5(sk)）把 transcript
    钉死，而 /v1/chat/completions **压根不读 x-openclaw-session-id** —— openclaw 2026.6.11 全量
    实测：该 header 只有 MCP 端点和对上游供应商的出站请求会用，OpenAI 兼容端点只认
    x-openclaw-session-key，transcript 文件名由网关自己挑。于是同一个 web 会话在两条路径下落在
    两份不同的 jsonl 上。实测拿 CLI 去续一个已有两轮 HTTP 历史的会话，agent 回答"无历史"。

    所以判定顺序（都不依赖内存态，web 重启后依然成立）：
      1) uuid5 那份 transcript 已落盘 → 这会话是 CLI 起的，继续 cli；
      2) 有 http 钉子文件 → 继续 http；
      3) 两者都没有 → 新会话，按总开关 + 真探针决定。
    """
    if (OPENCLAW_SESSIONS_DIR / f"{_openclaw_session_id(sk)}.jsonl").is_file():
        return "cli"
    try:
        if _transport_pin_file(sk).read_text(encoding="utf-8").strip() == "http":
            return "http"
    except OSError:
        pass
    # 3) 新会话：按总开关 + 真探针；探不通不报错，退回 CLI 但要吭一声
    if CHAT_TRANSPORT == "http" and _gateway_http_ready():
        return "http"
    if CHAT_TRANSPORT == "http":
        _warn_http_transport_unavailable()
    return "cli"


_HTTP_FALLBACK_WARNED = False


def _warn_http_transport_unavailable() -> None:
    """HTTP 直连不可用时只喊一次，并把**探的到底是哪个端点**写出来。

    以前这里是静默退回：端口写死探错地方，表现只是「每轮慢 3s」，没人会去查 —— 这次网关
    端口错配就是这么藏住的（面板离线 + 静默退 CLI）。
    """
    global _HTTP_FALLBACK_WARNED
    if _HTTP_FALLBACK_WARNED:
        return
    _HTTP_FALLBACK_WARNED = True
    print(f"[chat-transport] HTTP 直连探不通 {chat_completions_url()}（端口来自 {port_source()}），"
          f"已退回 CLI 传输（每轮多付一次客户端冷启动）。"
          f"查：{PROJECT_ROOT}/scripts/gateway.sh status", file=sys.stderr, flush=True)


def _pin_transport(sk: str, kind: str) -> None:
    """把会话钉在某条传输层上。只需钉 http —— cli 侧由 uuid5 transcript 文件自证。"""
    if kind != "http":
        return
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        _transport_pin_file(sk).write_text("http", encoding="utf-8")
    except OSError:
        pass


class _CrossProcLock:
    """跨进程会话锁（fcntl.flock）：同一会话同一时刻只允许一个 openclaw 进程在跑。

    现有 _session_lock（asyncio）只在单个 web 进程内串行；挡不住两个浏览器标签/常驻 gateway/
    cron 并发碰同一会话 → openclaw 抛 EmbeddedAttemptSessionTakeoverError（rc=1，答一半就停）。
    flock 在持有进程退出时自动释放，无 stale 死锁。返回 True=拿到锁，False=超时未拿到。
    """

    def __init__(self, sk: str):
        self._path = _session_flock_path(sk)
        self._fh = None
        self.acquired = False

    def acquire(self, timeout: float = 300.0, poll: float = 0.5) -> bool:
        try:
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "a+b")
            if os.name == "nt":
                self._fh.seek(0, os.SEEK_END)
                if self._fh.tell() == 0:
                    self._fh.write(b"0")
                    self._fh.flush()
        except OSError:
            return False  # 拿不到文件句柄就不强求（退化为仅 asyncio 锁）
        deadline = time.time() + timeout
        while True:
            try:
                if os.name == "nt":
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return True
            except OSError:
                if time.time() >= deadline:
                    return False
                time.sleep(poll)

    def release(self) -> None:
        if self._fh is not None:
            try:
                if self.acquired:
                    if os.name == "nt":
                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            self.acquired = False


def _turn_file(sk: str) -> Path:
    """每会话最近一轮结果的落盘路径（sk 做文件名安全化）。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sk)[:120]
    return SESSIONS_DIR / f"{safe}.json"


def _job_event_file(turn_id: str) -> Path:
    """Per-turn append-only event log used to resume SSE without restarting the agent."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", turn_id)[:160]
    # Preserve old replay files while avoiding sanitizer collisions for new IDs.
    if ":" in turn_id and not (SESSIONS_DIR / "jobs" / f"{safe}.jsonl").exists():
        safe = hashlib.sha256(turn_id.encode()).hexdigest()
    return SESSIONS_DIR / "jobs" / f"{safe}.jsonl"


def _read_job_events(turn_id: str, after: int = 0) -> list[dict]:
    path = _job_event_file(turn_id)
    events = []
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
                return []
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            return []
        for index, line in enumerate(raw.splitlines()):
            if index >= 100000:
                return []
            event = json.loads(line)
            if (not isinstance(event, dict) or type(event.get("id")) is not int
                    or event["id"] <= 0 or not isinstance(event.get("event"), str)):
                return []
            if event["id"] > after:
                events.append(event)
    except (OSError, ValueError, UnicodeError):
        return []
    return events


def _raw_event_for_run(line: str, expected_run_id: str | None) -> dict | None:
    """Parse one OpenClaw raw event and reject events from other runs.

    The gateway multiplexes every run into one shared raw-stream file, and its
    events carry `runId` (not `sessionId`). A turn latches onto its own runId —
    the first event seen after the turn starts — and must ignore any event with
    a different runId. `expected_run_id=None` means not-yet-latched → accept, so
    the caller can latch from `event['runId']`.
    """
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(event, dict):
        return None
    if expected_run_id is not None:
        rid = event.get("runId")
        if rid is not None and rid != expected_run_id:
            return None
    return event


def _save_turn(sk: str, status: str, text: str, extra: dict | None = None) -> None:
    """持久化本轮结果（running/done），供 SSE 连接中断后前端用 /api/chat/last 取回。

    后端跑完整轮不依赖客户端连接——长任务时 webide 代理会掐断 SSE，但 openclaw 仍跑到底，
    结果写这里，前端断线后轮询即可拿到完整回答（否则"运行完也不说一声"）。
    """
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"status": status, "text": text, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if extra:
            payload.update(extra)
        tmp = _turn_file(sk).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _turn_file(sk))
        if sk.startswith("web:") and extra and extra.get("turn_id"):
            _workbench_state().save_snapshot(sk[4:], extra["turn_id"], payload)
    except Exception:
        pass


def _guard_uncertain_central_turn(session_id: str | None) -> None:
    """Do not turn an uncertain paid turn into another model dispatch.

    This reads server-owned task metadata, not browser error text. It is checked
    before account authorization and again under the native session lock.
    """
    if not session_id:
        return
    path = _turn_file(f"web:{session_id}")
    message = ("上一轮中央订阅结果或费用尚未确认，请到设置 → 账号与订阅 → 中断请求状态查询。"
               "原请求和历史已保留，不会重新派发；如需发起独立任务，请新建对话。")
    try:
        binding = harness_store.binding(SESSIONS_DIR, session_id)
    except HarnessError:
        raise HTTPException(409, "无法确认会话归属，请保留原历史并新建对话。") from None
    known_central = (binding.get("service_source") == "subscription"
                     and binding.get("subscription_backend") == "central")
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    route = payload.get("route", {})
    central_route = (isinstance(route, dict) and route.get("service_source") == "subscription"
                     and route.get("subscription_backend") == "central")
    if ((known_central or central_route)
            and (payload.get("status") != "done" or payload.get("clean_end") is not True)):
        raise HTTPException(409, message)


# 后台 supervisor 任务集合：持有强引用防被 GC；每个对话流的 openclaw run 跑在这里，
# 与客户端 SSE 连接解耦（断线不杀 run）。
_BG_TASKS: set = set()
_WORKBENCH_TURNS: dict = {}


def _workbench_turn_live(turn_id):
    task = _WORKBENCH_TURNS.get((str(SESSIONS_DIR), turn_id))
    return task is not None and not task.done()


def _workbench_receipt(session_id, turn_id):
    try:
        return _workbench_state().owned(session_id, turn_id, _workbench_contract()["source_scope"])
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None


def _workbench_completion(session_id, turn_id):
    """Only a durable exact-turn snapshot can confirm execution termination."""
    result = {"version": 1, "session_id": session_id, "turn_id": turn_id,
              "state": "unknown", "clean_end": False, "stop_reason": "unknown"}
    try:
        snapshot = _workbench_state().snapshot(turn_id)
    except WorkbenchError:
        snapshot = None
    if snapshot is not None and snapshot.get("status") == "done":
        clean = snapshot.get("clean_end") is True
        reason = snapshot.get("stop_reason", "unknown")
        result.update(clean_end=clean, stop_reason=reason,
                      state="completed" if clean else "stopped" if reason in ("stopped", "user_stopped") else "failed")
    return result


def _snapshot_artifacts(snapshot, session_id, turn_id):
    """Return only additive exact-turn references, never infer them from text."""
    from easel.harnesses.pi_tools import validate_artifact_descriptor
    values = snapshot.get("artifacts")
    if not isinstance(values, list):
        return []
    found = {}
    fields = {"version", "path", "name", "size", "sha256", "session_id", "turn_id"}
    for value in values[:16]:
        if (type(value) is not dict or set(value) != fields
                or value.get("session_id") != session_id or value.get("turn_id") != turn_id):
            continue
        serialized = json.dumps(value, ensure_ascii=False)
        if _scrub_secrets(serialized) != serialized:
            continue
        base = {key: value[key] for key in fields - {"session_id", "turn_id"}}
        if validate_artifact_descriptor(base) is None:
            continue
        found[value["path"]] = dict(value)
    return list(found.values())


@app.get("/api/chat/turns/{session_id}/{turn_id}")
async def api_chat_turn(session_id: str, turn_id: str):
    require_local_http()
    record = _workbench_receipt(session_id, turn_id)
    result = {"version": 1, "session_id": session_id, "turn_id": turn_id, "state": "unknown", "accepted": record is not None}
    if record is None:
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    result.update(_workbench_state().public_receipt(record))
    if record["decision"] == "rejected":
        result.update(state="rejected", accepted=False, not_dispatched=True)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    try:
        snapshot = _workbench_state().snapshot(turn_id)
    except WorkbenchError:
        snapshot = None
    if snapshot is not None:
        for field in ("text", "clean_end", "stop_reason", "central_recovery"):
            if field in snapshot:
                result[field] = snapshot[field]
        if "artifacts" in snapshot:
            result["artifacts"] = _snapshot_artifacts(snapshot, session_id, turn_id)
        if snapshot.get("status") == "done":
            result["state"] = ("completed" if snapshot.get("clean_end") is True else
                               "stopped" if snapshot.get("stop_reason") in ("stopped", "user_stopped") else "failed")
        elif _workbench_turn_live(turn_id):
            result["state"] = "running"
    elif _workbench_turn_live(turn_id):
        result["state"] = "accepted"
    batch = _read_job_events(turn_id)
    result["last_event_id"] = max((int(event.get("id", 0)) for event in batch), default=0)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


class WithdrawRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


@app.post("/api/chat/turns/{session_id}/{turn_id}/withdraw")
async def api_chat_withdraw(session_id: str, turn_id: str, body: WithdrawRequest, request: Request):
    """An explicit local withdrawal fences an undecided submission permanently."""
    require_local_http()
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin not in (None, expected) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请求来源不允许")
    try:
        _workbench_identifier(session_id)
        _workbench_identifier(turn_id)
        ledger = _workbench_state()
        scope = _workbench_contract()["source_scope"]
        record = ledger.owned(session_id, turn_id, scope)
        if record is None and _job_event_file(turn_id).exists():
            # An old event log cannot be converted to a safe rejection merely
            # because it predates versioned durable acceptance decisions.
            record = ledger.owned(session_id, turn_id, scope)
            if record is None:
                raise WorkbenchError("原任务状态待确认", 409)
        if record is not None and record["request_hash"] != body.request_hash:
            raise WorkbenchError("任务标识已用于其他内容", 409)
        # A racing accept keeps its original receipt; a winning rejection is
        # permanent. Neither branch changes money or dispatches another model.
        ledger.reject(session_id, turn_id, scope, body.request_hash)
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None
    return await api_chat_turn(session_id, turn_id)


_DIRECT_CHAT_TASKS: set = set()

# 正在跑的对话 openclaw 进程（sk→proc），供用户**显式「停止」**终止；断线**不**经此路径（断线不杀）。
_RUNNING_CHAT: dict = {}
# 被用户显式停止的会话 key：supervisor 据此把本轮当作正常「已停止」收尾（不报「被中断」、释放会话锁）。
_STOPPED_CHAT: set = set()
_HARNESS_TASKS: set = set()
_SYNC_SESSIONS: dict[str, tuple[object, TaskContext]] = {}
_HARNESS_QUESTIONS: dict = {}


class _HarnessProc(_GatewayHttpProc):
    def __init__(self) -> None:
        super().__init__()
        self._cancel_requested = False

    def terminate(self) -> None:
        if self._task is not None:
            task = self._task
            def cancel_once():
                if (not task.done() and not self._cancel_requested
                        and not getattr(task, "cancelling", lambda: 0)()):
                    self._cancel_requested = True
                    task.cancel()
            task.get_loop().call_soon_threadsafe(cancel_once)


async def _harness_ask(sk: str, questions: list[dict], emit) -> dict:
    identifier = "harness_" + uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    record = {"session": sk, "future": future, "status": "pending", "questions": questions}
    _HARNESS_QUESTIONS[identifier] = record
    try:
        emit("question", _scrub_secrets(json.dumps(
            {"id": identifier, "questions": questions}, ensure_ascii=False)))
        answers = await asyncio.wait_for(future, TIMEOUT_CHAT)
        record["status"] = "resolved"
        return answers
    finally:
        _HARNESS_QUESTIONS.pop(identifier, None)


async def _run_harness_chat(req: ChatRequest, sk: str, message: str, runtime: str,
                            config: dict, emit, timeout: int = TIMEOUT_CHAT, *, interactive=True, context: TaskContext | None = None) -> str:
    context = context or await _accept_task_context(req, runtime, config)
    if sk in _SYNC_SESSIONS and _SYNC_SESSIONS[sk][1] is not context:
        raise HTTPException(409, "这个会话上一条还在运行，请稍后再试")
    pk = f"web:{sk}"
    turn_id = req.turnId or uuid.uuid4().hex
    recovery_metadata = {}
    if context.service_source == "subscription" and context.subscription_backend == "central":
        from easel.platform.execution_records import operation_turn_key
        operation_turn_key(sk, turn_id)
        recovery_metadata["central_recovery"] = {"version": 1, "turn_id": turn_id}
    full_text: list[str] = []
    artifacts: list[dict] = []
    clean_end = False
    reason = "error"
    error_message = ""
    lock = _session_lock(sk)
    xlock = _CrossProcLock(sk)
    acquired = False
    claimed = False
    locked = False
    proc = _HarnessProc()
    task = asyncio.current_task()
    _HARNESS_TASKS.add(task)

    def receive(kind, text):
        safe = _scrub_secrets(text)
        if kind == "artifact":
            from easel.harnesses.pi_tools import validate_artifact_descriptor
            try:
                if not isinstance(safe, str) or len(safe.encode()) > 8192:
                    return
                descriptor = validate_artifact_descriptor(json.loads(safe))
            except (ValueError, UnicodeError, TypeError):
                return
            if descriptor is None:
                return
            bound = {**descriptor, "session_id": sk, "turn_id": turn_id}
            index = next((i for i, value in enumerate(artifacts) if value["path"] == bound["path"]), None)
            if index is not None:
                if artifacts[index] == bound:
                    return
                artifacts[index] = bound
            elif len(artifacts) < 16:
                artifacts.append(bound)
            else:
                return
            _save_turn(pk, "running", "".join(full_text), {
                "turn_id": turn_id, "runtime": runtime, "route": context.metadata(),
                **recovery_metadata, "artifacts": list(artifacts)})
            emit(kind, json.dumps(bound, ensure_ascii=False))
            return
        if kind == "token":
            full_text.append(safe)
        emit(kind, safe)

    async def ask(questions):
        if not interactive:
            raise HarnessError("此操作需要权限确认，请在流式对话中执行并确认权限")
        return await _harness_ask(sk, questions, receive)

    try:
        if lock.locked():
            emit("activity", "这个会话上一条还在运行，等待完成…")
        await lock.acquire()
        locked = True
        deadline = time.monotonic() + min(timeout, 300)
        while not acquired and time.monotonic() < deadline:
            acquired = xlock.acquire(0)
            if not acquired:
                xlock.release()
                await asyncio.sleep(0.2)
        if not acquired:
            raise HarnessError("此会话正在另一个窗口运行，请稍后再试")
        _guard_uncertain_central_turn(sk)
        record = harness_store.binding(SESSIONS_DIR, sk)
        native_id = record.get("native_id", "")
        _bind_task(sk, context, native_id)
        _STOPPED_CHAT.discard(sk)
        proc._task = task
        _RUNNING_CHAT[sk] = proc
        _save_turn(pk, "running", "", {"turn_id": turn_id, "runtime": runtime, "route": context.metadata(), **recovery_metadata})
        claimed = True
        env = _fresh_media_gateway_env({**read_settings(ENV_FILE), **_proxy_env(), "EASEL_AGENT_RUNTIME": runtime,
                                       "EASEL_ROOT": str(PROJECT_ROOT)})
        request = RunRequest(message, PROJECT_ROOT, env, native_id,
                             context.model, config["sandbox"], timeout,
                             session_key=sk, codex_auto_approve=config.get("codex_auto_approve", False),
                             service_source=context.service_source, execution_target=context.execution_target,
                             account_id=context.account_id, platform_url=context.platform_url,
                             subscription_backend=context.subscription_backend, central_scope=context.central_scope,
                             invocation_key=context.invocation_key, reasoning_effort=context.reasoning_effort,
                             reasoning_efforts=context.reasoning_efforts, selection_verified=context.selection_verified,
                             provider_binding=context.provider_binding, turn_id=turn_id)
        await asyncio.wait_for(harnesses.adapter(runtime).run(
            request, receive, ask,
            lambda identifier: _bind_task(sk, context, identifier)), timeout)
        clean_end = True
        reason = "completed"
    except asyncio.CancelledError:
        reason = "stopped" if sk in _STOPPED_CHAT else "shutdown"
        emit("activity", "已停止" if reason == "stopped" else "服务关闭，本轮已取消")
    except asyncio.TimeoutError:
        reason = "timeout"
        error_message = "Agent 执行超时，已终止本轮进程"
        emit("error", error_message)
    except (HarnessError, HTTPException) as error:
        error_message = _scrub_secrets(str(error.detail if isinstance(error, HTTPException) else error))
        emit("error", error_message)
    except Exception:
        error_message = "Agent 执行失败，请检查本机 CLI、登录和模型配置"
        emit("error", error_message)
    finally:
        if claimed:
            snapshot = "".join(full_text) + (f"\n\n❌ {error_message}" if error_message else "")
            _save_turn(pk, "done", snapshot, {
                "turn_id": turn_id, "runtime": runtime, "route": context.metadata(),
                "clean_end": clean_end, "stop_reason": reason, **recovery_metadata,
                **({"artifacts": list(artifacts)} if artifacts else {})})
        proc.finish()
        if _RUNNING_CHAT.get(sk) is proc:
            _RUNNING_CHAT.pop(sk, None)
        if claimed:
            _STOPPED_CHAT.discard(sk)
        xlock.release()
        if locked:
            lock.release()
        _HARNESS_TASKS.discard(task)
    return _SyncOutcome("".join(full_text), clean_end=clean_end, stop_reason=reason)


async def _run_direct_chat(req: ChatRequest, sk: str, content, system: str, emit, *,
                           context: TaskContext | None = None, settings: dict | None = None) -> str:
    """Run one API turn with the same stop/recovery/session locking as Gateway chat."""
    if sk in _SYNC_SESSIONS and _SYNC_SESSIONS[sk][1] is not context:
        raise HTTPException(409, "这个会话上一条还在运行，请稍后再试")
    pk = f"web:{sk}"
    turn_id = req.turnId or uuid.uuid4().hex
    full_text = []
    clean_end = False
    reason = "error"
    error_message = ""
    lock = _session_lock(sk)
    xlock = _CrossProcLock(sk)
    proc = _GatewayHttpProc()
    acquired = False
    claimed = False
    locked = False
    settings = dict(settings if settings is not None else read_settings(ENV_FILE))
    context = context or await _accept_task_context(req, "api", _harness_settings(), settings)
    settings["EASEL_DIRECT_API_MODEL"] = context.model
    task = asyncio.current_task()
    _DIRECT_CHAT_TASKS.add(task)
    if lock.locked():
        emit("activity", "⏳ 这个会话上一条还在跑，排队等它结束…")
    try:
        await lock.acquire()
        locked = True
        deadline = time.monotonic() + min(TIMEOUT_CHAT, 300)
        while True:
            acquired = xlock.acquire(0)
            if acquired:
                break
            xlock.release()
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
        if not acquired:
            raise DirectAPIError("这个会话正在另一个窗口运行，请稍候再试")
        # Existing OpenClaw conversations have different histories. Require a new
        # session rather than silently discard their context when switching modes.
        if (_transport_pin_file(sk).is_file()
                or (OPENCLAW_SESSIONS_DIR / f"{_openclaw_session_id(sk)}.jsonl").is_file()
                or (OPENCLAW_SESSIONS_DIR / f"{_openclaw_session_id(pk)}.jsonl").is_file()):
            raise DirectAPIError("此会话属于 OpenClaw，请新建会话使用 API 直连")
        _bind_task(sk, context)
        _save_turn(pk, "running", "", {"turn_id": turn_id, "route": context.metadata()})
        claimed = True
        proc._task = asyncio.current_task()
        _RUNNING_CHAT[sk] = proc
        emit("activity", "🧠 正在通过 API 思考…")

        async def consume():
            nonlocal clean_end, reason
            async for kind, text in stream_chat(
                    settings, content, system=system,
                    history_file=history_path(SESSIONS_DIR, sk), timeout=TIMEOUT_CHAT,
                    **({"reasoning_effort": context.reasoning_effort, "reasoning_efforts": context.reasoning_efforts}
                       if context.reasoning_effort else {})):
                if kind == "finish":
                    clean_end, reason = True, text
                else:
                    if kind == "token":
                        full_text.append(text)
                    emit(kind, text)

        await asyncio.wait_for(consume(), timeout=TIMEOUT_CHAT)
    except asyncio.CancelledError:
        reason = "user_stopped" if sk in _STOPPED_CHAT else "cancelled"
        if reason != "user_stopped":
            raise
    except (DirectAPIError, HarnessError, asyncio.TimeoutError) as error:
        error_message = str(error) or "API 请求超时"
        emit("error", error_message)
    except OSError:
        error_message = "会话历史保存失败，请检查项目 outputs 目录权限"
        emit("error", error_message)
    finally:
        proc.finish()
        snapshot = "".join(full_text) + (f"\n\n❌ {error_message}" if error_message else "")
        try:
            if claimed:
                _save_turn(pk, "done", snapshot, {
                    "turn_id": turn_id, "clean_end": clean_end, "stop_reason": reason, "route": context.metadata(),
                })
        finally:
            if _RUNNING_CHAT.get(sk) is proc:
                _RUNNING_CHAT.pop(sk, None)
            if claimed:
                _STOPPED_CHAT.discard(sk)
            xlock.release()
            if locked:
                lock.release()
            _DIRECT_CHAT_TASKS.discard(task)
    return "".join(full_text)


@app.get("/api/chat/last/{session_id}")
async def api_chat_last(session_id: str, turn_id: str | None = None):
    """取某会话最近一轮的完整结果（SSE 断线后前端据此取回，避免丢结果）。"""
    f = _turn_file(f"web:{session_id}")
    if not f.is_file():
        return {"status": "none", "text": ""}
    try:
        payload = json.loads(f.read_text(encoding="utf-8"))
        if turn_id and payload.get("turn_id") != turn_id:
            return {"status": "stale", "text": "", "turn_id": payload.get("turn_id")}
        if "artifacts" in payload:
            try:
                owned = _workbench_receipt(session_id, payload.get("turn_id"))
            except (HTTPException, TypeError, ValueError):
                owned = None
            payload["artifacts"] = (_snapshot_artifacts(payload, session_id, payload.get("turn_id"))
                                    if owned is not None else [])
        return payload
    except Exception:
        return {"status": "none", "text": ""}


@app.get("/api/chat/jobs/{turn_id}/stream")
async def api_chat_job_stream(turn_id: str, after: int = 0):
    """Replay missed events, then tail this turn until its terminal event arrives."""
    # A stale browser-side pendingTurnId must fail promptly instead of receiving
    # heartbeats forever. The frontend can then recover from the final snapshot.
    require_local_http()
    try:
        _workbench_identifier(turn_id)
        receipt = _workbench_state().receipt(turn_id)
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None
    if receipt is not None:
        _workbench_receipt(receipt["session_id"], turn_id)
        if receipt["decision"] == "rejected":
            raise HTTPException(409, "本次请求未提交")
    if not _job_event_file(turn_id).is_file():
        raise HTTPException(404, "对话任务记录不存在或已失效")

    async def events():
        cursor = max(0, after)
        idle_since = time.monotonic()
        while True:
            if receipt is not None:
                try:
                    _workbench_receipt(receipt["session_id"], turn_id)
                except HTTPException:
                    yield {"event": "task_state", "data": json.dumps({"version": 1, "turn_id": turn_id, "state": "unknown"})}
                    return
            batch = _read_job_events(turn_id, cursor)
            if batch:
                idle_since = time.monotonic()
                for event in batch:
                    cursor = int(event["id"])
                    raw = event.get("data")
                    # job 日志里 question 的 data 已是序列化 JSON 字符串（实时流
                    # forward() 对 question 原样透传），这里若再 json.dumps 会双层
                    # 转义 → 前端 JSON.parse 拿到字符串而非对象 → ask_user 选项
                    # 卡片渲染成空壳。其余事件存原文/dict，与实时流的 dumps 规则
                    # 一致。因此仅 question 按实时流规则原样透传。
                    if event["event"] in ("question", "artifact") and isinstance(raw, str):
                        data_out = raw
                    else:
                        data_out = json.dumps(raw, ensure_ascii=False)
                    yield {
                        "id": str(cursor),
                        "event": event["event"],
                        "data": data_out,
                    }
                    if event["event"] == "done" or (event["event"] == "error" and receipt is None):
                        return
            else:
                if receipt is not None and not _workbench_turn_live(turn_id):
                    yield {"event": "task_state", "data": json.dumps({"version": 1, "turn_id": turn_id, "state": "unknown"})}
                    return
                # Keep proxy connections active; reconnecting remains safe if it still drops.
                if time.monotonic() - idle_since >= 15:
                    yield {"event": "ping", "data": "{}"}
                    idle_since = time.monotonic()
                await asyncio.sleep(0.25)

    return EventSourceResponse(events(), headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Content-Encoding": "identity",
    })


_CHAT_ACCEPTANCE_PHASE = ContextVar("chat_acceptance_phase", default=None)


def _with_submission_rejection(handler):
    @wraps(handler)
    async def wrapped(req):
        phase = {"claim_started": False, "scope": None}
        token = _CHAT_ACCEPTANCE_PHASE.set(phase)
        try:
            return await handler(req)
        except HTTPException as error:
            # A precise rejected request is different from a network error or
            # absent GET result. A claim attempt can never get this assertion.
            rejection = None
            if (400 <= error.status_code < 500 and not phase["claim_started"] and phase["scope"]
                    and req.sessionId and req.turnId):
                try:
                    require_local_http()
                    _workbench_identifier(req.sessionId)
                    _workbench_identifier(req.turnId)
                    # A rejection permanently wins or loses the same atomic
                    # decision as claim. Absence alone proves nothing.
                    if not _job_event_file(req.turnId).exists():
                        rejection = _workbench_state().reject(req.sessionId, req.turnId, phase["scope"],
                                                              _workbench_digest(req.model_dump()))
                except (WorkbenchError, HTTPException):
                    pass
            if rejection is not None:
                return JSONResponse({"detail": error.detail, "rejection": rejection}, status_code=error.status_code,
                                    headers={**(error.headers or {}), "Cache-Control": "no-store"})
            raise
        finally:
            _CHAT_ACCEPTANCE_PHASE.reset(token)
    return wrapped


@app.post("/api/chat/stream")
@_with_submission_rejection
async def api_chat_stream(req: ChatRequest):
    """SSE 真流式对话。

    `openclaw agent` CLI 会把整段模型输出缓冲到结束才打印（stdout 无增量）。真正跑模型的是
    常驻 gateway，它按自己的 OPENCLAW_RAW_STREAM/OPENCLAW_RAW_STREAM_PATH 把「模型原始流」逐
    token 写到**单个共享 jsonl**（SHARED_RAW_STREAM，见 scripts/gateway.sh）。后端在本轮开始时
    记下该文件尾偏移、实时 tail 之后追加的行，用 runId 闩锁隔离本轮，把 assistant_text_stream 的
    token delta 转成 SSE `token`、thinking delta 转成 `thinking`。stdout 仅留作错误/兜底。
    """
    # Reject unavailable/invalid routes before creating even private acceptance
    # state, retaining the existing no-side-effects rejection contract.
    phase = _CHAT_ACCEPTANCE_PHASE.get()
    configured = _harness_settings()
    try:
        ServiceRoute(req.service_source if "service_source" in req.model_fields_set else configured["service_source"],
                     req.execution_target if "execution_target" in req.model_fields_set else configured["execution_target"]).require_available()
        if ((req.service_source is not None and req.service_source != configured["service_source"])
                or (req.execution_target is not None and req.execution_target != configured["execution_target"])):
            raise HarnessError("当前服务已变化，请重新选择")
    except HarnessError as error:
        raise HTTPException(409, str(error)) from None
    accepted_session = req.sessionId or f"web-{int(time.time() * 1000)}"
    accepted_turn = req.turnId or uuid.uuid4().hex
    try:
        _workbench_identifier(accepted_session)
        _workbench_identifier(accepted_turn)
    except WorkbenchError:
        raise HTTPException(400, "无效的任务标识") from None
    phase["scope"] = _workbench_contract(configured)["source_scope"]
    ledger = _workbench_state()
    request_hash = _workbench_digest({**req.model_dump(), "sessionId": accepted_session, "turnId": accepted_turn})
    try:
        previous_receipt = ledger.receipt(accepted_turn)
        if previous_receipt is not None:
            ledger.owned(accepted_session, accepted_turn, phase["scope"])
            if previous_receipt["request_hash"] != request_hash:
                raise WorkbenchError("任务标识已用于其他内容", 409)
            if previous_receipt["decision"] == "rejected":
                return JSONResponse({"detail": "本次请求未提交", "rejection": ledger.public_rejection(previous_receipt)},
                                    status_code=409, headers={"Cache-Control": "no-store"})
            return await api_chat_job_stream(accepted_turn)
    except WorkbenchError as error:
        raise HTTPException(error.status, str(error)) from None
    if _job_event_file(accepted_turn).exists():
        raise HTTPException(409, "任务标识已有记录")
    _guard_uncertain_central_turn(req.sessionId)
    # 每轮末尾追加「先查技能库」提醒，抗长对话指令衰减（对用户不可见）
    runtime = _chat_backend(req.sessionId)
    config = _harness_settings()
    task_settings = dict(read_settings(ENV_FILE))
    context = await _accept_task_context(req, runtime, config, task_settings)
    central_recovery = (context.service_source == "subscription"
                        and context.subscription_backend == "central")
    if central_recovery:
        from easel.platform.execution_records import operation_turn_key
        try:
            operation_turn_key(accepted_session, accepted_turn)
        except ValueError:
            raise HTTPException(400, "本次对话标识无效，请新建对话。") from None
    direct = runtime == "api"
    alternate = runtime not in ("api", "openclaw")
    if direct:
        content, system = _direct_chat_content(req)
        message = ""
    else:
        if req.sessionId and history_path(SESSIONS_DIR, req.sessionId).is_file():
            raise HTTPException(409, "此会话属于 API 直连，请新建会话使用 OpenClaw")
        message = _chat_message(req)

    # Acceptance is durable before any agent task is created. A concurrent or
    # disconnected duplicate only reads this same receipt and original log.
    initial_scope = _workbench_contract(config)["source_scope"]
    if initial_scope != phase["scope"]:
        raise HTTPException(409, "当前账号或服务已变化")
    try:
        phase["claim_started"] = True
        receipt, created = ledger.claim(accepted_session, accepted_turn, initial_scope, request_hash)
        if not created:
            if receipt["decision"] == "rejected":
                return JSONResponse({"detail": "本次请求未提交", "rejection": ledger.public_rejection(receipt)},
                                    status_code=409, headers={"Cache-Control": "no-store"})
            return await api_chat_job_stream(accepted_turn)
        event_path = _job_event_file(accepted_turn)
        ledger._directory(event_path.parent)
        accepted_event = {"id": 1, "event": "accepted", "data": ledger.public_receipt(receipt)}
        with event_path.open("x", encoding="utf-8") as stream:
            os.chmod(event_path, 0o600)
            stream.write(json.dumps(accepted_event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (WorkbenchError, OSError) as error:
        raise HTTPException(error.status if isinstance(error, WorkbenchError) else 503, "任务记录暂不可用") from None

    # supervisor（跑 openclaw run）与 forward（转发 SSE 给浏览器）之间的事件通道。
    # 关键：run 跑在独立后台任务里，客户端断开只结束 forward，不取消 supervisor →
    # openclaw 照常跑到底、结果落盘，前端断线后 /api/chat/last 取回。
    loop = asyncio.get_event_loop()
    client_q: asyncio.Queue = asyncio.Queue()
    CLIENT_DONE = object()
    client_q.put_nowait({"t": "accepted", "text": ledger.public_receipt(receipt), "id": 1})

    async def supervisor():
        sk = accepted_session
        pk = f"web:{sk}"                 # 落盘 key（与 /api/chat/last 一致）
        turn_id = accepted_turn
        event_seq = 1
        full_text: list[str] = []        # 累积完整回答，供断线取回
        timed_out = False                # 只有真·超时才 terminate 进程；断线绝不杀

        # Claim this turn before waiting for locks, so recovery cannot return the previous turn.
        if not direct and not alternate:
            _bind_task(sk, context)
            _save_turn(pk, "running", "", {"turn_id": turn_id, "route": context.metadata()})

        event_path = _job_event_file(turn_id)

        def publish(kind, text=None, **extra):
            nonlocal event_seq
            if kind == "token" and not direct and not alternate:
                full_text.append(text)
            event_seq += 1
            data = ({"sessionKey": extra.get("sessionKey"), **_workbench_completion(sk, turn_id)} if kind == "done" else text)
            if kind == "done":
                extra["completion"] = data
            event = {"id": event_seq, "event": kind, "data": data}
            try:
                with event_path.open("a", encoding="utf-8") as ef:
                    ef.write(json.dumps(event, ensure_ascii=False) + "\n")
                    ef.flush()
            except OSError:
                pass
            client_q.put_nowait({"t": kind, "text": text, "id": event_seq, **extra})

        redactor = _web_output_redactor(lambda kind, text: publish(kind, text))

        def to_client(kind, text=None, **extra):
            if kind in ("done", "error"):
                redactor.flush()
            if kind == "done":
                publish(kind, **redactor.value(extra))
            else:
                redactor.emit(kind, text)

        # 秒级反馈：发出即亮「已收到」，不等 agent 冷启动（首个 SSE 事件，随流回放必达）
        to_client("activity", "⏳ 已收到…" if direct else "⏳ 已收到，正在唤醒 agent…")
        if central_recovery:
            # A safe reference to this accepted turn, never a financial result
            # or an authorization grant. Old clients ignore the additive event.
            publish("central_recovery", {"version": 1, "turn_id": turn_id})

        if direct:
            try:
                await _run_direct_chat(
                    req.model_copy(update={"turnId": turn_id}), sk, content, system, to_client, context=context, settings=task_settings)
            finally:
                to_client("done", sessionKey=sk)
                client_q.put_nowait(CLIENT_DONE)
            return

        if alternate:
            try:
                await _run_harness_chat(req.model_copy(update={"turnId": turn_id}),
                                        sk, message, runtime, config, to_client, context=context)
            finally:
                to_client("done", sessionKey=sk)
                client_q.put_nowait(CLIENT_DONE)
            return

        async def _run_gateway_turn(hproc):
            """HTTP 直连常驻网关跑一轮（OpenAI 兼容端点 /v1/chat/completions，原生 SSE）。

            与 CLI 路径的差异：agent 在常驻 gateway 进程里直接跑，无每轮进程冷启动；
            正文 token 经 SSE 直接进 q（与 CLI 路径共享同一消费出口）；
            收尾由主循环统一负责（proc.poll() 兼容面）。
            """
            body = {
                "model": context.model,
                "stream": True,
                "messages": [{"role": "user", "content": message}],
            }
            # session-id 必须跟 CLI 路径钉死同一个（见 _openclaw_session_id）：只带 session-key
            # 的话网关会自己另起一个 transcript —— 跨天空闲后丢历史，且万一本轮回退 CLI，
            # 两条路径会写进不同的会话文件，对话历史直接劈叉。
            headers = {"x-openclaw-session-key": f"agent:main:{sk}",
                       "x-openclaw-session-id": _openclaw_session_id(sk)}
            tool_noted = False
            saw_done = False
            got_text = False
            try:
                import httpx as _httpx
                timeout = _httpx.Timeout(TIMEOUT_CHAT + 60, connect=10)
                async with _httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                            "POST", chat_completions_url(),
                            json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            raw = _scrub_secrets((await resp.aread()).decode("utf-8", "replace"))[:200]
                            to_client("error", f"❌ 对话失败（HTTP {resp.status_code}）：{raw[:160]}")
                            return
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].lstrip()   # SSE 允许 `data:{…}`（冒号后无空格）
                            if payload == "[DONE]":
                                saw_done = True
                                break
                            try:
                                d = json.loads(payload)
                            except ValueError:
                                continue
                            if isinstance(d.get("error"), dict):   # 200 里夹错误对象：不能当正常流吞掉
                                to_client("error", f"❌ 网关返回错误：{_scrub_secrets(str(d['error']))[:160]}")
                                return
                            delta = (d.get("choices") or [{}])[0].get("delta") or {}
                            # 思考流的两个可能来源，先到先得（`sse_thinking` 闩锁，防两路都来时重复）：
                            # ① 这里的 reasoning 增量 —— openclaw 2026.6.11 的 chat/completions
                            #    实现里 reasoning/thinking 出现 0 次，**不会**给；留着是给别的网关/
                            #    以后的版本用。② 常驻 gateway 写的共享 raw 流（见下面 _tail），
                            #    它按自己的 env 写，跟这一轮是谁触发的无关，所以 HTTP 模式照样能读到。
                            rc = delta.get("reasoning_content") or delta.get("reasoning")
                            if rc:
                                run_info["sse_thinking"] = True
                                run_info["thinking_chars"] += len(rc)
                                q.put_nowait({"t": "thinking", "text": rc})
                            c = delta.get("content")
                            if c:
                                got_text = True
                                q.put_nowait({"t": "token", "text": c})
                            if delta.get("tool_calls") and not tool_noted:
                                tool_noted = True
                                to_client("activity", "🔧 正在执行操作…")
                # 流正常结束却既没正文也没 [DONE]：多半是端点没真开或中途断了。
                # 不报错的话这一轮会静默落一条空回答，还会被 /api/chat/last 原样取回。
                if not got_text and not saw_done:
                    to_client("error", "❌ 网关流异常结束：没有收到任何内容（检查 "
                                       "gateway.http.endpoints.chatCompletions 是否开启，"
                                       "或设 EASEL_CHAT_TRANSPORT=cli 回退）")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                to_client("error", f"❌ 网关连接失败：{_scrub_secrets(str(e))[:140]}")
            finally:
                # 先把 SENTINEL 排进 q（FIFO 保证它排在本轮所有 token 之后），再标记完成：
                # 主循环读到它时，前面的 token 必然已全部消费过。
                q.put_nowait(SENTINEL)
                hproc.finish()

        _heal_openclaw_session(sk)       # 清洗历史里无签名 thinking 块，防回放失效
        # 原始事件流由常驻 gateway 写到共享文件（见 SHARED_RAW_STREAM / scripts/gateway.sh），
        # 不是 agent 客户端写的。本轮开始时记下文件当前尾偏移：只读此偏移之后追加的行，
        # 再用首个新事件的 runId 闩锁本轮，隔离其它并发会话的事件。
        try:
            raw_start_offset = SHARED_RAW_STREAM.stat().st_size
        except OSError:
            raw_start_offset = 0

        cmd = openclaw_base_cmd() + [
            "--profile", OPENCLAW_PROFILE, "agent", "--agent", "main",
            "--session-key", f"agent:main:{sk}", "--session-id", _openclaw_session_id(sk),
            "--thinking", _agent_thinking_level(),
            "--timeout", str(TIMEOUT_CHAT), "--message", message,
        ]
        env = _proxy_env()
        # 注意：不要在客户端 env 上设 OPENCLAW_RAW_STREAM*——`agent` 客户端不写 raw 流，
        # 设了也没用；raw 流开关在 gateway 侧（scripts/gateway.sh）。
        # 告诉 skill：本部署的 ask_user 选项卡片是否可用。卡片依赖 gateway 的
        # question.* RPC（仅 2026.9.x 有），2026.6.11 上桥接不可用 → skill 改用
        # 「文字问答跨轮等待」拿短信验证码，而不是空等卡片超时。
        _cards_ok = question_bridge_supported is not None and question_bridge_supported()
        env["EASEL_ASKUSER_CARDS"] = "1" if _cards_ok else "0"

        # 会话级串行：同一会话若已有请求在跑，先提示排队，等它结束再开
        # （否则两个 openclaw 进程并发写同一 session 文件 → 崩溃 rc=1 / 会话串味）。
        # 双层锁：asyncio 锁管同 web 进程内并发；flock 跨进程锁管两个标签/gateway/cron 撞同一会话。
        lock = _session_lock(sk)
        xlock = _CrossProcLock(sk)
        if lock.locked():
            to_client("activity", "⏳ 这个会话上一条还在跑，排队等它结束再开始…")
        await lock.acquire()
        # flock 可能阻塞（等另一进程/标签跑完），放线程池避免卡住事件循环
        got = await loop.run_in_executor(None, xlock.acquire, min(TIMEOUT_CHAT, 300))
        if not got:
            lock.release()
            _save_turn(pk, "done", "这个会话正在另一个窗口运行，请稍候再试。", {
                "turn_id": turn_id, "route": context.metadata(), "clean_end": False, "stop_reason": "session_lock_timeout",
            })
            to_client("activity", "⏳ 这个会话正在另一个窗口运行，请稍候再试")
            to_client("done", sessionKey=sk)
            client_q.put_nowait(CLIENT_DONE)
            return

        # _resolve_transport 里既有 stat 又有阻塞 urllib 探针（最多 3s），必须丢线程：
        # 直接在协程里调会把整个事件循环——连同其它会话正在推的 SSE——一起卡住。
        is_http = (await asyncio.to_thread(_resolve_transport, sk)) == "http"
        if is_http:
            # HTTP 直连常驻网关：无进程冷启动（agent 在 gateway 进程里跑）
            proc = _GatewayHttpProc()
        else:
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT), text=True, bufsize=1, env=env,
                )
            except BaseException:
                lock.release()
                xlock.release()
                _save_turn(pk, "done", "❌ 启动失败，请重试", {
                    "turn_id": turn_id, "route": context.metadata(), "clean_end": False, "stop_reason": "spawn_failed",
                })
                to_client("error", "❌ 启动失败，请重试")
                to_client("done", sessionKey=sk)
                client_q.put_nowait(CLIENT_DONE)
                return
        _RUNNING_CHAT[sk] = proc         # 注册运行中进程（HTTP 模式为伪进程），供 /api/chat/stop
        # 经 gateway 后客户端 stdout 没有 model-fetch 标记（那是独立跑 agent 才有），先立刻
        # 给一个「正在思考」活动指示，随后 token 从共享 raw stream 流进来接管显示。
        to_client("activity", "🧠 正在思考…")

        q = asyncio.Queue()
        SENTINEL = object()
        stdout_lines = []
        run_info: dict = {"stop_reason": None, "last_ev": None, "saw_message_end": False,
                          "run_id": None,
                          "fetch_count": 0, "token_chars": 0, "thinking_chars": 0,
                          "delegated": False, "ignored_foreign_events": 0}

        def _drain_stdout():
            try:
                for line in proc.stdout:
                    stdout_lines.append(line)
                    c = re.sub(r"\x1b\[[0-9;]*m", "", line)
                    if "model-fetch] start" in c:
                        run_info["fetch_count"] += 1
                        fc = run_info["fetch_count"]
                        _emit("activity", "🧠 正在思考…" if fc == 1 else f"🔧 调用工具后继续推理（第 {fc} 步）…")
                    elif "[agent]" in c and "delegat" in c.lower():
                        run_info["delegated"] = True
                        _emit("activity", "🛠️ 制作中…")
                    m = re.search(r"ended with stopReason=(\S+)", c)
                    if m:
                        run_info["stop_reason"] = m.group(1)
            except Exception:
                pass

        def _emit(kind: str, text: str):
            loop.call_soon_threadsafe(q.put_nowait, {"t": kind, "text": text})

        # 必须等 q / run_info / _emit 都就位后再起这一轮：_run_gateway_turn 闭包引用它们，
        # 早一步 create_task 就只能靠「中间没有 await」来侥幸，改一行同步代码就会崩。
        if is_http:
            proc._task = loop.create_task(_run_gateway_turn(proc))

        # ---- ask_user 问答题桥接：轮询 gateway 的 pending question，推给前端渲染 ----
        # 背景：OpenClaw 的 ask_user 注册到 gateway 进程内，Easel 前端不消费 question RPC → 选项不可见。
        # 这里在 agent 运行期间每 2s 轮询一次，把新出现的 pending question 以 SSE `question` 事件推送，
        # 前端渲染选项卡片；用户点击后经 /api/chat/question/answer 调 question.resolve 完成回答。
        if GatewayClient is not None and not _QBRIDGE_DISABLED:
            def _question_poll():
                global _QBRIDGE_DISABLED
                if _QBRIDGE_DISABLED:
                    return
                # 版本能力门：旧版本 OpenClaw（<2026.9.x）没有 question.* RPC，连都不连——
                # 否则每轮 connect 都在网关上触发新的 scope-upgrade 配对申请。只提示一次，走文字问答。
                if question_bridge_supported is not None and not question_bridge_supported():
                    _QBRIDGE_DISABLED = True
                    _qbridge_warn_once(
                        "unsupported",
                        "[question-bridge] 当前 OpenClaw 版本无 question RPC（需 2026.9.x+），"
                        "已跳过 ask_user 选项卡片桥接，改用文字问答。")
                    return
                client = None
                pushed: set[str] = set()
                try:
                    client = GatewayClient()
                    client.connect()
                except Exception as e:
                    # connect 失败（如 NOT_PAIRED/scope-upgrade，或网关不可达）：熔断整个桥接，
                    # 不再每轮重试——否则每轮都会在网关上堆一个新的配对/权限申请。每进程只告警一次。
                    _QBRIDGE_DISABLED = True
                    _qbridge_warn_once(
                        "connect",
                        f"[question-bridge] connect gateway failed，已停用桥接（本进程），"
                        f"ask_user 改用文字问答: {e}")
                    return
                try:
                    while proc.poll() is None:
                        try:
                            items = client.list_questions(
                                session_key=f"agent:main:{sk}", status="pending")
                        except GatewayUnsupportedError as e:
                            # 连上了但没有 question RPC（版本判断漏网时的兜底）：熔断，安静退出。
                            _QBRIDGE_DISABLED = True
                            _qbridge_warn_once(
                                "unsupported",
                                f"[question-bridge] 当前 OpenClaw 版本无 question RPC，"
                                f"已停用 ask_user 选项卡片桥接（需 2026.9.x+）: {e}")
                            return
                        except Exception:
                            time.sleep(2)
                            continue
                        for it in items:
                            qid = it.get("id")
                            if qid and qid not in pushed:
                                pushed.add(qid)
                                _emit("question", json.dumps({
                                    "id": qid,
                                    "questions": it.get("questions", []),
                                    "expiresAtMs": it.get("expiresAtMs"),
                                }, ensure_ascii=False))
                        time.sleep(2)
                finally:
                    try:
                        if client is not None:
                            client.close()
                    except Exception:
                        pass
            loop.run_in_executor(None, _question_poll)

        def _handle(line: str):
            o = _raw_event_for_run(line, run_info["run_id"])
            if o is None:
                # 属其它并发 run 的事件（或无法解析）：绝不混入本轮可见流/收尾诊断，仅计数。
                try:
                    parsed = json.loads(line)
                    rid = parsed.get("runId") if isinstance(parsed, dict) else None
                    if rid is not None and run_info["run_id"] is not None and rid != run_info["run_id"]:
                        run_info["ignored_foreign_events"] += 1
                except Exception:
                    pass
                return
            # 首个带 runId 的事件闩锁本轮 run（之后 _raw_event_for_run 只放行这个 run）。
            if run_info["run_id"] is None:
                rid = o.get("runId")
                if rid is None:
                    return          # 还没拿到 runId，等下一条带 runId 的事件再闩锁
                run_info["run_id"] = rid
            ev, et, delta = o.get("event"), o.get("evtType"), o.get("delta") or ""
            # 记录最后一个 raw 事件：正常收尾 last_ev == assistant_message_end；
            # 若停在 text_delta/thinking_delta 说明输出或思考流被中断、没正常收尾（本次排查关键信号）。
            if ev:
                run_info["last_ev"] = ev
            if ev == "assistant_message_end":
                run_info["saw_message_end"] = True
            if not delta:
                return
            if ev == "assistant_text_stream" and et == "text_delta":
                if is_http:
                    # HTTP 模式正文以 SSE 为准（那条才是本请求自己的响应流）。这里再发一遍
                    # 就是同一段内容进两次队列 —— 前端会看到每个字重复。
                    return
                run_info["token_chars"] += len(delta)
                run_info["text_tail"] = (run_info.get("text_tail", "") + delta)[-160:]
                _emit("token", delta)
                return
            if ev == "assistant_thinking_stream" and et == "thinking_delta":
                if run_info.get("sse_thinking"):
                    return      # SSE 已经在供思考流了，别叠第二份
                run_info["thinking_chars"] += len(delta)
                _emit("thinking", delta)
                return

        def _tail():
            try:
                f = None
                # gateway 刚起或本轮还没产生事件时文件可能暂不存在：轮询等它出现（进程先退出则收尾）。
                while f is None:
                    try:
                        f = open(SHARED_RAW_STREAM, "r", encoding="utf-8")
                    except OSError:
                        if proc.poll() is not None:
                            return
                        time.sleep(0.04)
                with f:
                    f.seek(raw_start_offset)   # 只读本轮开始后追加的行，跳过历史轮次
                    buf = ""
                    while True:
                        chunk = f.readline()
                        if chunk == "":
                            if proc.poll() is not None:
                                buf += f.read()
                                for ln in buf.split("\n"):
                                    _handle(ln)
                                break
                            time.sleep(0.04)
                            continue
                        buf += chunk
                        while "\n" in buf:
                            ln, buf = buf.split("\n", 1)
                            _handle(ln)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(q.put_nowait, SENTINEL)

        stdout_fut = None
        if is_http:
            # 正文走 SSE，但思考流**不走**：openclaw 2026.6.11 的 chat/completions 实现里
            # reasoning/thinking 一次都没出现，不回传任何思考增量。思考只存在于常驻 gateway
            # 写的那份共享 raw 流里（它按 gateway 自己的 env 写，与谁触发无关）。所以这条路径
            # 照样 tail 它——_handle 里 text_delta 在 is_http 下直接丢弃，只取 thinking_delta，
            # 正文不会进两次。不 tail 的话「💭 思考过程」在 HTTP 模式下永远是空的。
            loop.run_in_executor(None, _tail)
        else:
            stdout_fut = loop.run_in_executor(None, _drain_stdout)
            loop.run_in_executor(None, _tail)

        deadline = time.monotonic() + TIMEOUT_CHAT + 30
        emitted = False
        # 两条路径都靠「队列里读到 SENTINEL」收尾。HTTP 模式若改用带外标志（一开始就置 True），
        # 最后一批还排在 q 里没被消费的 token 会随 poll() 转为已完成而被直接丢掉——答案尾巴被截。
        tail_finished = False
        try:
            while True:
                # A raw-stream reader failure must not be mistaken for model
                # completion. Keep the session lock until the process exits.
                if tail_finished and proc.poll() is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    to_client("error", "⏱️ 请求超时")
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=min(10, remaining))
                except asyncio.TimeoutError:
                    continue
                if item is SENTINEL:
                    tail_finished = True
                    if proc.poll() is not None:
                        break
                    continue
                if item["t"] == "token":
                    emitted = True
                    to_client("token", item["text"])
                elif item["t"] == "thinking":
                    to_client("thinking", item["text"])
                elif item["t"] == "activity":
                    to_client("activity", item["text"])
                elif item["t"] == "question":
                    to_client("question", item["text"])
            rc = proc.poll()
            # 等 stdout 读完（stopReason 行在进程收尾时才打印，避免 _tail 先发 SENTINEL 时漏读）
            if stdout_fut is not None:
                try:
                    await asyncio.wait_for(stdout_fut, timeout=2)
                except Exception:
                    pass
            sr = run_info.get("stop_reason")
            if not emitted:
                clean = clean_agent_output("".join(stdout_lines))
                if clean:
                    emitted = True
                    to_client("token", clean)
                elif rc not in (0, None):
                    err = _scrub_secrets(clean_agent_output("".join(stdout_lines)))[:200]
                    to_client("error", f"❌ 执行失败（退出码 {rc}）{' — ' + err if err else ''}")
            # 收尾检测：即使已吐了内容，只要不是「正常收尾」就显式告知——
            # 否则被截断（触顶）/被杀（负载）/流被中断，都会被当成「清晰地答完了」，
            # 用户看到的就是「答一半突然停、也不说做完」（本 bug 根因）。
            # 正常收尾的唯一标志：raw 流最后一个事件是 assistant_message_end。
            # 用户显式「停止」不是异常中断 → 不报「被中断」告警（前端已就地标注「已停止」）。
            if (emitted or run_info["thinking_chars"]) and sk not in _STOPPED_CHAT:
                note = None
                if sr and sr in ("max_tokens", "length", "model_length"):
                    note = (f"\n\n---\n⚠️ 上面这条**被截断**了（stopReason={sr}，单条回复触顶）。"
                            f"回我「继续」我接着写完，或让我把任务拆小一点。")
                elif rc not in (0, None):
                    note = (f"\n\n---\n⚠️ 生成**被中断**（退出码 {rc}，多半是超时或系统负载过高把进程杀了），"
                            f"不是正常收尾。可以让我重试。")
                elif sr == "tool_use":
                    note = ("\n\n---\n⚠️ 我刚做完这一步、**正要执行下一步操作时中断了**"
                            "（本轮以工具调用结尾却没能继续，前端把它当成答完了）。回我「继续」我接着做。")
                elif run_info.get("last_ev") not in (None, "assistant_message_end"):
                    note = ("\n\n---\n⚠️ 这条**可能没写完**——模型的输出/思考流被中断、没有正常收尾"
                            "（多为网络或模型代理把长回复的流掐断了）。回我「继续」，或重试。")
                elif run_info.get("text_tail", "").rstrip()[-1:] in ("：", ":"):
                    # 正常收尾但正文停在冒号 = 模型"我要做X："后没接着做（多为要接工具/下一步却断了）。
                    # 用户实测「所有莫名停止都停在冒号」——这一条兜住这个模式。
                    note = ("\n\n---\n⚠️ 我似乎停在了冒号处、没接着把后面的内容/操作做出来。"
                            "回我「继续」我补上。")
                if note:
                    to_client("token", note)
        finally:
            # 只有真的在 HTTP 上跑出了内容，才把这个会话钉到 http 上。钉早了（比如选路时就钉）
            # 会把一个其实没跑成的会话锁死在 http，之后每轮都往一条不通的路上撞；而钉住之后
            # 就绝不能再换回 cli —— 网关那份 transcript 我们按名字找不回来，换边即丢历史。
            if is_http and emitted:
                _pin_transport(sk, "http")
            user_stopped = sk in _STOPPED_CHAT
            _STOPPED_CHAT.discard(sk)
            # Reaching finally while the child is alive means timeout, explicit
            # stop, cancellation, or an internal stream failure. Never release
            # the session locks while such a process can still write history.
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    await asyncio.to_thread(proc.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        await asyncio.to_thread(proc.wait, timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            redactor.flush()  # also before cancellation recovery persistence
            # 诊断日志：每次对话流收尾都记一行，供事后定位「莫名停下」到底是哪种情况。
            try:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                tail = _scrub_secrets(clean_agent_output("".join(stdout_lines)))[-800:]
                with (DEBUG_DIR / "chat-stream.jsonl").open("a", encoding="utf-8") as lf:
                    lf.write(_scrub_secrets(json.dumps({
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "session": sk,
                        "rc": proc.poll(),
                        "stop_reason": run_info["stop_reason"],
                        "last_ev": run_info["last_ev"],
                        "clean_end": run_info["last_ev"] == "assistant_message_end",
                        "saw_message_end": run_info["saw_message_end"],
                        "fetch_count": run_info["fetch_count"],
                        "token_chars": run_info["token_chars"],
                        "thinking_chars": run_info["thinking_chars"],
                        "delegated": run_info["delegated"],
                        "ignored_foreign_events": run_info["ignored_foreign_events"],
                        "text_tail": "".join(full_text)[-160:],
                        "stdout_tail": tail,
                    }, ensure_ascii=False)) + "\n")
            except Exception:
                pass
            # 落盘完整结果：后端跑完整轮不依赖客户端连接，断线后前端用 /api/chat/last 取回
            _save_turn(pk, "done", "".join(full_text), {
                "turn_id": turn_id, "route": context.metadata(),
                "clean_end": run_info.get("last_ev") == "assistant_message_end",
                "stop_reason": "user_stopped" if user_stopped else run_info.get("stop_reason"),
            })
            xlock.release()
            lock.release()
            _RUNNING_CHAT.pop(sk, None)
            to_client("done", sessionKey=sk)
            client_q.put_nowait(CLIENT_DONE)

    # 把 run 跑在独立后台任务里（持强引用防 GC）——客户端断开不取消它。
    task = asyncio.create_task(supervisor())
    _BG_TASKS.add(task)
    _WORKBENCH_TURNS[(str(SESSIONS_DIR), accepted_turn)] = task

    def _bg_done(t):
        _BG_TASKS.discard(t)
        _WORKBENCH_TURNS.pop((str(SESSIONS_DIR), accepted_turn), None)
        try:
            exc = t.exception()   # 取出异常避免「never retrieved」告警
        except (Exception, asyncio.CancelledError):
            exc = None
        if t.cancelled():
            client_q.put_nowait(CLIENT_DONE)
        if exc is not None:
            # supervisor 意外崩溃：解锁 forward，别让它空等
            try:
                client_q.put_nowait(CLIENT_DONE)
            except Exception:
                pass
    task.add_done_callback(_bg_done)

    async def forward():
        """纯转发：从 client_q 取事件 yield 给浏览器。

        客户端断开（关标签/代理掐断）只会结束本生成器，supervisor 任务不受影响，
        继续把 openclaw run 跑完并落盘 → 前端断线后 /api/chat/last 取回完整结果。
        """
        idle_since = time.monotonic()
        while True:
            try:
                item = await asyncio.wait_for(client_q.get(), timeout=10)
            except asyncio.TimeoutError:
                # 长时间无输出（等模型长回复 / 制作类长任务）→ 发心跳，让用户知道没卡死。
                # 用独立的 `heartbeat` 事件，不走 `activity`：否则会覆盖掉真实的
                # 「🧠 正在思考…/🛠️ 制作中…」状态与思考流（防呆消息把思考设计顶掉的根因）。
                # 发出后重置 idle_since → 心跳按 30s 一次的节奏，不再每 10s 重复刷屏。
                if time.monotonic() - idle_since >= 30:
                    idle_since = time.monotonic()
                    yield {"event": "heartbeat", "data": json.dumps(
                        "仍在处理中，未卡住…（复杂或制作类任务会花点时间）", ensure_ascii=False)}
                continue
            if item is CLIENT_DONE:
                break
            idle_since = time.monotonic()
            t = item["t"]
            if t == "token":
                yield {"id": str(item["id"]), "event": "token", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "thinking":
                yield {"id": str(item["id"]), "event": "thinking", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "activity":
                yield {"id": str(item["id"]), "event": "activity", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "artifact":
                yield {"id": str(item["id"]), "event": "artifact", "data": item["text"]}
            elif t == "central_recovery":
                yield {"id": str(item["id"]), "event": "central_recovery", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "accepted":
                yield {"id": str(item["id"]), "event": "accepted", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "question":
                yield {"id": str(item["id"]), "event": "question", "data": item["text"]}
            elif t == "error":
                yield {"id": str(item["id"]), "event": "error", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "done":
                yield {"id": str(item["id"]), "event": "done", "data": json.dumps(item.get("completion", {"sessionKey": item.get("sessionKey")}), ensure_ascii=False)}

    return EventSourceResponse(forward(), headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Content-Encoding": "identity"})


class QuestionAnswerRequest(BaseModel):
    sessionId: str | None = None
    questionId: str
    answers: dict  # {questionId: [optionValues]}
    resolvedBy: str | None = None


@app.post("/api/chat/question/answer")
async def api_question_answer(req: QuestionAnswerRequest):
    """前端点击 ask_user 选项后调用：转发 gateway question.resolve，让等待的 agent 拿到答案。"""
    if req.questionId.startswith("harness_"):
        record = _HARNESS_QUESTIONS.get(req.questionId)
        if record is None:
            return {"ok": False, "error": "权限或问答请求已过期，请重试"}
        required = {question["questionId"] for question in record["questions"]}
        if set(req.answers or {}) != required:
            raise HTTPException(400, "回答与当前问题不匹配")
        if record["future"].done():
            return {"ok": False, "error": "请求已回答"}
        record["future"].set_result(req.answers)
        return {"ok": True}
    if _direct_api_enabled():
        raise HTTPException(409, "API 直连模式使用文字问答，请新建对话")
    if _harness_settings()["runtime"] != "openclaw":
        raise HTTPException(409, "此问题属于旧 OpenClaw 会话，请使用当前底座的新对话")
    if GatewayClient is None:
        return {"ok": False, "error": "gateway question bridge unavailable"}
    client = GatewayClient()
    try:
        client.connect()
        result = client.resolve(req.questionId, req.answers or {}, req.resolvedBy)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        client.close()


class QuestionStatusRequest(BaseModel):
    questionIds: list[str]


@app.post("/api/chat/question/status")
async def api_question_status(req: QuestionStatusRequest):
    """批量查 question 状态（重放旧事件时过滤已解决的题）。"""
    if req.questionIds and all(identifier.startswith("harness_") for identifier in req.questionIds):
        return {"ok": True, "questions": {
            identifier: {"status": _HARNESS_QUESTIONS.get(identifier, {}).get("status", "not_found")}
            for identifier in req.questionIds}}
    if _direct_api_enabled():
        return {"ok": True, "questions": {qid: {"status": "not_found"} for qid in req.questionIds}}
    if _harness_settings()["runtime"] != "openclaw":
        return {"ok": False, "questions": {}}
    if GatewayClient is None:
        return {"ok": False, "questions": {}}
    client = GatewayClient()
    try:
        client.connect()
        out = {}
        for qid in req.questionIds:
            try:
                q = client.get_question(qid)
                # QUESTION_NOT_FOUND 抛异常捕获后报 not_found；正常返回则按 status
                out[qid] = {"status": q.get("status") if q else "not_found"}
            except GatewayQuestionError as e:
                # gateway 明确错：问题已清理/不存在 = 已答或已过期，一律 not_found
                out[qid] = {"status": "not_found"}
            except Exception:
                out[qid] = {"status": "unknown"}   # 网络/连接异常：保持 unknown（前端保留显示，宁不缺题）
        return {"ok": True, "questions": out}
    except Exception as e:
        return {"ok": False, "error": str(e), "questions": {}}
    finally:
        client.close()


class StopRequest(BaseModel):
    sessionId: str | None = None
    turnId: str | None = None


@app.post("/api/chat/stop")
async def api_chat_stop(req: StopRequest):
    """用户显式停止当前会话正在跑的对话 agent：终止进程 → supervisor 收尾释放会话锁 →
    下一句立刻能发（不再卡「上一条还在跑」）。仅此显式入口会杀进程；客户端断线不经此路径。"""
    sk = (req.sessionId or "").strip()
    target = None
    if req.turnId:
        require_local_http()
        target = _workbench_receipt(sk, req.turnId)
        if target is None:
            return {"stopped": False, "state": "unknown", "turn_id": req.turnId}
        task = _WORKBENCH_TURNS.get((str(SESSIONS_DIR), req.turnId))
        current = {}
        try:
            current = json.loads(_turn_file(f"web:{sk}").read_text())
        except (OSError, ValueError):
            pass
        if current.get("turn_id") != req.turnId:
            # Accepted but still queued: cancel that supervisor, never the
            # different turn currently holding this session's process lock.
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                payload = {"turn_id": req.turnId, "status": "done", "text": "", "clean_end": False, "stop_reason": "stopped"}
                _workbench_state().save_snapshot(sk, req.turnId, payload)
                batch = _read_job_events(req.turnId)
                if not any(event.get("event") in ("done", "error") for event in batch):
                    with _job_event_file(req.turnId).open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"id": max((int(event.get("id", 0)) for event in batch), default=0) + 1,
                                                 "event": "done", "data": {"sessionKey": sk, **_workbench_completion(sk, req.turnId)}}) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                return {"stopped": True, "state": "stopped", "turn_id": req.turnId}
            response = await api_chat_turn(sk, req.turnId)
            state = json.loads(response.body)["state"]
            return {"stopped": state == "stopped", "state": state, "turn_id": req.turnId}
    proc = _RUNNING_CHAT.get(sk) if sk else None
    if proc is not None and proc.poll() is None:
        _STOPPED_CHAT.add(sk)          # 标记为用户停止，供 supervisor 正常收尾（不报「被中断」）
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            await asyncio.to_thread(proc.wait, timeout=3)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        # supervisor removes the running marker only after persisting the final
        # snapshot and releasing both session locks.
        deadline = time.monotonic() + 5
        while _RUNNING_CHAT.get(sk) is proc and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if _RUNNING_CHAT.get(sk) is proc:
            return {"stopped": False, "state": "stopping", **({"turn_id": req.turnId} if req.turnId else {})}
        if target is not None:
            response = await api_chat_turn(sk, req.turnId)
            state = json.loads(response.body)["state"]
            return {"stopped": state == "stopped", "state": state, "turn_id": req.turnId}
        return {"stopped": True, "state": "stopped"}
    if target is not None:
        response = await api_chat_turn(sk, req.turnId)
        state = json.loads(response.body)["state"]
        return {"stopped": state == "stopped", "state": state, "turn_id": req.turnId}
    return {"stopped": False, "state": "unknown"}


def _submit_sync_task(message: str, timeout: int, context: TaskContext, *,
                      session_id: str | None = None, turn_id: str | None = None,
                      on_complete=None) -> asyncio.Task:
    """Own the thread until it actually exits, independent of request cancellation."""
    context.require_available()
    route = context.metadata()
    owner = (object(), context)
    if session_id:
        if session_id in _SYNC_SESSIONS or session_id in _RUNNING_CHAT or _session_lock(session_id).locked():
            raise HTTPException(409, "这个会话上一条还在运行，请稍后再试")
        _SYNC_SESSIONS[session_id] = owner

    async def execute():
        lock = _session_lock(session_id) if session_id and context.runtime == "openclaw" else None
        locked = False
        try:
            if lock is not None:
                await lock.acquire()
                locked = True
            if session_id:
                _bind_task(session_id, context)
                _save_turn(f"web:{session_id}", "running", "", {"route": route})
            worker = asyncio.get_running_loop().run_in_executor(
                None, run_agent_sync, message, timeout, session_id, context)
            while True:
                try:
                    result = await asyncio.shield(worker)
                    break
                except asyncio.CancelledError:
                    # Cancelling an asyncio waiter cannot stop the underlying thread.
                    # Keep ownership and settings protection until its real completion.
                    continue
            clean_end = getattr(result, "clean_end", True)
            reason = getattr(result, "stop_reason", "completed")
            if session_id:
                _save_turn(f"web:{session_id}", "done", result, {
                    "turn_id": turn_id, "route": route, "stop_reason": reason, "clean_end": clean_end})
            if on_complete:
                on_complete(clean_end, result)
            return result
        except Exception as error:
            detail = "Agent 执行失败，请检查本机 CLI、登录和模型配置"
            if session_id:
                _save_turn(f"web:{session_id}", "done", detail, {
                    "turn_id": turn_id, "route": route, "stop_reason": getattr(error, "stop_reason", "error"), "clean_end": False})
            if on_complete:
                on_complete(False, detail)
            raise HTTPException(502, detail) from None
        finally:
            if locked:
                lock.release()
            if session_id and _SYNC_SESSIONS.get(session_id) is owner:
                _SYNC_SESSIONS.pop(session_id, None)
            task = asyncio.current_task()
            _HARNESS_TASKS.discard(task)
            _BG_TASKS.discard(task)

    task = asyncio.create_task(execute())
    _HARNESS_TASKS.add(task)
    _BG_TASKS.add(task)
    # Detached requests have no waiter to retrieve execution failures.
    def consume_error(done):
        if session_id and _SYNC_SESSIONS.get(session_id) is owner:
            _SYNC_SESSIONS.pop(session_id, None)
        _HARNESS_TASKS.discard(done)
        _BG_TASKS.discard(done)
        if not done.cancelled():
            done.exception()
    task.add_done_callback(consume_error)
    return task


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """非流式对话（备选）。"""
    _guard_uncertain_central_turn(req.sessionId)
    runtime = _chat_backend(req.sessionId)
    config = _harness_settings()
    task_settings = dict(read_settings(ENV_FILE))
    context = await _accept_task_context(req, runtime, config, task_settings)
    if runtime not in ("api", "openclaw"):
        message = _chat_message(req)
        sk = req.sessionId or uuid.uuid4().hex
        errors = []
        def emit(kind, text):
            if kind == "error":
                errors.append(text)
        result = await _run_harness_chat(req, sk, message, runtime, config, emit,
                                         interactive=False, context=context)
        if errors:
            raise HTTPException(502, errors[0])
        return {"response": result, "sessionKey": sk}
    if runtime == "api":
        content, system = _direct_chat_content(req)
        errors = []
        def emit(kind, text):
            if kind == "error":
                errors.append(text)
        sk = req.sessionId or uuid.uuid4().hex
        result = await _run_direct_chat(req, sk, content, system, emit, context=context, settings=task_settings)
        if errors:
            raise HTTPException(502, errors[0])
        return {"response": result, "sessionKey": sk}
    if req.sessionId and history_path(SESSIONS_DIR, req.sessionId).is_file():
        raise HTTPException(409, "此会话属于 API 直连，请新建会话使用 OpenClaw")
    # 每轮末尾追加「先查技能库」提醒，抗长对话指令衰减（对用户不可见）
    message = _chat_message(req)
    # chat 可能中途触发制作层长任务 → 用 TIMEOUT_CHAT，与流式 /api/chat/stream 一致（勿用 300s）
    sk = req.sessionId or uuid.uuid4().hex
    task = _submit_sync_task(message, TIMEOUT_CHAT, context, session_id=sk, turn_id=req.turnId)
    result = await asyncio.shield(task)
    return {"response": result}


class SkillRequest(BaseModel):
    skill: str
    input: str
    persona: str | None = None


@app.post("/api/skill")
async def api_skill(req: SkillRequest):
    config = _harness_settings()
    context = await _accept_task_context(ChatRequest(message=req.input), config["runtime"], config)
    if context.runtime == "api":
        raise HTTPException(409, "API 直连模式支持聊天；SKILL 执行需要具备本机工具的 Agent 运行时")
    skill_full = find_skill(req.skill)
    if skill_full is None:
        raise HTTPException(404, f"SKILL '{req.skill}' 不存在")
    message = f"{_persona_prefix(req.persona)}请执行 /{skill_full}，内容如下：\n\n{req.input}"
    # 统一给足超时：制作类 SKILL（生视频/多镜合成）可能跑很久，取安全上界
    timeout = TIMEOUT_PRODUCE
    result = await asyncio.shield(_submit_sync_task(message, timeout, context))
    return {"response": result}


@app.get("/api/outputs")
def api_outputs():
    # 产物树是全量递归扫描（文件量大时单次可达数十秒）。必须是同步 handler：
    # FastAPI 会自动放入线程池执行；若写成 async def 直调，扫描期间会阻塞事件循环，
    # 全站所有请求（含 /api/status）一起挂起等它。
    return get_output_tree()


@app.get("/api/artifacts/archive")
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
        with safe_snapshot(full, OUTPUTS_DIR) as snapshot, zipfile.ZipFile(snapshot) as archive:
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
                relative = (full.parent / name).relative_to(OUTPUTS_DIR.resolve()).as_posix()
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
    scan = asyncio.create_task(asyncio.to_thread(safe_snapshot, full, OUTPUTS_DIR))
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


@app.get("/api/output/{path:path}")
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


@app.get("/api/media/{path:path}")
async def api_media(path: str, request: Request):
    """Serve a scanned immutable snapshot, including Range requests."""
    from easel.platform.privacy import snapshot_response
    full = _safe_output_path(path)
    snapshot = await _output_snapshot(full)
    return snapshot_response(snapshot, full.name, request.headers.get("range", ""))


# 系统数据目录/文件——不允许从内容库删除（删了会丢登录态/日历/发布记录）
PROTECTED_OUTPUTS = {"_login", "_analytics", "_schedule.json", "_ideas.json",
                     "_publish", "_publish.log", "_sessions", "_profile_build", "_debug", "_inbox"}
UPLOAD_EXTS = IMAGE_EXTS | VIDEO_EXTS | {
    ".pdf", ".txt", ".md", ".markdown", ".csv", ".json", ".srt", ".vtt",
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".mp3", ".wav", ".m4a"}
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
    if _private_output_path(OUTPUTS_DIR / rel):
        raise HTTPException(403, '运行时私有数据不可通过产物接口访问')
    full = (OUTPUTS_DIR / rel).resolve()
    root = OUTPUTS_DIR.resolve()
    if full == root or root not in full.parents:
        raise HTTPException(403, '非法路径')
    if must_exist and not full.exists():
        raise HTTPException(404, '不存在')
    return full


def _is_protected(full: Path) -> bool:
    """路径的顶层段是否属于受保护的系统项。"""
    try:
        rel = full.relative_to(OUTPUTS_DIR.resolve())
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


@app.delete("/api/output/{path:path}")
async def api_output_delete(path: str):
    """删除内容库里的单个文件或整个项目目录。系统数据（_login/_analytics/日历/发布记录）受保护。"""
    full = _safe_output_target(path)
    if _is_protected(full):
        raise HTTPException(403, '系统数据受保护，不可从内容库删除')
    is_dir = full.is_dir()
    try:
        if is_dir:
            shutil.rmtree(full)
        else:
            full.unlink()
    except OSError as e:
        raise HTTPException(500, f'删除失败：{e}')
    return {"ok": True, "deleted": path, "kind": "dir" if is_dir else "file"}


@app.post("/api/upload")
async def api_upload(
    files: list[UploadFile] = File(...),
    sessionId: str = Form(...),
):
    """Store chat attachments in a session-scoped inbox and return opaque refs."""
    scope = _attachment_scope(sessionId)
    batch = time.strftime('%Y%m%d-') + uuid.uuid4().hex[:6]
    dest = OUTPUTS_DIR / "_inbox" / scope / batch
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
        saved.append({"id": _attachment_id(scope, rel), "name": target.name, "path": rel})
    if not saved:
        raise HTTPException(400, '没有文件')
    return {"ok": True, "files": saved}


@app.get("/api/upload/limits")
async def api_upload_limits():
    """当前上传上限（MB）——前端在文件超限时据此切换到本地复制通道。"""
    return {"ok": True, "max_mb": MAX_UPLOAD_MB}


@app.post("/api/upload/local")
async def api_upload_local(
    files: list[UploadFile] = File(...),
    sessionId: str = Form(...),
):
    """超过上传上限的文件复制通道：1MB 分块流式落盘（不整读进内存）、无大小上限；
    产出与 /api/upload 同构的附件引用（id/name/path），附件校验与清单链路零改动。"""
    scope = _attachment_scope(sessionId)
    batch = time.strftime('%Y%m%d-') + uuid.uuid4().hex[:6]
    dest = OUTPUTS_DIR / "_inbox" / scope / batch
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
        saved.append({"id": _attachment_id(scope, rel), "name": target.name, "path": rel})
    if not saved:
        raise HTTPException(400, '没有文件')
    return {"ok": True, "files": saved}


def _write_login_marker(platform: str, state: str, message: str = '') -> None:
    """回写登录标记 outputs/_login/<平台>.json（与 login_state.write_status 同格式，原子写）。
    whoami 真校验确认已登录后调用 → _account_logged_in 的快速路径此后自愈并持久。"""
    LOGIN_DIR.mkdir(parents=True, exist_ok=True)
    data = {"state": state, "message": message, "qr": "", "ts": int(time.time())}
    st = LOGIN_DIR / f'{platform}.json'
    tmp = st.with_suffix('.json.tmp')
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, st)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _account_logged_in(platform: str, cfg: dict) -> bool:
    """尽力判断某平台是否已登录。
    浏览器平台的登录态只有启动浏览器才真能知道（profile 里总有 Cookies 文件，存在≠已登录，
    会误报），故这里只信「本流程最近一次登录成功」——即 status.json == success。
    biliup 的 cookies.json 只有登录成功才生成，可直接判。"""
    backend = cfg['backend']
    if backend == 'unsupported':
        return False
    if backend == 'biliup':
        return (PROJECT_ROOT / 'cookies.json').is_file()
    if backend == 'wechat-oa':
        # 发布+数据都走「后台会话」→ 以 mp 后台登录成功为准；AppID 凭证作为兜底（旧配置）
        try:
            if _mp_login_status().get('state') == 'success':
                return True
        except Exception:
            pass
        return _wechat_has_credentials()
    st = LOGIN_DIR / f'{platform}.json'
    if st.is_file():
        try:
            return json.loads(st.read_text(encoding="utf-8")).get('state') == 'success'
        except Exception:
            return False
    return False


def _login_status(platform: str) -> dict:
    """读登录状态文件 + 二维码是否就绪。"""
    st = LOGIN_DIR / f'{platform}.json'
    data = {'state': 'unknown', 'message': ''}
    if st.is_file():
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            data = {'state': d.get('state', 'unknown'), 'message': d.get('message', '')}
        except Exception:
            pass
    # A runner that exits before writing its status must become an actionable error,
    # never the ambiguous ``unknown`` state shown as an endless spinner in the UI.
    proc = LOGIN_PROCESSES.get(platform)
    if data['state'] not in ('success', 'expired', 'error') and proc is not None:
        code = proc.poll()
        if code is not None:
            data = {'state': 'error', 'message': f'登录程序异常退出（退出码 {code}），请查看 outputs/_login/{platform}.log'}
    qr = LOGIN_DIR / f'{platform}.png'
    if data['state'] in ('qr_ready', 'window_login') and qr.is_file():
        data['qr'] = f'_login/{platform}.png'
        try:
            data['qrTs'] = qr.stat().st_mtime_ns // 1_000_000
        except OSError:
            data['qrTs'] = 0
    else:
        data['qr'] = ''
        data['qrTs'] = 0
    return data


@app.get("/api/accounts")
async def api_accounts():
    return [
        {'platform': pf, 'name': cfg['name'], 'backend': cfg['backend'],
         'supported': cfg['backend'] != 'unsupported',
         'loggedIn': _account_logged_in(pf, cfg),
         'note': cfg.get('note', '')}
        for pf, cfg in LOGIN_RUNNERS.items()
    ]


@app.post("/api/login/{platform}")
async def api_login_start(platform: str, restart: bool = False):
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    lock = _LOGIN_START_LOCKS.setdefault(platform, asyncio.Lock())
    async with lock:
        return await _start_login(platform, restart)


async def _start_login(platform: str, restart: bool = False):
    """启动后台登录并立即返回，二维码由前端状态轮询获取。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    backend = cfg['backend']
    if backend == 'unsupported':
        raise HTTPException(400, f"{cfg['name']} 暂不可用：{cfg.get('note', '')}")
    if backend == 'wechat-oa':
        # 公众号不走扫码：前端应改用凭证表单提交到 /api/accounts/{platform}/credentials。
        return {'mode': 'credentials', 'configured': _wechat_has_credentials(),
                'message': '微信公众号请填写 AppID / AppSecret'}
    existing = LOGIN_PROCESSES.get(platform)
    if existing is not None and existing.poll() is None:
        if not restart:
            return {'mode': 'qr', **_login_status(platform)}
        try:
            existing.terminate()
            await asyncio.to_thread(existing.wait, timeout=8)
        except subprocess.TimeoutExpired:
            raise HTTPException(409, '上一次登录仍在退出，请稍后重试') from None
        LOGIN_PROCESSES.pop(platform, None)
    LOGIN_DIR.mkdir(parents=True, exist_ok=True)
    qr = LOGIN_DIR / f'{platform}.png'
    status = LOGIN_DIR / f'{platform}.json'
    for f in (qr, status):
        try:
            f.unlink()
        except OSError:
            pass
    if backend == 'xhs':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'xhs_publish.py'), 'login', '--no-proxy',
               '--qr-out', str(qr), '--status-file', str(status), '--timeout', str(LOGIN_TIMEOUT)]
    elif backend == 'biliup':
        # B站：TV 端扫码登录 API 生成二维码 + 写 biliup cookie（biliup login 需真终端，前端用不了）
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'bili_login.py'), 'login',
               '--qr-out', str(qr), '--status-file', str(status),
               '--cookie', str(PROJECT_ROOT / 'cookies.json'), '--timeout', str(LOGIN_TIMEOUT)]
    elif backend == 'douyin':
        code_file = LOGIN_DIR / f'{platform}.code'
        try:
            code_file.unlink()
        except OSError:
            pass
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'douyin_publish.py'), 'login',
               '--no-proxy',
               '--qr-out', str(qr), '--status-file', str(status),
               '--sms-code-file', str(code_file), '--timeout', str(LOGIN_TIMEOUT)]
    else:
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'web_publisher.py'), 'login-qr',
               '--platform', cfg['wp'], '--qr-out', str(qr), '--status-file', str(status),
               '--timeout', str(LOGIN_TIMEOUT)]
    # 新登录开始 → 清掉旧的 whoami 缓存（登录前可能缓存了「未登录」），避免登录成功后仍读到旧结果
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE.pop(platform, None)
    log_path = LOGIN_DIR / f'{platform}.log'
    log_file = log_path.open('a', encoding='utf-8')
    login_env = _proxy_env()
    login_env['PYTHONIOENCODING'] = 'utf-8'
    login_env['PYTHONUTF8'] = '1'
    try:
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=login_env,
                                stdout=log_file, stderr=subprocess.STDOUT)
    finally:
        log_file.close()
    LOGIN_PROCESSES[platform] = proc
    s = _login_status(platform)
    if s['state'] == 'unknown':
        s.update(state='starting', message='正在启动浏览器并准备二维码…')
    return {'mode': 'qr', **s}


@app.get("/api/login/{platform}/status")
async def api_login_status(platform: str):
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    s = _login_status(platform)
    if s.get('state') == 'success':
        # 登录刚成功 → 清掉登录前缓存的「未登录」whoami 结果，令下次 whoami 重新真校验；
        # 否则卡片会因 WHOAMI_TTL(600s) 内的旧 false 持续显示「未登录」（本次视频号问题的根因）。
        # 只清缓存、不改任何登录/检测逻辑。
        with _WHOAMI_LOCK:
            _WHOAMI_CACHE.pop(platform, None)
    return {'mode': 'qr', **s}


class SmsCodeRequest(BaseModel):
    code: str


@app.post("/api/login/{platform}/sms")
async def api_login_sms(platform: str, req: SmsCodeRequest):
    """回填短信验证码：写入 runner 轮询的一次性验证码文件（见 login_state.read_sms_code）。

    登录 runner 检测到风控短信墙时把状态置 sms_required，前端弹输入框，用户把手机
    收到的验证码提交到这里，runner 读走后填码提交，继续完成登录。
    """
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    code = ''.join(ch for ch in (req.code or '') if ch.isdigit())
    if not (4 <= len(code) <= 8):
        raise HTTPException(400, '验证码应为 4-8 位数字')
    LOGIN_DIR.mkdir(parents=True, exist_ok=True)
    (LOGIN_DIR / f'{platform}.code').write_text(code, encoding='utf-8')
    return {'ok': True}


class WechatCredentials(BaseModel):
    appId: str = ''
    appSecret: str = ''
    name: str = ''
    author: str = ''


@app.get("/api/accounts/{platform}/credentials")
async def api_get_credentials(platform: str):
    """读取凭证式平台（目前仅公众号）的已配置状态（AppID 脱敏，AppSecret 不回传）。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get('backend') != 'wechat-oa':
        raise HTTPException(404, '该平台不使用凭证登录')
    acc = _wechat_web_account()
    app_id = acc.get('app_id', '') or ''
    return {
        'configured': bool(app_id and acc.get('app_secret')),
        'appIdMasked': (app_id[:6] + '***' + app_id[-4:]) if len(app_id) > 10 else ('***' if app_id else ''),
        'name': acc.get('name', '') or '',
        'author': acc.get('author', '') or '',
    }


@app.post("/api/accounts/{platform}/credentials")
async def api_save_credentials(platform: str, req: WechatCredentials):
    """保存凭证式平台（公众号）的 AppID/AppSecret，写入 skill 配置并调官方接口验证。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get('backend') != 'wechat-oa':
        raise HTTPException(404, '该平台不使用凭证登录')
    app_id = (req.appId or '').strip()
    app_secret = (req.appSecret or '').strip()
    if not app_id or not app_secret:
        raise HTTPException(400, 'AppID 和 AppSecret 都不能为空')
    _wechat_save_credentials(app_id, app_secret, name=req.name.strip(), author=req.author.strip())
    ok, msg = _wechat_verify_token()
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE.pop(platform, None)
    if ok:
        _write_login_marker(platform, 'success', req.name.strip() or '微信公众号')
        return {'ok': True, 'message': '公众号凭证已保存并验证通过'}
    # 校验失败：凭证已存（下次改正后可直接重试），但明确告知失败原因（常见 40164 IP 白名单 / 40125 密钥错误）
    return {'ok': False, 'message': f'凭证已保存但验证未通过：{msg}。若是 40164 请把服务器出口 IP 加入公众号 IP 白名单。'}


def _mp_login_status() -> dict:
    """读公众号后台(mp)登录状态 + 二维码（文件由 weixin_mp_stats.py login 写）。"""
    st = LOGIN_DIR / "wechat-oa-mp.json"
    data = {"state": "unknown", "message": ""}
    if st.is_file():
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            data = {"state": d.get("state", "unknown"), "message": d.get("message", "")}
        except Exception:
            pass
    proc = LOGIN_PROCESSES.get("wechat-oa-mp")
    if data["state"] not in ("success", "expired", "error") and proc is not None and proc.poll() is not None:
        data = {"state": "error", "message": f"登录程序退出（码 {proc.poll()}），见 outputs/_login/wechat-oa-mp.log"}
    qr = LOGIN_DIR / "wechat-oa-mp.png"
    if data["state"] in ("qr_ready", "window_login") and qr.is_file():
        data["qr"] = "_login/wechat-oa-mp.png"
        try:
            data["qrTs"] = qr.stat().st_mtime_ns // 1_000_000
        except OSError:
            data["qrTs"] = 0
    else:
        data["qr"] = ""
        data["qrTs"] = 0
    return data


def _stop_mp_login_on_shutdown() -> None:
    """正常重启 Web 时回收扫码进程，避免它继续写入下一次登录的状态。"""
    proc = LOGIN_PROCESSES.pop("wechat-oa-mp", None)
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        _write_login_marker("wechat-oa-mp", "expired", "服务已重启，请重新扫码登录")


@app.post("/api/accounts/{platform}/mp-login")
async def api_mp_login_start(platform: str):
    """启动「公众号后台」扫码登录（数据中心取数用，管理员级会话，独立于 AppID 凭证）。
    默认直连起 Playwright 出二维码；受限网络可设 EASEL_PROXY / https_proxy 走正向代理。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get("backend") != "wechat-oa":
        raise HTTPException(404, "该平台不使用公众号后台登录")
    # 重复点击复用正在进行的登录，不能删除其二维码或启动第二个 Chromium。
    proc = LOGIN_PROCESSES.get("wechat-oa-mp")
    if proc is not None and proc.poll() is None:
        return {"mode": "qr", **_mp_login_status()}
    LOGIN_DIR.mkdir(parents=True, exist_ok=True)
    for f in (LOGIN_DIR / "wechat-oa-mp.png", LOGIN_DIR / "wechat-oa-mp.json"):
        try:
            f.unlink()
        except OSError:
            pass
    wx_proxy = os.environ.get("EASEL_PROXY") or os.environ.get("https_proxy") or ""
    cmd = [sys.executable, str(SHARED_SCRIPTS / "weixin_mp_stats.py"), "login",
           "--proxy", wx_proxy, "--qr-out", str(LOGIN_DIR / "wechat-oa-mp.png"),
           "--status-file", str(LOGIN_DIR / "wechat-oa-mp.json"), "--timeout", "240"]
    log_file = (LOGIN_DIR / "wechat-oa-mp.log").open("a", encoding="utf-8")
    env = _proxy_env()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env,
                            stdout=log_file, stderr=subprocess.STDOUT)
    log_file.close()
    LOGIN_PROCESSES["wechat-oa-mp"] = proc
    status = _mp_login_status()
    if status["state"] == "unknown":
        status.update(state="starting", message="正在启动浏览器并准备二维码…")
    return {"mode": "qr", **status}


@app.get("/api/accounts/{platform}/mp-login/status")
async def api_mp_login_status(platform: str):
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get("backend") != "wechat-oa":
        raise HTTPException(404, "该平台不使用公众号后台登录")
    return {"mode": "qr", **_mp_login_status()}


@app.get("/api/accounts/{platform}/whoami")
async def api_account_whoami(platform: str, force: bool = False):
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    task = _WHOAMI_TASKS.get(platform)
    if task is None or task.done():
        task = asyncio.create_task(_check_account_whoami(platform, force))
        _WHOAMI_TASKS[platform] = task
        def finish(completed):
            if _WHOAMI_TASKS.get(platform) is completed:
                _WHOAMI_TASKS.pop(platform, None)

        task.add_done_callback(finish)
    return await asyncio.shield(task)


async def _check_account_whoami(platform: str, force: bool = False):
    """真校验登录态 + 读昵称/头像（起 headless 浏览器，数秒）。前端开页后台调用以自愈假阳性。
    带 TTL 进程内缓存（避免账号页+工作台重复起浏览器）；确认已登录则回写标记，令快速路径自愈。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    backend = cfg['backend']
    if backend == 'unsupported':
        return {'loggedIn': False, 'name': '', 'avatar': ''}
    if backend == 'wechat-oa':
        if _mp_login_status()['state'] != 'success':
            acc = _wechat_web_account()
            return {'loggedIn': _account_logged_in(platform, cfg),
                    'name': acc.get('name', '') or '微信公众号', 'avatar': ''}
    process_key = 'wechat-oa-mp' if backend == 'wechat-oa' else platform
    login_process = LOGIN_PROCESSES.get(process_key)
    if login_process is not None and login_process.poll() is None:
        if (_mp_login_status() if backend == 'wechat-oa' else _login_status(platform))['state'] == 'success':
            try:
                await asyncio.to_thread(login_process.wait, timeout=8)
                if login_process.poll() is None:
                    raise HTTPException(409, '登录已确认，浏览器仍在保存账号，请稍后重试校验')
            except subprocess.TimeoutExpired:
                raise HTTPException(409, '登录已确认，浏览器仍在保存账号，请稍后重试校验')
        else:
            with _WHOAMI_LOCK:
                previous = _WHOAMI_CACHE.get(platform)
            return previous[1] if previous else {
                'loggedIn': _account_logged_in(platform, cfg), 'name': '', 'avatar': '',
            }
    marker_path = LOGIN_DIR / f'{process_key}.json'
    def marker_version():
        try:
            return marker_path.read_bytes()
        except OSError:
            return None

    initial_marker = marker_version()
    with _WHOAMI_LOCK:
        hit = _WHOAMI_CACHE.get(platform)
    cache_ttl = (min(WHOAMI_TTL, 60) if hit and hit[1].get('loggedIn')
                 and (not hit[1].get('name') or not hit[1].get('avatar')) else WHOAMI_TTL)
    if (not force and hit and (time.time() - hit[0]) < cache_ttl
            and hit[1]['loggedIn'] == _account_logged_in(platform, cfg)):
        return hit[1]
    if backend == 'biliup':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'bili_login.py'), 'whoami',
               '--cookie', str(PROJECT_ROOT / 'cookies.json')]
    elif backend == 'xhs':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'xhs_publish.py'), 'whoami', '--no-proxy']
    elif backend == 'wechat-oa':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'weixin_mp_stats.py'), 'whoami', '--no-proxy']
    elif backend == 'douyin':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'douyin_publish.py'), 'whoami', '--no-proxy']
    else:
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'web_publisher.py'), 'whoami',
               '--platform', cfg['wp']]
    try:
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=str(PROJECT_ROOT), env=_proxy_env(),
                                       capture_output=True, text=True, encoding='utf-8',
                                       timeout=35 if backend == 'xhs' else 40 if backend in ('douyin', 'wechat-oa') else 60)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, '校验超时（浏览器起不来或网络慢）')
    data = {'loggedIn': False, 'name': '', 'avatar': ''}
    confident = False
    for line in reversed((proc.stdout or '').strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                d = json.loads(line)
                data = {'loggedIn': bool(d.get('loggedIn')), 'name': d.get('name') or '', 'avatar': d.get('avatar') or ''}
                # 有 error 字段 = 校验本身失败（浏览器起不来/网络抖动/崩溃），不是可信的「未登录」结论
                confident = proc.returncode == 0 and isinstance(d.get('loggedIn'), bool) and not d.get('error')
                break
            except Exception:
                continue
    current_process = LOGIN_PROCESSES.get(process_key)
    if (marker_version() != initial_marker
            or (current_process is not None and current_process.poll() is None)):
        return {'loggedIn': _account_logged_in(platform, cfg), 'name': '', 'avatar': ''}
    if not confident:
        raise HTTPException(502, '未能确认当前账号身份，请稍后重试校验；原登录状态和账号资料保持不变')
    verified_logged_in = data['loggedIn']
    if backend == 'wechat-oa' and not verified_logged_in and _wechat_has_credentials():
        account = _wechat_web_account()
        data = {'loggedIn': True, 'name': account.get('name', '') or '微信公众号', 'avatar': ''}
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE[platform] = (time.time(), data)
    # 回写标记：确认已登录 → 快速路径（/api/accounts、/api/analytics/platforms）此后也正确；
    # biliup 走 cookies.json 判定，不用标记文件。
    if backend != 'biliup':
        if verified_logged_in:
            _write_login_marker(process_key, 'success', data.get('name') or '')
        else:
            try:
                marker_path.unlink()
            except OSError:
                pass
    return data


@app.post("/api/logout/{platform}")
async def api_logout(platform: str):
    """退出登录：删持久化浏览器 profile + 登录状态/二维码/头像文件（biliup 删 cookies.json）。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    deleted = []
    if cfg['backend'] == 'wechat-oa':
        # 1) 清 AppID 凭证（旧配置兜底）
        _wechat_clear_credentials()
        deleted.append('wechat-publisher.yaml:accounts.web')
        # 2) 停掉可能仍在跑的后台登录进程（避免它又写回 success 标记）
        proc = LOGIN_PROCESSES.pop('wechat-oa-mp', None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        # 3) 删 AppID 标记 + mp 后台会话标记/二维码/日志（登录态判定看的就是 wechat-oa-mp.json）
        for name in (f'{platform}.json', f'{platform}.png',
                     'wechat-oa-mp.json', 'wechat-oa-mp.png', 'wechat-oa-mp.log'):
            f = LOGIN_DIR / name
            try:
                if f.is_file():
                    f.unlink()
                    deleted.append(f.name)
            except OSError:
                pass
        # 4) 删 mp 后台浏览器持久化会话（真正退出登录）
        mpdir = (BROWSER_PROFILES / 'WeixinMpProfile').resolve()
        if BROWSER_PROFILES.resolve() in mpdir.parents and mpdir.is_dir():
            shutil.rmtree(mpdir, ignore_errors=True)
            deleted.append('WeixinMpProfile')
        with _WHOAMI_LOCK:
            _WHOAMI_CACHE.pop(platform, None)
        return {'ok': True, 'deleted': deleted}
    prof_name = cfg.get('profile')
    if prof_name:
        pdir = (BROWSER_PROFILES / prof_name).resolve()
        if BROWSER_PROFILES.resolve() in pdir.parents and pdir.is_dir():
            shutil.rmtree(pdir, ignore_errors=True)
            deleted.append(prof_name)
    if cfg['backend'] == 'biliup':
        ck = PROJECT_ROOT / 'cookies.json'
        if ck.is_file():
            ck.unlink()
            deleted.append('cookies.json')
    for suffix in ('.json', '.png', '-me.png', '.code'):
        f = LOGIN_DIR / f'{platform}{suffix}'
        try:
            if f.is_file():
                f.unlink()
                deleted.append(f.name)
        except OSError:
            pass
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE.pop(platform, None)
    return {'ok': True, 'deleted': deleted}


# 归因层：可抓创作数据的平台（多数走 Playwright 登录态；bilibili 用 biliup cookies 不起浏览器、
# wechat-oa 走 mp 后台会话 Playwright 拦截数据 XHR）
ANALYTICS_PLATFORMS = {"xiaohongshu", "douyin", "kuaishou", "zhihu", "weixin-channels", "bilibili", "wechat-oa"}
_analytics_inflight: dict[str, asyncio.Task] = {}


def _run_analytics_command(cmd: list[str], env: dict[str, str], timeout: int):
    process = subprocess.Popen(
        cmd, cwd=str(PROJECT_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.stdout.close()
            process.stderr.close()
            process.wait(timeout=5)
        raise


@app.get("/api/analytics/platforms")
async def api_analytics_platforms():
    """列出支持抓数据的平台 + 各自登录态（前端据此渲染平台选择器）。"""
    return [
        {"platform": pf, "name": LOGIN_RUNNERS.get(pf, {}).get("name", pf),
         "loggedIn": _account_logged_in(pf, LOGIN_RUNNERS.get(pf, {}))}
        for pf in LOGIN_RUNNERS if pf in ANALYTICS_PLATFORMS
    ]


@app.get("/api/analytics/{platform}")
async def api_analytics(platform: str):
    """抓取某平台已登录账号的创作数据，同平台并发刷新共享一次有时限的任务。"""
    if platform not in ANALYTICS_PLATFORMS:
        raise HTTPException(404, "该平台暂不支持数据抓取")
    task = _analytics_inflight.get(platform)
    if task is None or task.done():
        task = asyncio.create_task(_fetch_analytics(platform))
        _analytics_inflight[platform] = task
        task.add_done_callback(lambda finished: _finish_analytics(platform, finished))
    return await asyncio.shield(task)


def _finish_analytics(platform: str, task: asyncio.Task) -> None:
    if _analytics_inflight.get(platform) is task:
        _analytics_inflight.pop(platform, None)
    if not task.cancelled():
        task.exception()


async def _fetch_analytics(platform: str):
    # B站用 cookie 调 API（无浏览器 profile）、公众号走 mp 后台会话（Playwright 拦截数据 XHR，见下），单独分支；其余走 account_stats（Playwright）
    if platform == "bilibili":
        cmd = [sys.executable, str(SHARED_SCRIPTS / "bili_login.py"), "stats",
               "--cookie", str(PROJECT_ROOT / "cookies.json")]
    elif platform == "wechat-oa":
        # 公众号数据走「后台网页端」(mp.weixin.qq.com 管理员会话 + Playwright 拦截数据 XHR)：
        # 开发者 datacube 接口需认证+群发+接口权限，多数号取不到；后台端有登录态即可看到发表记录/数据。
        # 需先扫码登录 mp 后台（weixin_mp_stats.py login）；默认直连，受限网络才需 EASEL_PROXY/https_proxy。
        wx_proxy = os.environ.get("EASEL_PROXY") or os.environ.get("https_proxy") or ""
        cmd = [sys.executable, str(SHARED_SCRIPTS / "weixin_mp_stats.py"), "stats",
               "--proxy", wx_proxy, "--count", "20"]
    else:
        # 代理策略由 account_stats.py 按平台自定（xhs 直连、其它走 env），后端照常传 _proxy_env
        cmd = [sys.executable, str(SHARED_SCRIPTS / "account_stats.py"), "fetch", "--platform", platform]
    ana_env = _proxy_env()
    ana_env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        proc = await asyncio.to_thread(
            _run_analytics_command, cmd, ana_env, 180 if platform == "wechat-oa" else 90)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "抓取超时，已停止浏览器；请检查网络，关闭正在进行的登录或校验后重试")
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                result = json.loads(line)
            except Exception:
                continue
            if not isinstance(result, dict):
                continue
            if proc.returncode or result.get("error"):
                raise HTTPException(502, str(result.get("error") or "平台数据抓取失败")[:240])
            if (result.get("loggedIn") is True
                    and all(result.get(key) is None for key in ("followers", "likes", "following", "posts"))
                    and not result.get("metrics") and not result.get("notes")):
                raise HTTPException(502, "账号已登录，但平台未返回可用数据；请检查网络或平台维护状态后重试")
            return result
    detail = (proc.stderr or "").strip().splitlines()[-1:] or ["未取到数据"]
    raise HTTPException(502, f"未取到数据（可能未登录或平台改版）：{detail[0][:120]}")


MEDIA_REQUIRED = {"xiaohongshu", "douyin", "kuaishou", "weixin-channels", "bilibili"}
VIDEO_ONLY_PUBLISH = {"douyin", "weixin-channels", "bilibili"}   # 只能发视频的平台


class PublishRequest(BaseModel):
    title: str = ''
    body: str = ''
    media: list[str] = []
    tags: str = ''


def _write_publish_status(status_file: Path, state: str, message: str = '') -> None:
    """写异步发布状态（与 login_state 同格式），原子写。"""
    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = status_file.with_suffix('.tmp')
        tmp.write_text(json.dumps({'state': state, 'message': message, 'ts': int(time.time())},
                                  ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, status_file)
    except Exception:
        pass


def _read_publish_status(platform: str) -> dict:
    st = PUBLISH_DIR / f'{platform}.json'
    if st.is_file():
        try:
            d = json.loads(st.read_text(encoding='utf-8'))
            return {'state': d.get('state', 'unknown'), 'message': d.get('message', '')}
        except Exception:
            pass
    return {'state': 'unknown', 'message': ''}


def _run_publish_bg(platform: str, cmd: list, title: str, body: str, cfg: dict,
                    status_file: Path, code_file: Path) -> None:
    """后台线程跑发布脚本（脚本自身把 starting/sms_required/verifying/success/error 写进 status_file）。
    结束后兜底补写终态 + 记 _publish.log + 成功则回流排期。"""
    ok = False
    out = err = ''
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=_publish_env(),
                              capture_output=True, text=True, timeout=900)
        ok = proc.returncode == 0
        out, err = proc.stdout or '', proc.stderr or ''
    except subprocess.TimeoutExpired:
        err = '发布超时（>900s）'
    except Exception as e:  # noqa: BLE001
        err = f'发布进程异常：{e}'
    try:
        with (OUTPUTS_DIR / '_publish.log').open('a', encoding='utf-8') as lf:
            lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {platform}(async) ok={ok} =====\n")
            lf.write('CMD: ' + ' '.join(cmd) + '\nSTDOUT:\n' + out[-2000:] + '\nSTDERR:\n' + err[-2000:] + '\n')
    except Exception:
        pass
    # 脚本正常会写终态；异常/超时没写到时兜底补一个
    if _read_publish_status(platform)['state'] not in ('success', 'error'):
        _write_publish_status(status_file, 'success' if ok else 'error',
                              '发布成功' if ok else ('\n'.join((err or out).strip().splitlines()[-4:]) or '发布失败'))
    try:
        code_file.unlink()
    except OSError:
        pass
    if ok:
        try:
            items = _read_schedule()
            items.append({'id': uuid.uuid4().hex[:12], 'title': title,
                          'date': time.strftime('%Y-%m-%d'), 'platform': cfg['name'],
                          'time': time.strftime('%H:%M'), 'status': 'published', 'note': body[:200],
                          'kind': 'content', 'source': 'publish-page'})
            _write_schedule(items)
        except Exception:
            pass


def _start_async_publish(platform: str, cmd: list, title: str, body: str, cfg: dict,
                         status_file: Path, code_file: Path) -> dict:
    """启动异步发布：清旧码/状态 → 起后台线程 → 立即返回。前端轮询 /api/publish/{p}/status，
    遇 sms_required 弹输入框、提交到 /api/publish/{p}/sms。"""
    try:
        code_file.unlink()
    except OSError:
        pass
    _write_publish_status(status_file, 'starting', '发布中…（若触发风控会要求短信验证）')
    threading.Thread(target=_run_publish_bg,
                     args=(platform, cmd, title, body, cfg, status_file, code_file),
                     daemon=True).start()
    # 关键：**不返回 ok:true**——这只是「已启动」的应答，真正结果要靠轮询 /status。
    # 若这里给 ok:true，旧前端会把它当「已发布」立刻显示成功（假成功 bug，真机踩过）。
    return {'async': True, 'pending': True, 'message': '发布已启动，请稍候…'}


@app.get("/api/publish/{platform}/status")
async def api_publish_status(platform: str):
    """轮询异步发布状态：starting/sms_required/verifying/success/error。"""
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    return {'mode': 'publish', **_read_publish_status(platform)}


@app.post("/api/publish/{platform}/sms")
async def api_publish_sms(platform: str, req: SmsCodeRequest):
    """发布触发短信墙时回填验证码（写发布 runner 轮询的一次性验证码文件）。"""
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    code = ''.join(ch for ch in (req.code or '') if ch.isdigit())
    if not (4 <= len(code) <= 8):
        raise HTTPException(400, '验证码应为 4-8 位数字')
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLISH_DIR / f'{platform}.code').write_text(code, encoding='utf-8')
    return {'ok': True}


@app.post("/api/publish/{platform}")
async def api_publish(platform: str, req: PublishRequest):
    """一键发布：分发到对应 publisher 脚本真发（--exec）。二次确认在前端。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    backend = cfg['backend']
    if backend == 'unsupported':
        raise HTTPException(400, f"{cfg['name']} 暂不支持一键发布")
    if not req.title.strip() and not req.body.strip():
        raise HTTPException(400, '标题/正文不能为空')
    imgs, vids = [], []
    for rel in req.media or []:
        full = _safe_output_path(rel)
        ext = full.suffix.lower()
        if ext in VIDEO_EXTS:
            vids.append(str(full))
        elif ext in IMAGE_EXTS:
            imgs.append(str(full))
    if platform in MEDIA_REQUIRED and not imgs and not vids:
        raise HTTPException(400, f"{cfg['name']} 需附带图片或视频")
    if imgs and vids:
        raise HTTPException(400, '同一条内容不能同时发图片和视频，请二选一')
    if platform in VIDEO_ONLY_PUBLISH and not vids:
        raise HTTPException(400, f"{cfg['name']} 只能发视频，请附带一个视频文件")
    title = req.title.strip() or req.body.strip()[:20]
    tags = req.tags or ''
    py = sys.executable
    if platform == 'xiaohongshu':
        base = [py, str(SHARED_SCRIPTS / 'xhs_publish.py')]
        cmd = base + ['publish-video', '--no-proxy', '--video', vids[0]] if vids else base + ['publish', '--no-proxy', '--images', ','.join(imgs)]
        cmd += ['--title', title, '--content', req.body, '--tags', tags, '--exec']
    elif platform == 'bilibili':
        # B站投稿：直接调 biliup CLI（需 cookies.json，PATH 上有 biliup）。必须视频；
        # tid=36「知识」；B站投稿必须≥1 标签，无则兜底「日常」。
        bili_tag = tags.replace('#', '').replace('，', ',').strip().strip(',') or '日常'
        cmd = ['biliup', '-u', str(PROJECT_ROOT / 'cookies.json'), 'upload', vids[0],
               '--title', title[:80], '--tid', '36', '--copyright', '1', '--tag', bili_tag]
        if req.body.strip():
            cmd += ['--desc', req.body[:2000]]
    elif platform == 'douyin':
        base = [py, str(SHARED_SCRIPTS / 'douyin_publish.py')]
        cmd = base + ['publish-video', '--no-proxy', '--video', vids[0]] if vids else base + ['publish', '--no-proxy', '--images', ','.join(imgs)]
        cmd += ['--title', title, '--content', req.body, '--tags', tags, '--exec']
        # 抖音发布可能触发风控短信墙——异步跑 + 状态/验证码文件，前端轮询到 sms_required 时弹输入框
        PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        status_file = PUBLISH_DIR / 'douyin.json'
        code_file = PUBLISH_DIR / 'douyin.code'
        cmd += ['--status-file', str(status_file), '--sms-code-file', str(code_file)]
        return _start_async_publish(platform, cmd, title, req.body, cfg, status_file, code_file)
    elif platform == 'wechat-oa':
        # 微信公众号：走「后台会话」发布（免 AppID/AppSecret、免 IP 白名单）。
        # 正文 MD → 公众号 HTML（skill 排版器）→ 会话建草稿（weixin_mp_stats.py publish，内嵌图传 mp CDN）。
        if _mp_login_status().get('state') != 'success':
            raise HTTPException(400, '公众号后台未登录：请先在账号页点「登录公众号后台」扫码')
        if not imgs:
            raise HTTPException(400, '公众号文章需要一张封面图，请附带至少一张图片')
        if vids:
            raise HTTPException(400, '公众号发图文文章，请附带封面/正文图片而非视频')
        cover = imgs[0]
        PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = uuid.uuid4().hex[:12]
        md_path = PUBLISH_DIR / f'wechat-oa-{stamp}.md'
        html_path = PUBLISH_DIR / f'wechat-oa-{stamp}.html'
        body_md = req.body or ''
        extra_imgs = imgs[1:]
        if extra_imgs:
            body_md += "\n\n" + "\n\n".join(f'![]({p})' for p in extra_imgs)
        md_path.write_text(f"# {title}\n\n{body_md}\n", encoding='utf-8')
        conv = subprocess.run([py, str(WECHAT_SKILL_SCRIPTS / 'html_converter.py'),
                               str(md_path), '-o', str(html_path)],
                              cwd=str(PROJECT_ROOT), env=_proxy_env(),
                              capture_output=True, text=True, timeout=60)
        if conv.returncode != 0 or not html_path.is_file():
            raise HTTPException(500, f"排版失败：{(conv.stderr or conv.stdout or '')[-200:]}")
        wx_proxy = os.environ.get('EASEL_PROXY') or os.environ.get('https_proxy') or ''
        cmd = [py, str(SHARED_SCRIPTS / 'weixin_mp_stats.py'), 'publish', '--proxy', wx_proxy,
               '--html', str(html_path), '--cover', cover, '--title', title,
               '--digest', (req.body or '').strip()[:100], '--author', '']
    else:
        cmd = [py, str(SHARED_SCRIPTS / 'web_publisher.py'), 'publish',
               '--platform', cfg['wp'], '--title', title, '--desc', req.body,
               '--tags', tags, '--exec']
        media = vids[0] if vids else (imgs[0] if imgs else None)
        if media:
            cmd += ['--media', media]
    # 公众号走后台会话（Playwright，脚本自带 --proxy，默认直连）；其余平台走 _publish_env
    pub_env = _proxy_env() if platform == 'wechat-oa' else _publish_env()
    try:
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=str(PROJECT_ROOT), env=pub_env,
                                       capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, '发布超时（媒体处理慢或流程卡住）')
    ok = proc.returncode == 0
    tail = (proc.stderr or proc.stdout or '').strip().splitlines()
    detail = '\n'.join(tail[-8:])
    try:
        with (OUTPUTS_DIR / '_publish.log').open('a', encoding='utf-8') as lf:
            lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {platform} rc={proc.returncode} ok={ok} =====\n")
            lf.write('CMD: ' + ' '.join(cmd) + '\n')
            lf.write('STDOUT:\n' + (proc.stdout or '')[-2000:] + '\n')
            lf.write('STDERR:\n' + (proc.stderr or '')[-2000:] + '\n')
    except Exception:
        pass
    if ok:
        try:
            items = _read_schedule()
            items.append({'id': uuid.uuid4().hex[:12], 'title': title,
                          'date': time.strftime('%Y-%m-%d'), 'platform': cfg['name'],
                          'time': time.strftime('%H:%M'), 'status': 'published',
                          'note': req.body[:200], 'kind': 'content', 'source': 'publish-page'})
            _write_schedule(items)
        except Exception:
            pass
    return {'ok': ok, 'message': '发布成功' if ok else '发布失败（见 detail）', 'detail': detail}


class ProfileBuildRequest(BaseModel):
    name: str
    form: dict


@app.post("/api/profile/build")
async def api_profile_build(req: ProfileBuildRequest):
    """首次引导：表单 → 写基线画像（确定性，秒可用）→ **后台**跑 agent 分析社媒链接增强。

    改异步：立即返回（基线已写、画像即可用），避免 agent 增强(~2min)阻塞请求被 code-server
    代理超时掐断（前端曾因此报 API 400）。前端轮询 /api/profile/build/status/{name} 看增强进度。
    """
    name = (req.name or '').strip()  # 自动去掉首尾空格
    if not name:
        raise HTTPException(400, '画像名不能为空（去掉首尾空格后为空，请输入有效名称）')
    if '/' in name or '\\' in name:
        raise HTTPException(400, '画像名不能包含 / 或 \\ 字符，请改掉后重试')
    if name.startswith(('.', '_')):
        raise HTTPException(400, '画像名不能以 . 或 _ 开头，请换个开头')
    config = _harness_settings()
    context = await _accept_task_context(ChatRequest(message="profile enhancement"), config["runtime"], config)
    if context.runtime == "api":
        raise HTTPException(409, "画像增强需要具备本机工具的 Agent 运行时")
    pd = PROFILES_DIR / name
    if pd.exists():
        raise HTTPException(409, f'画像「{name}」已存在，请换一个名字')
    _write_baseline_profile(name, req.form or {})
    instruction = _form_to_instruction(name, req.form or {})
    msg = (f"请执行 /skill-profile-builder 完善已存在的画像「{name}」。用户已通过表单提供以下信息，我已按此写好 profiles/{name}"
           f"/ 的基线六维文件。请：①尽力抓取用户给的社媒链接分析已发内容/风格/受众（抓不到就降级，标注[待补充]，勿臆造）②据分析结果润色/补全各维度文件 ③给出一句话完成度摘要。表单信息如下：\n\n{instruction}")

    _write_profile_status(name, 'running', 'AI 正在分析并增强画像…')

    def completed(ok, text):
        _write_profile_status(name, 'done' if ok else 'failed', text)

    _submit_sync_task(msg, TIMEOUT_PRODUCE, context, on_complete=completed)
    # 基线已写、画像立即可用；增强在后台，前端轮询状态
    return {'created': pd.is_dir(), 'name': name, 'async': True, 'status': 'running'}


def _profile_status_file(name: str) -> Path:
    return PROFILE_BUILD_DIR / f'{name}.json'


def _write_profile_status(name: str, state: str, log: str = '') -> None:
    """原子写画像增强状态。"""
    try:
        PROFILE_BUILD_DIR.mkdir(parents=True, exist_ok=True)
        f = _profile_status_file(name)
        tmp = f.with_suffix('.tmp')
        tmp.write_text(json.dumps({'state': state, 'log': log, 'ts': int(time.time())},
                                  ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, f)
    except Exception:
        pass


@app.get("/api/profile/build/status/{name}")
async def api_profile_build_status(name: str):
    """查画像增强进度：running / done / failed / unknown。"""
    f = _profile_status_file(name)
    if f.is_file():
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            return {'state': d.get('state', 'unknown'), 'log': d.get('log', '')}
        except Exception:
            pass
    return {'state': 'unknown', 'log': ''}


def _form_to_instruction(name: str, form: dict) -> str:
    def g(k: str, default: str = '（未填）') -> str:
        v = form.get(k)
        if isinstance(v, list):
            return '、'.join(str(x) for x in v) if v else default
        return str(v).strip() if v not in (None, '') else default
    links = form.get('links') or {}
    links_txt = '\n'.join(f'  - {p}: {u}' for p, u in links.items() if u) or '  （未提供）'
    return (f"画像名：{name}\n运营平台：{g('platforms')}\n起号状态：{g('accountStage')}"
            f"\n社媒主页链接：\n{links_txt}\n想做的方向：{g('direction')}"
            f"\n为什么做/我的优势：{g('reason')}\n运营目标：{g('goal')}"
            f"\n想产出的形式：{g('formats')}\n喜欢看的内容/对标账号：{g('likes')}"
            f"\n期望调性：{g('tone')}\n不做的内容/红线：{g('avoid')}\n")


def _write_baseline_profile(name: str, form: dict) -> None:
    """从表单确定性生成六维基线文件。链接派生字段标 [待 AI 分析]。"""
    pd = PROFILES_DIR / name
    pd.mkdir(parents=True, exist_ok=True)

    def g(k: str, default: str = '') -> str:
        v = form.get(k)
        if isinstance(v, list):
            return '、'.join(str(x) for x in v)
        return str(v).strip() if v not in (None, '') else default
    direction = g('direction') or '[待补充]'
    reason = g('reason') or '[待补充]'
    goal = g('goal')
    formats = g('formats')
    tone = g('tone') or '[待分析]'
    likes = g('likes')
    avoid = g('avoid')
    platforms = form.get('platforms') or []
    links = form.get('links') or {}
    (pd / 'identity.md').write_text(
        f"# 身份定位\n\n## 我是谁\n\n{direction}\n\n## 差异化\n\n{reason}\n\n## 内容方向\n\n{direction}"
        f"{'（形式：' + formats + '）' if formats else ''}\n"
        f"{'运营目标：' + goal if goal else ''}\n",
        encoding='utf-8')
    (pd / 'style.md').write_text(
        f"# 内容风格\n\n## 语气\n\n{tone}\n\n## 开头结构\n\n[待 AI 分析已发内容]\n\n## 视觉风格\n\n[待 AI 分析]\n\n## 内容节奏\n\n{formats or '[待补充]'}\n\n## 标志性元素\n\n[待 AI 分析]\n",
        encoding='utf-8')
    (pd / 'audience.md').write_text(
        '# 目标受众\n\n## 核心人群\n\n[待 AI 分析/待补充]\n\n## 兴趣标签\n\n[待补充]\n\n## 痛点\n\n[待补充]\n\n## 互动特征\n\n[待 AI 分析已发内容]\n',
        encoding='utf-8')
    plat_lines = []
    for p in platforms:
        url = links.get(p, '')
        plat_lines.append(f"## {p}\n\n主页：{url or '[待补充]'}\n粉丝量级 / 内容形式：[待补充]\n")
    (pd / 'platforms.md').write_text(
        '# 平台运营\n\n' + ('\n'.join(plat_lines) if plat_lines else '[待补充]\n'),
        encoding='utf-8')
    (pd / 'preferences.md').write_text(
        f"# 偏好与红线\n\n## 要做的\n\n{direction}\n\n## 不做的\n\n{avoid or '[待补充]'}\n\n## 合规底线\n\n{avoid or '[待补充]'}\n",
        encoding='utf-8')
    (pd / 'memory.md').write_text(
        f"# 经验沉淀\n\n## 内容洞察\n\n{'喜欢的内容/对标：' + likes if likes else '[待 AI 分析已收藏/点赞]'}\n\n## 踩过的坑\n\n[待积累]\n",
        encoding='utf-8')


@app.delete("/api/session/{session_key}")
async def api_delete_session(session_key: str):
    """删除 OpenClaw 本地的 session 记录。"""
    if session_key in _SYNC_SESSIONS or session_key in _RUNNING_CHAT or _session_lock(session_key).locked():
        raise HTTPException(409, "请先停止当前对话再删除")
    record = harness_store.binding(SESSIONS_DIR, session_key)
    if record and record.get("runtime") not in ("api", "openclaw"):
        from easel.harnesses.rpc import close_sessions
        await close_sessions(session_key)
        harness_store.binding_path(SESSIONS_DIR, session_key).unlink(missing_ok=True)
        _turn_file(f"web:{session_key}").unlink(missing_ok=True)
        return {"deleted": True, "nativeHistoryDeleted": False}

    def missing_history(reason):
        # A failed first turn can own Easel state before native history exists.
        if record:
            harness_store.binding_path(SESSIONS_DIR, session_key).unlink(missing_ok=True)
            _turn_file(f"web:{session_key}").unlink(missing_ok=True)
            return {"deleted": True, "nativeHistoryDeleted": False}
        return {"deleted": False, "reason": reason}

    direct_history = history_path(SESSIONS_DIR, session_key)
    if direct_history.is_file():
        if session_key in _RUNNING_CHAT:
            raise HTTPException(409, "请先停止当前对话再删除")
        direct_history.unlink()
        harness_store.binding_path(SESSIONS_DIR, session_key).unlink(missing_ok=True)
        _turn_file(f"web:{session_key}").unlink(missing_ok=True)
        return {"deleted": True}
    if _direct_api_enabled():
        return missing_history("session not found")
    sessions_file = _oc_state_dir() / 'agents' / 'main' / 'sessions' / 'sessions.json'
    if not sessions_file.is_file():
        return missing_history("sessions file not found")
    data = json.loads(sessions_file.read_text(encoding="utf-8"))
    full_key = f'agent:main:{session_key}' if not session_key.startswith('agent:') else session_key
    for key in (full_key, session_key):
        if key in data:
            del data[key]
            sessions_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            harness_store.binding_path(SESSIONS_DIR, session_key).unlink(missing_ok=True)
            _turn_file(f"web:{session_key}").unlink(missing_ok=True)
            return {'deleted': True}
    return missing_history("session not found")


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


@app.get("/api/trends")
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


SCHEDULE_FILE = OUTPUTS_DIR / "_schedule.json"
SCHEDULE_STATUSES = {"idea", "draft", "scheduled", "published"}
SCHEDULE_KINDS = {"content", "event"}


def _read_schedule() -> list[dict]:
    if not SCHEDULE_FILE.is_file():
        return []
    try:
        d = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _write_schedule(items: list[dict]) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SCHEDULE_FILE)


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


@app.get("/api/schedule")
async def api_schedule_list():
    return _read_schedule()


@app.post("/api/schedule")
async def api_schedule_create(req: ScheduleItem):
    items = _read_schedule()
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
    _write_schedule(items)
    return item


@app.put("/api/schedule/{sid}")
async def api_schedule_update(sid: str, req: ScheduleItem):
    items = _read_schedule()
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
            _write_schedule(items)
            return it
    raise HTTPException(404, "排期不存在")


@app.delete("/api/schedule/{sid}")
async def api_schedule_delete(sid: str):
    items = _read_schedule()
    new = [it for it in items if it.get("id") != sid]
    if len(new) == len(items):
        raise HTTPException(404, "排期不存在")
    _write_schedule(new)
    return {"ok": True, "deleted": sid}


@app.get("/api/schedule/context")
async def api_schedule_context(days: int = 14):
    """规划摘要（发布节奏/断更缺口 + 待发排期 + 临近节点 + 建议）——薄封装 calendar_ops，
    前端页头「近期节点/建议」与 Agent 读回共用同一逻辑。失败返回空摘要不抛错。"""
    cmd = [sys.executable, str(SHARED_SCRIPTS / "calendar_ops.py"),
           "--data", str(SCHEDULE_FILE), "context", "--days", str(max(1, min(days, 90)))]
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=_proxy_env(),
                              capture_output=True, text=True, timeout=20)
        return json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
    except Exception:
        return {}


IDEAS_FILE = OUTPUTS_DIR / "_ideas.json"
IDEA_STATUSES = {"pending", "doing", "done"}


def _read_ideas() -> list[dict]:
    if not IDEAS_FILE.is_file():
        return []
    try:
        d = json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _write_ideas(items: list[dict]) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = IDEAS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(IDEAS_FILE)


class IdeaItem(BaseModel):
    title: str
    note: str = ""
    source: str = ""
    status: str = "pending"


@app.get("/api/ideas")
async def api_ideas_list():
    return _read_ideas()


@app.post("/api/ideas")
async def api_ideas_create(req: IdeaItem):
    items = _read_ideas()
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
    _write_ideas(items)
    return item


@app.put("/api/ideas/{iid}")
async def api_ideas_update(iid: str, req: IdeaItem):
    items = _read_ideas()
    for it in items:
        if it.get("id") == iid:
            it.update({
                "title": req.title.strip() or it.get("title", "未命名选题"),
                "note": req.note,
                "source": req.source,
                "status": req.status if req.status in IDEA_STATUSES else it.get("status", "pending"),
            })
            _write_ideas(items)
            return it
    raise HTTPException(404, "选题不存在")


@app.delete("/api/ideas/{iid}")
async def api_ideas_delete(iid: str):
    items = _read_ideas()
    new = [it for it in items if it.get("id") != iid]
    if len(new) == len(items):
        raise HTTPException(404, "选题不存在")
    _write_ideas(new)
    return {"ok": True, "deleted": iid}


# Focused private account bridge. Dynamic roots preserve workspace/test isolation.
_PLATFORM_PREFLIGHTS = 0

def _platform_change_guard():
    if (_RUNNING_CHAT or _BG_TASKS or _DIRECT_CHAT_TASKS or _HARNESS_TASKS
            or _SYNC_SESSIONS or _PLATFORM_PREFLIGHTS):
        raise HarnessError("请等待已接受或运行中的任务完成后修改平台账号")

from easel.platform.web import create_router as _create_platform_router
from easel.harnesses.rpc import close_sessions as _close_platform_sessions
_platform_router = _create_platform_router(lambda: PROJECT_ROOT, _platform_change_guard, _close_platform_sessions)
app.include_router(_platform_router)
from easel.platform.central_web import create_router as _create_central_platform_router
_central_platform_router = _create_central_platform_router(lambda: PROJECT_ROOT, _platform_change_guard, _close_platform_sessions)
app.include_router(_central_platform_router)

async def _accept_task_context(req, runtime, config, env=None):
    global _PLATFORM_PREFLIGHTS
    if _platform_router.platform_changes or _central_platform_router.platform_changes:
        raise HTTPException(409, "平台账号正在修改，请稍后重新提交任务")
    _PLATFORM_PREFLIGHTS += 1
    try:
        return await asyncio.to_thread(_task_context, req, runtime, config, env)
    finally:
        _PLATFORM_PREFLIGHTS -= 1

async def _subscription_readiness(config):
    from easel.platform.client import PlatformClient
    from easel.harnesses.capabilities import supports_subscription
    def read():
        if config.get("subscription_backend", "legacy") == "central":
            from easel.platform.central import CentralClient
            client = CentralClient(PROJECT_ROOT)
            state = client.status()
            ready = False
            detail = "请登录中央账号并选择可用文本模型"
            if state["authenticated"]:
                try:
                    models = client.models()["models"]
                    selected = next((item for item in models if item["id"] == config.get("subscription_model")), None)
                    ready = bool(supports_subscription(config["runtime"], "central") and config["execution_target"] == "local" and selected
                                 and harnesses.describe(config["runtime"])["installed"])
                    detail = ("中央测试文本执行可用（模拟供应商）" if selected and selected["test_only"] else
                              "中央文本执行可用") if ready else "中央文本执行尚未开放或当前账号无可用模型授权"
                except HarnessError as error:
                    detail = str(error)
            return {"configured": state["configured"], "ready": ready, "authenticated": state["authenticated"], "detail": detail}
        client = PlatformClient(PROJECT_ROOT)
        state = client.status()
        ready = False
        detail = "平台服务未配置" if not state["configured"] else "请登录平台并获得可用测试授权"
        if state["authenticated"]:
            try:
                catalog = client.models()
                grants = client.call("GET", "/v1/entitlements").get("entitlements", [])
                model = config.get("subscription_model", "")
                ready = bool(supports_subscription(config["runtime"], "legacy") and config["execution_target"] == "local"
                             and catalog.get("ready") and model in [x.get("id") for x in catalog.get("models", [])]
                             and any(g.get("status") == "active" and model in g.get("models", []) and g.get("remaining_tokens", 0) > 0 for g in grants))
                detail = "平台测试授权可用；真实上游认证尚未验证" if ready else "平台模型或测试授权不可用"
            except HarnessError as error:detail = str(error)
        return {"configured": state["configured"], "ready": ready, "authenticated": state["authenticated"], "detail": detail}
    return await asyncio.to_thread(read)

if __name__ == "__main__":
    import uvicorn
    from easel.runtime_env import web_environment

    os.environ.update(web_environment(dict(os.environ)))
    openclaw_base_cmd.cache_clear()
    port = int(os.environ.get("EASEL_PORT", "7860"))
    proxy_url = os.environ.get("VSCODE_PROXY_URI", "").replace("{{port}}", str(port))
    print("\n  ✦ Easel Web")
    print(f"  http://localhost:{port}")
    if proxy_url:
        print(f"  {proxy_url}")
    print()
    # 监听 0.0.0.0（非仅回环）是为了兼容 Docker/远程容器端口转发访问 Easel 的场景；
    # local_write_guard/TrustedHostMiddleware 只挡浏览器发起的跨站请求，见其注释里的局限说明。
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", proxy_headers=False)
