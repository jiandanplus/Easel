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
from easel.routes.schemas import AttachmentRef, ChatRequest
from easel.routes import planning as _planning
from easel.routes import updates as _updates
from easel.routes import system as _system
from easel.routes import accounts as _accounts
from easel.routes import outputs as _outputs
from easel.routes import settings as _settings
from easel.routes import chat as _chat
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


_SUBSCRIPTION_STATE = None


def _subscription_state():
    global _SUBSCRIPTION_STATE
    from easel.subscription_state import SubscriptionState
    if _SUBSCRIPTION_STATE is None or _SUBSCRIPTION_STATE.root != PROJECT_ROOT:
        _SUBSCRIPTION_STATE = SubscriptionState(PROJECT_ROOT)
    return _SUBSCRIPTION_STATE


def _effective_source(config: dict) -> str:
    """With an active central subscription use it; otherwise use the custom providers."""
    if os.environ.get("EASEL_SUBSCRIPTION_AUTO", "1") == "0":
        return config["service_source"]
    from easel.harnesses.capabilities import supports_subscription
    active = _subscription_state().active()
    if active is None:  # Not known yet (first seconds after start): keep the saved choice.
        return config["service_source"]
    return "subscription" if active and supports_subscription(config["runtime"], "central") else "custom"


def _harness_settings() -> dict:
    try:
        env = read_settings(ENV_FILE)
        config = harness_store.settings(OUTPUTS_DIR, env)
        source = _effective_source(config)
        if source != config["service_source"] or (source == "subscription" and config["subscription_backend"] != "central"):
            config = harness_store.settings(OUTPUTS_DIR, env, service_source=source)
        return config
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


















































app.include_router(_chat.create_router(sys.modules[__name__]))








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








app.include_router(_outputs.create_router(sys.modules[__name__]))
app.include_router(_system.create_router(sys.modules[__name__]))


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
_central_platform_router = _create_central_platform_router(lambda: PROJECT_ROOT, _platform_change_guard, _close_platform_sessions,
                                                           on_change=lambda: _subscription_state().refresh())
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
