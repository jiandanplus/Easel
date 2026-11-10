#!/usr/bin/env python3
"""ai_music.py — AI 音乐 / BGM 生成的可插拔客户端（纯标准库，无第三方依赖）。

给短视频 / 社媒内容生成原创背景音乐（BGM / 配乐 / 纯音乐），支持可插拔 provider：

  - dashscope        DashScope 旧版异步接口，或显式 New API Fun-Music 同步插件
  - suno-compatible  Suno 类第三方 API 的通用格式（异步提交 → 轮询 → 下载）

legacy 保留原提交/轮询流程；new-api 按对应插件的同步或异步协议执行，下载到 --output。

配置来自环境变量或 .env 文件（脚本会从当前目录向上查找 .env）：

  【通用】
    MUSIC_PROVIDER       选择 provider（dashscope / suno-compatible），也可用 --provider
    MUSIC_API_MODE       默认 legacy；显式 new-api 使用 New API 插件协议，不猜测域名

  【dashscope】
    DASHSCOPE_API_KEY    API key（别名：DASHSCOPE_KEY / ALIYUN_API_KEY）
    DASHSCOPE_MUSIC_MODEL 模型名（可选；兼容旧名 DASHSCOPE_MODEL）
    DASHSCOPE_BASE_URL   API 根地址（可选，默认官方地址）

  【suno-compatible】
    MUSIC_API_KEY        API key
    MUSIC_BASE_URL       API 根地址（如 https://api.example.com/v1）
    MUSIC_MODEL          模型名（可选，默认见 DEFAULT_SUNO_MODEL）

子命令：
    generate   生成音乐（--prompt 风格/情绪 + 可选 --lyrics/--duration/--instrumental）
    check      离线校验当前 provider 所需 env 是否配好（不发任何请求）

用法示例：
    ai_music.py check
    ai_music.py check --provider suno-compatible
    ai_music.py generate --provider dashscope \\
        --prompt "轻快的 lo-fi 嘻哈，适合 vlog 片头，钢琴 + 鼓点" \\
        --duration 30 --instrumental -o outputs/ai-music/bgm.mp3
    ai_music.py generate --provider suno-compatible \\
        --prompt "史诗感电影配乐，弦乐渐强" --lyrics "..." -o outputs/ai-music/track.mp3

说明：legacy 保留既有自定义接口；new-api 对照锁定插件源码实现。
本地 HTTP mock 测试不代表已用真实供应商 key 完成生成，详见 docs/platform/new-api-media-client.md。
"""
from __future__ import annotations

from media_gateway import require_media_available
require_media_available()

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from output_paths import validate_output_path
from typing import Any

from model_registry import env_aliases, provider_ids, provider_required_env

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# provider 默认值（可被 env / --model 覆盖）
DEFAULT_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_DASHSCOPE_MODEL = "audio-generation"
DEFAULT_SUNO_MODEL = "music-1"
DEFAULT_NEWAPI_DASHSCOPE_MODEL = "fun-music-v1"

# 每个 provider 声明所需 env（主名 → 别名元组）。check / 报错都基于这份声明。
PROVIDERS = provider_ids("music")
ENV_ALIASES = env_aliases("music")


def fail(message: str, exit_code: int = 1) -> "None":
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(exit_code)


# ── 配置与环境 ──────────────────────────────────────────────

def strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def find_default_env_file() -> Path | None:
    for directory in (Path.cwd(), *Path.cwd().parents):
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
        fail(f"无法读取 .env 文件：{exc}")
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
    """按主名 + 别名查找 env，返回去空白后的值（找不到返回空串）。"""
    for candidate in (name, *ENV_ALIASES.get(name, ())):
        value = os.environ.get(candidate, "").strip()
        if value:
            return value
    return ""


def require_env(name: str) -> str:
    value = env_lookup(name)
    if not value:
        accepted = "、".join((name, *ENV_ALIASES.get(name, ())))
        fail(f"缺少配置 {name}。请在 .env 或环境变量中设置（兼容变量名：{accepted}）。")
    return value


