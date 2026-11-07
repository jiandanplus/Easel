"""Synthetic, bounded HTTP receiver contract tests; no transport resources."""
import importlib.util
import sys
from pathlib import Path
from dataclasses import FrozenInstanceError
import pytest

SOURCE = Path(__file__).parents[1] / "scripts/av_sources/pack_http.py"


def module():
    assert SOURCE.exists(), "bounded HTTP receiver is missing"
    spec = importlib.util.spec_from_file_location("av_pack_http_test_target", SOURCE)
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


class Reader:
    def __init__(self, wire, split=65536):
        self.wire, self.position, self.split = wire, 0, split
        self.calls = []
        self.events = []

    def read(self, n):
        assert type(n) is int and 0 < n <= 65536
        self.calls.append((self.position, n))
        self.events.append("read")
        data = self.wire[self.position:self.position + min(n, self.split)]
        self.position += len(data)
        return data

    def guard(self):
        self.events.append("guard")


def receive(m, wire, *, split=65536, status=200, limits=None, types=("application/json",)):
    reader = Reader(wire, split)
    result = m.read_response(reader.read, guard=reader.guard, expected_status=status,
                             content_types=types, limits=limits or m.METADATA_LIMITS)
    assert reader.events == [item for _ in reader.calls for item in ("guard", "read", "guard")]
    return result, reader


def cl(body=b"{}", extra=b"", media=b"application/json"):
    return (b"HTTP/1.1 200 OK\r\nContent-Type: " + media + b"\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\n" + extra + b"\r\n" + body)


def test_content_length_split_and_numeric_facts():
    m = module()
    wire = cl()
    result, reader = receive(m, wire, split=1)
    assert result.body == b"{}"
    assert result.status == 200 and result.framing == "content-length"
    assert result.wire_bytes == len(wire)
    assert result.overhead_bytes == result.header_bytes == len(wire) - 2
    assert result.header_fields == 2 and result.chunks == 0
    assert reader.calls[-1] == (len(wire), 1)
    with pytest.raises(FrozenInstanceError):
        result.body = b"other"


@pytest.mark.parametrize("preset,media", [
    ("METADATA_LIMITS", b"Application/JSON; charset=utf-8"),
    ("STDOUT_LIMITS", b"application/vnd.docker.raw-stream"),
    ("ARTIFACT_LIMITS", b"application/vnd.docker.multiplexed-stream"),
])
@pytest.mark.parametrize("split", [1, 2, 7, 65536])
def test_chunked_types_and_segmentation(preset, media, split):
    m = module()
    wire = (b"HTTP/1.1 201 Created\r\ncOnTeNt-TyPe: " + media +
            b"\r\nTransfer-Encoding: ChUnKeD\r\nX-Test: \tvalue\t \r\n\r\n"
            b"1\r\na\r\n2\r\nbc\r\n0\r\n\r\n")
    result, _ = receive(m, wire, split=split, status=201, limits=getattr(m, preset),
                         types=(media.split(b";")[0].decode().lower(),))
    assert result.body == b"abc" and result.framing == "chunked"
    assert result.chunks == 3 and result.header_fields == 3
    assert result.wire_bytes == len(wire)
    assert result.overhead_bytes == len(wire) - 3
    assert result.header_bytes == wire.index(b"\r\n\r\n") + 4
    assert set(vars(result)) == {"body", "status", "framing", "wire_bytes", "overhead_bytes",
                                "header_bytes", "header_fields", "chunks"}


@pytest.mark.parametrize("extra,framing", [(b"", "none"), (b"Content-Length: 0\r\n", "content-length")])
def test_empty_204(extra, framing):
    m = module()
    result, _ = receive(m, b"HTTP/1.1 204 No Content\r\n" + extra + b"\r\n", status=204)
    assert result.body == b"" and result.framing == framing


