#!/usr/bin/env python3
"""AI 视频生成（文生视频 / 图生视频 / 数字人口播），多 provider 可插拔。

用户自备 API key（视频生成 API 均为异步：提交任务 → 轮询 → 下载结果）。
配置来自环境变量或 .env（脚本从当前目录向上查找 .env）。

Provider（--provider 或 env VIDEO_PROVIDER）：
  dashscope          阿里通义万相视频（Wan）。env DASHSCOPE_API_KEY
  ark                火山引擎 ARK（Seedance）。env ARK_API_KEY
  kling              快手可灵。env KLING_ACCESS_KEY / KLING_SECRET_KEY（JWT 鉴权）
  openai-compatible  通用 /videos 端点。env VIDEO_API_KEY / VIDEO_BASE_URL / VIDEO_MODEL
  xhs-maas           小红书内网 MaaS（happyhorse 文/图生视频）。env XHS_MAAS_API_KEY
                     （可选 XHS_MAAS_VIDEO_BASE / XHS_MAAS_T2V_MODEL / XHS_MAAS_I2V_MODEL /
                     XHS_MAAS_RESOLUTION）。DashScope 风格异步 + api-key 头，内网直连（绕代理）。
  agnes              Agnes（apihub.agnes-ai.com，agnes-video-2.5-flash）。env AGNES_API_KEY
                     （可选 AGNES_BASE_URL / AGNES_MODEL / AGNES_SIZE）。OpenAI Videos 兼容创建
                     POST /v1/videos + 自定义端点 GET /agnesapi 轮询；默认带原生音频；外网走代理。

子命令：
  text2video    文生视频：--prompt [--duration --ratio --model -o]
  image2video   图生视频（含数字人首帧驱动）：--image --prompt [...]
  check         离线校验当前 provider 所需 env（不发请求）

说明：各 provider 的请求/响应 schema 依据其公开 REST 文档实现，端点/字段各版本略有差异，
model / base_url 均可用 env 覆盖；如报错请对照最新官方文档调整。
"""
from __future__ import annotations

from media_gateway import require_media_available
require_media_available()

import argparse
import base64
import difflib
import hashlib
import hmac
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
from output_paths import validate_output_path
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from model_registry import env_aliases, provider_ids, provider_required_env

try:
    # Windows 非交互式/管道 stdout 默认落到系统 ANSI 代码页（如 cp1252），打印中文
    # 提示（必填/可选/缺失……）会抛 UnicodeEncodeError 而不是正常退出。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

UA = "Easel-ai-video/0.1"

DEFAULT_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_DASHSCOPE_MODEL = "wan2.1-t2v-turbo"
DEFAULT_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_ARK_MODEL = "doubao-seedance-1-0-lite-t2v"
DEFAULT_KLING_BASE = "https://api.klingai.com"
# 小红书内网 MaaS（happyhorse 文/图生视频，DashScope 风格异步 + api-key 头，需直连）
DEFAULT_XHS_MAAS_VIDEO_BASE = "https://maas.devops.xiaohongshu.com/openai/openai/qwen/v1"
# Agnes（apihub.agnes-ai.com）：OpenAI Videos 兼容创建 + 自定义端点轮询（外网，走代理）
DEFAULT_AGNES_BASE = "https://apihub.agnes-ai.com/v1"
DEFAULT_AGNES_POLL_BASE = "https://apihub.agnes-ai.com/agnesapi"
DEFAULT_AGNES_MODEL = "agnes-video-2.5-flash"

# 内网/OSS 直连 opener（绕过环境代理）——外网代理常挡内网 MaaS 与 aliyuncs OSS
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ENV_ALIASES = env_aliases("video")


# ── 基础工具 ──────────────────────────────────────────────

def fail(message: str, exit_code: int = 1) -> "None":
    print(f"错误：{message}", file=sys.stderr)
    sys.exit(exit_code)


def strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def find_default_env_file() -> Path | None:
    for directory in [Path.cwd(), *Path.cwd().parents]:
        env_file = directory / ".env"
        if env_file.is_file():
            return env_file
    return None


def load_env_file(env_file: Path | None) -> None:
    require_media_available()
    if env_file is None:
        return
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"警告：无法读取 .env 文件：{exc}", file=sys.stderr)
        return
    saved_gateway = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            print(f"警告：.env 第 {line_number} 行不是 KEY=value，已跳过。", file=sys.stderr)
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        saved_gateway[key] = strip_env_value(value)
        if not key:
            print(f"警告：.env 第 {line_number} 行缺少变量名，已跳过。", file=sys.stderr)
            continue
        if key not in os.environ:
            os.environ[key] = strip_env_value(value)

    from media_gateway import GATEWAY_ENV_KEYS, refresh_gateway_environment
    effective = refresh_gateway_environment(os.environ, saved_gateway)
    for name in GATEWAY_ENV_KEYS:
        os.environ.pop(name, None)
    os.environ.update(effective)


def env_lookup(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    for alias in ENV_ALIASES.get(name, ()):
        val = os.environ.get(alias, "").strip()
        if val:
            return val
    return ""


def require_env(name: str) -> str:
    val = env_lookup(name)
    if not val:
        aliases = ENV_ALIASES.get(name, ())
        hint = f"（别名：{ '、'.join(aliases) }）" if aliases else ""
        fail(f"未配置 {name}{hint}，请在 .env 或环境变量中填入你的 API key。")
    return val


PROVIDERS = provider_ids("video")

# Provider 能力只负责请求映射；最终是否真的产出可用音频必须以 ffprobe/ASR 审计为准。
# native_audio=能否生成原生同步音频；dialogue=能否按 prompt 尝试生成画内对白；
# dialogue_faithful=能否**逐字忠实**生成我们指定的台词（默认 False，保守——多数模型做不到，
#   需 `probe-dialogue` 真机探针或 VIDEO_CAPABILITIES_JSON 确认后才置 True）。
# 短剧会区分未实测默认与探针结论：未实测仍 native-first，探针确认不忠实才预留口型后配音。
PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "dashscope": {"native_audio": False, "dialogue": False, "dialogue_faithful": False, "audio_reference": False},
    "ark": {"native_audio": True, "dialogue": True, "dialogue_faithful": False, "audio_reference": False,
            "audio_field": "generate_audio", "audio_location": "root"},
    "kling": {"native_audio": False, "dialogue": False, "dialogue_faithful": False, "audio_reference": False},
    "openai-compatible": {"native_audio": False, "dialogue": False, "dialogue_faithful": False, "audio_reference": False},
    "xhs-maas": {"native_audio": True, "dialogue": True, "dialogue_faithful": False, "audio_reference": False,
                 "audio_default": True},
    # agnes-video-2.5-flash：实测默认生成原生音频（prompt 描述声音，无开关字段）；reference 模式可传 audios。
    # dialogue 忠实度未测 → 保守 False，需 probe-dialogue 或 VIDEO_CAPABILITIES_JSON 确认后再置 True。
    "agnes": {"native_audio": True, "dialogue": False, "dialogue_faithful": False,
              "audio_reference": True, "audio_default": True},
}