def resolve_provider(explicit: str | None) -> str:
    from media_gateway import gateway_managed, resolve_media_environment
    try:
        effective = resolve_media_environment("music", os.environ)
    except ValueError as error:
        fail(str(error))
    selected = effective.get("MUSIC_PROVIDER", "").strip()
    if gateway_managed("music", os.environ) and explicit and explicit != selected:
        fail("显式 provider 与统一音乐网关配置冲突；请求未提交。")
    provider = (explicit or selected or "").strip()
    if not provider:
        fail(
            "未指定 provider。请用 --provider 或设置 env MUSIC_PROVIDER。"
            f"可选：{ '、'.join(PROVIDERS) }。"
        )
    if provider not in PROVIDERS:
        fail(f"不支持的 provider：{provider}。可选：{ '、'.join(PROVIDERS) }。")
    return provider


def resolve_api_mode() -> str:
    mode = os.environ.get("MUSIC_API_MODE", "").strip() or "legacy"
    if mode not in ("legacy", "new-api"):
        fail("MUSIC_API_MODE 仅支持 legacy 或 new-api；不会根据域名自动选择协议。")
    return mode


# ── HTTP 工具 ──────────────────────────────────────────────

class _NewAPINoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Surface 3xx locally: never forward Authorization or resend to Location.
        return None


def _new_api_open(request: urllib.request.Request, timeout: int):
    return urllib.request.build_opener(_NewAPINoRedirect()).open(request, timeout=timeout)


