"""Codex native app-server adapter; credentials remain in Codex."""
from __future__ import annotations

import asyncio

from easel import __version__

from .base import Descriptor, HarnessError, RunRequest
from .commands import base_command
from .runtime import prepare_codex
from .permissions import can_auto_approve
from .rpc import connection
from .workspace import harness_instructions
from .secrets import private_run


class CodexAdapter:
    descriptor = Descriptor("codex", "Codex", "app-server", "npm install -g @openai/codex；codex login")

    @private_run
    async def run(self, request: RunRequest, emit, ask, bind):
        if request.service_source == "subscription" and request.subscription_backend == "central":
            from easel.platform.central import CentralClient
            from easel.platform.execution_proxy import ExecutionProxy
            from .runtime import prepare_subscription
            if request.sandbox not in ("read-only", "workspace-write"):
                raise HarnessError("Codex 只允许只读或工作区写入权限")
            client = CentralClient(request.cwd)
            # Capture the accepted identity once. Every renewal stays in the same
            # invocation, preserving server operation/budget/expiration caps.
            account, model, origin, invocation_key = (request.account_id, request.model,
                                                       request.platform_url, request.invocation_key)
            scope = dict(request.central_scope)
            async def renew():
                return await asyncio.to_thread(client.task_credential, account, model,
                                               origin, dict(scope), invocation_key)
            credential = await renew()
            directory = request.cwd / "outputs" / "_harness" / "central-execution"
            if not directory.resolve().is_relative_to(request.cwd.resolve()):
                raise HarnessError("中央执行私有目录不能指向工作区外部")
            async with ExecutionProxy(directory, request.platform_url, credential["task_token"],
                                      request.model, credential["invocation_id"],
                                      credential=credential, renew=renew,
                                      **({"session_key": request.session_key, "turn_id": request.turn_id}
                                         if request.turn_id else {})) as proxy:
                command = list(request.command) if request.command else base_command("codex") + ["app-server"]
                prepared, command = prepare_subscription(request, command, proxy_url=proxy.base_url,
                                                         proxy_token=proxy.local_token)
                await self._run(prepared, emit, ask, bind, prepared_command=command)
            return
        await self._run(request, emit, ask, bind)

    async def _run(self, request, emit, ask, bind, *, prepared_command=None):
        if request.sandbox not in ("read-only", "workspace-write"):
            raise HarnessError("Codex 只允许只读或工作区写入权限")
        command = list(request.command) if request.command else base_command("codex") + ["app-server"]
        from easel.platform.privacy import remember
        if prepared_command is None:
            request, command = await asyncio.to_thread(prepare_codex, request, command)
        else:
            command = prepared_command
        # Newly minted task credentials join the same transient output context.
        remember(request.cwd, [value for key, value in request.env.items()
                               if any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))])
        completion = asyncio.get_running_loop().create_future()
        thread_id = request.native_id
        turn_id = ""
        streamed: set[str] = set()

        def notify(method, params):
            if not turn_id or params.get("threadId") != thread_id or params.get("turnId", turn_id) != turn_id:
                return
            if method == "item/agentMessage/delta":
                streamed.add(params.get("itemId", ""))
                emit("token", params.get("delta", ""))
            elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                emit("thinking", params.get("delta", ""))
            elif method == "item/started":
                item = params.get("item") or {}
                if item.get("type") not in ("agentMessage", "reasoning", "userMessage"):
                    emit("activity", f"工具：{item.get('type', 'tool')}")
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("id") not in streamed:
                    emit("token", item.get("text", ""))
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("id") != turn_id or completion.done():
                    return
                if turn.get("status") == "completed":
                    completion.set_result(None)
                else:
                    error = turn.get("error") or {}
                    completion.set_exception(HarnessError(
                        error.get("message") or f"Codex 本轮未完成：{turn.get('status')}"))
            elif method == "error" and not params.get("willRetry", False):
                if not completion.done():
                    completion.set_exception(HarnessError("Codex 执行失败，请检查原生登录与模型配置"))

        async def respond(method, params):
            if params.get("threadId") != thread_id or (
                    turn_id and params.get("turnId", turn_id) != turn_id):
                raise HarnessError("不属于当前会话的请求")
            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                if can_auto_approve(method, params, request):
                    emit("activity", "Codex：已自动通过普通操作授权")
                    return {"decision": "accept"}
                detail = params.get("command") or params.get("reason") or params.get("itemId", "")
                answers = await ask([{
                    "questionId": "permission", "header": "执行授权",
                    "question": f"Codex 请求额外权限（仅本次）：{str(detail)[:3000]}" + (
                        "\n此操作不属于可自动批准的普通操作，请确认风险。" if request.codex_auto_approve else ""),
                    "options": [{"label": "允许本次"}, {"label": "拒绝"}],
                }])
                return {"decision": "accept" if answers.get("permission") == ["允许本次"] else "decline"}
            if method == "item/tool/requestUserInput":
                questions = params.get("questions") or []
                if any(question.get("isSecret") for question in questions):
                    raise HarnessError("请在终端完成敏感凭证输入")
                normalized = [{**question, "questionId": question["id"]} for question in questions]
                answers = await ask(normalized)
                return {"answers": {key: {"answers": value} for key, value in answers.items()}}
            raise HarnessError("不支持的 Codex 客户端请求")

        async with connection(request, command, notify, respond) as rpc:
            if rpc.initialized is None:
                rpc.initialized = await rpc.call("initialize", {
                    "clientInfo": {"name": "easel", "version": __version__},
                    "capabilities": {"experimentalApi": True}})
                await rpc.send({"method": "initialized"})
            options = {"cwd": str(request.cwd), "approvalPolicy": "on-request",
                       "sandbox": request.sandbox,
                       "developerInstructions": harness_instructions(request)}
            if request.model:
                options["model"] = request.model
            if thread_id and rpc.native_id == thread_id:
                result = {"thread": {"id": thread_id}}
            elif thread_id:
                result = await rpc.call("thread/resume", {**options, "threadId": thread_id})
            else:
                result = await rpc.call("thread/start", options)
            resumed_id = (result.get("thread") or {}).get("id", "")
            if request.native_id and resumed_id != request.native_id:
                raise HarnessError("Codex 返回了不同的原生会话 ID，不会替换已有历史")
            thread_id = resumed_id
            if not thread_id:
                raise HarnessError("Codex 未返回原生会话 ID")
            bind(thread_id)
            rpc.native_id = thread_id
            policy = {"type": "readOnly"} if request.sandbox == "read-only" else {
                "type": "workspaceWrite", "writableRoots": [str(request.cwd)],
                "networkAccess": False}
            params = {"threadId": thread_id, "input": [{"type": "text", "text": request.prompt}],
                      "approvalPolicy": "on-request", "sandboxPolicy": policy}
            if request.model:
                params["model"] = request.model
            early: list[tuple[str, dict]] = []
            original_notify = rpc.notify
            rpc.notify = lambda method, data: early.append((method, data))
            result = await rpc.call("turn/start", params)
            turn_id = (result.get("turn") or {}).get("id", "")
            if not turn_id:
                raise HarnessError("Codex 未返回本轮 ID")
            rpc.notify = original_notify
            for method, data in early:
                notify(method, data)
            try:
                await rpc.wait_notification(completion)
            except asyncio.CancelledError:
                try:
                    await rpc.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=2)
                except HarnessError:
                    pass
                raise
            finally:
                # Finalize before connection cleanup can deliver a late notification.
                if not completion.done():
                    completion.cancel()
                elif not completion.cancelled():
                    completion.exception()