# 能力协议里允许被缓存/env 覆盖的字段（统一给探针缓存合并与 VIDEO_CAPABILITIES_JSON 直接键用）
CAP_KEYS = {"native_audio", "dialogue", "dialogue_faithful", "audio_reference",
            "audio_default", "audio_field", "audio_location"}
# probe-dialogue 真机探针结论缓存（默认 < 缓存 < VIDEO_CAPABILITIES_JSON）
PROBE_CACHE_PATH = Path.home() / ".easel-video-capabilities.json"


def _load_probe_cache() -> dict:
    """读探针缓存 {"provider:model": {"dialogue_faithful": bool, "probe": {...}}}；无/损坏返回空。"""
    try:
        data = json.loads(PROBE_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_probe_cache(provider: str, model: str | None, entry: dict) -> None:
    """把某 provider:model 的探针结论并入缓存（原子写）。"""
    cache = _load_probe_cache()
    cache[f"{provider}:{model or ''}"] = entry
    tmp = PROBE_CACHE_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, PROBE_CACHE_PATH)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def provider_capabilities(provider: str, model: str | None = None) -> dict[str, Any]:
    """返回统一能力协议；VIDEO_CAPABILITIES_JSON 可覆盖任意新 provider/model 网关差异。"""
    cap = {"provider": provider, "model": model, **PROVIDER_CAPABILITIES[provider]}
    # 优先级：内置默认 < 探针缓存 < VIDEO_CAPABILITIES_JSON（env 覆盖最高＝「配置可覆盖」）
    cache = _load_probe_cache()
    for key in (provider, f"{provider}:{model or ''}"):
        if isinstance(cache.get(key), dict):
            cap.update({k: v for k, v in cache[key].items() if k in CAP_KEYS})
    raw = os.environ.get("VIDEO_CAPABILITIES_JSON", "").strip()
    if raw:
        try:
            override = json.loads(raw)
        except json.JSONDecodeError as exc:
            fail(f"VIDEO_CAPABILITIES_JSON 不是有效 JSON：{exc}")
        if not isinstance(override, dict):
            fail("VIDEO_CAPABILITIES_JSON 顶层必须是对象")
        # 支持 {"native_audio":true} 或 {"ark": {...}, "ark:model": {...}}。
        if CAP_KEYS & override.keys():
            cap.update({k: v for k, v in override.items() if k in CAP_KEYS})
        for key in (provider, f"{provider}:{model}" if model else None):
            if key and isinstance(override.get(key), dict):
                cap.update(override[key])
    return cap


def dialogue_faithful_source(provider: str, model: str | None = None) -> str:
    """`dialogue_faithful` 的判定来源：'env'（VIDEO_CAPABILITIES_JSON 显式声明）|
    'probe'（probe-dialogue 探针缓存）| 'default'（**从未实测**的保守默认）。
    用于让上游区分「实测过的 false」和「没测过、默认 false」，避免跳过探针直接采用默认。"""
    raw = os.environ.get("VIDEO_CAPABILITIES_JSON", "").strip()
    if raw:
        try:
            override = json.loads(raw)
        except json.JSONDecodeError:
            override = None
        if isinstance(override, dict):
            if "dialogue_faithful" in override:
                return "env"
            for key in (provider, f"{provider}:{model}" if model else None):
                if key and isinstance(override.get(key), dict) and "dialogue_faithful" in override[key]:
                    return "env"
    cache = _load_probe_cache()
    for key in (provider, f"{provider}:{model or ''}"):
        if isinstance(cache.get(key), dict) and "dialogue_faithful" in cache[key]:
            return "probe"
    return "default"


def _audio_value(args: argparse.Namespace, provider: str, model: str | None) -> bool | None:
    """None=不向不支持/未知的接口乱塞字段；bool=显式映射 provider 音频开关。"""
    mode = getattr(args, "audio", "auto")
    cap = provider_capabilities(provider, model)
    if mode == "on" and not cap.get("native_audio"):
        fail(f"{provider} / {model or '默认模型'} 未声明原生音频能力；"
             "请切换模型或用 VIDEO_CAPABILITIES_JSON 登记其请求字段，不能盲传参数。")
    if not cap.get("native_audio"):
        return None
    return mode != "off"


def _put_audio(payload: dict[str, Any], params: dict[str, Any] | None,
               args: argparse.Namespace, provider: str, model: str | None) -> None:
    value = _audio_value(args, provider, model)
    if value is None:
        return
    cap = provider_capabilities(provider, model)
    field = cap.get("audio_field")
    if not field:
        if getattr(args, "audio", "auto") == "off":
            fail(f"{provider} 已知会默认生成原生音频，但未登记关闭音频的请求字段；"
                 "请用 VIDEO_CAPABILITIES_JSON 补充 audio_field/audio_location。")
        return
    field = str(field)
    if cap.get("audio_location") == "parameters":
        if params is None:
            fail(f"{provider} 音频能力配置要求 parameters，但该适配器没有 parameters")
        params[field] = value
    else:
        payload[field] = value


def resolve_provider(explicit: str | None) -> str:
    from media_gateway import gateway_managed, resolve_media_environment
    try:
        effective = resolve_media_environment("video", os.environ)
    except ValueError as error:
        fail(str(error))
    selected = effective.get("VIDEO_PROVIDER", "").strip()
    if gateway_managed("video", os.environ) and explicit and explicit != selected:
        fail("显式 provider 与统一视频网关配置冲突；请求未提交。")
    provider = (explicit or selected or "").strip()
    if not provider:
        fail("未指定 provider。请用 --provider 或设置 env VIDEO_PROVIDER。"
             f"可选：{ '、'.join(PROVIDERS) }。")
    if provider not in PROVIDERS:
        fail(f"不支持的 provider：{provider}。可选：{ '、'.join(PROVIDERS) }。")
    return provider


def resolve_model(provider: str, explicit: str | None = None, *, image: bool = False) -> str:
    """Resolve the effective model exactly as the provider adapter will use it."""
    from media_gateway import resolve_media_environment
    try:
        effective = resolve_media_environment("video", os.environ)
    except ValueError as error:
        fail(str(error))
    if explicit and explicit.strip():
        return explicit.strip()
    env_names = {
        "dashscope": ("DASHSCOPE_VIDEO_MODEL",),
        "ark": ("ARK_MODEL",),
        "openai-compatible": ("VIDEO_MODEL",),
        "xhs-maas": (("XHS_MAAS_I2V_MODEL",) if image else ("XHS_MAAS_T2V_MODEL",)),
        "agnes": ("AGNES_MODEL",),
    }.get(provider, ())
    for name in env_names:
        value = effective.get(name, "").strip() or env_lookup(name)
        if value:
            return value
    defaults = {
        "dashscope": DEFAULT_DASHSCOPE_MODEL,
        "ark": DEFAULT_ARK_MODEL,
        "openai-compatible": "sora-1",
        "xhs-maas": "happyhorse-1.0-i2v" if image else "happyhorse-1.0-t2v",
        "agnes": DEFAULT_AGNES_MODEL,
    }
    return defaults.get(provider, "")