def http_post(url: str, headers: dict[str, str], payload: dict[str, Any],
              timeout: int = 60, *, redact_errors: bool = False) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    base_headers = {"Content-Type": "application/json", "User-Agent": UA}
    base_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=base_headers, method="POST")
    try:
        open_request = _new_api_open if redact_errors else urllib.request.urlopen
        with open_request(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if redact_errors:
            fail(f"New API 接口返回 HTTP {exc.code}；未重新提交任务。")
        detail = exc.read().decode("utf-8", errors="replace")
        fail(f"接口返回 HTTP {exc.code}：{detail}")
    except urllib.error.URLError as exc:
        if redact_errors:
            fail("New API 接口连接失败；未切换协议或重新提交任务。")
        fail(f"无法连接接口：{exc.reason}")
    except (http.client.RemoteDisconnected, TimeoutError):
        fail("接口连接失败或超时，请稍后重试。")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if redact_errors:
            fail("New API 接口未返回有效 JSON；未重新提交任务。")
        fail(f"接口返回的不是有效 JSON：{raw[:500]}")
    if not isinstance(parsed, dict):
        fail("接口返回格式不正确：顶层结果不是对象。")
    return parsed


def http_get(url: str, headers: dict[str, str], timeout: int = 30, *, redact_errors: bool = False) -> dict[str, Any]:
    base_headers = {"User-Agent": UA}
    base_headers.update(headers)
    request = urllib.request.Request(url, headers=base_headers, method="GET")
    try:
        open_request = _new_api_open if redact_errors else urllib.request.urlopen
        with open_request(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if redact_errors:
            fail(f"New API 查询返回 HTTP {exc.code}；未重新提交任务。")
        detail = exc.read().decode("utf-8", errors="replace")
        fail(f"查询接口返回 HTTP {exc.code}：{detail}")
    except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError):
        fail("查询接口连接失败或超时。")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if redact_errors:
            fail("New API 查询未返回有效 JSON；未重新提交任务。")
        fail(f"查询接口返回的不是有效 JSON：{raw[:500]}")
    if not isinstance(parsed, dict):
        fail("查询接口返回格式不正确：顶层结果不是对象。")
    return parsed


def download_audio(url: str, output: Path, timeout: int = 180) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    # 若 output 未带扩展名，从 URL 推断（默认 mp3）
    if not output.suffix:
        suffix = Path(urllib.parse.urlparse(url).path).suffix or ".mp3"
        output = output.with_suffix(suffix)
    print(f"  下载音频：{url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            output.write_bytes(resp.read())
    except urllib.error.URLError as exc:
        fail(f"无法下载音频：{exc.reason}")
    except TimeoutError:
        fail("下载音频超时。")
    return output


def _find_audio_url(obj: Any) -> str | None:
    """在任意嵌套 dict/list 中找第一个像音频 URL 的字符串。

    兼容各家返回结构差异（audio_url / url / output.audio 等常见字段命名）。
    """
    audio_keys = ("audio_url", "audio", "audio_file", "url", "file_url",
                  "song_url", "stream_audio_url", "mp3", "output_url")
    if isinstance(obj, dict):
        # 优先命中明确的音频字段
        for key in audio_keys:
            val = obj.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
        for val in obj.values():
            found = _find_audio_url(val)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_audio_url(item)
            if found:
                return found
    elif isinstance(obj, str):
        low = obj.lower()
        if obj.startswith("http") and (
            low.endswith((".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"))
        ):
            return obj
    return None


# ── 显式 New API 插件协议；不会回退或二次提交 ──────────────────

def _new_api_endpoint(base_url: str, route: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        fail("New API base URL 必须是无凭据、query 或 fragment 的网关根地址，可包含反向代理路径前缀。")
    # urljoin('/route') 会丢失自定义前缀。此处只去末尾斜线，不猜测 /v1。
    return base_url.rstrip("/") + route


def _new_api_audio_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme in ("http", "https") and parsed.hostname and not (parsed.username or parsed.password):
            return value
    except ValueError:
        pass
    return None


def _generate_dashscope_new_api(args: argparse.Namespace) -> Path:
    if args.duration is not None:
        fail("New API Fun-Music 不支持 --duration；不会发送旧版 parameters.duration。")
    api_key = require_env("DASHSCOPE_API_KEY")
    endpoint = _new_api_endpoint(require_env("DASHSCOPE_BASE_URL"),
        "/easel/dashscope/api/v1/services/audio/music/generation")
    model = args.model or env_lookup("DASHSCOPE_MUSIC_MODEL") or DEFAULT_NEWAPI_DASHSCOPE_MODEL
    inputs: dict[str, Any] = {"prompt": args.prompt,
                              "is_instrumental": bool(args.instrumental) and not bool(args.lyrics)}
    if args.lyrics:
        inputs["lyrics"] = args.lyrics
    if args.timeout <= 0:
        fail("New API 同步音乐请求的 --timeout 必须为正数。")
    print(f"[dashscope/new-api] 同步生成（model={model}）...", file=sys.stderr)
    result = http_post(endpoint, {"Authorization": f"Bearer {api_key}"},
                       {"model": model, "input": inputs}, timeout=args.timeout, redact_errors=True)
    if result.get("code") or result.get("error") or result.get("success") is False:
        fail("New API Fun-Music 返回业务错误；未轮询或重新提交。")
    output = result.get("output")
    if (not isinstance(output, dict) or output.get("task_status") != "SUCCEEDED"
            or output.get("finish_reason") != "stop"):
        fail("New API Fun-Music 未返回可验证的同步完成态；未调用上游轮询接口或重新提交。")
    audio = output.get("audio")
    audio_url = _new_api_audio_url(audio.get("url") if isinstance(audio, dict) else None)
    if not audio_url:
        fail("New API Fun-Music 完成响应缺少 output.audio.url；未下载其他 URL。")
    return download_audio(audio_url, Path(args.output))


def _new_api_suno_envelope(result: dict[str, Any]) -> Any:
    if result.get("code") != "success" or result.get("error") or result.get("success") is False:
        fail("New API Suno 返回业务错误；未切换协议或重新提交任务。")
    return result.get("data")


def _new_api_suno_audio(data: Any) -> str | None:
    # Match sunoapi artifactData: direct songs or a nested task-data wrapper.
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict) and nested.get("task_id") and "data" in nested:
            data = nested["data"]
    songs = data if isinstance(data, list) else [data]
    for song in songs:
        if isinstance(song, dict):
            audio_url = _new_api_audio_url(song.get("audio_url"))
            if audio_url:
                return audio_url
    return None


def _poll_suno_new_api(base_url: str, headers: dict[str, str], task_id: str,
                       poll_interval: int, timeout: int) -> str:
    endpoint = _new_api_endpoint(base_url, "/suno/fetch/" + urllib.parse.quote(task_id, safe=""))
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fail("New API Suno 查询超时；原任务可能仍在运行，未重新提交任务。")
        result = http_get(endpoint, headers, timeout=min(30, remaining), redact_errors=True)
        task = _new_api_suno_envelope(result)
        if not isinstance(task, dict):
            fail("New API Suno 查询缺少任务对象；未重新提交任务。")
        status = task.get("status")
        if status == "SUCCESS":
            audio_url = _new_api_suno_audio(task.get("data"))
            if not audio_url:
                fail("New API Suno 完成任务缺少 audio_url；未下载封面或其他 URL。")
            return audio_url
        if status == "FAILURE":
            fail("New API Suno 任务失败；未重新提交任务。")
        if status not in ("NOT_START", "SUBMITTED", "QUEUED", "IN_PROGRESS"):
            fail("New API Suno 返回未知或不支持的任务状态；未下载或重新提交。")
        print(f"  [suno/new-api] 轮询中... 状态={status}", file=sys.stderr)
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))


