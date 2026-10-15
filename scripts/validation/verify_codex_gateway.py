"""Exercise a real Codex app-server against a local, nonbillable Responses stub."""

import argparse
import asyncio
import gc
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from easel.harnesses.codex import CodexAdapter
import easel.harnesses.codex as codex_module

ORIGINAL_CONNECTION = codex_module.connection
from easel.harnesses.base import RunRequest

ROOT = None
MODE = "tool"
REQUESTED = threading.Event()
stats = {
    "requests": 0,
    "bearer_correct": True,
    "paths": [],
    "tool_names": [],
    "tool_result_seen": False,
    "events": {},
    "marker_verified": False,
}
TOKEN = "easel-loopback-dummy-not-a-secret"
MARKER = "EASEL_FAKE_GATEWAY_MARKER"
# Optional owned tool command for packaged-runtime validation.
TOOL_COMMAND = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        stats["requests"] += 1
        stats["bearer_correct"] &= (
            self.headers.get("Authorization") == "Bearer " + TOKEN
        )
        stats["paths"].append(self.path)
        REQUESTED.set()
        if MODE == "error":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "error": {
                            "message": "Easel validation dummy unauthorized",
                            "type": "invalid_request_error",
                            "code": "invalid_api_key",
                        }
                    }
                ).encode()
            )
            return
        if MODE == "cancel":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(30):
                    self.wfile.write(b": waiting\n\n")
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        tools = data.get("tools", [])
        names = [t.get("name") or (t.get("function") or {}).get("name") for t in tools]
        stats["tool_names"] = names
        stats["stream_requested"] = data.get("stream")
        inputs = data.get("input", [])
        outputs = [
            i
            for i in inputs
            if isinstance(i, dict) and i.get("type") == "function_call_output"
        ]
        stats["tool_result_seen"] |= bool(outputs)
        stats["tool_output_marker"] = any(
            MARKER in str(i.get("output", "")) for i in outputs
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(kind, **fields):
            msg = {"type": kind, **fields}
            self.wfile.write(
                ("event: " + kind + "\ndata: " + json.dumps(msg) + "\n\n").encode()
            )
            self.wfile.flush()

        rid = "resp_loopback_" + str(stats["requests"])
        event(
            "response.created",
            response={
                "id": rid,
                "object": "response",
                "status": "in_progress",
                "output": [],
            },
        )
        if not stats["tool_result_seen"] and stats["requests"] == 1:
            name = next(
                (n for n in ("exec_command", "shell_command", "shell") if n in names),
                None,
            )
            stats["selected_tool"] = name
            command = TOOL_COMMAND or ("printf '" + MARKER + "\\n' > marker.txt; cat marker.txt")
            args = (
                {"cmd": command, "max_output_tokens": 100}
                if name == "exec_command"
                else (
                    {"command": command}
                    if name == "shell_command"
                    else {"command": ["/bin/sh", "-c", command]}
                )
            )
            item = {
                "id": "fc_marker",
                "type": "function_call",
                "call_id": "call_marker",
                "name": name or "exec_command",
                "arguments": json.dumps(args),
                "status": "completed",
            }
            event(
                "response.output_item.added",
                output_index=0,
                item={**item, "arguments": "", "status": "in_progress"},
            )
            event(
                "response.function_call_arguments.delta",
                item_id="fc_marker",
                output_index=0,
                delta=item["arguments"],
            )
            event(
                "response.function_call_arguments.done",
                item_id="fc_marker",
                output_index=0,
                arguments=item["arguments"],
            )
            event("response.output_item.done", output_index=0, item=item)
        else:
            item = {
                "id": "msg_marker",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": MARKER, "annotations": []}],
            }
            event(
                "response.output_item.added",
                output_index=0,
                item={**item, "status": "in_progress", "content": []},
            )
            event(
                "response.content_part.added",
                item_id="msg_marker",
                output_index=0,
                content_index=0,
                part={"type": "output_text", "text": "", "annotations": []},
            )
            event(
                "response.output_text.delta",
                item_id="msg_marker",
                output_index=0,
                content_index=0,
                delta=MARKER,
            )
            event(
                "response.output_text.done",
                item_id="msg_marker",
                output_index=0,
                content_index=0,
                text=MARKER,
            )
            event(
                "response.content_part.done",
                item_id="msg_marker",
                output_index=0,
                content_index=0,
                part=item["content"][0],
            )
            event("response.output_item.done", output_index=0, item=item)
        event(
            "response.completed",
            response={
                "id": rid,
                "object": "response",
                "status": "completed",
                "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )


async def scenario(mode, executable):
    global ROOT, MODE, stats
    MODE = mode
    REQUESTED.clear()
    stats = {
        "scenario": mode,
        "requests": 0,
        "bearer_correct": True,
        "paths": [],
        "tool_names": [],
        "tool_result_seen": False,
        "events": {},
        "marker_verified": False,
    }
    temp = tempfile.TemporaryDirectory(prefix="easel-codex-validation-")
    ROOT = Path(temp.name)

    @asynccontextmanager
    async def observed(request, command, notify, respond):
        rpc = None
        try:
            async with ORIGINAL_CONNECTION(request, command, notify, respond) as rpc:
                original_call = rpc.call

                async def call(method, params, **kwargs):
                    result = await original_call(method, params, **kwargs)
                    if method == "turn/interrupt":
                        stats["interrupt_acknowledged"] = True
                    return result

                rpc.call = call
                yield rpc
        finally:
            stats["owned_process_exited"] = (
                rpc is not None and rpc.process.returncode is not None
            )

    codex_module.connection = observed
    work = Path(tempfile.mkdtemp(prefix="fake-work-", dir=ROOT))
    home = Path(tempfile.mkdtemp(prefix="fake-home-", dir=ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(x in k.upper() for x in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
    }
    env.update(
        CODEX_HOME=str(home),
        EASEL_TEST_TOKEN=TOKEN,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    command = (
        executable,
        "app-server",
        "-c",
        'model_provider="easel-test"',
        "-c",
        'model="easel-fake"',
        "-c",
        'model_providers.easel-test={name="Easel Test",base_url="http://127.0.0.1:'
        + str(server.server_port)
        + '/v1",env_key="EASEL_TEST_TOKEN",wire_api="responses",requires_openai_auth=false}',
    )

    def emit(kind, text):
        stats["events"][kind] = stats["events"].get(kind, 0) + 1
        if kind == "token" and MARKER in text:
            stats["token_marker"] = True

    async def reject(q):
        stats["permission_requested"] = True
        return {"permission": ["拒绝"]}

    req = RunRequest(
        prompt="Run a local shell tool to create marker.txt and read it, then reply with marker.",
        cwd=work,
        env=env,
        command=command,
        model="easel-fake",
    )
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    stats["unhandled_asyncio_errors"] = 0

    def unhandled_error(_loop, _context):
        # Count errors without retaining tracebacks or printing request payloads.
        stats["unhandled_asyncio_errors"] += 1

    loop.set_exception_handler(unhandled_error)
    task = None
    started = time.monotonic()
    try:
        task = asyncio.create_task(
            CodexAdapter().run(req, emit, reject, lambda n: None)
        )
        if mode == "cancel":
            for _ in range(150):
                if REQUESTED.is_set():
                    break
                await asyncio.sleep(0.1)
            if not REQUESTED.is_set():
                raise AssertionError("No model request arrived")
            task.cancel()
        await asyncio.wait_for(task, 40)
        stats["status"] = "completed"
    except asyncio.CancelledError:
        stats["status"] = "cancelled"
    except Exception as e:
        stats.update(status="failed", error_type=type(e).__name__)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        codex_module.connection = ORIGINAL_CONNECTION
        task = None
        gc.collect()
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)
    marker = work / "marker.txt"
    stats["marker_verified"] = marker.is_file() and marker.read_text().strip() == MARKER
    stats["elapsed_seconds"] = round(time.monotonic() - started, 2)
    result = dict(stats)
    temp.cleanup()
    if not result["bearer_correct"] or not result["requests"]:
        raise AssertionError("Dummy bearer not observed")
    if any(path != "/v1/responses" for path in result["paths"]):
        raise AssertionError("Unexpected model endpoint")
    if not result.get("owned_process_exited"):
        raise AssertionError("Owned app-server did not exit")
    if result["unhandled_asyncio_errors"]:
        raise AssertionError("Unhandled asynchronous completion error")
    if mode == "tool" and not (
        result["status"] == "completed"
        and result["marker_verified"]
        and result["tool_result_seen"]
        and result.get("tool_output_marker")
        and result.get("token_marker")
        and result.get("stream_requested")
        and result["events"].get("activity")
        and result["events"].get("token")
    ):
        raise AssertionError("Responses tool/stream cycle failed")
    if mode == "error" and not (
        result["status"] == "failed"
        and result.get("error_type") == "HarnessError"
        and not result["marker_verified"]
    ):
        raise AssertionError("401 did not fail closed")
    if mode == "cancel" and not (
        result["status"] == "cancelled"
        and result.get("interrupt_acknowledged")
        and not result["marker_verified"]
    ):
        raise AssertionError("Prompt-scoped interrupt did not complete")
    return result


async def main():
    parser = argparse.ArgumentParser(
        description="Nonbillable Codex Responses gateway regression: isolated temporary CODEX_HOME/workspace, dummy bearer, loopback SSE tool/error/cancel cases."
    )
    parser.add_argument(
        "--codex",
        default=shutil.which("codex"),
        help="Codex executable path (defaults to PATH lookup)",
    )
    parser.add_argument(
        "--scenario", choices=("all", "tool", "error", "cancel"), default="all"
    )
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex executable not found")
    cases = ("tool", "error", "cancel") if args.scenario == "all" else (args.scenario,)
    results = []
    for case in cases:
        results.append(await scenario(case, args.codex))
    print(
        json.dumps({"passed": True, "results": results}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    asyncio.run(main())