@pytest.mark.parametrize("wire", [
    b"HTTP/1.0 200 OK\r\n\r\n", b"HTTP/2 200 OK\r\n\r\n",
    b"HTTP/1.1 100 Continue\r\n\r\n", b"HTTP/1.1 302 Found\r\n\r\n",
    b"HTTP/1.1 500 secret\r\n\r\n", b"HTTP/1.1 0200 OK\r\n\r\n",
    b"HTTP/1.1 200\r\n\r\n", b"HTTP/1.1 200 bad\x01\r\n\r\n",
    cl().replace(b"\r\n", b"\n"), cl().replace(b"Content-Type", b"Content Type"),
    cl().replace(b"Content-Type", b"Content-Type "), cl(extra=b" X-Fold: a\r\n"),
    cl(extra=b"X-Test: a\x00b\r\n"), cl(extra=b"X-Test: \xff\r\n"),
    cl(extra=b"X-A: a\r\nx-a: b\r\n"), cl(extra=b"Content-Length: 2\r\n"),
    cl(extra=b"Transfer-Encoding: chunked\r\n"), cl(extra=b"Content-Encoding: identity\r\n"),
    cl(extra=b"Upgrade: websocket\r\n"), cl(media=b"multipart/mixed"),
    cl(media=b"text/plain"), cl(media=b"application/jsonx"),
    cl().replace(b"Content-Type: application/json\r\n", b""),
    cl().replace(b"Content-Length: 2\r\n", b""),
    cl().replace(b"Content-Length: 2", b"Content-Length: +2"),
    cl().replace(b"Content-Length: 2", b"Content-Length: 02"),
    cl().replace(b"Content-Length: 2", b"Content-Length: 2,2"),
    cl().replace(b"Content-Length: 2", b"Content-Length: 2 0"),
    cl().replace(b"Content-Length: 2", b"Content-Length: -2"),
    cl()[:-1], cl() + b"x", b"", b"HTTP/1.1 200 OK\r",
])
def test_invalid_responses_are_safe(wire):
    m = module()
    with pytest.raises(m.HTTPError, match="^invalid HTTP response$") as failure:
        receive(m, wire)
    assert failure.value.__cause__ is None


def chunk_wire(chunks):
    return b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n" + chunks


@pytest.mark.parametrize("chunks", [
    b"\r\n", b"+1\r\na\r\n0\r\n\r\n", b" 1\r\na\r\n0\r\n\r\n",
    b"1 \r\na\r\n0\r\n\r\n", b"1;x=y\r\na\r\n0\r\n\r\n",
    b"g\r\n", b"00000000000000000\r\n", b"1\r\naXX0\r\n\r\n",
    b"2\r\na", b"0\r\nX-Trailer: secret\r\n\r\n", b"0\r\n\r\nx", b"0\r\n",
])
def test_chunk_grammar_trailers_and_eof(chunks):
    m = module()
    with pytest.raises(m.HTTPError):
        receive(m, chunk_wire(chunks))


@pytest.mark.parametrize("extra", [b"Transfer-Encoding: chunked\r\n", b"Content-Length: 1\r\n",
                                    b"Content-Type: text/plain\r\n"])
def test_204_rejects_body_framing_or_wrong_type(extra):
    m = module()
    with pytest.raises(m.HTTPError):
        receive(m, b"HTTP/1.1 204 No Content\r\n" + extra + b"\r\n", status=204)


@pytest.mark.parametrize("value", [b"gzip", b"gzip, chunked", b"chunked,chunked", b"identity"])
def test_transfer_encoding_exactly_chunked(value):
    m = module()
    with pytest.raises(m.HTTPError):
        receive(m, chunk_wire(b"0\r\n\r\n").replace(b": chunked", b": " + value))


def test_named_presets_and_tightening():
    m = module()
    for name, body, wire in [("metadata", 131072, 163840), ("stdout", 65536, 98304),
                             ("artifact", 16809985, 16842752)]:
        caps = getattr(m, name.upper() + "_LIMITS")
        assert caps == m.ResponseLimits(preset=name)
        assert (caps.body_bytes, caps.wire_bytes, caps.overhead_bytes, caps.header_bytes,
                caps.header_fields, caps.line_bytes, caps.status_bytes, caps.chunks,
                caps.chunk_size_digits, caps.read_bytes) == (
                body, wire, 32767, 16384, 64, 8192, 1024, 8192, 16, 65536)
        assert caps.tighten(body_bytes=3).body_bytes == 3
        with pytest.raises(FrozenInstanceError):
            caps.body_bytes = 1
    with pytest.raises(m.HTTPError):
        m.STDOUT_LIMITS.tighten(body_bytes=65537)
    with pytest.raises(m.HTTPError):
        m.STDOUT_LIMITS.tighten(preset="artifact")