def _generate_suno_new_api(args: argparse.Namespace) -> Path:
    if args.duration is not None:
        fail("New API Suno native 协议未声明 --duration；不会静默忽略或发送通用 duration 字段。")
    if args.timeout <= 0 or args.poll_interval <= 0:
        fail("New API Suno 的 --timeout 与 --poll-interval 必须为正数。")
    api_key = require_env("MUSIC_API_KEY")
    base_url = require_env("MUSIC_BASE_URL")
    endpoint = _new_api_endpoint(base_url, "/suno/submit/music")
    payload: dict[str, Any] = {"make_instrumental": bool(args.instrumental) and not bool(args.lyrics)}
    if args.lyrics:
        payload.update(prompt=args.lyrics, tags=args.prompt)
    else:
        payload["gpt_description_prompt"] = args.prompt
    # Native MUSIC routes fix the gateway billing model to suno_music. An
    # explicit --model/MUSIC_MODEL selects the upstream mv, not body.model.
    mv = args.model or os.environ.get("MUSIC_MODEL", "").strip()
    if mv:
        payload["mv"] = mv
    headers = {"Authorization": f"Bearer {api_key}"}
    print("[suno/new-api] 提交音乐任务...", file=sys.stderr)
    result = http_post(endpoint, headers, payload, timeout=60, redact_errors=True)
    task_id = _new_api_suno_envelope(result)
    if not isinstance(task_id, str) or not task_id.strip():
        fail("New API Suno 提交响应缺少字符串 data 任务 ID；未重新提交任务。")
    audio_url = _poll_suno_new_api(base_url, headers, task_id, args.poll_interval, args.timeout)
    return download_audio(audio_url, Path(args.output))


# ── provider: dashscope ────────────────────────────────────
# 依据阿里云 DashScope（百炼）公开的异步任务规范实现：
#   提交时带 header  X-DashScope-Async: enable  → 返回 output.task_id / task_status
#   轮询 GET {base}/tasks/{task_id} → task_status PENDING/RUNNING/SUCCEEDED/FAILED
# 音乐生成的具体 model 名 / 输入字段各版本略有差异，故 model 与 base_url 均可用 env 覆盖。
# 未用真实 key 实测。

def generate_dashscope(args: argparse.Namespace) -> Path:
    if resolve_api_mode() == "new-api":
        return _generate_dashscope_new_api(args)
    api_key = require_env("DASHSCOPE_API_KEY")
    base_url = (os.environ.get("DASHSCOPE_BASE_URL", "").strip()
                or DEFAULT_DASHSCOPE_BASE).rstrip("/")
    model = args.model or env_lookup("DASHSCOPE_MUSIC_MODEL") or DEFAULT_DASHSCOPE_MODEL

    endpoint = f"{base_url}/services/aigc/text2audio/generation"
    input_block: dict[str, Any] = {"prompt": args.prompt}
    if args.lyrics:
        input_block["lyrics"] = args.lyrics
    parameters: dict[str, Any] = {}
    if args.duration:
        parameters["duration"] = args.duration
    # 纯音乐（无人声）；DashScope 用布尔开关表达
    parameters["instrumental"] = bool(args.instrumental)

    payload = {"model": model, "input": input_block, "parameters": parameters}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
    }
    print(f"[dashscope] 提交任务到 {endpoint}（model={model}）...", file=sys.stderr)
    result = http_post(endpoint, headers, payload, timeout=60)

    output = result.get("output", {})
    if not isinstance(output, dict):
        fail(f"提交响应缺少 output 对象：{json.dumps(result)[:300]}")
    task_id = output.get("task_id")
    if not task_id:
        # 有些错误直接在顶层带 code/message
        code = result.get("code")
        message = result.get("message", json.dumps(result)[:300])
        fail(f"提交失败：{message}（code={code}）")

    print(f"[dashscope] 任务已提交：{task_id}", file=sys.stderr)
    task_url = f"{base_url}/tasks/{task_id}"
    poll_headers = {"Authorization": f"Bearer {api_key}"}
    task_output = _poll_dashscope(task_url, poll_headers, args.poll_interval, args.timeout)

    audio_url = _find_audio_url(task_output)
    if not audio_url:
        fail(f"任务完成但未找到音频 URL：{json.dumps(task_output)[:300]}")
    return download_audio(audio_url, Path(args.output))


