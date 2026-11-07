"""Settings: model channels, execution engine, bundled provider, local agents, environment install and gateway restart.

Moved out of web/app.py. Shared paths and helpers are read from the core app
module (`_core`) at call time; state owned by this domain lives here.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from easel import harnesses
from easel.direct_api import api_config, api_mode, DirectAPIError
from easel.harnesses import store as harness_store
from easel.harnesses.base import HarnessError
from easel.service_contracts import availability, ServiceRoute

router = APIRouter()
_core = None

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


def _model_target_allowed(base: str, slot: str = "") -> bool:
    # Trust only the exact gateway configured by the user, not arbitrary private URLs.
    settings = _core.read_settings(_core.ENV_FILE)
    configured = settings.get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/")
    if slot == "direct-api" and _core._direct_api_enabled() and base.rstrip("/") == configured:
        try:
            api_config(settings, require_model=False)
            return True
        except DirectAPIError:
            return False
    return _ssrf_safe(base)


def _write_env(updates: dict[str, str]) -> None:
    '就地更新命中的 KEY、其余行原样保留，未命中的追加末尾；空串则删除该行。原子写。'
    updates = {k: v for k, v in updates.items() if k in _core._ENV_ALLOWLIST}
    _guard_env_values(updates)
    if set(updates).intersection(_core.GATEWAY_ENV_KEYS):
        current = _core._read_env()
        try:
            # An explicitly re-entered key authorizes retaining the same key at a
            # new address. Without one, validate against the previous endpoint.
            previous = {**current, "NEW_API_KEY": ""} if updates.get("NEW_API_KEY", "").strip() else current
            _core.validate_gateway_env({**current, **updates}, previous=previous)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
    if not updates:
        return
    lines = _core.ENV_FILE.read_text(encoding='utf-8').splitlines() if _core.ENV_FILE.is_file() else []
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
    tmp = _core.ENV_FILE.with_suffix('.env.tmp')
    tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
    tmp.replace(_core.ENV_FILE)


@router.get("/api/settings/bootstrap")
async def api_settings_bootstrap():
    """首次打开时的自检：模型配好了吗、网关活着吗、上次安装留了什么没做完。

    安装脚本不再在终端问 API Key（改为引导到这里配），所以前端需要一个可靠的
    「还缺什么」判据。modelConfigured 必须同时满足两件事，这正是 doctor 里那条
    「全绿却对话报错」防线：
      - .env 里有可用的认证（_env_key_valid，带占位符识别）；
      - primary 指向的 provider 在 openclaw.json 里真的配了认证
        （_primary_model_routable）—— 只查 .env 查不出 provider 一个字没写的情况。
    """
    direct = _core._direct_api_enabled()
    config = _core._harness_settings()
    runtime = config["runtime"]
    if config.get("service_source", "custom") == "subscription":
        state = await _core._subscription_readiness(config)
        ready = state["ready"]
        return {"modelConfigured": state["configured"], "envKeyOk": state["authenticated"], "routeOk": ready,
                "routeDetail": state["detail"], "gatewayUp": ready, "openclawConfigExists": False,
                "installWarnings": [], "runtime": runtime, "authVerified": False}
    if runtime not in ("api", "openclaw"):
        descriptor = harnesses.describe(runtime)
        installed = descriptor["installed"]
        if runtime == "pi" or (runtime == "codex" and descriptor.get("origin") == "bundled"):
            from easel.harnesses.provider import provider_status
            configured = provider_status(_core.PROJECT_ROOT)["configured"]
            ready = bool(installed and configured)
            detail = ("内置供应商本地设置已完成；尚未验证上游认证，请发送消息验证模型可用性"
                      if ready else "请在设置中配置内置供应商的地址、模型与 API Key")
            if not installed:
                detail += "；内置运行时不可用，请重新安装完整优创应用"
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
            api_config(_core.read_settings(_core.ENV_FILE))
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
        rep = _core.PROJECT_ROOT / 'outputs' / '_install' / 'last-install.json'
        if rep.is_file():
            warnings = json.loads(rep.read_text(encoding='utf-8')).get('warnings') or []
    except Exception:  # noqa: BLE001
        warnings = []

    return {
        'modelConfigured': bool(env_ok and route_ok),
        'envKeyOk': env_ok,
        'routeOk': bool(route_ok),
        'routeDetail': route_detail,
        'gatewayUp': bool(route_ok) if direct else bool(_core.check_gateway()),
        'openclawConfigExists': _core._oc_config_path().is_file(),
        'installWarnings': warnings,
    }


@router.post("/api/gateway/restart")
async def api_gateway_restart():
    """重启本机 gateway，让刚保存的模型配置立刻生效。

    为什么需要：默认传输层是 http（见 CHAT_TRANSPORT），对话走的是一个常驻 gateway，
    由 setup.sh 启动一次。配置改了之后它是否重新读 openclaw.json 取决于 OpenClaw 版本，
    不是一条该押在安装流程上的假设。

    安全性：web/app.py 绑 0.0.0.0 且无鉴权，所以这个端点刻意做成**无参数**，argv
    完全固定（复用 easel.commands.gateway 的同一条分发），不接受任何外部输入拼进命令。
    """
    if _core._direct_api_enabled():
        return {'ok': True, 'note': 'API 直连配置下一轮生效，无需重启 OpenClaw'}
    if _core._harness_settings()["runtime"] != "openclaw":
        raise HTTPException(409, "当前 Agent 使用独立按轮启动的进程，不需要重启 OpenClaw 网关")
    script = _core.PROJECT_ROOT / 'scripts' / ('gateway.ps1' if os.name == 'nt' else 'gateway.sh')
    if not script.is_file():
        raise HTTPException(500, '找不到 gateway 启动脚本')
    cmd = (['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), 'restart']
           if os.name == 'nt' else ['bash', str(script), 'restart'])
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, cwd=str(_core.PROJECT_ROOT), capture_output=True,
                                   text=True, timeout=120, env=_core._proxy_env()),
        )
    except subprocess.TimeoutExpired:
        # 超时返回提示而不是 500：网关可能只是起得慢，用户刷新一下就好。
        return {'ok': False, 'note': '重启超时；可稍后刷新，或手动运行 bash scripts/gateway.sh restart'}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'note': f'重启失败：{e}'}
    up = _core.check_gateway()
    tail = _core.clean_agent_output(r.stdout or '')[-200:]
    return {'ok': bool(up), 'gatewayUp': bool(up),
            'note': tail or ('网关已重启' if up else '网关未就绪，请查看 /tmp/easel-gateway.log')}


class EnvUpdateRequest(BaseModel):
    updates: dict[str, str]


@router.get("/api/settings/media-gateway")
async def api_media_gateway_settings():
    """Saved media routes and configuration readiness; never an inference test."""
    return _core.gateway_settings(_core._read_env())


@router.put("/api/settings/media-gateway")
async def api_media_gateway_save(req: Request):
    try:
        payload = await req.json()
        updates = _core.gateway_updates(payload, _core._read_env())
    except (ValueError, TypeError):
        # Keep malformed input and credentials out of diagnostics.
        raise HTTPException(400, "统一媒体网关设置无效，请检查模式、地址、模型与密钥；更换地址需重新填写密钥，清除密钥前须全部切回自定义") from None
    try:
        _guard_env_values(updates)
    except HTTPException:
        raise HTTPException(400, "统一媒体网关设置包含不允许的字符") from None
    _write_env(updates)
    return {"ok": True, **_core.gateway_settings(_core._read_env())}


@router.post("/api/env")
async def api_env_save(req: EnvUpdateRequest):
    """写 API key 到项目根 .env（仅允许注册表内 env 名）。返回更新后各 skill 的配置状态。"""
    bad = [k for k in (req.updates or {}) if k not in _core._ENV_ALLOWLIST]
    if bad:
        raise HTTPException(400, f"不允许写入的变量：{', '.join(bad)}")
    _write_env(req.updates or {})
    env = _core._read_env()
    return {
        "ok": True,
        "skills": {s: _core._skill_api_configured(s, env) for s in _core.SKILL_API_REQUIREMENTS},
    }




_ENV_TOOLS_CACHE: dict = {"ts": 0.0, "data": None}


_ENV_JOBS: dict[str, dict] = {}


@router.get("/api/env/tools")
async def api_env_tools(refresh: bool = False):
    """环境体检：引擎 check --json（15 秒缓存；refresh=1 强制重测）。"""
    if not refresh and _ENV_TOOLS_CACHE["data"] and time.time() - _ENV_TOOLS_CACHE["ts"] < 15:
        return _ENV_TOOLS_CACHE["data"]
    try:
        proc = await asyncio.to_thread(lambda: subprocess.run(
            [sys.executable, str(INSTALL_TOOL), "--json", "check"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=240, cwd=str(_core.PROJECT_ROOT)))
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
                           errors="replace", timeout=30, cwd=str(_core.PROJECT_ROOT))
        ids = frozenset(t["id"] for t in (json.loads(p.stdout).get("tools") or []) if t.get("id"))
    except Exception:  # noqa: BLE001
        return _INSTALL_IDS_CACHE["ids"]
    if ids:
        _INSTALL_IDS_CACHE.update({"ts": time.time(), "ids": ids})
    return ids


@router.post("/api/env/install")
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
                text=True, encoding="utf-8", errors="replace", cwd=str(_core.PROJECT_ROOT))
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


@router.get("/api/env/job/{job_id}")
async def api_env_job(job_id: str):
    """查安装进度：running / ok / fail + 最近日志行 + 最终结果。"""
    job = _ENV_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在（服务可能重启过）")
    return job


def _mask_key(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    return f"«{v[:5]}…{v[-4:]}»" if len(v) > 14 else "«已配置»"


def _model_channels(runtime: str | None = None) -> dict:
    env = _core._read_env()
    primary = ""
    direct = runtime == "api" if runtime else _core._direct_api_enabled()
    try:
        oc = _core._oc_config_path()
        if not direct and oc.is_file():
            primary = str(json.loads(oc.read_text(encoding="utf-8"))
                          .get("agents", {}).get("defaults", {}).get("model", {}).get("primary", ""))
    except Exception:  # noqa: BLE001
        pass
    if direct:
        env = _core.read_settings(_core.ENV_FILE)
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
    ok_key = _core._is_set(env.get("OPENAI_API_KEY"))
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
    ak_ok = _core._is_set(ak)
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
    lk_ok = _core._is_set(lk)
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
        oc = _core._oc_config_path()
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
                                           if _core._is_set(str(pv.get("apiKey") or "")) else ""),
                    "role": "主" if primary == f"{pkey}/{mid}" else "备",
                    "result": "已配置" if _core._is_set(str(pv.get("apiKey") or "")) else "缺 key",
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
    env = _core._fresh_media_gateway_env(env)
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
                _ok = all(_core._is_set(env.get(k["env"])) or any(_core._is_set(env.get(a)) for a in k.get("aliases", []))
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
            if _core.gateway_managed(_gid, env):
                _ready = _core.gateway_readiness(_gid, env)
                _gateway = _core.gateway_settings(env)
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


@router.get("/api/settings/harness/provider")
async def api_bundled_provider():
    from easel.harnesses.provider import provider_status
    try:
        _require_bundled_provider()
        return provider_status(_core.PROJECT_ROOT)
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/api/settings/harness/provider")
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
        if _core._RUNNING_CHAT or _core._BG_TASKS or _core._DIRECT_CHAT_TASKS or _core._HARNESS_TASKS or _core._SYNC_SESSIONS or _core._PLATFORM_PREFLIGHTS or _core._platform_router.platform_changes or _core._central_platform_router.platform_changes:
            raise HTTPException(409, "请等待当前任务完成或停止后修改供应商")
        result = save_provider(_core.PROJECT_ROOT, **req.model_dump())
    except HarnessError as error:
        raise HTTPException(400, str(error)) from error
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    return result


@router.get("/api/settings/harness")
async def api_harness_settings():
    config = _core._harness_settings()
    agents = await asyncio.to_thread(lambda: [harnesses.describe(runtime) for runtime in harness_store.IDS])
    return {**config, "agents": agents, "availability": availability(),
            "locked": "EASEL_AGENT_RUNTIME" in _core.read_settings(_core.ENV_FILE)}


@router.post("/api/settings/harness")
async def api_harness_settings_save(req: HarnessSettingsRequest):
    config = _core._harness_settings()
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
    if "EASEL_AGENT_RUNTIME" in _core.read_settings(_core.ENV_FILE):
        raise HTTPException(409, "启动环境固定了底座，请修改 EASEL_AGENT_RUNTIME 后重启")
    if _core._RUNNING_CHAT or _core._BG_TASKS or _core._DIRECT_CHAT_TASKS or _core._HARNESS_TASKS or _core._SYNC_SESSIONS or _core._PLATFORM_PREFLIGHTS or _core._platform_router.platform_changes or _core._central_platform_router.platform_changes:
        raise HTTPException(409, "请等待当前任务完成或停止后切换底座")
    if req.runtime not in ("api", "openclaw") and not harnesses.describe(req.runtime)["installed"]:
        raise HTTPException(409, "内置 Pi 请重新安装完整优创应用；外部引擎请先安装并配置后再切换" if req.runtime == "pi" else "请先在终端安装并配置对应 Agent CLI，再切换")
    if req.runtime == "openclaw" and api_mode(_core.read_settings(_core.ENV_FILE)):
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
    original = harness_store.read_json(_core.OUTPUTS_DIR / "_harness" / "config.json")
    config["models"] = original.get("models", config["models"])
    config["subscription_model"] = original.get("subscription_model", config.get("subscription_model", ""))
    model = req.model.strip() if "model" in req.model_fields_set else config["models"].get(req.runtime, "")
    if route.service_source == "custom" and req.runtime == "codex" and "model" in req.model_fields_set:
        from easel.harnesses.runtime import codex_model
        try:
            model = codex_model(_core.PROJECT_ROOT, model, require_configured=False)
        except HarnessError as error:
            raise HTTPException(400, str(error)) from error
    elif route.service_source == "custom" and req.runtime == "pi":
        from easel.harnesses.provider import pi_model
        try:
            model = pi_model(_core.PROJECT_ROOT, model, require_configured=False)
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
    harness_store.write_json(_core.OUTPUTS_DIR / "_harness" / "config.json", config)
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    return await api_harness_settings()


@router.post("/api/settings/chat/transport")
async def api_chat_transport_save(req: ChatTransportRequest):
    if req.transport not in ("api", "openclaw"):
        raise HTTPException(400, "请选择 api 或 openclaw")
    if "EASEL_AGENT_RUNTIME" in _core.read_settings(_core.ENV_FILE):
        raise HTTPException(409, "启动环境固定了执行引擎，请修改 EASEL_AGENT_RUNTIME 后重启")
    if "EASEL_CHAT_TRANSPORT" in os.environ:
        raise HTTPException(409, "运行环境已指定 EASEL_CHAT_TRANSPORT，请修改启动环境后重启")
    if _core._RUNNING_CHAT or _core._BG_TASKS or _core._DIRECT_CHAT_TASKS or _core._HARNESS_TASKS or _core._SYNC_SESSIONS or _core._PLATFORM_PREFLIGHTS or _core._platform_router.platform_changes or _core._central_platform_router.platform_changes:
        raise HTTPException(409, "请等待当前对话完成或停止后切换模式")
    config = _core._harness_settings()
    _write_env_direct({"EASEL_CHAT_TRANSPORT": "api" if req.transport == "api" else "http"})
    config["runtime"] = "api" if req.transport == "api" else "openclaw"
    harness_store.write_json(_core.OUTPUTS_DIR / "_harness" / "config.json", config)
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    return _model_channels()


@router.get("/api/settings/models")
async def api_settings_models(runtime: str | None = None):
    """模型通道：chat / transcribe（含可编辑的原值；key 只回脱敏）。"""
    if runtime is not None and runtime not in ("api", "openclaw"):
        raise HTTPException(400, "仅聊天和 OpenClaw 才有 API 供应商配置")
    if runtime is None:
        return await asyncio.to_thread(_model_channels)
    return await asyncio.to_thread(_model_channels, runtime)


@router.post("/api/settings/harness/models/{runtime}")
async def api_harness_models(runtime: str):
    if runtime not in ("pi", "codex", "opencode"):
        raise HTTPException(400, "此底座不支持原生模型列表")
    from easel.harnesses.base import RunRequest
    from easel.harnesses.models import available_models
    request = RunRequest("", _core.PROJECT_ROOT, {
        **_core._proxy_env(), "EASEL_ROOT": str(_core.PROJECT_ROOT), "EASEL_AGENT_RUNTIME": runtime},
        service_source=_core._harness_settings()["service_source"],
        subscription_backend=_core._harness_settings().get("subscription_backend", "legacy"))
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
    lines = _core.ENV_FILE.read_text(encoding='utf-8').splitlines() if _core.ENV_FILE.is_file() else []
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
        out.append('# ---- 优创模型配置（Web 设置面板写入）----')
        out.extend(appended)
    tmp = _core.ENV_FILE.with_suffix('.env.tmp')
    tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
    tmp.replace(_core.ENV_FILE)


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
        oc = _core._oc_config_path()
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
        oc = _core._oc_config_path()
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
        oc = _core._oc_config_path()
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
            if not key and not base and not _core._is_set(new_prov.get('apiKey')) \
                    and _core._is_set(legacy.get('apiKey')) and legacy.get('baseUrl'):
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
    if _core._RUNNING_CHAT or _core._BG_TASKS or _core._DIRECT_CHAT_TASKS or _core._HARNESS_TASKS or _core._SYNC_SESSIONS or _core._PLATFORM_PREFLIGHTS or _core._platform_router.platform_changes or _core._central_platform_router.platform_changes:
        raise HTTPException(409, "请等待当前任务完成或停止后修改聊天模型")


@router.post("/api/settings/models/save")
async def api_settings_models_save(req: ModelSaveRequest):
    """保存模型通道：.env 就地更新（key 留空=不改）；chat 同步 openclaw；媒体通道写 provider 配置。"""
    ch0 = (req.channel or "").strip()
    if ch0 == "chat":
        _guard_chat_model_mutation()
    if ch0 == "chat" and _core._harness_settings()["runtime"] not in ("api", "openclaw"):
        raise HTTPException(409, "当前底座使用原生认证；模型覆盖请在 Agent 底座设置中保存")
    direct = _core._direct_api_enabled()
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
        _env0 = _core._read_env()
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
    _cur_env = _core.read_settings(_core.ENV_FILE) if direct else _core._read_env()
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
            if _be and _core._is_set(_cur_env.get(_ke)):
                _pb = (_cur_env.get(_be, '') or '').strip().rstrip('/')
                _pk = (_cur_env.get(_ke, '') or '').strip()
                if slot == 'anthropic' and not _pb:
                    _pb = 'https://api.anthropic.com'
            else:
                _pb, _pk = _cur_prov.get(pkey, ('', ''))
                _pb = _pb.strip().rstrip('/')
            if _pk and base != _pb and not _is_local_gateway_base(_pb):
                raise HTTPException(400, f'更换 Base URL 时必须重新填写 API Key（{pkey}）')
        if is_chat and slot == 'openai' and not key and _core._is_set(_cur_env.get('OPENAI_API_KEY')) \
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
            if candidate in _slots and _core._is_set(effective_env.get(key_var)) \
                    and (candidate == 'anthropic' or effective_env.get(base_var, '').strip()):
                sync_slot = candidate
                break
        if not sync_slot:
            sync_slot = next((candidate for candidate in ('relay', 'anthropic')
                              if candidate in _slots), '')
        if sync_slot:
            base_var, key_var = _SLOT_ENV_KEYS[sync_slot]
            if _core._is_set(effective_env.get(key_var)):
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


@router.get("/api/settings/local-agents")
async def api_local_agents():
    """探测本机已装的 agent CLI（Claude Code / Gemini CLI / Codex…）。

    目的：装了 Claude Code / Gemini CLI 且已登录的用户**不需要再填 API Key** ——
    OpenClaw 底座有对应的 CLI 后端，直接复用 CLI 自己的登录态。返回值同时如实
    标出哪些 CLI 暂无底座后端（installed 但 supported=false），不假装支持。
    """
    from easel.local_agents import summarize_local_agents
    return await asyncio.to_thread(summarize_local_agents)


@router.post("/api/settings/local-agents/enable")
async def api_local_agent_enable(req: LocalAgentEnableRequest):
    """把一个本机 CLI agent（目前支持 claude-code / gemini-cli）接入优创。

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
            (_core._read_env().get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
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
        oc = _core._oc_config_path()
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
        oc = _core._oc_config_path()
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


@router.post("/api/settings/models/available")
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
        _env = _core.read_settings(_core.ENV_FILE) if _core._direct_api_enabled() else _core._read_env()
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
    if slot == "direct-api" and _core._direct_api_enabled() and base == _core.read_settings(_core.ENV_FILE).get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/"):
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


@router.post("/api/settings/models/selftest")
async def api_models_selftest(req: SelftestRequest):
    """真自测：对已配置的 OpenAI 兼容通道发 GET {base}/models 并计耗时。"""
    channel = (req.channel or "all").strip()
    _group = {"image": "image", "video": "video", "music": "music", "speech": "voice"}.get(channel)
    if _group and _core.gateway_managed(_group, _core._read_env()):
        raise HTTPException(409, "统一媒体网关显示配置完整度；此旧接口不验证媒体调用")
    if channel in ("chat", "all") and _core._harness_settings()["runtime"] not in ("api", "openclaw"):
        raise HTTPException(409, "此底座使用原生模型配置，请新建对话或运行 easel ping 验证")
    direct = _core._direct_api_enabled()
    env = _core.read_settings(_core.ENV_FILE) if direct else _core._read_env()
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


def create_router(core):
    """Bind the core app module and return this domain's routes."""
    global _core, INSTALL_TOOL
    _core = core
    INSTALL_TOOL = _core.SHARED_SCRIPTS / "install_tool.py"
    return router
