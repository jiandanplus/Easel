"""Social-platform accounts: login, identity checks, analytics and publishing.

Moved out of web/app.py. Shared paths and helpers are read from the core app
module (`_core`) at call time; state owned by this domain lives here.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from easel.routes import planning as _planning

router = APIRouter()
_core = None

BROWSER_PROFILES = Path.home() / ".easel-browser-profiles"

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
                              env=_core._wechat_env(), capture_output=True, text=True, timeout=30)
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
        return (_core.PROJECT_ROOT / 'cookies.json').is_file()
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


@router.get("/api/accounts")
async def api_accounts():
    return [
        {'platform': pf, 'name': cfg['name'], 'backend': cfg['backend'],
         'supported': cfg['backend'] != 'unsupported',
         'loggedIn': _account_logged_in(pf, cfg),
         'note': cfg.get('note', '')}
        for pf, cfg in LOGIN_RUNNERS.items()
    ]


@router.post("/api/login/{platform}")
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
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'xhs_publish.py'), 'login', '--no-proxy',
               '--qr-out', str(qr), '--status-file', str(status), '--timeout', str(LOGIN_TIMEOUT)]
    elif backend == 'biliup':
        # B站：TV 端扫码登录 API 生成二维码 + 写 biliup cookie（biliup login 需真终端，前端用不了）
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'bili_login.py'), 'login',
               '--qr-out', str(qr), '--status-file', str(status),
               '--cookie', str(_core.PROJECT_ROOT / 'cookies.json'), '--timeout', str(LOGIN_TIMEOUT)]
    elif backend == 'douyin':
        code_file = LOGIN_DIR / f'{platform}.code'
        try:
            code_file.unlink()
        except OSError:
            pass
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'douyin_publish.py'), 'login',
               '--no-proxy',
               '--qr-out', str(qr), '--status-file', str(status),
               '--sms-code-file', str(code_file), '--timeout', str(LOGIN_TIMEOUT)]
    else:
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'web_publisher.py'), 'login-qr',
               '--platform', cfg['wp'], '--qr-out', str(qr), '--status-file', str(status),
               '--timeout', str(LOGIN_TIMEOUT)]
    # 新登录开始 → 清掉旧的 whoami 缓存（登录前可能缓存了「未登录」），避免登录成功后仍读到旧结果
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE.pop(platform, None)
    log_path = LOGIN_DIR / f'{platform}.log'
    log_file = log_path.open('a', encoding='utf-8')
    login_env = _core._proxy_env()
    login_env['PYTHONIOENCODING'] = 'utf-8'
    login_env['PYTHONUTF8'] = '1'
    try:
        proc = subprocess.Popen(cmd, cwd=str(_core.PROJECT_ROOT), env=login_env,
                                stdout=log_file, stderr=subprocess.STDOUT)
    finally:
        log_file.close()
    LOGIN_PROCESSES[platform] = proc
    s = _login_status(platform)
    if s['state'] == 'unknown':
        s.update(state='starting', message='正在启动浏览器并准备二维码…')
    return {'mode': 'qr', **s}


@router.get("/api/login/{platform}/status")
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


@router.post("/api/login/{platform}/sms")
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


@router.get("/api/accounts/{platform}/credentials")
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


@router.post("/api/accounts/{platform}/credentials")
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


@router.post("/api/accounts/{platform}/mp-login")
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
    cmd = [sys.executable, str(_core.SHARED_SCRIPTS / "weixin_mp_stats.py"), "login",
           "--proxy", wx_proxy, "--qr-out", str(LOGIN_DIR / "wechat-oa-mp.png"),
           "--status-file", str(LOGIN_DIR / "wechat-oa-mp.json"), "--timeout", "240"]
    log_file = (LOGIN_DIR / "wechat-oa-mp.log").open("a", encoding="utf-8")
    env = _core._proxy_env()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen(cmd, cwd=str(_core.PROJECT_ROOT), env=env,
                            stdout=log_file, stderr=subprocess.STDOUT)
    log_file.close()
    LOGIN_PROCESSES["wechat-oa-mp"] = proc
    status = _mp_login_status()
    if status["state"] == "unknown":
        status.update(state="starting", message="正在启动浏览器并准备二维码…")
    return {"mode": "qr", **status}


@router.get("/api/accounts/{platform}/mp-login/status")
async def api_mp_login_status(platform: str):
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get("backend") != "wechat-oa":
        raise HTTPException(404, "该平台不使用公众号后台登录")
    return {"mode": "qr", **_mp_login_status()}


@router.get("/api/accounts/{platform}/whoami")
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
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'bili_login.py'), 'whoami',
               '--cookie', str(_core.PROJECT_ROOT / 'cookies.json')]
    elif backend == 'xhs':
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'xhs_publish.py'), 'whoami', '--no-proxy']
    elif backend == 'wechat-oa':
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'weixin_mp_stats.py'), 'whoami', '--no-proxy']
    elif backend == 'douyin':
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'douyin_publish.py'), 'whoami', '--no-proxy']
    else:
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / 'web_publisher.py'), 'whoami',
               '--platform', cfg['wp']]
    try:
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=str(_core.PROJECT_ROOT), env=_core._proxy_env(),
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


@router.post("/api/logout/{platform}")
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
        ck = _core.PROJECT_ROOT / 'cookies.json'
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
        cmd, cwd=str(_core.PROJECT_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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


@router.get("/api/analytics/platforms")
async def api_analytics_platforms():
    """列出支持抓数据的平台 + 各自登录态（前端据此渲染平台选择器）。"""
    return [
        {"platform": pf, "name": LOGIN_RUNNERS.get(pf, {}).get("name", pf),
         "loggedIn": _account_logged_in(pf, LOGIN_RUNNERS.get(pf, {}))}
        for pf in LOGIN_RUNNERS if pf in ANALYTICS_PLATFORMS
    ]


@router.get("/api/analytics/{platform}")
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
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / "bili_login.py"), "stats",
               "--cookie", str(_core.PROJECT_ROOT / "cookies.json")]
    elif platform == "wechat-oa":
        # 公众号数据走「后台网页端」(mp.weixin.qq.com 管理员会话 + Playwright 拦截数据 XHR)：
        # 开发者 datacube 接口需认证+群发+接口权限，多数号取不到；后台端有登录态即可看到发表记录/数据。
        # 需先扫码登录 mp 后台（weixin_mp_stats.py login）；默认直连，受限网络才需 EASEL_PROXY/https_proxy。
        wx_proxy = os.environ.get("EASEL_PROXY") or os.environ.get("https_proxy") or ""
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / "weixin_mp_stats.py"), "stats",
               "--proxy", wx_proxy, "--count", "20"]
    else:
        # 代理策略由 account_stats.py 按平台自定（xhs 直连、其它走 env），后端照常传 _proxy_env
        cmd = [sys.executable, str(_core.SHARED_SCRIPTS / "account_stats.py"), "fetch", "--platform", platform]
    ana_env = _core._proxy_env()
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
        proc = subprocess.run(cmd, cwd=str(_core.PROJECT_ROOT), env=_core._publish_env(),
                              capture_output=True, text=True, timeout=900)
        ok = proc.returncode == 0
        out, err = proc.stdout or '', proc.stderr or ''
    except subprocess.TimeoutExpired:
        err = '发布超时（>900s）'
    except Exception as e:  # noqa: BLE001
        err = f'发布进程异常：{e}'
    try:
        with (_core.OUTPUTS_DIR / '_publish.log').open('a', encoding='utf-8') as lf:
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
            items = _planning.read_schedule(_core.OUTPUTS_DIR)
            items.append({'id': uuid.uuid4().hex[:12], 'title': title,
                          'date': time.strftime('%Y-%m-%d'), 'platform': cfg['name'],
                          'time': time.strftime('%H:%M'), 'status': 'published', 'note': body[:200],
                          'kind': 'content', 'source': 'publish-page'})
            _planning.write_schedule(_core.OUTPUTS_DIR, items)
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


@router.get("/api/publish/{platform}/status")
async def api_publish_status(platform: str):
    """轮询异步发布状态：starting/sms_required/verifying/success/error。"""
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    return {'mode': 'publish', **_read_publish_status(platform)}


@router.post("/api/publish/{platform}/sms")
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


@router.post("/api/publish/{platform}")
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
        full = _core._safe_output_path(rel)
        ext = full.suffix.lower()
        if ext in _core.VIDEO_EXTS:
            vids.append(str(full))
        elif ext in _core.IMAGE_EXTS:
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
        base = [py, str(_core.SHARED_SCRIPTS / 'xhs_publish.py')]
        cmd = base + ['publish-video', '--no-proxy', '--video', vids[0]] if vids else base + ['publish', '--no-proxy', '--images', ','.join(imgs)]
        cmd += ['--title', title, '--content', req.body, '--tags', tags, '--exec']
    elif platform == 'bilibili':
        # B站投稿：直接调 biliup CLI（需 cookies.json，PATH 上有 biliup）。必须视频；
        # tid=36「知识」；B站投稿必须≥1 标签，无则兜底「日常」。
        bili_tag = tags.replace('#', '').replace('，', ',').strip().strip(',') or '日常'
        cmd = ['biliup', '-u', str(_core.PROJECT_ROOT / 'cookies.json'), 'upload', vids[0],
               '--title', title[:80], '--tid', '36', '--copyright', '1', '--tag', bili_tag]
        if req.body.strip():
            cmd += ['--desc', req.body[:2000]]
    elif platform == 'douyin':
        base = [py, str(_core.SHARED_SCRIPTS / 'douyin_publish.py')]
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
                              cwd=str(_core.PROJECT_ROOT), env=_core._proxy_env(),
                              capture_output=True, text=True, timeout=60)
        if conv.returncode != 0 or not html_path.is_file():
            raise HTTPException(500, f"排版失败：{(conv.stderr or conv.stdout or '')[-200:]}")
        wx_proxy = os.environ.get('EASEL_PROXY') or os.environ.get('https_proxy') or ''
        cmd = [py, str(_core.SHARED_SCRIPTS / 'weixin_mp_stats.py'), 'publish', '--proxy', wx_proxy,
               '--html', str(html_path), '--cover', cover, '--title', title,
               '--digest', (req.body or '').strip()[:100], '--author', '']
    else:
        cmd = [py, str(_core.SHARED_SCRIPTS / 'web_publisher.py'), 'publish',
               '--platform', cfg['wp'], '--title', title, '--desc', req.body,
               '--tags', tags, '--exec']
        media = vids[0] if vids else (imgs[0] if imgs else None)
        if media:
            cmd += ['--media', media]
    # 公众号走后台会话（Playwright，脚本自带 --proxy，默认直连）；其余平台走 _publish_env
    pub_env = _core._proxy_env() if platform == 'wechat-oa' else _core._publish_env()
    try:
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=str(_core.PROJECT_ROOT), env=pub_env,
                                       capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, '发布超时（媒体处理慢或流程卡住）')
    ok = proc.returncode == 0
    tail = (proc.stderr or proc.stdout or '').strip().splitlines()
    detail = '\n'.join(tail[-8:])
    try:
        with (_core.OUTPUTS_DIR / '_publish.log').open('a', encoding='utf-8') as lf:
            lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {platform} rc={proc.returncode} ok={ok} =====\n")
            lf.write('CMD: ' + ' '.join(cmd) + '\n')
            lf.write('STDOUT:\n' + (proc.stdout or '')[-2000:] + '\n')
            lf.write('STDERR:\n' + (proc.stderr or '')[-2000:] + '\n')
    except Exception:
        pass
    if ok:
        try:
            items = _planning.read_schedule(_core.OUTPUTS_DIR)
            items.append({'id': uuid.uuid4().hex[:12], 'title': title,
                          'date': time.strftime('%Y-%m-%d'), 'platform': cfg['name'],
                          'time': time.strftime('%H:%M'), 'status': 'published',
                          'note': req.body[:200], 'kind': 'content', 'source': 'publish-page'})
            _planning.write_schedule(_core.OUTPUTS_DIR, items)
        except Exception:
            pass
    return {'ok': ok, 'message': '发布成功' if ok else '发布失败（见 detail）', 'detail': detail}


def create_router(core):
    """Bind the core module, set paths derived from it at import time, return the routes."""
    global _core, LOGIN_DIR, PUBLISH_DIR, WECHAT_CONFIG_YAML, WECHAT_PUBLISH_SCRIPT, WECHAT_SKILL_DIR, WECHAT_SKILL_SCRIPTS
    _core = core
    LOGIN_DIR = _core.OUTPUTS_DIR / "_login"
    PUBLISH_DIR = _core.OUTPUTS_DIR / "_publish"   # 异步发布的状态/验证码文件（抖音发布可能触发短信墙）
    WECHAT_SKILL_DIR = _core.PROJECT_ROOT / "skills" / "builtin" / "skill-wechat-publisher"
    WECHAT_SKILL_SCRIPTS = WECHAT_SKILL_DIR / "scripts"
    WECHAT_PUBLISH_SCRIPT = WECHAT_SKILL_SCRIPTS / "publish.py"
    WECHAT_CONFIG_YAML = WECHAT_SKILL_DIR / "wechat-publisher.yaml"
    return router
