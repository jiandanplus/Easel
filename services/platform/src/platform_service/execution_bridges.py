"""Let suppliers without the Responses API serve the one format the clients speak.

Requests are translated down; replies are rebuilt as Responses events, so the runtime's
usage checks and settlement treat every supplier alike. A reply without token usage
never reaches a terminal event, so it can never settle.
"""
import json
from uuid import uuid4

from .errors import PlatformError
from .execution_protocol import MAX_EVENT, parse_json

ANTHROPIC_VERSION = "2023-06-01"


def _reject():
    return PlatformError(400, "unsupported_request_shape")


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise _reject()
    parts = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") not in ("input_text", "output_text", "text") or not isinstance(part.get("text"), str):
            raise _reject()
        parts.append(part["text"])
    return "".join(parts)


def _output_text(value):
    return value if isinstance(value, str) else _text(value)


def _functions(value):
    tools = []
    for tool in value.get("tools", []):
        if tool.get("type") != "function":
            raise _reject()
        tools.append(tool)
    return tools


def _items(value):
    source = value.get("input", "")
    if isinstance(source, str):
        return [{"type": "message", "role": "user", "content": source}]
    return source


def _max_tokens(value):
    return value["max_output_tokens"]


def chat_request(value):
    messages = []
    if value.get("instructions"):
        messages.append({"role": "system", "content": value["instructions"]})
    for item in _items(value):
        kind = item.get("type", "message") if isinstance(item, dict) else None
        if kind == "message":
            role = "system" if item.get("role") == "developer" else item.get("role")
            if role not in ("system", "user", "assistant"):
                raise _reject()
            messages.append({"role": role, "content": _text(item.get("content"))})
        elif kind == "function_call":
            call = {"id": item["call_id"], "type": "function", "function": {"name": item["name"], "arguments": item["arguments"]}}
            if messages and messages[-1]["role"] == "assistant":
                messages[-1].setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": _output_text(item.get("output"))})
        elif kind != "reasoning":
            raise _reject()
    result = {"model": value["model"], "messages": messages, "max_tokens": _max_tokens(value), "stream": bool(value.get("stream"))}
    if result["stream"]:
        result["stream_options"] = {"include_usage": True}
    for name in ("temperature", "top_p", "parallel_tool_calls"):
        if name in value and (name != "parallel_tool_calls" or value.get("tools")):
            result[name] = value[name]
    tools = _functions(value)
    if tools:
        result["tools"] = [{"type": "function", "function": {key: tool[key] for key in ("name", "description", "parameters", "strict") if key in tool}} for tool in tools]
        choice = value.get("tool_choice", "auto")
        result["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}} if isinstance(choice, dict) else choice
    fmt = value.get("text", {}).get("format")
    if fmt and fmt["type"] == "json_object":
        result["response_format"] = {"type": "json_object"}
    elif fmt and fmt["type"] == "json_schema":
        result["response_format"] = {"type": "json_schema", "json_schema": {key: fmt[key] for key in ("name", "schema", "strict", "description") if key in fmt}}
    return result


def anthropic_request(value):
    system, messages = [], []

    def add(role, block):
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].append(block)
        else:
            messages.append({"role": role, "content": [block]})

    if value.get("instructions"):
        system.append(value["instructions"])
    for item in _items(value):
        kind = item.get("type", "message") if isinstance(item, dict) else None
        if kind == "message":
            role = item.get("role")
            if role in ("system", "developer"):
                system.append(_text(item.get("content")))
            elif role in ("user", "assistant"):
                text = _text(item.get("content"))
                if text:
                    add(role, {"type": "text", "text": text})
            else:
                raise _reject()
        elif kind == "function_call":
            try:
                arguments = json.loads(item["arguments"] or "{}")
            except ValueError:
                raise _reject() from None
            add("assistant", {"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": arguments})
        elif kind == "function_call_output":
            add("user", {"type": "tool_result", "tool_use_id": item["call_id"], "content": _output_text(item.get("output"))})
        elif kind != "reasoning":
            raise _reject()
    result = {"model": value["model"], "messages": messages, "max_tokens": _max_tokens(value), "stream": bool(value.get("stream"))}
    if system:
        result["system"] = "\n\n".join(system)
    if "temperature" in value:
        result["temperature"] = value["temperature"]
    elif "top_p" in value:
        result["top_p"] = value["top_p"]
    tools = _functions(value)
    if tools:
        result["tools"] = [{"name": tool["name"], "description": tool.get("description", ""), "input_schema": tool.get("parameters") or {"type": "object", "properties": {}}} for tool in tools]
        choice = value.get("tool_choice", "auto")
        result["tool_choice"] = {"type": "tool", "name": choice["name"]} if isinstance(choice, dict) else {"type": {"auto": "auto", "required": "any", "none": "none"}[choice]}
    if value.get("text", {}).get("format", {}).get("type") in ("json_object", "json_schema"):
        raise _reject()
    return result


