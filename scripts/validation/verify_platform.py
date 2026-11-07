"""Owned nonbillable account → native Codex → metered loopback gateway proof.

All writable state is disposable. Python sockets deny non-loopback networking;
native tools retain Codex workspace-write/networkAccess=False. Native app-server
uses explicit loopback provider plus denied external proxies (not OS-wide egress
isolation). No global Codex state.
"""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "synthetic-validation-password-123"
EMAIL = "platform-proof@example.invalid"


def clean_environment(home, workspace):
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(
            word in k.upper()
            for word in (
                "KEY",
                "TOKEN",
                "SECRET",
                "PASSWORD",
                "CODEX",
                "OPENAI",
                "ANTHROPIC",
                "EASEL",
                "PROXY",
            )
        )
    }
    env.update(
        HOME=str(home),
        EASEL_WORKSPACE_ROOT=str(workspace),
        EASEL_RESOURCE_ROOT=str(ROOT),
        PYTHONPATH=str(ROOT),
        HTTP_PROXY="http://127.0.0.1:9",
        HTTPS_PROXY="http://127.0.0.1:9",
        ALL_PROXY="http://127.0.0.1:9",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    return env


def deny_outside_network():
    connect = socket.socket.connect

    def loopback_only(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            try:
                if not ipaddress.ip_address(address[0]).is_loopback:
                    raise ValueError
            except ValueError:
                raise OSError("Validation denies non-loopback networking") from None
        return connect(sock, address)

    socket.socket.connect = loopback_only


def serve(args):
    deny_outside_network()
    import uvicorn

    if args.role == "platform":
        from easel_platform.api import create_app
        from easel_platform.config import Settings

        app = create_app(
            Settings(
                Path(args.database),
                upstream_url=args.upstream,
                upstream_key="easel-loopback-dummy-not-a-secret",
                upstream_model="dummy-upstream",
                public_model="easel-test",
            )
        )
    else:
        from web.app import app
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        log_level="error",
        access_log=False,
    )


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(args):
    if not args.codex or not Path(args.codex).is_file():
        raise SystemExit("Requires an explicit native Codex binary")
    sys.path.insert(0, str(ROOT))
    from scripts.validation import verify_codex_gateway as dummy
    from http.server import ThreadingHTTPServer
    import httpx

    results = {}
    processes = []
    server = None
    with (
        tempfile.TemporaryDirectory(prefix="easel-platform-proof-") as temporary,
        tempfile.TemporaryDirectory(
            prefix="native-denial-", dir=ROOT / ".git/sdd"
        ) as outside,
    ):
        temporary = Path(temporary)
        home, work = temporary / "home", temporary / "workspace"
        home.mkdir()
        work.mkdir()
        env = clean_environment(home, work)
        # Do not wrap Seatbelt around app-server: Codex must apply its own
        # workspace-write sandbox to tools. Nested sandbox_apply exits 71.
        import shlex

        native_pids = temporary / "native-pids"
        wrapper = temporary / "codex"
        wrapper.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$$\" >> "
            + shlex.quote(str(native_pids))
            + "\nexec "
            + shlex.quote(args.codex)
            + ' "$@"\n'
        )
        wrapper.chmod(0o700)
        env["EASEL_CODEX_EXECUTABLE"] = str(wrapper)
        dummy.MODE = "tool"
        dummy.stats.update(
            requests=0,
            bearer_correct=True,
            paths=[],
            tool_names=[],
            tool_result_seen=False,
            events={},
        )
        tool_code = (
            "from pathlib import Path; import socket, json; "
            "p=Path('marker.txt'); p.write_text("
            + repr(dummy.MARKER + "\n")
            + "); print(p.read_text())\n"
            "result={}\n"
            "try: Path("
            + repr(str(Path(outside) / "forbidden.txt"))
            + ").write_text('denial probe')\n"
            "except PermissionError: result['outside_workspace_denied']=True\n"
            "s=socket.socket(); s.settimeout(1)\n"
            "try: s.connect(('192.0.2.1', 9))\n"
            "except OSError as error: result['external_network_denied']=error.errno in (1,13)\n"
            "finally: s.close()\n"
            "Path('sandbox-proof.json').write_text(json.dumps(result)); print(json.dumps(result))"
        )
        dummy.TOOL_COMMAND = (
            shlex.quote(sys.executable) + " -c " + shlex.quote(tool_code)
        )

        class NativeHandler(dummy.Handler):
            def do_POST(self):
                import io

                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                data = json.loads(raw)
                # Adapt only the dummy's discovery to native namespaced tools.
                tools = []
                for tool in data.get("tools", []):
                    if tool.get("type") == "namespace":
                        tools.extend(tool.get("tools", []))
                    else:
                        tools.append(tool)
                data["tools"] = tools
                converted = json.dumps(data).encode()
                self.headers.replace_header("Content-Length", str(len(converted)))
                self.rfile = io.BytesIO(converted)
                return super().do_POST()

        server = ThreadingHTTPServer(("127.0.0.1", 0), NativeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        platform_port, local_port = free_port(), free_port()
        env["EASEL_PORT"] = str(local_port)
        database = temporary / "platform.sqlite"

        def launch(role, port):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--role",
                role,
                "--port",
                str(port),
                "--database",
                str(database),
                "--upstream",
                f"http://127.0.0.1:{server.server_port}/v1",
            ]
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=(temporary / (role + ".log")).open("w"),
                start_new_session=True,
            )
            processes.append(process)
            return f"http://127.0.0.1:{port}"

        try:
            custom_config = work / ".env"
            custom_bytes = (
                "EASEL_CHAT_TRANSPORT=api\nEASEL_DIRECT_API_MODEL=custom-sentinel\nEASEL_DIRECT_API_KEY=fake-custom-sentinel\nEASEL_DIRECT_API_BASE_URL=http://127.0.0.1:"
                + str(server.server_port)
                + "/v1\n"
            ).encode()
            custom_config.write_bytes(custom_bytes)
            platform, local = (
                launch("platform", platform_port),
                launch("local", local_port),
            )
            deny_outside_network()
            client = httpx.Client(trust_env=False, timeout=80)
            for url in (platform + "/v1/plans", local + "/api/platform/status"):
                for attempt in range(100):
                    try:
                        if client.get(url).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("Owned service did not start")

            def bridge(method, path, body=None, expected=200):
                response = client.request(
                    method, local + path, json=body, headers={"Origin": local}
                )
                assert response.status_code == expected, (
                    path,
                    response.status_code,
                    response.text[:300],
                )
                return response.json()

            bridge("POST", "/api/platform/configure", {"url": platform})
            account = bridge(
                "POST", "/api/platform/register", {"email": EMAIL, "password": PASSWORD}
            )
            assert (
                "access_token" not in json.dumps(account)
                and account["account"]["email_verified"] is False
            )
            assert bridge("GET", "/api/platform/entitlements")["entitlements"] == []
            bridge(
                "POST",
                "/api/settings/harness",
                {
                    "runtime": "codex",
                    "model": "easel-test",
                    "subscription_model": "easel-test",
                    "sandbox": "workspace-write",
                    "service_source": "subscription",
                    "execution_target": "local",
                },
            )
            chat = {
                "message": "Use Python to write marker.txt then print its contents.",
                "sessionId": "platform-proof",
                "turnId": "proof-1",
            }
            bridge("POST", "/api/chat/stream", chat, expected=409)
            assert dummy.stats["requests"] == 0
            results["registration_no_grant_no_fallback"] = True

            def operator(*arguments):
                command = [
                    sys.executable,
                    "-m",
                    "easel_platform",
                    *arguments,
                    "--database",
                    str(database),
                ]
                return json.loads(subprocess.check_output(command, env=env, cwd=ROOT))

            operator(
                "plan",
                "create",
                "--id",
                "proof-test",
                "--name",
                "Loopback test entitlement",
                "--tokens",
                "1000000",
                "--concurrency",
                "1",
                "--days",
                "1",
                "--model",
                "easel-test",
            )
            grant = operator(
                "grant",
                "--email",
                EMAIL,
                "--plan",
                "proof-test",
                "--key",
                "proof-grant",
            )
            assert (
                operator(
                    "grant",
                    "--email",
                    EMAIL,
                    "--plan",
                    "proof-test",
                    "--key",
                    "proof-grant",
                )["id"]
                == grant["id"]
            )
            assert bridge("GET", "/api/platform/models")["models"] == [
                {"id": "easel-test"}
            ]
            # Force bridge refresh using only its private disposable session file.
            session_path = work / "outputs/_harness/platform-session.json"
            session = json.loads(session_path.read_text())
            old_access = session["auth"]["access_token"]
            session["auth"]["expires_at"] = 0
            session_path.write_text(json.dumps(session))
            session_path.chmod(0o600)
            assert bridge("GET", "/api/platform/status")["authenticated"]
            refreshed = json.loads(session_path.read_text())
            assert refreshed["auth"]["access_token"] != old_access
            results["serialized_refresh"] = True
            response = client.post(
                local + "/api/chat/stream", json=chat, headers={"Origin": local}
            )
            assert response.status_code == 200
            stream = response.text
            assert "event: error" not in stream, (
                stream[-1500:],
                (temporary / "platform.log").read_text()[-1000:],
                dummy.stats["requests"],
            )
            assert (
                "event: token" in stream
                and "event: done" in stream
                and dummy.MARKER in stream
            ), stream[-1500:]
            marker = work / "marker.txt"
            assert marker.is_file(), (dummy.stats, stream[-2500:])
            assert marker.read_text().strip() == dummy.MARKER
            sandbox_result = json.loads((work / "sandbox-proof.json").read_text())
            assert sandbox_result == {
                "outside_workspace_denied": True,
                "external_network_denied": True,
            }, sandbox_result
            results.update(sandbox_result)
            assert (
                dummy.stats["tool_result_seen"]
                and dummy.stats["tool_output_marker"]
                and dummy.stats["bearer_correct"]
            )
            usage = bridge("GET", "/api/platform/usage")
            assert (
                usage["used_tokens"] == 2 * dummy.stats["requests"]
                and usage["reserved_tokens"] == 0
                and usage["pending_reconciliation"] == 0
            )
            results.update(
                native_requests=dummy.stats["requests"],
                actual_python_marker=True,
                sse_token_done=True,
                measured_used_tokens=usage["used_tokens"],
                settled_requests=len(usage["usage"]),
                reserved_tokens=0,
                pending_reconciliation=0,
            )
            # Quota/expiry checks mutate only the owned synthetic operator database.
            import sqlite3

            def update(sql, parameters):
                with sqlite3.connect(database) as db:
                    db.execute(sql, parameters)

            update("UPDATE grants SET token_quota=1 WHERE id=?", (grant["id"],))
            before = dummy.stats["requests"]
            bridge(
                "POST",
                "/api/chat/stream",
                {**chat, "sessionId": "quota", "turnId": "quota"},
                expected=409,
            )
            assert dummy.stats["requests"] == before
            results["quota_rejected_no_fallback"] = True
            update(
                "UPDATE grants SET token_quota=1000000,ends_at=1 WHERE id=?",
                (grant["id"],),
            )
            bridge(
                "POST",
                "/api/chat/stream",
                {**chat, "sessionId": "expired", "turnId": "expired"},
                expected=409,
            )
            results["expired_rejected"] = True
            update(
                "UPDATE grants SET ends_at=? WHERE id=?",
                (time.time() + 86400, grant["id"]),
            )
            update("UPDATE grants SET status='revoked' WHERE id=?", (grant["id"],))
            bridge(
                "POST",
                "/api/chat/stream",
                {**chat, "sessionId": "revoked-grant", "turnId": "revoked-grant"},
                expected=409,
            )
            assert dummy.stats["requests"] == before
            results["revoked_grant_rejected"] = True
            update("UPDATE grants SET status='active' WHERE id=?", (grant["id"],))
            owner_session = bridge("GET", "/api/platform/status")["session_id"]
            bridge("POST", "/api/platform/logout")
            foreign = bridge(
                "POST",
                "/api/platform/register",
                {"email": "foreign@example.invalid", "password": PASSWORD},
            )
            bridge("DELETE", "/api/platform/devices/" + owner_session, expected=404)
            assert bridge("GET", "/api/platform/usage")["used_tokens"] == 0
            assert bridge("GET", "/api/platform/entitlements")["entitlements"] == []
            # Give foreign account a test grant so old-session binding itself rejects.
            operator(
                "grant",
                "--email",
                "foreign@example.invalid",
                "--plan",
                "proof-test",
                "--key",
                "foreign-grant",
            )
            bridge(
                "POST", "/api/chat/stream", {**chat, "turnId": "foreign"}, expected=409
            )
            results["cross_account_device_usage_and_native_binding"] = True
            bridge("DELETE", "/api/platform/devices/" + foreign["session_id"])
            assert bridge("GET", "/api/platform/status")["authenticated"] is False
            bridge(
                "POST",
                "/api/chat/stream",
                {**chat, "sessionId": "revoked", "turnId": "revoked"},
                expected=409,
            )
            assert dummy.stats["requests"] == before
            results["revoked_rejected_no_fallback"] = True
            assert (
                client.post(
                    platform + "/v1/auth/login", json={}, headers={"Origin": local}
                ).status_code
                == 403
            )
            results["standalone_browser_origin_denied"] = True
            # Credential values must not appear in SSE or nonprivate persisted outputs.
            forbidden = [
                "fake-custom-sentinel",
                PASSWORD,
                old_access,
                refreshed["auth"]["access_token"],
                refreshed["auth"]["refresh_token"],
                dummy.TOKEN,
            ]
            assert not any(value in stream for value in forbidden)
            for path in (work / "outputs").rglob("*"):
                if path.is_file() and "_harness" not in path.parts:
                    assert not any(
                        value in path.read_text(errors="replace") for value in forbidden
                    )
            results["public_outputs_and_sse_secret_free"] = True
            private_home = work / "outputs/_harness/codex-home"
            assert (
                private_home.is_dir() and private_home.stat().st_mode & 0o777 == 0o700
            )
            results["private_codex_home"] = True
            assert custom_config.read_bytes() == custom_bytes
            results["custom_credentials_preserved"] = True
            client.close()
        finally:
            import signal

            for process in reversed(processes):
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
            if server:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            results["owned_services_exited"] = all(
                process.poll() is not None for process in processes
            )
            if native_pids.is_file():
                for native_pid in native_pids.read_text().split():
                    try:
                        os.kill(int(native_pid), 0)
                    except ProcessLookupError:
                        continue
                    raise AssertionError("Owned native process did not exit")
                results["owned_native_exited"] = True
        assert results["owned_services_exited"]
    print(json.dumps({"passed": True, "results": results}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", help="Explicit native Codex executable; required for proof"
    )
    parser.add_argument("--role", choices=("platform", "local"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--database")
    parser.add_argument("--upstream")
    arguments = parser.parse_args()
    if arguments.role:
        serve(arguments)
    else:
        main(arguments)
