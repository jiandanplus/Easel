"""Pure bounded text Responses validation. No credentials, I/O or accounting."""

import copy
import hashlib
import json

from .errors import PlatformError

MAX_REQUEST = 1024 * 1024
MAX_EVENT = 256 * 1024
MAX_JSON = 2 * 1024 * 1024
MAX_RESULT = 8 * 1024 * 1024
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


def model_reasoning(value):
    """Normalize an explicitly published capability; absence is handled by caller."""
    if (not isinstance(value, dict) or set(value) != {"efforts"}
            or not isinstance(value["efforts"], list)
            or len(value["efforts"]) > len(REASONING_EFFORTS)
            or any(not isinstance(level, str) or level not in REASONING_EFFORTS
                   for level in value["efforts"])
            or len(set(value["efforts"])) != len(value["efforts"])):
        raise PlatformError(422, "invalid_model_reasoning")
    return {"efforts": list(value["efforts"])}


def validate_reasoning_effort(value, capability):
    """Reject explicit unsupported effort before reservation or provider I/O.

    Older definitions omit the capability. Summary-only requests retain their
    previous wire shape, while explicit effort requires a verified declaration.
    """
    reasoning = value.get("reasoning", {})
    if "effort" not in reasoning:
        return
    effort = reasoning["effort"]
    if (not isinstance(effort, str) or effort not in REASONING_EFFORTS
            or capability is None
            or effort not in model_reasoning(capability)["efforts"]):
        raise PlatformError(422, "unsupported_reasoning_effort")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def parse_json(raw, *, upstream=False):
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PlatformError(
            502 if upstream else 400,
            "invalid_upstream_response" if upstream else "invalid_request",
        ) from None


