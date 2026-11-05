import json

import pytest

from platform_service.errors import PlatformError


def protocol():
    from platform_service import execution_protocol

    return execution_protocol


def test_native_text_tools_canonical_hash_and_finite_budget():
    value = {
        "model": "public",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "你好"}]}
        ],
        "tools": [
            {
                "type": "namespace",
                "name": "local",
                "tools": [
                    {
                        "type": "function",
                        "name": "run",
                        "parameters": {"type": "object"},
                    }
                ],
            },
            {"type": "custom", "name": "edit"},
        ],
    }
    obj, digest = protocol().validate_request(value, "public")
    assert obj["max_output_tokens"] == 4096
    assert (
        digest
        == protocol().validate_request(dict(reversed(list(value.items()))), "public")[1]
    )
    assert value.get("max_output_tokens") is None


@pytest.mark.parametrize(
    "patch",
    [
        {"previous_response_id": "r"},
        {"conversation": "c"},
        {"background": True},
        {"base_url": "http://evil"},
        {"max_tool_calls": 9},
        {"service_tier": "priority"},
        {"tools": [{"type": "web_search"}]},
        {
            "tools": [
                {
                    "type": "namespace",
                    "name": "n",
                    "tools": [{"type": "namespace", "name": "nested", "tools": []}],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "message",
                    "content": [{"type": "input_image", "image_url": "x"}],
                }
            ]
        },
        {"max_output_tokens": True},
        {"max_output_tokens": 4097},
        {"input": {"text": "x"}},
        {"stream": 1},
        {"include": ["web_search_call.action.sources"]},
        {"text": {"format": {"type": "audio"}}},
        {"tool_choice": []},
        {"input": [{"type": []}]},
        {"input": [42]},
    ],
)
def test_unsupported_shapes_are_rejected(patch):
    with pytest.raises(PlatformError):
        protocol().validate_request(
            {"model": "public", "input": "x", **patch}, "public"
        )


def test_terminal_requires_status_id_and_integer_usage():
    valid = {
        "id": "resp_fixture",
        "status": "incomplete",
        "error": None,
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
    assert protocol().terminal_usage(valid) == (3, 2, "incomplete", "resp_fixture")
    for patch in (
        {"status": "failed"},
        {"status": None},
        {"id": None},
        {"usage": {"input_tokens": True, "output_tokens": 0}},
        {"usage": {"input_tokens": -1, "output_tokens": 0}},
    ):
        with pytest.raises(PlatformError):
            protocol().terminal_usage({**valid, **patch})


def test_sse_split_utf8_crlf_and_limits():
    parser = protocol().SSEParser()
    event = (
        "event: response.output_text.delta\r\ndata: "
        + json.dumps(
            {"type": "response.output_text.delta", "delta": "你好"}, ensure_ascii=False
        )
        + "\r\n\r\n"
    ).encode()
    result = []
    for byte in event:
        result.extend(parser.feed(bytes([byte])))
    assert len(result) == 1 and result[0][1]["delta"] == "你好"
    with pytest.raises(PlatformError):
        protocol().SSEParser().feed(b"x" * (protocol().MAX_EVENT + 1))
    with pytest.raises(PlatformError):
        protocol().SSEParser().feed(
            b'event: response.completed\ndata: {"type":"response.failed"}\n\n'
        )


def test_duplicate_json_and_nonfinite_are_rejected():
    for raw in (b'{"model":"a","model":"b"}', b'{"model":"a","input":NaN}'):
        with pytest.raises(PlatformError):
            protocol().parse_json(raw)


def test_native_client_metadata_is_bounded_and_removed_before_provider():
    value = {"model": "public", "input": "owned fixture",
             "client_metadata": {"session_id": "owned-session", "turn_id": "owned-turn",
                                 "x-codex-installation-id": "owned-installation",
                                 "x-codex-turn-metadata": "{}", "x-codex-window-id": "owned-window",
                                 "thread_id": "owned-thread"}}
    result, digest = protocol().validate_request(value, "public")
    assert "client_metadata" not in result
    assert value["client_metadata"]["session_id"] == "owned-session"
    assert digest != protocol().validate_request({"model": "public", "input": "owned fixture"}, "public")[1]
    assert digest == protocol().validate_request(value, "public")[1]


@pytest.mark.parametrize("metadata", [None, [], "text", {"session_id": 1},
                                       {"unknown": "x"}, {"base_url": "https://invalid.test"},
                                       {"api_key": "owned-dummy"}, {"session_id": "x" * 4097},
                                       {"session_id": "你" * 1366}])
def test_native_client_metadata_invalid_shapes_are_rejected(metadata):
    with pytest.raises(PlatformError):
        protocol().validate_request({"model": "public", "input": "x", "client_metadata": metadata}, "public")


def test_native_metadata_remains_in_identity_hash_and_size_limit(monkeypatch):
    p = protocol()
    body = {"model": "public", "input": "owned", "client_metadata": {"session_id": "a" * 4096}}
    result, digest = p.validate_request(body, "public")
    assert digest != p.validate_request({**body, "client_metadata": {"session_id": "changed"}}, "public")[1]
    monkeypatch.setattr(p, "MAX_REQUEST", len(p.encode(result)) + 100)
    with pytest.raises(PlatformError, match="request_too_large"):
        p.validate_request(body, "public")
