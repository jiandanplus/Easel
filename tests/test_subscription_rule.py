"""AI 服务商 rule: an active subscription is used first; otherwise the custom providers are used."""
import json
import time

import web.app as web
from easel.subscription_state import SubscriptionState
from tests import test_agent_harnesses as harness_tests

sandbox = harness_tests.sandbox


class Known:
    def __init__(self, value):
        self.value, self.root = value, web.PROJECT_ROOT

    def active(self):
        return self.value


def saved(source):
    path = web.OUTPUTS_DIR / "_harness" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runtime": "pi", "service_source": source, "subscription_backend": "central", "subscription_model": "m"}), encoding="utf-8")


def test_active_subscription_wins_over_the_saved_custom_choice(sandbox, monkeypatch):
    monkeypatch.setenv("EASEL_SUBSCRIPTION_AUTO", "1")
    saved("custom")
    monkeypatch.setattr(web, "_SUBSCRIPTION_STATE", Known(True))
    config = web._harness_settings()
    assert (config["service_source"], config["subscription_backend"]) == ("subscription", "central")


def test_without_a_subscription_the_custom_providers_are_used(sandbox, monkeypatch):
    monkeypatch.setenv("EASEL_SUBSCRIPTION_AUTO", "1")
    saved("subscription")
    monkeypatch.setattr(web, "_SUBSCRIPTION_STATE", Known(False))
    assert web._harness_settings()["service_source"] == "custom"


def test_until_the_first_answer_the_saved_choice_stays(sandbox, monkeypatch):
    monkeypatch.setenv("EASEL_SUBSCRIPTION_AUTO", "1")
    saved("subscription")
    monkeypatch.setattr(web, "_SUBSCRIPTION_STATE", Known(None))
    assert web._harness_settings()["service_source"] == "subscription"


def wait(state):
    for _ in range(200):
        if not state._running:
            return
        time.sleep(0.01)


def test_state_is_checked_in_the_background_and_keeps_the_last_answer_on_errors(tmp_path):
    import threading
    answers, gate = [True, RuntimeError("offline"), False], threading.Event()
    def checker(root):
        gate.wait(2)
        value = answers.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    now = [0.0]
    state = SubscriptionState(tmp_path, checker=checker, clock=lambda: now[0])
    assert state.active() is None  # Starts a check, does not wait for it.
    gate.set(); wait(state)
    assert state.active() is True
    now[0] = 61.0
    state.active(); wait(state)
    assert state.active() is True  # The failed check kept the last known answer.
    state.refresh(); wait(state)
    assert state.active() is False