# ── HTTP ──────────────────────────────────────────────────

def http_request(url: str, headers: dict[str, str], method: str = "GET",
                 payload: dict[str, Any] | None = None, timeout: int = 60,
                 direct: bool = False) -> dict[str, Any]:
    base_headers = {"User-Agent": UA}
    base_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        base_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=base_headers, method=method)
    open_fn = _NO_PROXY_OPENER.open if direct else urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        fail(f"接口返回 HTTP {exc.code}：{detail[:500]}")
    except urllib.error.URLError as exc:
        fail(f"无法连接接口：{exc.reason}")
    except (http.client.RemoteDisconnected, TimeoutError):
        fail("接口连接失败或超时，请稍后重试。")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        fail(f"接口返回的不是有效 JSON：{raw[:500]}")
    if not isinstance(parsed, dict):
        fail("接口返回格式不正确：顶层不是对象。")
    return parsed


def download_video(url: str, output: Path, timeout: int = 300, direct: bool = False) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.suffix:
        suffix = Path(urllib.parse.urlparse(url).path).suffix or ".mp4"
        output = output.with_suffix(suffix)
    print(f"  下载视频：{url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    open_fn = _NO_PROXY_OPENER.open if direct else urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            output.write_bytes(resp.read())
    except urllib.error.URLError as exc:
        fail(f"无法下载视频：{exc.reason}")
    except TimeoutError:
        fail("下载视频超时。")
    return output


def _find_video_url(obj: Any) -> str | None:
    keys = ("video_url", "video", "url", "file_url", "output_url", "videoUrl")
    if isinstance(obj, dict):
        for key in keys:
            val = obj.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
        for val in obj.values():
            found = _find_video_url(val)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_video_url(item)
            if found:
                return found
    elif isinstance(obj, str):
        if obj.startswith("http") and obj.lower().split("?")[0].endswith((".mp4", ".mov", ".webm")):
            return obj
    return None


def _image_to_data_or_url(image: str) -> str:
    """输入图：http(s) 直接用 URL；本地文件转 base64 data URI。"""
    if image.startswith("http"):
        return image
    p = Path(image)
    if not p.is_file():
        fail(f"输入图片不存在：{image}")
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _poll(task_url: str, headers: dict[str, str], interval: int, timeout: int,
          status_path: tuple[str, ...] = ("output", "task_status"),
          done=("SUCCEEDED", "SUCCESS", "COMPLETED"),
          bad=("FAILED", "CANCELED", "ERROR", "UNKNOWN"),
          payload: dict[str, Any] | None = None, direct: bool = False) -> dict[str, Any]:
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            fail(f"任务超时（{timeout}s），可稍后用 task_id 手动查询。")
        result = http_request(task_url, headers, method="GET", payload=payload, direct=direct)
        node: Any = result
        for k in status_path:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        status = str(node).upper() if isinstance(node, str) else ""
        if status in done:
            return result
        if status in bad:
            fail(f"任务失败：状态={status}，{json.dumps(result)[:300]}")
        print(f"  轮询中... 状态={status or '?'} 耗时={elapsed:.0f}s", file=sys.stderr)
        time.sleep(interval)


# ── provider: dashscope（通义万相 Wan）─────────────────────
def generate_dashscope(args: argparse.Namespace, image: str | None) -> Path:
    api_key = require_env("DASHSCOPE_API_KEY")
    base = (os.environ.get("DASHSCOPE_BASE_URL", "").strip() or DEFAULT_DASHSCOPE_BASE).rstrip("/")
    model = resolve_model("dashscope", args.model, image=image is not None)
    if image:
        endpoint = f"{base}/services/aigc/image2video/video-synthesis"
        input_block = {"img_url": _image_to_data_or_url(image), "prompt": args.prompt or ""}
    else:
        endpoint = f"{base}/services/aigc/text2video/video-synthesis"
        input_block = {"prompt": args.prompt}
    params: dict[str, Any] = {}
    if args.ratio:
        params["size"] = args.ratio.replace(":", "*")
    if args.duration:
        params["duration"] = args.duration
    payload = {"model": model, "input": input_block, "parameters": params}
    _put_audio(payload, params, args, "dashscope", model)
    headers = {"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable"}
    print(f"[dashscope] 提交任务 {endpoint}（model={model}）...", file=sys.stderr)
    result = http_request(endpoint, headers, method="POST", payload=payload)
    task_id = (result.get("output") or {}).get("task_id")
    if not task_id:
        fail(f"提交失败：{result.get('message', json.dumps(result)[:300])}")
    print(f"[dashscope] 任务已提交：{task_id}", file=sys.stderr)
    task = _poll(f"{base}/tasks/{task_id}", {"Authorization": f"Bearer {api_key}"},
                 args.poll_interval, args.timeout)
    url = _find_video_url(task)
    if not url:
        fail(f"任务完成但未找到视频 URL：{json.dumps(task)[:300]}")
    return download_video(url, Path(args.output))


# ── provider: ark（火山 Seedance）──────────────────────────
def generate_ark(args: argparse.Namespace, image: str | None) -> Path:
    api_key = require_env("ARK_API_KEY")
    base = (os.environ.get("ARK_BASE_URL", "").strip() or DEFAULT_ARK_BASE).rstrip("/")
    model = resolve_model("ark", args.model, image=image is not None)
    content: list[dict[str, Any]] = [{"type": "text", "text": args.prompt or ""}]
    if image:
        content.append({"type": "image_url", "image_url": {"url": _image_to_data_or_url(image)}})
    payload: dict[str, Any] = {"model": model, "content": content}
    if args.ratio:
        payload["ratio"] = args.ratio
    if args.duration:
        payload["duration"] = args.duration
    _put_audio(payload, None, args, "ark", model)
    headers = {"Authorization": f"Bearer {api_key}"}
    endpoint = f"{base}/contents/generations/tasks"
    print(f"[ark] 提交任务 {endpoint}（model={model}）...", file=sys.stderr)
    result = http_request(endpoint, headers, method="POST", payload=payload)
    task_id = result.get("id") or (result.get("data") or {}).get("id")
    if not task_id:
        fail(f"提交失败：{json.dumps(result)[:300]}")
    print(f"[ark] 任务已提交：{task_id}", file=sys.stderr)
    task = _poll(f"{endpoint}/{task_id}", headers, args.poll_interval, args.timeout,
                 status_path=("status",))
    url = _find_video_url(task)
    if not url:
        fail(f"任务完成但未找到视频 URL：{json.dumps(task)[:300]}")
    return download_video(url, Path(args.output))


# ── provider: kling（快手可灵，JWT 鉴权）───────────────────
def _kling_jwt(access_key: str, secret_key: str) -> str:
    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = b64url(json.dumps({"iss": access_key, "exp": now + 1800, "nbf": now - 5}).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret_key.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{b64url(sig)}"


def generate_kling(args: argparse.Namespace, image: str | None) -> Path:
    ak = require_env("KLING_ACCESS_KEY")
    sk = require_env("KLING_SECRET_KEY")
    base = (os.environ.get("KLING_BASE_URL", "").strip() or DEFAULT_KLING_BASE).rstrip("/")
    token = _kling_jwt(ak, sk)
    headers = {"Authorization": f"Bearer {token}"}
    if image:
        endpoint = f"{base}/v1/videos/image2video"
        payload: dict[str, Any] = {"image": _image_to_data_or_url(image), "prompt": args.prompt or ""}
    else:
        endpoint = f"{base}/v1/videos/text2video"
        payload = {"prompt": args.prompt}
    if args.model:
        payload["model_name"] = args.model
    if args.duration:
        payload["duration"] = str(args.duration)
    if args.ratio:
        payload["aspect_ratio"] = args.ratio
    _put_audio(payload, None, args, "kling", args.model)
    print(f"[kling] 提交任务 {endpoint} ...", file=sys.stderr)
    result = http_request(endpoint, headers, method="POST", payload=payload)
    data = result.get("data") or {}
    task_id = data.get("task_id") or result.get("task_id")
    if not task_id:
        fail(f"提交失败：{json.dumps(result)[:300]}")
    print(f"[kling] 任务已提交：{task_id}", file=sys.stderr)
    query_base = endpoint  # 可灵查询与提交同路径 + /{task_id}
    task = _poll(f"{query_base}/{task_id}", headers, args.poll_interval, args.timeout,
                 status_path=("data", "task_status"),
                 done=("SUCCEED", "SUCCEEDED", "SUCCESS"))
    url = _find_video_url(task)
    if not url:
        fail(f"任务完成但未找到视频 URL：{json.dumps(task)[:300]}")
    return download_video(url, Path(args.output))


# ── opt-in New API /v1/videos client ──────────────────────
class _NewAPIVideoNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # POST must never be replayed by urllib; content redirects are handled
        # explicitly below and carry no gateway credentials after the first hop.
        return None


_NEWAPI_VIDEO_OPENER = urllib.request.build_opener(_NewAPIVideoNoRedirect())
_NEWAPI_VIDEO_MAX_BYTES = 512 * 1024 * 1024
_NEWAPI_VIDEO_JSON_BYTES = 1024 * 1024
_NEWAPI_VIDEO_IMAGE_BYTES = 10 * 1024 * 1024
# These are deliberately narrow, administrator-selected mappings, not guesses
# based on the gateway domain or the client's model alias. See the client guide.
_NEWAPI_VIDEO_PROFILES = {
    "standard": {"seconds": (4, 8, 12), "default": 4, "square": None, "image": "file"},
    "veo": {"seconds": (4, 6, 8), "default": 4, "square": None, "image": "file"},
    "kling": {"seconds": (5, 10), "default": 5, "square": "1024x1024", "image": "both"},
    "wan26-t2v": {"seconds": (5, 10, 15), "default": 5, "square": "960x960", "image": "none"},
    "seedance1": {"seconds": tuple(range(2, 13)), "default": 5, "square": "960x960", "image": "url"},
}


def _newapi_video_url(value: str) -> str:
    """Validate URLs without exposing userinfo, query secrets or upstream bodies."""
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError("unsafe URL")
        parsed.port  # Invalid ports must be rejected before a charged submission.
    except ValueError:
        fail("New API 视频 URL 无效：需要无用户凭据的 HTTP(S) 地址。")
    return value


def _newapi_video_remaining(deadline: float, task_id: str = "") -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        fail(f"New API 视频任务超时，结果未知；请用 public_id={task_id or '[尚未取得]'} 查询，勿重复提交。")
    return remaining


def _newapi_video_body(args: argparse.Namespace, image: str | None) -> tuple[bytes, str, str]:
    model = (args.model or env_lookup("VIDEO_MODEL") or "").strip()
    if not model:
        fail("New API 模式必须显式配置 VIDEO_MODEL 或 --model，不使用 legacy 的默认模型。")
    profile_name = os.environ.get("VIDEO_NEWAPI_PROFILE", "standard").strip().lower()
    profile = _NEWAPI_VIDEO_PROFILES.get(profile_name)
    if profile is None:
        fail("未知 VIDEO_NEWAPI_PROFILE；可用：" + "、".join(_NEWAPI_VIDEO_PROFILES))
    duration = args.duration if args.duration is not None else profile["default"]
    if type(duration) is not int or duration not in profile["seconds"]:
        fail(f"New API profile={profile_name} 不支持 duration={duration}。")
    ratio = args.ratio or "16:9"
    sizes = {"16:9": "1280x720", "9:16": "720x1280"}
    if profile["square"]:
        sizes["1:1"] = profile["square"]
    if ratio not in sizes:
        fail(f"New API profile={profile_name} 不支持 ratio={ratio}；支持：" + "、".join(sizes))
    if getattr(args, "audio", "auto") != "auto":
        fail("New API 视频 profile 尚未确认音频开关映射；--audio 仅支持 auto。")
    prompt = args.prompt or ""
    if not isinstance(prompt, str) or len(prompt.encode("utf-8")) > 128 * 1024 or (not image and not prompt.strip()):
        fail("New API 视频 prompt 不能为空，且 UTF-8 长度不能超过 128 KiB。")
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "seconds": str(duration), "size": sizes[ratio]}
    if profile_name == "seedance1":
        # Doubao only derives resolution from size; aspect ratio belongs in metadata.
        payload["metadata"] = {"ratio": ratio, "resolution": "720p"}
    file_bytes, file_type = None, ""
    if image:
        image_kind = profile["image"]
        if image_kind == "none":
            fail(f"New API profile={profile_name} 仅支持文生视频。")
        if image.startswith(("https://", "http://")):
            if image_kind == "file":
                fail(f"New API profile={profile_name} 的参考图仅支持本地 multipart 文件。")
            image_url = _newapi_video_url(image)
            if profile_name == "seedance1":
                payload["metadata"]["content"] = [{"type": "image_url", "image_url": {"url": image_url}}]
            else:
                payload["input_reference"] = image_url
        else:
            if image_kind == "url":
                fail(f"New API profile={profile_name} 的参考图必须是 HTTP(S) URL，不接受本地文件。")
            try:
                with Path(image).open("rb") as source:
                    file_bytes = source.read(_NEWAPI_VIDEO_IMAGE_BYTES + 1)
            except OSError:
                fail("New API 视频参考图无法读取。")
            if not file_bytes or len(file_bytes) > _NEWAPI_VIDEO_IMAGE_BYTES:
                fail("New API 视频参考图必须非空且不超过 10 MiB。")
            if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                file_type = "png"
            elif file_bytes.startswith(b"\xff\xd8\xff"):
                file_type = "jpeg"
            elif file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP":
                file_type = "webp"
            else:
                fail("New API 视频参考图必须是 PNG、JPEG 或 WebP。")
    if file_bytes is None:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json", model
    boundary = "easel_video_" + os.urandom(16).hex()
    parts = []
    for name, value in payload.items():
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{text}\r\n'.encode("utf-8"))
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="input_reference"; filename="reference.{file_type}"'
                  f'\r\nContent-Type: image/{file_type}\r\n\r\n').encode("ascii") + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary, model


