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

# This file is imported both as "app" (web/ on sys.path) and as "web.app". Register
# one instance under both names so its state and the routes bound to it are shared.
_MODULE_NAMES = {"app", "web.app"}
if __name__ in _MODULE_NAMES:
    for _name in _MODULE_NAMES - {__name__}:
        sys.modules.setdefault(_name, sys.modules[__name__])

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
from easel.routes import planning as _planning
from easel.routes import updates as _updates
from easel.routes import accounts as _accounts
from easel.routes import outputs as _outputs
from easel.routes import settings as _settings
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

PROFILE_BUILD_DIR = OUTPUTS_DIR / "_profile_build"   # 异步画像构建的状态文件（避免长请求被代理超时）
DEBUG_DIR = OUTPUTS_DIR / "_debug"   # 诊断日志（对话流收尾情况等），_ 前缀不进内容库
SESSIONS_DIR = OUTPUTS_DIR / "_sessions"   # 每会话最近一轮的完整结果，供 SSE 连接中断后前端取回
# 非 _ 前缀的历史系统目录（归因层数据），内容库不展示（真产物一律在项目目录内）
SYSTEM_TOPLEVEL_DIRS = {"analytics"}
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
    _accounts._stop_mp_login_on_shutdown()


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


app.include_router(_settings.create_router(sys.modules[__name__]))






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










# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 环境安装（skills/shared/scripts/install_tool.py 引擎桥）
# 体检走引擎 check --json；安装后台跑、前端轮询 /api/env/job/{id}。
# ═══════════════════════════════════════════════════════════════════════















# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 模型配置（v1：按真实 .env / openclaw.json 只读展示 + 真自测）
# ═══════════════════════════════════════════════════════════════════════


















def _busy() -> bool:
    return bool(_RUNNING_CHAT or _BG_TASKS or _DIRECT_CHAT_TASKS or _HARNESS_TASKS or _SYNC_SESSIONS
                or _PLATFORM_PREFLIGHTS or _platform_router.platform_changes
                or _central_platform_router.platform_changes)


app.include_router(_updates.create_router(sys.modules[__name__]))


















































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
        full = _outputs._safe_output_target(normalized)
        if not full.is_file():
            raise HTTPException(404, "附件不存在")
        return full
    source = _workbench_contract()["source_scope"]
    expected = _workbench_state().reference_id(normalized, source)
    import hmac
    if not hmac.compare_digest(attachment.id, expected):
        raise HTTPException(403, "附件不可用")
    return _outputs._safe_output_path(normalized)


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
    full = _outputs._safe_output_path(path)
    if _outputs._is_protected(full):
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
        path = _outputs._safe_output_target(attachment.path)
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
            current = json.loads(_turn_file(f"web:{sk}").read_text(encoding="utf-8"))
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


app.include_router(_outputs.create_router(sys.modules[__name__]))


app.include_router(_accounts.create_router(sys.modules[__name__]))


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


app.include_router(_planning.create_router(sys.modules[__name__]))


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