def _poll_dashscope(task_url: str, headers: dict[str, str],
                    poll_interval: int, timeout: int) -> dict[str, Any]:
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            fail(f"任务超时（{timeout}s），可稍后用 task_id 手动查询。")
        result = http_get(task_url, headers)
        output = result.get("output", {})
        status = str(output.get("task_status", "")).upper()
        if status == "SUCCEEDED":
            return output
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            fail(f"任务失败：状态={status}，{json.dumps(output)[:300]}")
        print(f"  轮询中... 状态={status or '?'} 耗时={elapsed:.0f}s", file=sys.stderr)
        time.sleep(poll_interval)


# ── provider: suno-compatible ──────────────────────────────
# 依据 Suno 类第三方 API 的通用格式实现（各家网关多兼容此形态）：
#   POST {base}/generate  →  返回任务 id（id / task_id / data.id）
#   轮询 GET {base}/feed/{id} 或 {base}/tasks/{id} → status complete/failed + audio_url
# base_url / model 均由 env 提供。未用真实 key 实测。

def generate_suno(args: argparse.Namespace) -> Path:
    if resolve_api_mode() == "new-api":
        return _generate_suno_new_api(args)
    api_key = require_env("MUSIC_API_KEY")
    base_url = require_env("MUSIC_BASE_URL").rstrip("/")
    model = args.model or os.environ.get("MUSIC_MODEL", "").strip() or DEFAULT_SUNO_MODEL

    endpoint = f"{base_url}/generate"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": args.prompt,
        "make_instrumental": bool(args.instrumental),
    }
    if args.lyrics:
        # 有歌词 → 关闭纯音乐，走自定义歌词模式
        payload["lyrics"] = args.lyrics
        payload["make_instrumental"] = False
    if args.duration:
        payload["duration"] = args.duration
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"[suno] 提交任务到 {endpoint}（model={model}）...", file=sys.stderr)
    result = http_post(endpoint, headers, payload, timeout=60)
    task_id = _extract_suno_id(result)
    if not task_id:
        fail(f"提交响应缺少任务 id：{json.dumps(result)[:300]}")

    print(f"[suno] 任务已提交：{task_id}", file=sys.stderr)
    audio_url = _poll_suno(base_url, headers, task_id, args.poll_interval, args.timeout)
    return download_audio(audio_url, Path(args.output))


def _extract_suno_id(result: dict[str, Any]) -> str | None:
    for key in ("task_id", "id", "request_id"):
        val = result.get(key)
        if isinstance(val, (str, int)):
            return str(val)
    data = result.get("data")
    if isinstance(data, dict):
        return _extract_suno_id(data)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _extract_suno_id(data[0])
    return None


def _poll_suno(base_url: str, headers: dict[str, str], task_id: str,
               poll_interval: int, timeout: int) -> str:
    task_url = f"{base_url}/feed/{task_id}"
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            fail(f"任务超时（{timeout}s），可稍后用任务 id 手动查询。")
        result = http_get(task_url, headers)
        status = _extract_suno_status(result)
        if status in ("complete", "completed", "succeeded", "success"):
            audio_url = _find_audio_url(result)
            if not audio_url:
                fail(f"任务完成但未找到音频 URL：{json.dumps(result)[:300]}")
            return audio_url
        if status in ("failed", "error"):
            fail(f"任务失败：{json.dumps(result)[:300]}")
        print(f"  轮询中... 状态={status or '?'} 耗时={elapsed:.0f}s", file=sys.stderr)
        time.sleep(poll_interval)


def _extract_suno_status(result: dict[str, Any]) -> str:
    for key in ("status", "task_status", "state"):
        val = result.get(key)
        if isinstance(val, str):
            return val.lower()
    data = result.get("data")
    if isinstance(data, dict):
        return _extract_suno_status(data)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _extract_suno_status(data[0])
    return ""


# ── 子命令 ──────────────────────────────────────────────

# 每个 provider 需要哪些 env（用于 check + 分发）
PROVIDER_REQUIRED_ENV = provider_required_env("music")