def _sse(kind, **fields):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode()


class ResponseBuilder:
    """Assemble one Responses object while emitting its streaming events."""

    def __init__(self, model):
        self.id, self.model, self.output, self.open = "resp_" + uuid4().hex, model, [], None

    def _object(self, status, usage=None):
        value = {"id": self.id, "object": "response", "model": self.model, "status": status, "error": None, "output": self.output}
        if usage is not None:
            value["usage"] = {**usage, "total_tokens": usage["input_tokens"] + usage["output_tokens"]}
        if status == "incomplete":
            value["incomplete_details"] = {"reason": "max_output_tokens"}
        return value

    def start(self):
        return [_sse("response.created", response=self._object("in_progress"))]

    def close(self):
        if self.open is None:
            return []
        item, self.open = self.open, None
        index = len(self.output)
        self.output.append(item)
        if item["type"] == "message":
            part = item["content"][0]
            return [_sse("response.output_text.done", item_id=item["id"], output_index=index, content_index=0, text=part["text"]),
                    _sse("response.content_part.done", item_id=item["id"], output_index=index, content_index=0, part=part),
                    _sse("response.output_item.done", output_index=index, item=item)]
        return [_sse("response.function_call_arguments.done", item_id=item["id"], output_index=index, arguments=item["arguments"]),
                _sse("response.output_item.done", output_index=index, item=item)]

    def text(self, delta):
        events = []
        if self.open is None or self.open["type"] != "message":
            events += self.close()
            self.open = {"id": "msg_" + uuid4().hex, "type": "message", "role": "assistant", "status": "completed",
                         "content": [{"type": "output_text", "text": "", "annotations": []}]}
            index = len(self.output)
            events.append(_sse("response.output_item.added", output_index=index, item={**self.open, "status": "in_progress", "content": []}))
            events.append(_sse("response.content_part.added", item_id=self.open["id"], output_index=index, content_index=0, part={"type": "output_text", "text": "", "annotations": []}))
        self.open["content"][0]["text"] += delta
        events.append(_sse("response.output_text.delta", item_id=self.open["id"], output_index=len(self.output), content_index=0, delta=delta))
        return events

    def tool_start(self, call_id, name):
        events = self.close()
        self.open = {"id": "fc_" + uuid4().hex, "type": "function_call", "status": "completed", "call_id": call_id, "name": name, "arguments": ""}
        events.append(_sse("response.output_item.added", output_index=len(self.output), item={**self.open, "status": "in_progress"}))
        return events

    def tool_arguments(self, delta):
        if self.open is None or self.open["type"] != "function_call":
            raise PlatformError(502, "invalid_upstream_response")
        self.open["arguments"] += delta
        return [_sse("response.function_call_arguments.delta", item_id=self.open["id"], output_index=len(self.output), delta=delta)]

    def finish(self, complete, usage):
        events = self.close()
        status = "completed" if complete else "incomplete"
        return events + [_sse("response." + status, response=self._object(status, usage))]


async def _server_events(chunks):
    """(event name, data text) pairs from a plain SSE byte stream."""
    buffer = bytearray()
    async for chunk in chunks:
        buffer.extend(chunk.replace(b"\r\n", b"\n"))
        while True:
            end = buffer.find(b"\n\n")
            if end < 0:
                break
            block = bytes(buffer[:end])
            del buffer[:end + 2]
            lines = block.decode("utf-8", "strict").split("\n")
            data = "\n".join(line[5:].removeprefix(" ") for line in lines if line.startswith("data:"))
            if data:
                yield data
        if len(buffer) > MAX_EVENT:
            raise PlatformError(502, "upstream_event_too_large")


def _usage(value, input_name, output_name):
    if not isinstance(value, dict):
        raise PlatformError(502, "invalid_upstream_usage")
    counts = value.get(input_name), value.get(output_name)
    if any(type(count) is not int or count < 0 for count in counts):
        raise PlatformError(502, "invalid_upstream_usage")
    return {"input_tokens": counts[0], "output_tokens": counts[1]}


