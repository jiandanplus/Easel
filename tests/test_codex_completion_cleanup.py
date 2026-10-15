"""Deterministic completion races without starting a process or model call."""
import asyncio
import gc
from contextlib import asynccontextmanager

import pytest

from easel.harnesses import codex
from easel.harnesses.base import HarnessError, RunRequest


@pytest.mark.parametrize("failure", ["cancel", "reader"])
def test_completion_is_finalized_before_late_cleanup_notification(tmp_path, monkeypatch, failure):
    async def scenario():
        loop = asyncio.get_running_loop()
        unhandled = []
        loop.set_exception_handler(lambda loop, context: unhandled.append(context["message"]))
        ready = asyncio.Event()
        release = asyncio.Event()
        calls = []
        events = []
        states = []

        class ControlledRPC:
            initialized = True
            native_id = "thread-owned"
            completion = None

            async def call(self, method, params, **kwargs):
                calls.append((method, params, kwargs))
                if method == "turn/start":
                    return {"turn": {"id": "turn-owned"}}
                assert method == "turn/interrupt"
                return {}

            async def wait_notification(self, completion):
                self.completion = completion
                ready.set()
                await release.wait()
                raise HarnessError("controlled reader failure")

        rpc = ControlledRPC()

        @asynccontextmanager
        async def connection(request, command, notify, respond):
            rpc.notify = notify
            try:
                yield rpc
            finally:
                states.append(rpc.completion.cancelled())
                rpc.notify("turn/completed", {
                    "threadId": "thread-owned", "turnId": "turn-owned",
                    "turn": {"id": "turn-owned", "status": "interrupted"}})

        monkeypatch.setattr(codex, "connection", connection)

        async def reject(questions):
            raise AssertionError("Unexpected permission request")

        request = RunRequest("hello", tmp_path, {}, native_id="thread-owned", command=("unused",))
        task = asyncio.create_task(codex.CodexAdapter().run(
            request, lambda *event: events.append(event), reject, lambda identifier: None))
        await asyncio.wait_for(ready.wait(), 1)
        if failure == "cancel":
            task.cancel()
        else:
            release.set()
        try:
            await task
        except asyncio.CancelledError:
            assert failure == "cancel"
        except HarnessError as error:
            assert failure == "reader"
            assert str(error) == "controlled reader failure"
        else:
            raise AssertionError("Run failure was swallowed")
        interrupts = [call for call in calls if call[0] == "turn/interrupt"]
        assert interrupts == ([("turn/interrupt", {
            "threadId": "thread-owned", "turnId": "turn-owned"}, {"timeout": 2})]
            if failure == "cancel" else [])
        assert not events
        rpc.completion = None
        rpc.notify = None
        del task
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert not unhandled
        assert states == [True]

    asyncio.run(scenario())