def encode(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PlatformError(400, "invalid_request") from None


def validate_request(value, model, *, default_output=4096):
    def reject():
        raise PlatformError(400, "unsupported_request_shape")

    if not isinstance(value, dict):
        reject()
    allowed = {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "text",
        "include",
        "stream",
        "store",
        "metadata",
        "client_metadata",
        "prompt_cache_key",
        "max_output_tokens",
        "temperature",
        "top_p",
    }
    if set(value) - allowed:
        reject()
    # Codex transport telemetry is not provider input or an authorization source.
    # Bound known native fields, hash them for retry conflicts, then drop below.
    if "client_metadata" in value:
        metadata = value["client_metadata"]
        fields = {"session_id", "turn_id", "thread_id", "x-codex-installation-id",
                  "x-codex-turn-metadata", "x-codex-window-id"}
        if (not isinstance(metadata, dict) or set(metadata) - fields
                or any(not isinstance(item, str) or len(item.encode("utf-8", errors="surrogatepass")) > 4096
                       for item in metadata.values())):
            reject()
    if value.get("model") != model:
        raise PlatformError(403, "model_not_allowed")
    for name in ("stream", "parallel_tool_calls", "store"):
        if name in value and type(value[name]) is not bool:
            reject()
    if value.get("store", False):
        reject()
    if "instructions" in value and not isinstance(value["instructions"], str):
        reject()
    tools = value.get("tools", [])
    if not isinstance(tools, list) or len(tools) > 256:
        reject()

    def tool(item, nested=False):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"]
        ):
            reject()
        kind = item.get("type")
        if isinstance(kind, str) and kind in {"function", "custom"}:
            if "tools" in item:
                reject()
        elif kind == "namespace" and not nested:
            children = item.get("tools")
            if not isinstance(children, list) or not children or len(children) > 256:
                reject()
            for child in children:
                tool(child, True)
        else:
            reject()

    for item in tools:
        tool(item)
    choice = value.get("tool_choice", "auto")
    if isinstance(choice, dict):
        if (
            not isinstance(choice.get("type"), str)
            or choice["type"] not in {"function", "custom"}
            or not isinstance(choice.get("name"), str)
        ):
            reject()
    elif not isinstance(choice, str) or choice not in {"auto", "none", "required"}:
        reject()
    include = value.get("include", [])
    if not isinstance(include, list) or any(
        item != "reasoning.encrypted_content" for item in include
    ):
        reject()
    text = value.get("text", {})
    if not isinstance(text, dict) or set(text) - {"format", "verbosity"}:
        reject()
    if "format" in text and (
        not isinstance(text["format"], dict)
        or not isinstance(text["format"].get("type"), str)
        or text["format"]["type"] not in {"text", "json_object", "json_schema"}
    ):
        reject()
    reasoning = value.get("reasoning", {})
    if not isinstance(reasoning, dict) or set(reasoning) - {"effort", "summary"}:
        reject()
    source = value.get("input", "")
    if not isinstance(source, (str, list)):
        reject()
    kinds = {
        "message",
        "input_text",
        "output_text",
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "reasoning",
        "summary_text",
        "reasoning_text",
    }
    pending = [(source, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            reject()
        if isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            if any(
                key in item
                for key in (
                    "file_id",
                    "image_url",
                    "file_url",
                    "audio",
                    "video",
                    "image",
                    "computer_call",
                )
            ):
                reject()
            if "type" in item and (
                not isinstance(item["type"], str) or item["type"] not in kinds
            ):
                reject()
            pending.extend((child, depth + 1) for child in item.values())
        elif item is not None and not isinstance(item, str):
            reject()
    maximum = value.get("max_output_tokens", default_output)
    if type(maximum) is not int or not 1 <= maximum <= 4096:
        reject()
    result = copy.deepcopy(value)
    result["max_output_tokens"] = maximum
    result["store"] = False
    canonical = encode(result)
    if len(canonical) > MAX_REQUEST:
        raise PlatformError(413, "request_too_large")
    # Keep metadata in the identity hash and request-size limit even though no
    # client identity/telemetry is sent to the provider.
    result.pop("client_metadata", None)
    return result, hashlib.sha256(canonical).hexdigest()


def terminal_usage(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("status"), str)
        or value["status"] not in {"completed", "incomplete"}
        or value.get("error") is not None
        or not isinstance(value.get("id"), str)
        or not 1 <= len(value["id"]) <= 256
    ):
        raise PlatformError(502, "invalid_upstream_usage")
    usage = value.get("usage")
    if not isinstance(usage, dict):
        raise PlatformError(502, "invalid_upstream_usage")
    counts = usage.get("input_tokens"), usage.get("output_tokens")
    if any(type(count) is not int or not 0 <= count <= 2**63 - 1 for count in counts):
        raise PlatformError(502, "invalid_upstream_usage")
    return *counts, value["status"], value["id"]


class SSEParser:
    """Incremental bytes preserve split UTF8 and CRLF, with per-event bounds."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        result = []
        for start in range(0, len(chunk), 8192):
            self.buffer.extend(chunk[start : start + 8192])
            while True:
                end, width = self.buffer.find(b"\n\n"), 2
                cr = self.buffer.find(b"\r\n\r\n")
                if cr >= 0 and (end < 0 or cr < end):
                    end, width = cr, 4
                if end < 0:
                    break
                if end > MAX_EVENT:
                    raise PlatformError(502, "upstream_event_too_large")
                event = bytes(self.buffer[: end + width])
                del self.buffer[: end + width]
                try:
                    lines = event.decode("utf-8").splitlines()
                except UnicodeError:
                    raise PlatformError(502, "invalid_upstream_response") from None
                names = [
                    line[6:].strip() for line in lines if line.startswith("event:")
                ]
                data = "\n".join(
                    line[5:].removeprefix(" ")
                    for line in lines
                    if line.startswith("data:")
                )
                name = names[-1] if names else None
                if not data:
                    if name and name != "message":
                        raise PlatformError(502, "invalid_upstream_response")
                    result.append((event, None))
                    continue
                value = parse_json(data, upstream=True)
                if (
                    not isinstance(value.get("type"), str)
                    or name
                    and name not in {"message", value["type"]}
                ):
                    raise PlatformError(502, "invalid_upstream_response")
                result.append((event, value))
            if len(self.buffer) > MAX_EVENT:
                raise PlatformError(502, "upstream_event_too_large")
        return result