class ChatBridge:
    @staticmethod
    def request(value):
        return chat_request(value)

    @staticmethod
    def headers(secret):
        return {"Authorization": "Bearer " + secret}

    @staticmethod
    async def stream(chunks, model):
        builder, usage, reason, tools = ResponseBuilder(model), None, None, {}
        for event in builder.start():
            yield event
        async for data in _server_events(chunks):
            if data == "[DONE]":
                break
            value = parse_json(data, upstream=True)
            if value.get("error"):
                raise PlatformError(502, "upstream_error")
            if value.get("usage"):
                usage = _usage(value["usage"], "prompt_tokens", "completion_tokens")
            for choice in value.get("choices") or ():
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    for event in builder.text(delta["content"]):
                        yield event
                for call in delta.get("tool_calls") or ():
                    key, function = call.get("index", 0), call.get("function") or {}
                    if key not in tools:
                        tools[key] = True
                        for event in builder.tool_start(call["id"], function["name"]):
                            yield event
                    if function.get("arguments"):
                        for event in builder.tool_arguments(function["arguments"]):
                            yield event
                reason = choice.get("finish_reason") or reason
        if usage is None or reason is None:
            raise PlatformError(502, "invalid_upstream_usage")
        for event in builder.finish(reason != "length", usage):
            yield event

    @staticmethod
    def response(value, model):
        builder = ResponseBuilder(model)
        choice = (value.get("choices") or [None])[0]
        if not isinstance(choice, dict):
            raise PlatformError(502, "invalid_upstream_response")
        message = choice.get("message") or {}
        if message.get("content"):
            builder.text(message["content"])
        for call in message.get("tool_calls") or ():
            builder.tool_start(call["id"], call["function"]["name"])
            builder.tool_arguments(call["function"]["arguments"])
        builder.close()
        return builder._object("incomplete" if choice.get("finish_reason") == "length" else "completed",
                               _usage(value.get("usage"), "prompt_tokens", "completion_tokens"))


class AnthropicBridge:
    @staticmethod
    def request(value):
        return anthropic_request(value)

    @staticmethod
    def headers(secret):
        return {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION}

    @staticmethod
    def _input_tokens(usage):
        if not isinstance(usage, dict) or type(usage.get("input_tokens")) is not int:
            raise PlatformError(502, "invalid_upstream_usage")
        # Cached tokens are reported apart from input_tokens; they were still sent.
        return usage["input_tokens"] + sum(usage.get(key) or 0 for key in ("cache_read_input_tokens", "cache_creation_input_tokens"))

    @classmethod
    async def stream(cls, chunks, model):
        builder, input_tokens, output_tokens, reason = ResponseBuilder(model), None, None, None
        for event in builder.start():
            yield event
        async for data in _server_events(chunks):
            value = parse_json(data, upstream=True)
            kind = value.get("type")
            if kind == "error":
                raise PlatformError(502, "upstream_error")
            if kind == "message_start":
                input_tokens = cls._input_tokens((value.get("message") or {}).get("usage"))
            elif kind == "content_block_start":
                block = value.get("content_block") or {}
                if block.get("type") == "tool_use":
                    for event in builder.tool_start(block["id"], block["name"]):
                        yield event
                elif block.get("type") == "text" and block.get("text"):
                    for event in builder.text(block["text"]):
                        yield event
            elif kind == "content_block_delta":
                delta = value.get("delta") or {}
                if delta.get("type") == "text_delta":
                    for event in builder.text(delta["text"]):
                        yield event
                elif delta.get("type") == "input_json_delta" and delta.get("partial_json"):
                    for event in builder.tool_arguments(delta["partial_json"]):
                        yield event
            elif kind == "message_delta":
                reason = (value.get("delta") or {}).get("stop_reason") or reason
                count = (value.get("usage") or {}).get("output_tokens")
                if type(count) is int:
                    output_tokens = count
            elif kind == "message_stop":
                break
        if input_tokens is None or output_tokens is None or reason is None:
            raise PlatformError(502, "invalid_upstream_usage")
        for event in builder.finish(reason != "max_tokens", {"input_tokens": input_tokens, "output_tokens": output_tokens}):
            yield event

    @classmethod
    def response(cls, value, model):
        builder = ResponseBuilder(model)
        for block in value.get("content") or ():
            if block.get("type") == "text":
                builder.text(block["text"])
            elif block.get("type") == "tool_use":
                builder.tool_start(block["id"], block["name"])
                builder.tool_arguments(json.dumps(block.get("input") or {}, ensure_ascii=False, separators=(",", ":")))
        builder.close()
        usage = value.get("usage")
        if not isinstance(usage, dict) or type(usage.get("output_tokens")) is not int:
            raise PlatformError(502, "invalid_upstream_usage")
        return builder._object("incomplete" if value.get("stop_reason") == "max_tokens" else "completed",
                               {"input_tokens": cls._input_tokens(usage), "output_tokens": usage["output_tokens"]})


BRIDGES = {"chat": ChatBridge, "anthropic": AnthropicBridge}