@pytest.mark.parametrize("field", ["body_bytes", "wire_bytes", "overhead_bytes", "header_bytes",
                                   "header_fields", "line_bytes", "status_bytes", "chunks",
                                   "chunk_size_digits", "read_bytes"])
@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "1", 100000000])
def test_invalid_limits(field, value):
    m = module()
    with pytest.raises(m.HTTPError):
        m.ResponseLimits(**{field: value})


@pytest.mark.parametrize("kwargs", [{"preset": "unknown"}, {"preset": []},
    {"wire_bytes": 100}, {"overhead_bytes": 100}, {"header_bytes": 100},
    {"line_bytes": 100}, {"status_bytes": 8193}])
def test_limits_relationships(kwargs):
    m = module()
    with pytest.raises(m.HTTPError):
        m.ResponseLimits(**kwargs)


@pytest.mark.parametrize("field", ["body_bytes", "wire_bytes", "overhead_bytes", "header_bytes",
                                   "header_fields", "line_bytes", "status_bytes", "chunks",
                                   "chunk_size_digits", "read_bytes"])
def test_each_independent_limit_at_exact_and_one_over(field):
    m = module()
    # All limits reduced together to keep their relationships valid.
    wire = chunk_wire(b"01\r\na\r\n01\r\nb\r\n0\r\n\r\n")
    header = wire.index(b"\r\n\r\n") + 4
    caps = dict(body_bytes=2, wire_bytes=len(wire), overhead_bytes=len(wire)-2,
                header_bytes=header, header_fields=2, line_bytes=33,
                status_bytes=17, chunks=3, chunk_size_digits=2, read_bytes=2)
    # Longest field line and status include CRLF.
    caps["line_bytes"] = max(len(x)+2 for x in wire[:header-2].split(b"\r\n"))
    result, reader = receive(m, wire, limits=m.ResponseLimits(**caps))
    assert result.body == b"ab"
    assert max(n for _, n in reader.calls) <= 2
    if field == "read_bytes":
        receive(m, wire, limits=m.ResponseLimits(**{**caps, field: 1}))
        return
    caps[field] -= 1
    with pytest.raises(m.HTTPError):
        receive(m, wire, limits=m.ResponseLimits(**caps))


def test_content_length_and_chunk_overbudget_before_body_read():
    m = module()
    for wire in (cl(b"abc"), chunk_wire(b"3\r\nabc\r\n0\r\n\r\n")):
        reader = Reader(wire)
        with pytest.raises(m.HTTPError):
            m.read_response(reader.read, guard=reader.guard, expected_status=200,
                content_types=("application/json",), limits=m.METADATA_LIMITS.tighten(body_bytes=2))
        assert reader.position <= wire.index(b"\r\n\r\n") + 4 + (3 if b"chunked" in wire else 0)


@pytest.mark.parametrize("kwargs", [
    {"read": None}, {"guard": None}, {"expected_status": True}, {"expected_status": 202},
    {"expected_status": 200.0}, {"content_types": ()}, {"content_types": ["application/json"]},
    {"content_types": ("text/plain",)}, {"content_types": (b"application/json",)},
    {"limits": None}, {"limits": object()},
])
def test_invalid_dependencies_fail_before_read(kwargs):
    m = module()
    reader = Reader(cl())
    args = dict(read=reader.read, guard=reader.guard, expected_status=200,
                content_types=("application/json",), limits=m.METADATA_LIMITS)
    args.update(kwargs)
    with pytest.raises(m.HTTPError):
        m.read_response(**args)
    assert not reader.calls