PROVIDER_GENERATORS = {
    "dashscope": generate_dashscope,
    "suno-compatible": generate_suno,
}


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def cmd_check(args: argparse.Namespace) -> int:
    """离线校验：不发任何请求，只检查所需 env 是否配好。"""
    provider = resolve_provider(args.provider)
    mode = resolve_api_mode()
    print(f"provider = {provider}")
    required = PROVIDER_REQUIRED_ENV[provider]
    if mode == "new-api":
        print("api_mode = new-api")
        required = [(name, is_required or name == "DASHSCOPE_BASE_URL") for name, is_required in required]
        if provider == "dashscope" and not any(name == "DASHSCOPE_BASE_URL" for name, _ in required):
            required = [*required, ("DASHSCOPE_BASE_URL", True)]
    all_required_ok = True
    for name, is_required in required:
        value = env_lookup(name)
        aliases = ENV_ALIASES.get(name, ())
        alias_hint = f"（别名：{ '、'.join(aliases) }）" if aliases else ""
        tag = "必填" if is_required else "可选"
        if value:
            print(f"  [OK]   {name} [{tag}] = {_mask(value)}")
        elif is_required:
            all_required_ok = False
            print(f"  [缺失] {name} [{tag}] 未设置{alias_hint}")
        else:
            default = {
                "DASHSCOPE_MUSIC_MODEL": DEFAULT_NEWAPI_DASHSCOPE_MODEL if mode == "new-api" else DEFAULT_DASHSCOPE_MODEL,
                "DASHSCOPE_BASE_URL": DEFAULT_DASHSCOPE_BASE,
                "MUSIC_MODEL": "由 sunoapi 插件默认 mv=chirp-v3-0" if mode == "new-api" else DEFAULT_SUNO_MODEL,
            }.get(name, "")
            hint = f"（将用默认值：{default}）" if default else ""
            print(f"  [默认] {name} [{tag}] 未设置{hint}")
    if all_required_ok:
        print("结果：配置完整，可以执行 generate。")
        return 0
    print("结果：缺少必填配置，请补齐后再执行 generate。", file=sys.stderr)
    return 1


def cmd_generate(args: argparse.Namespace) -> int:
    if not args.prompt or not args.prompt.strip():
        fail("--prompt 不能为空，请描述音乐的风格 / 情绪 / 乐器。")
    provider = resolve_provider(args.provider)
    generator = PROVIDER_GENERATORS[provider]
    output = generator(args)
    print("生成完成：")
    print(output)
    return 0


# ── CLI ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_music.py",
        description="AI 音乐 / BGM 生成的可插拔客户端（dashscope / suno-compatible），纯标准库。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", metavar="<子命令>")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--provider", choices=PROVIDERS,
                        help="选择 provider；不指定时读 env MUSIC_PROVIDER。")
    common.add_argument("--env-file", help="指定 .env；不指定时从当前目录向上查找。")

    pc = sub.add_parser("check", parents=[common],
                        help="离线校验所需 env 是否配好（不发请求）")
    pc.set_defaults(func=cmd_check)

    pg = sub.add_parser("generate", parents=[common], help="生成音乐 / BGM")
    pg.add_argument("--prompt", required=True, help="风格 / 情绪 / 乐器描述")
    pg.add_argument("--lyrics", help="可选歌词（有歌词时不再是纯音乐）")
    pg.add_argument("--duration", type=int, help="时长（秒），部分 provider 支持")
    pg.add_argument("--instrumental", action="store_true",
                    help="纯音乐（无人声 BGM）")
    pg.add_argument("--model", help="覆盖模型名（默认读 env / 内置默认）")
    pg.add_argument("-o", "--output", required=True,
                    help="输出音频路径（无扩展名时按返回类型补 .mp3）")
    pg.add_argument("--poll-interval", type=int, default=5, help="轮询间隔秒，默认 5")
    pg.add_argument("--timeout", type=int, default=300, help="轮询超时秒，默认 300")
    pg.set_defaults(func=cmd_generate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    if getattr(args, "output", None):
        args.output = str(validate_output_path(args.output))
    env_file = Path(args.env_file) if getattr(args, "env_file", None) else find_default_env_file()
    load_env_file(env_file)
    from media_gateway import resolve_media_environment
    try:
        os.environ.update(resolve_media_environment("music", os.environ))
    except ValueError as error:
        fail(str(error))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