def _newapi_video_json(url: str, api_key: str, deadline: float, *, task_id: str = "",
                       body: bytes | None = None, content_type: str = "application/json") -> dict[str, Any]:
    headers = {"User-Agent": UA, "Accept": "application/json", "Authorization": "Bearer " + api_key}
    if body is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    label = f"public_id={task_id}" if task_id else "提交结果未知；勿自动重复提交"
    try:
        with _NEWAPI_VIDEO_OPENER.open(request, timeout=min(60, _newapi_video_remaining(deadline, task_id))) as response:
            raw = response.read(_NEWAPI_VIDEO_JSON_BYTES + 1)
    except urllib.error.HTTPError as exc:
        fail(f"New API 视频 HTTP {exc.code}（{label}），未重发 POST。")
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        fail(f"New API 视频连接失败或超时（{label}），未重发 POST。")
    _newapi_video_remaining(deadline, task_id)
    if len(raw) > _NEWAPI_VIDEO_JSON_BYTES:
        fail(f"New API 视频 JSON 超过 1 MiB（{label}）。")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        fail(f"New API 视频未返回有效 JSON（{label}）。")
    if not isinstance(result, dict) or result.get("error") or result.get("success") is False:
        fail(f"New API 视频返回业务错误或无效对象（{label}）。")
    return result