@pytest.mark.parametrize("fault", [RuntimeError, KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("target", ["read", "guard_before", "guard_after", "guard_eof"])
def test_all_dependency_faults_are_sanitized(fault, target):
    m = module()
    reader = Reader(cl())
    guard_count = 0
    def guard():
        nonlocal guard_count
        guard_count += 1
        if (target == "guard_before" and guard_count == 1 or
            target == "guard_after" and guard_count == 2 or
            target == "guard_eof" and reader.position == len(reader.wire)):
            raise fault("SECRET-SENTINEL")
    def read(n):
        if target == "read":
            raise fault("SECRET-SENTINEL")
        return reader.read(n)
    with pytest.raises(m.HTTPError) as failure:
        m.read_response(read, guard=guard, expected_status=200,
            content_types=("application/json",), limits=m.METADATA_LIMITS)
    import traceback
    assert "SECRET-SENTINEL" not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize("value", [None, "secret", bytearray(b"secret"), memoryview(b"secret"), b"xx"])
def test_reader_must_return_exact_bounded_bytes(value):
    m = module()
    with pytest.raises(m.HTTPError):
        m.read_response(lambda n: value, guard=lambda: None, expected_status=200,
            content_types=("application/json",), limits=m.METADATA_LIMITS)


@pytest.mark.parametrize("field", ["body_bytes", "wire_bytes", "overhead_bytes", "header_bytes",
                                   "header_fields", "line_bytes", "status_bytes", "chunks",
                                   "chunk_size_digits", "read_bytes"])
def test_explicit_none_is_not_an_integer_cap(field):
    m = module()
    with pytest.raises(m.HTTPError):
        m.ResponseLimits(**{field: None})


@pytest.mark.parametrize("preset,media", [("METADATA_LIMITS", b"application/json"),
    ("STDOUT_LIMITS", b"application/vnd.docker.raw-stream"),
    ("ARTIFACT_LIMITS", b"application/vnd.docker.multiplexed-stream")])
def test_content_length_all_presets_and_segmentation(preset, media):
    m = module()
    wire = cl(b"abcde", media=media)
    results = [receive(m, wire, split=split, limits=getattr(m, preset),
                       types=(media.decode(),))[0] for split in (1, 2, 3, 19, 65536)]
    assert all(result == results[0] for result in results)
    assert results[0].body == b"abcde" and results[0].chunks == 0


def test_arbitrary_reader_partition_produces_identical_facts():
    m = module()
    wire = chunk_wire(b"5\r\nabcde\r\n4\r\nfghi\r\n0\r\n\r\n")
    expected, _ = receive(m, wire)
    for seed in range(12):
        reader = Reader(wire)
        def read(n):
            reader.split = 1 + (len(reader.calls) * 7 + seed) % 5
            return reader.read(n)
        actual = m.read_response(read, guard=reader.guard, expected_status=200,
            content_types=("application/json",), limits=m.METADATA_LIMITS)
        assert actual == expected
        assert reader.events == [e for _ in reader.calls for e in ("guard", "read", "guard")]


def test_read_cap_is_used_for_payload_and_short_reads_remain_valid():
    m = module()
    wire = cl(b"abcde")
    result, reader = receive(m, wire, limits=m.METADATA_LIMITS.tighten(read_bytes=2))
    start = wire.index(b"\r\n\r\n") + 4
    assert [(pos-start, n) for pos, n in reader.calls if start <= pos < len(wire)] == [(0, 2), (2, 2), (4, 1)]
    assert result.body == b"abcde"


@pytest.mark.parametrize("chunked,cap", [(False, "wire_bytes"), (True, "wire_bytes"),
                                        (True, "overhead_bytes")])
def test_wire_and_chunk_crlf_budgets_checked_before_body(chunked, cap):
    m = module()
    wire = chunk_wire(b"3\r\nabc\r\n0\r\n\r\n") if chunked else cl(b"abc")
    start = wire.index(b"\r\n\r\n") + 4 + (3 if chunked else 0)
    # Keep cap relationships valid by reducing header/line/status caps together.
    values = dict(body_bytes=3, wire_bytes=start + 4 if chunked else start + 2,
                  overhead_bytes=start + 1 if chunked else start,
                  header_bytes=wire.index(b"\r\n\r\n")+4, line_bytes=32, status_bytes=17)
    if cap == "overhead_bytes":
        values["wire_bytes"] = len(wire)
    elif chunked:
        values["overhead_bytes"] = start + 2
    reader = Reader(wire)
    with pytest.raises(m.HTTPError):
        m.read_response(reader.read, guard=reader.guard, expected_status=200,
            content_types=("application/json",), limits=m.ResponseLimits(**values))
    assert reader.position == start


@pytest.mark.parametrize("phase", ["before", "after"])
def test_eof_probe_guard_both_phases(phase):
    m = module()
    reader = Reader(cl())
    events = []
    boundary_guards = []
    faults = []
    guard_calls = 0
    def read(n):
        events.append("EOF-read" if reader.position == len(reader.wire) else "read")
        return reader.read(n)
    def guard():
        nonlocal guard_calls
        guard_calls += 1
        if reader.position == len(reader.wire):
            if guard_calls == 2 * len(reader.calls) + 1:
                boundary = "EOF-pre"
            elif reader.calls[-1][0] == len(reader.wire):
                boundary = "EOF-post"
            else:
                boundary = "payload-post"
            boundary_guards.append(boundary)
            events.append("EOF-guard")
            if (phase == "before" and boundary == "EOF-pre" or
                phase == "after" and boundary == "EOF-post"):
                faults.append((boundary, reader.position, guard_calls, len(reader.calls)))
                raise KeyboardInterrupt("EOF-SECRET")
    with pytest.raises(m.HTTPError, match="^invalid HTTP response$") as failure:
        m.read_response(read, guard=guard, expected_status=200,
            content_types=("application/json",), limits=m.METADATA_LIMITS)
    assert failure.value.__context__ is None
    assert ("EOF-read" in events) == (phase == "after")
    assert boundary_guards == (["payload-post", "EOF-pre"] if phase == "before" else
                               ["payload-post", "EOF-pre", "EOF-post"])
    boundary, position, guard_number, read_count = faults[0]
    assert boundary == ("EOF-pre" if phase == "before" else "EOF-post")
    assert position == len(reader.wire)
    assert guard_number == 2 * read_count + (1 if phase == "before" else 0)
    eof_calls = [(pos, n) for pos, n in reader.calls if pos == len(reader.wire)]
    assert eof_calls == ([] if phase == "before" else [(len(reader.wire), 1)])


def test_guard_still_runs_after_failed_read():
    m = module()
    events = []
    def read(n):
        events.append("read")
        raise SystemExit("READ-SECRET")
    def guard():
        events.append("guard")
    with pytest.raises(m.HTTPError):
        m.read_response(read, guard=guard, expected_status=200,
            content_types=("application/json",), limits=m.METADATA_LIMITS)
    assert events == ["guard", "read", "guard"]


def test_header_field_cap_rejects_before_retaining_an_extra_field():
    m = module()
    wire = cl(extra=b"X-Secret: extra\r\n")
    reader = Reader(wire)
    with pytest.raises(m.HTTPError):
        m.read_response(reader.read, guard=reader.guard, expected_status=200,
            content_types=("application/json",), limits=m.METADATA_LIMITS.tighten(header_fields=2))
    assert reader.position <= wire.index(b"X-Secret") + 2


def test_connection_upgrade_is_not_admitted():
    m = module()
    with pytest.raises(m.HTTPError):
        receive(m, cl(extra=b"Connection: keep-alive, UpGrAdE\r\n"))


def test_exact_fixed_read_chunk_bound_without_large_sample():
    m = module()
    with pytest.raises(m.HTTPError):
        m.ResponseLimits(read_bytes=65537)
    assert m.ARTIFACT_LIMITS.read_bytes == 65536


def test_no_admission_or_store_callback_api():
    m = module()
    import inspect
    assert tuple(inspect.signature(m.read_response).parameters) == (
        "read", "guard", "expected_status", "content_types", "limits", "on_status")


@pytest.mark.parametrize("wire,status", [(cl(), 201),
    (cl().replace(b"200 OK", b"201 Created"), 200),
    (b"HTTP/1.1 204 No Content\r\n\r\nx", 204)])
def test_status_must_match_and_204_still_requires_eof(wire, status):
    m = module()
    with pytest.raises(m.HTTPError):
        receive(m, wire, status=status)


def test_exact_wire_cap_eof_probe_never_admits_extra_byte():
    m = module()
    wire = cl()
    caps = m.ResponseLimits(body_bytes=2, wire_bytes=len(wire), overhead_bytes=len(wire)-2,
        header_bytes=len(wire)-2, line_bytes=32, status_bytes=17)
    result, reader = receive(m, wire, limits=caps)
    assert result.wire_bytes == caps.wire_bytes
    assert reader.calls[-1] == (len(wire), 1)
    reader = Reader(wire+b"s")
    with pytest.raises(m.HTTPError, match="^invalid HTTP response$"):
        m.read_response(reader.read, guard=reader.guard, expected_status=200,
            content_types=("application/json",), limits=caps)
    assert reader.position == len(wire) + 1
    assert reader.events[-3:] == ["guard", "read", "guard"]


def test_exact_empty_content_length_and_sixteen_digit_zero_chunk():
    m = module()
    result, _ = receive(m, cl(b""))
    assert result.body == b"" and result.chunks == 0
    result, _ = receive(m, chunk_wire(b"0000000000000000\r\n\r\n"))
    assert result.body == b"" and result.chunks == 1


@pytest.fixture(autouse=True)
def diagnostic_native_tripwires(monkeypatch):
    import socket, threading, subprocess, ctypes, os, signal, resource
    calls = []
    def forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError("native resource reached")
    for mod, names in ((socket, ('socket',)), (threading, ('Thread', 'Timer')),
                       (subprocess, ('Popen',)), (ctypes, ('CDLL',)),
                       (os, ('fork', 'statvfs')), (signal, ('signal', 'alarm', 'setitimer', 'pthread_sigmask')),
                       (resource, ('getrlimit', 'setrlimit'))):
        for name in names:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, forbidden)
    yield
    assert not calls, "native tripwire swallowed by normalized exception"


def test_diagnostic_wrong_status_observes_only_number_before_rejection():
    m = module(); line = b"HTTP/1.1 400 SECRET-REASON\r\n"
    reader = Reader(line + b"X-Secret: HEADER\r\n\r\nSECRET-BODY")
    seen = []
    with pytest.raises(m.HTTPError, match="^invalid HTTP response$"):
        m.read_response(reader.read, guard=reader.guard, expected_status=200,
            content_types=("application/json",), limits=m.METADATA_LIMITS, on_status=seen.append)
    assert seen == [400] and type(seen[0]) is int
    assert reader.position == len(line)


@pytest.mark.parametrize("line", [b"HTTP/1.1 099 NO\r\n", b"HTTP/1.1 600 NO\r\n",
    b"HTTP/1.1 400 BAD\n", b"HTTP/1.1 400 BAD\r", b"HTTP/1.1 200 "+b"X"*1024+b"\r\n"])
def test_diagnostic_invalid_status_has_no_event(line):
    m=module();r=Reader(line);seen=[]
    with pytest.raises(m.HTTPError):
        m.read_response(r.read,guard=r.guard,expected_status=200,content_types=("application/json",),
            limits=m.METADATA_LIMITS,on_status=seen.append)
    assert seen==[]

@pytest.mark.parametrize("fault", ['callback','expiry','forged-read','not-callable','success'])
def test_diagnostic_callback_guard_and_dependency_failure(fault):
    m=module();r=Reader(cl());seen=[];expired=[False]
    def observe(status):
        seen.append(status)
        if fault=='callback':raise RuntimeError('SECRET')
        if fault=='expiry':expired[0]=True
    def guard():
        if expired[0]:raise RuntimeError('SECRET')
    def read(n):
        if fault=='forged-read':raise RuntimeError('http_status=400 operation=create SECRET')
        return r.read(n)
    kwargs=dict(guard=guard,expected_status=200,content_types=("application/json",),
        limits=m.METADATA_LIMITS,on_status=object() if fault=='not-callable' else observe)
    if fault=='success':assert m.read_response(read,**kwargs).body==b'{}'
    else:
        with pytest.raises(m.HTTPError,match='^invalid HTTP response$') as err:m.read_response(read,**kwargs)
        assert err.value.__context__ is None
    assert seen==([] if fault in ('forged-read','not-callable') else [200])