def _newapi_video_download(endpoint: str, task_id: str, api_key: str, task: dict[str, Any],
                           output: Path, deadline: float) -> Path:
    # Prefer the gateway-owned content route, even if a completed response also
    # contains a CDN URL. Native completion JSON often has no URL at all.
    target = f"{endpoint}/{task_id}/content"
    authenticated, fallback_allowed, redirects = True, True, 0
    temp_path: Path | None = None
    if not output.suffix:
        output = output.with_suffix(".mp4")
    try:
        while True:
            headers = {"User-Agent": UA, "Accept": "video/*, application/octet-stream"}
            if authenticated:
                headers["Authorization"] = "Bearer " + api_key
            request = urllib.request.Request(target, headers=headers, method="GET")
            try:
                response = _NEWAPI_VIDEO_OPENER.open(request, timeout=min(300, _newapi_video_remaining(deadline, task_id)))
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    if redirects >= 3:
                        fail(f"New API 视频下载重定向超过 3 次（public_id={task_id}）。")
                    location = exc.headers.get("Location", "") if exc.headers else ""
                    if not location:
                        fail(f"New API 视频下载重定向缺少地址（public_id={task_id}）。")
                    next_target = _newapi_video_url(urllib.parse.urljoin(target, location))
                    if urllib.parse.urlsplit(target).scheme == "https" and urllib.parse.urlsplit(next_target).scheme != "https":
                        fail(f"New API 视频下载拒绝 HTTPS 降级（public_id={task_id}）。")
                    target = next_target
                    authenticated, fallback_allowed = False, False
                    redirects += 1
                    continue
                if fallback_allowed and exc.code in (404, 405, 501):
                    # Only explicit result fields, never recursive reference/input URLs.
                    candidate = task.get("video_url") or task.get("url")
                    if isinstance(candidate, str) and candidate:
                        target = _newapi_video_url(candidate)
                        if urllib.parse.urlsplit(endpoint).scheme == "https" and urllib.parse.urlsplit(target).scheme != "https":
                            fail(f"New API 视频下载拒绝 HTTPS 降级（public_id={task_id}）。")
                        authenticated, fallback_allowed = False, False
                        continue
                fail(f"New API 视频内容下载失败 HTTP {exc.code}（public_id={task_id}），未重发任务。")
            with response:
                if response.status != 200:
                    fail(f"New API 视频内容不是完整 HTTP 200 响应（public_id={task_id}）。")
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if not (mime.startswith("video/") or mime == "application/octet-stream"):
                    fail(f"New API 视频内容不是视频二进制（public_id={task_id}）。")
                expected = None
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    if not re.fullmatch(r"[0-9]{1,12}", declared_length.strip()):
                        fail(f"New API 视频 Content-Length 无效（public_id={task_id}）。")
                    expected = int(declared_length)
                    if not 0 < expected <= _NEWAPI_VIDEO_MAX_BYTES:
                        fail(f"New API 视频 Content-Length 为空或超过上限（public_id={task_id}）。")
                output.parent.mkdir(parents=True, exist_ok=True)
                total = 0
                with tempfile.NamedTemporaryFile(prefix=".easel-video-", dir=output.parent, delete=False) as temp:
                    temp_path = Path(temp.name)
                    while True:
                        _newapi_video_remaining(deadline, task_id)
                        chunk = response.read(min(64 * 1024, _NEWAPI_VIDEO_MAX_BYTES - total + 1))
                        if not chunk:
                            break
                        if total == 0:
                            # MIME alone is insufficient: a 200 error page or JSON
                            # may be mislabeled. Accept common video containers.
                            mp4 = len(chunk) >= 12 and chunk[4:8] in (b"ftyp", b"styp", b"moov", b"mdat", b"wide", b"free")
                            webm = chunk.startswith(b"\x1aE\xdf\xa3")
                            avi = chunk.startswith(b"RIFF") and chunk[8:12] == b"AVI "
                            if not (mp4 or webm or avi):
                                fail(f"New API 视频内容缺少 MP4/MOV、WebM 或 AVI 文件头（public_id={task_id}）。")
                        total += len(chunk)
                        if total > _NEWAPI_VIDEO_MAX_BYTES:
                            fail(f"New API 视频内容超过 512 MiB（public_id={task_id}）。")
                        temp.write(chunk)
                _newapi_video_remaining(deadline, task_id)
                if not total:
                    fail(f"New API 视频内容为空（public_id={task_id}）。")
                if expected is not None and total != expected:
                    fail(f"New API 视频下载字节数与 Content-Length 不符（public_id={task_id}），请重新下载已有任务。")
                os.replace(temp_path, output)
                temp_path = None
                return output
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        fail(f"New API 视频下载连接或文件写入失败（public_id={task_id}），未重发任务。")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _generate_newapi_video(args: argparse.Namespace, image: str | None) -> Path:
    api_key = require_env("VIDEO_API_KEY")
    base = _newapi_video_url(require_env("VIDEO_BASE_URL").rstrip("/"))
    if urllib.parse.urlsplit(base).query:
        fail("VIDEO_BASE_URL 不能包含查询参数，请使用网关 API 根路径。")
    if not urllib.parse.urlsplit(base).path.endswith("/v1"):
        base += "/v1"
    endpoint = base + "/videos"
    if (not isinstance(args.timeout, (int, float)) or not 0 < args.timeout <= 86400
            or not isinstance(args.poll_interval, (int, float)) or not 0 < args.poll_interval <= 3600):
        fail("New API 视频 timeout 必须在 (0, 86400] 秒，poll_interval 必须在 (0, 3600] 秒。")
    body, content_type, model = _newapi_video_body(args, image)
    deadline = time.monotonic() + args.timeout
    print(f"[new-api/video] 提交已配置的视频渠道（model={model}）...", file=sys.stderr)
    task = _newapi_video_json(endpoint, api_key, deadline, body=body, content_type=content_type)
    task_id = task.get("id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        fail("New API 视频提交未返回有效 public_id；提交结果未知，勿自动重复提交。")
    print(f"[new-api/video] public_id={task_id}", file=sys.stderr)
    while True:
        _newapi_video_remaining(deadline, task_id)
        if task.get("id") != task_id:
            fail(f"New API 视频轮询 public_id 不一致（原 public_id={task_id}）。")
        status = str(task.get("status", "")).lower()
        if status == "completed":
            return _newapi_video_download(endpoint, task_id, api_key, task, Path(args.output), deadline)
        if status in ("failed", "error", "cancelled", "canceled"):
            fail(f"New API 视频任务失败（public_id={task_id}，status={status}）。")
        if status not in ("queued", "pending", "in_progress", "processing", "running"):
            fail(f"New API 视频任务状态未知（public_id={task_id}），请人工查询，勿重复提交。")
        time.sleep(min(args.poll_interval, _newapi_video_remaining(deadline, task_id)))
        _newapi_video_remaining(deadline, task_id)
        task = _newapi_video_json(f"{endpoint}/{task_id}", api_key, deadline, task_id=task_id)


# ── provider: openai-compatible ───────────────────────────
def generate_openai_compatible(args: argparse.Namespace, image: str | None) -> Path:
    api_mode = os.environ.get("VIDEO_API_MODE", "custom").strip().lower() or "custom"
    if api_mode == "new-api":
        return _generate_newapi_video(args, image)
    if api_mode not in ("custom", "legacy"):
        fail("VIDEO_API_MODE 仅支持 custom、legacy 或 new-api。")
    api_key = require_env("VIDEO_API_KEY")
    base = require_env("VIDEO_BASE_URL").rstrip("/")
    model = resolve_model("openai-compatible", args.model, image=image is not None)
    payload: dict[str, Any] = {"model": model, "prompt": args.prompt or ""}
    if image:
        payload["image"] = _image_to_data_or_url(image)
    if args.duration:
        payload["seconds"] = args.duration
    if args.ratio:
        payload["size"] = args.ratio
    _put_audio(payload, None, args, "openai-compatible", model)
    headers = {"Authorization": f"Bearer {api_key}"}
    endpoint = f"{base}/videos"
    print(f"[openai-compatible] 提交任务 {endpoint}（model={model}）...", file=sys.stderr)
    result = http_request(endpoint, headers, method="POST", payload=payload)
    # 同步返回 URL 则直接下载；否则轮询 /videos/{id}
    url = _find_video_url(result)
    if url:
        return download_video(url, Path(args.output))
    task_id = result.get("id") or (result.get("data") or {}).get("id")
    if not task_id:
        fail(f"提交失败：{json.dumps(result)[:300]}")
    task = _poll(f"{endpoint}/{task_id}", headers, args.poll_interval, args.timeout,
                 status_path=("status",))
    url = _find_video_url(task)
    if not url:
        fail(f"任务完成但未找到视频 URL：{json.dumps(task)[:300]}")
    return download_video(url, Path(args.output))


# ── provider: xhs-maas（小红书内网 happyhorse 文/图生视频）─────
def generate_xhs_maas(args: argparse.Namespace, image: str | None) -> Path:
    """小红书内网 MaaS：DashScope 风格异步，鉴权 api-key 头，全程直连（绕代理）。

    单端点 video-synthesis，t2v/i2v 靠 model 区分；轮询 GET /tasks/{id} 必须带
    body {"model": <model>}（否则返回误导性的 invalid token）；视频 URL 在 output.video_url。
    """
    api_key = require_env("XHS_MAAS_API_KEY")
    base = (os.environ.get("XHS_MAAS_VIDEO_BASE", "").strip()
            or DEFAULT_XHS_MAAS_VIDEO_BASE).rstrip("/")
    if image:
        model = resolve_model("xhs-maas", args.model, image=True)
        input_block: dict[str, Any] = {
            "prompt": args.prompt or "",
            "media": [{"type": "first_frame", "url": _image_to_data_or_url(image)}],
        }
    else:
        model = resolve_model("xhs-maas", args.model, image=False)
        input_block = {"prompt": args.prompt}
    params: dict[str, Any] = {"resolution": os.environ.get("XHS_MAAS_RESOLUTION", "720P")}
    if args.ratio:
        params["ratio"] = args.ratio
    if args.duration:
        params["duration"] = args.duration
    payload = {"model": model, "input": input_block, "parameters": params}
    _put_audio(payload, params, args, "xhs-maas", model)
    headers = {"api-key": api_key}
    endpoint = f"{base}/services/aigc/video-generation/video-synthesis"
    print(f"[xhs-maas] 提交任务 {endpoint}（model={model}）...", file=sys.stderr)
    result = http_request(endpoint, headers, method="POST", payload=payload, direct=True)
    task_id = (result.get("output") or {}).get("task_id")
    if not task_id:
        fail(f"提交失败：{result.get('message') or json.dumps(result, ensure_ascii=False)[:300]}")
    print(f"[xhs-maas] 任务已提交：{task_id}", file=sys.stderr)
    task = _poll(f"{base}/tasks/{task_id}", headers, args.poll_interval, args.timeout,
                 status_path=("output", "task_status"),
                 payload={"model": model}, direct=True)
    url = _find_video_url(task)
    if not url:
        fail(f"任务完成但未找到视频 URL：{json.dumps(task, ensure_ascii=False)[:300]}")
    return download_video(url, Path(args.output), direct=True)


# ── provider: agnes（apihub.agnes-ai.com，OpenAI Videos 兼容创建 + 自定义端点轮询）──
def generate_agnes(args: argparse.Namespace, image: str | None) -> Path:
    """Agnes 视频：POST /v1/videos 返回 video_id；GET /agnesapi?video_id=&model_name= 轮询到
    status=completed，根级 url 为成片。默认带原生音频（prompt 描述声音，无开关字段）。外网走代理。"""
    api_key = require_env("AGNES_API_KEY")
    base = (os.environ.get("AGNES_BASE_URL", "").strip() or DEFAULT_AGNES_BASE).rstrip("/")
    poll_base = (os.environ.get("AGNES_POLL_BASE", "").strip() or DEFAULT_AGNES_POLL_BASE).rstrip("/")
    model = resolve_model("agnes", args.model, image=image is not None)
    seconds = "5"
    if args.duration:
        seconds = str(max(4, min(int(args.duration), 12)))   # flash 支持 4-12s
    payload: dict[str, Any] = {
        "model": model,
        "prompt": args.prompt or "",
        "seconds": seconds,
        "size": os.environ.get("AGNES_SIZE", "").strip() or "720P",   # flash 锁 720P
        "aspect_ratio": args.ratio or "16:9",
    }
    if image:
        payload["mode"] = "keyframe"          # 首帧控制的图生视频
        payload["first_frame"] = _image_to_data_or_url(image)
    else:
        payload["mode"] = "text"
    _put_audio(payload, None, args, "agnes", model)
    headers = {"Authorization": f"Bearer {api_key}"}
    endpoint = f"{base}/videos"
    print(f"[agnes] 提交任务 {endpoint}（model={model}）...", file=sys.stderr)
    result = http_request(endpoint, headers, method="POST", payload=payload)
    vid = result.get("video_id") or result.get("id") or result.get("task_id")
    if not vid:
        fail(f"提交失败：{json.dumps(result, ensure_ascii=False)[:300]}")
    print(f"[agnes] 任务已提交：{vid}", file=sys.stderr)
    poll_url = (f"{poll_base}?video_id={urllib.parse.quote(str(vid))}"
                f"&model_name={urllib.parse.quote(model)}")
    task = _poll(poll_url, headers, args.poll_interval, args.timeout, status_path=("status",))
    url = _find_video_url(task)
    if not url:
        fail(f"任务完成但未找到视频 URL：{json.dumps(task, ensure_ascii=False)[:300]}")
    return download_video(url, Path(args.output))


GENERATORS = {
    "dashscope": generate_dashscope,
    "ark": generate_ark,
    "kling": generate_kling,
    "openai-compatible": generate_openai_compatible,
    "xhs-maas": generate_xhs_maas,
    "agnes": generate_agnes,
}

PROVIDER_REQUIRED_ENV = provider_required_env("video")


def _mask(value: str) -> str:
    return "*" * len(value) if len(value) <= 8 else f"{value[:4]}…{value[-4:]}"


def cmd_check(args: argparse.Namespace) -> int:
    provider = resolve_provider(args.provider)
    print(f"provider = {provider}")
    ok = True
    for name, required in PROVIDER_REQUIRED_ENV[provider]:
        value = env_lookup(name)
        aliases = ENV_ALIASES.get(name, ())
        hint = f"（别名：{ '、'.join(aliases) }）" if aliases else ""
        tag = "必填" if required else "可选"
        if value:
            print(f"  [OK]   {name} [{tag}] = {_mask(value)}")
        elif required:
            ok = False
            print(f"  [缺失] {name} [{tag}] 未设置{hint}")
        else:
            print(f"  [默认] {name} [{tag}] 未设置（用内置默认值）")
    if ok:
        print("结果：配置完整，可以执行 text2video / image2video。")
        return 0
    print("结果：缺少必填配置，请补齐后再执行。", file=sys.stderr)
    return 1


def cmd_capabilities(args: argparse.Namespace) -> int:
    provider = resolve_provider(args.provider)
    print(json.dumps(provider_capabilities(provider, args.model), ensure_ascii=False, indent=2))
    return 0


def _norm_text(s: str) -> str:
    """归一化：繁体→简体（消除 ASR 常把中文转繁体导致的假阴性）+ 转小写 + 只留字母数字与 CJK（去标点/空格）。"""
    s = s or ""
    try:
        import zhconv  # 轻量纯 Python；缺了就跳过繁简归一（不影响英文/已简体）
        s = zhconv.convert(s, "zh-cn")
    except Exception:  # noqa: BLE001
        pass
    return "".join(ch for ch in s.lower() if ch.isalnum())


def dialogue_faithful_verdict(expected: str, heard: str, detected_lang: str | None,
                              expected_language: str, threshold: float = 0.9) -> tuple[bool, float, bool]:
    """纯函数：由「要求台词 vs ASR 听到的」判模型是否逐字忠实。返回 (faithful, similarity, language_ok)。
    忠实 = 语言正确 且 文本相似度 ≥ 阈值（默认 0.9，因要求「完全按照」）。便于单测。"""
    lang = str(detected_lang or "").lower()
    exp = (expected_language or "").split("-")[0].lower()
    language_ok = (not exp) or lang.startswith(exp)
    sim = difflib.SequenceMatcher(None, _norm_text(expected), _norm_text(heard)).ratio() if expected else 0.0
    faithful = bool(expected and language_ok and sim >= threshold)
    return faithful, round(sim, 3), language_ok


def cmd_probe_dialogue(args: argparse.Namespace) -> int:
    """真机探针：让配置的视频模型逐字说一句测试台词 → ASR 核对 → 判 dialogue_faithful 并缓存。
    忠实→短剧用原生对白直通；不忠实→短剧生成阶段不给台词（有口型+预留时长）、后期配音。"""
    provider = resolve_provider(args.provider)
    model = args.model
    text = args.text or "你好，这是一条视频原生对白测试。"
    language = args.language or "zh-CN"
    cap = provider_capabilities(provider, model)

    def _finish(faithful: bool, probe: dict) -> int:
        entry = {"dialogue_faithful": faithful, "probe": {**probe, "ts": int(time.time())}}
        _save_probe_cache(provider, model, entry)
        print(json.dumps({"provider": provider, "model": model,
                          "dialogue_faithful": faithful, **probe}, ensure_ascii=False, indent=2))
        print(f"\n结论：{provider}:{model or '默认'} 逐字忠实 = {faithful}（已写入 {PROBE_CACHE_PATH}）")
        if not faithful:
            print("→ 短剧将走「无台词生成 + 后期配音」（人物有口型+预留时长，不产出台词）。")
        print("如需手动覆盖，设 env：VIDEO_CAPABILITIES_JSON='"
              + json.dumps({f"{provider}:{model or ''}": {"dialogue_faithful": faithful}}, ensure_ascii=False) + "'")
        return 0

    if not cap.get("native_audio"):
        return _finish(False, {"skipped": "no_native_audio", "note": "该 provider/model 未声明原生音频，无法生成对白"})

    outdir = Path("outputs/_probe")
    outdir.mkdir(parents=True, exist_ok=True)
    clip = outdir / f"probe_{provider}_{(model or 'default').replace('/', '_').replace(':', '_')}.mp4"
    scene = (getattr(args, "scene", None) or "").strip()
    if scene:
        # 用真实短剧画面 + native-first 契约压测（贴近实际生成，可测中英混杂等硬场景）
        prompt = (f"{scene}\n\n【音频硬约束】画面中的人物必须用{language}清晰地**逐字说出**："
                  f"「{text}」，口型与声音同步；不要改写、翻译、增删或重复台词；"
                  "台词之外只保留与动作同步的自然环境声，不加旁白或背景音乐。")
    else:
        prompt = (f"一个人物正对镜头说话，口型清晰、与声音同步，背景简单。"
                  f"必须用{language}逐字说出：「{text}」，不要改写、翻译、增删或重复。")
    gen_args = argparse.Namespace(
        provider=provider, model=model, prompt=prompt,
        duration=args.duration or 5, ratio=args.ratio or "9:16", audio="on",
        output=str(clip), poll_interval=10, timeout=args.timeout or 900,
        image=getattr(args, "image", None))
    image = None
    if args.mode == "i2v":
        if not gen_args.image:
            fail("--mode i2v 需要 --image 提供首帧图")
        image = gen_args.image
    print(f"[probe] 生成测试片段：{provider}:{model or '默认'} 说「{text}」…", file=sys.stderr)
    GENERATORS[provider](gen_args, image)

    asr = Path(__file__).resolve().parent / "asr.py"
    asr_out = outdir / "probe_asr.json"
    cp = subprocess.run([sys.executable, str(asr), "transcribe", "-i", str(clip),
                         "--model", args.asr_model or "base", "--format", "json", "-o", str(asr_out)],
                        capture_output=True, text=True)
    if cp.returncode != 0 or not asr_out.is_file():
        return _finish(False, {"error": "asr_failed", "asr_stderr": (cp.stderr or "").strip()[-400:],
                               "note": "ASR 失败，无法判定，保守判不忠实"})
    try:
        data = json.loads(asr_out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _finish(False, {"error": "asr_bad_json"})
    heard = "".join(str(s.get("text") or "") for s in data.get("segments", []))
    detected = data.get("language")
    faithful, sim, lang_ok = dialogue_faithful_verdict(text, heard, detected, language, args.threshold)
    return _finish(faithful, {"expected": text, "heard": heard, "detected_language": detected,
                              "language_ok": lang_ok, "similarity": sim, "threshold": args.threshold,
                              "clip": str(clip), "asr_model": args.asr_model or "base"})


def cmd_text2video(args: argparse.Namespace) -> int:
    if not args.prompt or not args.prompt.strip():
        fail("--prompt 不能为空，请描述视频画面 / 镜头 / 风格。")
    out = GENERATORS[resolve_provider(args.provider)](args, None)
    print("生成完成：")
    print(out)
    return 0


def cmd_image2video(args: argparse.Namespace) -> int:
    if not args.image:
        fail("--image 不能为空（图生视频/数字人首帧驱动需要输入图）。")
    out = GENERATORS[resolve_provider(args.provider)](args, args.image)
    print("生成完成：")
    print(out)
    return 0


def _add_common(p: argparse.ArgumentParser, need_prompt: bool) -> None:
    p.add_argument("--env-file", help="指定 .env；默认从当前目录向上查找。")
    p.add_argument("--provider", help=f"provider（{ '/'.join(PROVIDERS) }），也可用 env VIDEO_PROVIDER")
    p.add_argument("--prompt", required=need_prompt, help="画面/镜头/风格描述")
    p.add_argument("--model", help="模型名（覆盖默认 / env）")
    p.add_argument("--duration", type=int, help="时长（秒，各家取值范围不同）")
    p.add_argument("--ratio", help="画幅比例，如 16:9 / 9:16 / 1:1")
    p.add_argument("--audio", choices=["auto", "on", "off"], default="auto",
                   help="原生音频：auto=按能力表开启，on=要求支持，off=显式关闭（默认 auto）")
    p.add_argument("-o", "--output", required=True,
                   help="输出视频路径；必须位于 outputs/<人类可读主题>/")
    p.add_argument("--poll-interval", type=int, default=10, help="轮询间隔秒（默认 10）")
    p.add_argument("--timeout", type=int, default=900, help="任务总超时秒（默认 900）")


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 视频生成（文/图生视频，多 provider）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("text2video", help="文生视频")
    _add_common(p1, need_prompt=True)
    p1.set_defaults(func=cmd_text2video)

    p2 = sub.add_parser("image2video", help="图生视频 / 数字人首帧驱动")
    _add_common(p2, need_prompt=False)
    p2.add_argument("--image", required=True, help="输入图片（本地路径或 http URL）")
    p2.set_defaults(func=cmd_image2video)

    p3 = sub.add_parser("check", help="离线校验 provider 所需 env（不发请求）")
    p3.add_argument("--env-file", help="指定 .env；默认从当前目录向上查找。")
    p3.add_argument("--provider", help=f"provider（{ '/'.join(PROVIDERS) }）")
    p3.set_defaults(func=cmd_check)

    p4 = sub.add_parser("capabilities", help="打印统一模型能力协议（不发请求）")
    p4.add_argument("--env-file", help="指定 .env；默认从当前目录向上查找。")
    p4.add_argument("--provider", help=f"provider（{ '/'.join(PROVIDERS) }）")
    p4.add_argument("--model", help="模型名，用于读取 provider:model 覆盖")
    p4.set_defaults(func=cmd_capabilities)

    p5 = sub.add_parser("probe-dialogue",
                        help="真机探针：测配置模型能否逐字忠实生成台词，判 dialogue_faithful 并缓存（真发1次生成，计费）")
    p5.add_argument("--env-file", help="指定 .env；默认从当前目录向上查找。")
    p5.add_argument("--provider", help=f"provider（{ '/'.join(PROVIDERS) }），也可用 env VIDEO_PROVIDER")
    p5.add_argument("--model", help="模型名（覆盖默认 / env）")
    p5.add_argument("--text", help="测试台词（默认一句中文测试句；可含中英混杂压测）")
    p5.add_argument("--scene", help="真实短剧画面描述（给了就按 native-first 契约嵌入真实场景压测，而非温室短句）")
    p5.add_argument("--language", default="zh-CN", help="期望语言（默认 zh-CN；中英混杂时按主语言判、辅以文本相似度）")
    p5.add_argument("--mode", choices=["t2v", "i2v"], default="t2v", help="探针生成方式（默认 t2v，最省；i2v 需 --image）")
    p5.add_argument("--image", help="i2v 模式的首帧图")
    p5.add_argument("--duration", type=int, help="测试片段时长秒")
    p5.add_argument("--ratio", help="画幅，默认 9:16")
    p5.add_argument("--timeout", type=int, help="生成超时秒")
    p5.add_argument("--asr-model", dest="asr_model", default="base", help="faster-whisper 模型（默认 base）")
    p5.add_argument("--threshold", type=float, default=0.9, help="逐字忠实相似度阈值（默认 0.9）")
    p5.set_defaults(func=cmd_probe_dialogue)

    args = ap.parse_args()
    env_file = Path(args.env_file) if getattr(args, "env_file", None) else find_default_env_file()
    load_env_file(env_file)
    from media_gateway import resolve_media_environment
    try:
        os.environ.update(resolve_media_environment("video", os.environ))
    except ValueError as error:
        fail(str(error))
    if getattr(args, "output", None):
        args.output = str(validate_output_path(args.output))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
