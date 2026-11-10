"""Synthetic-only behavioral coverage for the bounded canonical USTAR codec."""

import hashlib
import importlib
import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def codec():
    # An assertion gives a genuine missing-capability RED before module creation.
    try:
        return importlib.import_module("media_backup.ustar")
    except ModuleNotFoundError:
        pytest.fail("canonical streaming USTAR capability is not implemented")


def physical(payload_sizes):
    n = sum(512 + size + (-size) % 512 for size in payload_sizes) + 1024
    return n + (-n) % 10240


class ShortSink:
    def __init__(self, step=3):
        self.data = bytearray()
        self.requests = []
        self.step = step

    def write(self, raw):
        self.requests.append(len(raw))
        n = min(self.step, len(raw))
        self.data.extend(raw[:n])
        return n


class ShortSource:
    def __init__(self, raw, step=5):
        self.raw = io.BytesIO(raw)
        self.requests = []
        self.step = step

    def read(self, size):
        assert size > 0
        self.requests.append(size)
        return self.raw.read(min(size, self.step))


def archive(rows, chunk=17):
    c = codec()
    sink = ShortSink()
    writer = c.TarWriter(sink, physical([len(body) for _, body in rows]), 102400, chunk)
    for name, body in rows:
        chunks = (body[i:i + chunk] for i in range(0, len(body), chunk))
        assert writer.add(name, len(body), chunks) == hashlib.sha256(body).hexdigest()
    assert writer.finish() == len(sink.data)
    assert max(sink.requests) <= chunk
    return bytes(sink.data)


def test_roundtrip_streams_empty_and_boundary_payloads_with_short_io():
    c = codec()
    rows = [("manifest.json", b"{}"), ("db/central.dump", b""),
            ("media/" + "a" * 64, bytes(range(256)) * 3)]
    raw = archive(rows)
    source = ShortSource(raw)
    reader = c.TarReader(source, len(raw), 17)
    for name, payload in rows:
        assert reader.header() == (name, len(payload))
        sink = ShortSink()
        assert reader.body(len(payload), sink) == hashlib.sha256(payload).hexdigest()
        assert bytes(sink.data) == payload
        assert max(sink.requests, default=0) <= 17
    assert reader.finish(len(raw)) == len(raw)
    assert max(source.requests) <= 17


@pytest.mark.parametrize("name", ["", "/x", "../x", "x/../y", "x/./y", "x//y",
                                  "x/", "x\\y", "x\x00y", "x\ny", "你好", "a" * 101])
def test_rejects_unsafe_names(name):
    c = codec()
    with pytest.raises(c.TarError):
        c.encode_header(name, 0)


@pytest.mark.parametrize("size", [-1, True, 1.0, None, 4 * 1024**3 + 1])
def test_rejects_invalid_header_sizes(size):
    c = codec()
    with pytest.raises(c.TarError):
        c.encode_header("media/a", size)


def test_header_is_fixed_canonical_ustar_and_accepts_name_boundary():
    c = codec()
    raw = c.encode_header("a" * 100, 4 * 1024**3)
    assert len(raw) == 512
    assert raw[100:108] == b"0000600\0"
    assert raw[108:124] == b"0000000\0" * 2
    assert raw[124:136] == b"40000000000\0"
    assert raw[136:148] == b"00000000000\0"
    assert raw[156:157] == b"0"
    assert raw[257:265] == b"ustar\x0000"
    assert c.decode_header(raw) == ("a" * 100, 4 * 1024**3)


def mutated_header(offset, replacement, repair=True):
    c = codec()
    raw = bytearray(c.encode_header("db/central.dump", 1))
    raw[offset:offset + len(replacement)] = replacement
    if repair:
        raw[148:156] = b" " * 8
        raw[148:156] = f"{sum(raw):06o}\0 ".encode("ascii")
    return bytes(raw)


@pytest.mark.parametrize("offset,value,repair", [
    (0, b"X", False), (156, b"x", True), (156, b"g", True),
    (156, b"L", True), (156, b"2", True), (156, b"1", True),
    (156, b"5", True), (156, b"3", True), (156, b"\0", True),
    (157, b"target", True), (345, b"prefix", True), (265, b"user", True),
    (297, b"group", True), (329, b"0", True), (337, b"0", True),
    (500, b"x", True), (100, b"0000644\0", True),
    (108, b"0000001\0", True), (136, b"00000000001\0", True),
    (124, b"\x80", True), (257, b"invalid", True), (263, b"01", True),
    (16, b"\0hidden", True), (124, b"00000000009\0", True),
])
def test_bad_headers_rejected_before_body_consumption(offset, value, repair):
    c = codec()
    source = ShortSource(mutated_header(offset, value, repair) + b"secret", step=512)
    reader = c.TarReader(source, 10240, 512)
    with pytest.raises(c.TarError):
        reader.header()
    assert source.raw.tell() == 512


@pytest.mark.parametrize("raw", [b"", b"x" * 511, b"x" * 513, bytes(512)])
def test_invalid_raw_header_lengths_or_zero_header(raw):
    c = codec()
    with pytest.raises(c.TarError):
        c.decode_header(raw)


@pytest.mark.parametrize("which,value", [("max_bytes", 0), ("max_bytes", True),
    ("max_bytes", 32 * 1024**3 + 1), ("chunk_bytes", 0), ("chunk_bytes", True),
    ("chunk_bytes", 1024**2 + 1), ("chunk_bytes", 1.5)])
def test_strict_io_budgets(which, value):
    c = codec()
    args = {"max_bytes": 10240, "chunk_bytes": 512, which: value}
    with pytest.raises(c.TarError):
        c.TarReader(io.BytesIO(), **args)
    with pytest.raises(c.TarError):
        c.TarWriter(io.BytesIO(), total_size=10240, **args)


@pytest.mark.parametrize("total", [-1, True, 0, 10239, 10241, 20480])
def test_writer_rejects_invalid_total_size(total):
    c = codec()
    with pytest.raises(c.TarError):
        c.TarWriter(io.BytesIO(), total, 10240, 512)


@pytest.mark.parametrize("chunks,size", [([None], 1), ([b""], 0), ([b"ab"], 1),
    ([b"a"], 2), ([b"x" * 18], 18), ([1], 1), ([b"a", b"b"], 1)])
def test_writer_rejects_invalid_chunk_or_size(chunks, size):
    c = codec()
    writer = c.TarWriter(io.BytesIO(), 10240, 10240, 17)
    with pytest.raises(c.TarError):
        writer.add("db/central.dump", size, chunks)


@pytest.mark.parametrize("answer", [None, 0, -1, True, 1.0, 999999])
def test_invalid_sink_progress_rejected(answer):
    c = codec()
    class Sink:
        def write(self, raw):
            return answer
    with pytest.raises(c.TarError):
        c.TarWriter(Sink(), 10240, 10240, 17).add("a", 0, [])


@pytest.mark.parametrize("answer", [None, 0, True, "", b"x" * 18])
def test_invalid_source_results_rejected(answer):
    c = codec()
    class Source:
        def read(self, size):
            return answer
    with pytest.raises(c.TarError):
        c.TarReader(Source(), 10240, 17).header()


def test_reader_requires_matching_pending_body_and_state():
    c = codec()
    raw = archive([("a", b"ab")])
    reader = c.TarReader(io.BytesIO(raw), len(raw), 17)
    with pytest.raises(c.TarError):
        reader.body(0, io.BytesIO())
    reader = c.TarReader(io.BytesIO(raw), len(raw), 17)
    assert reader.header() == ("a", 2)
    with pytest.raises(c.TarError):
        reader.header()
    reader = c.TarReader(io.BytesIO(raw), len(raw), 17)
    reader.header()
    with pytest.raises(c.TarError):
        reader.body(1, io.BytesIO())


@pytest.mark.parametrize("position", [513, 1024, 1536, 2048, 10239])
def test_nonzero_body_padding_footer_or_record_padding_rejected(position):
    c = codec()
    raw = bytearray(archive([("a", b"x")]))
    raw[position] = 1
    reader = c.TarReader(io.BytesIO(raw), len(raw), 17)
    reader.header()
    with pytest.raises(c.TarError):
        reader.body(1, io.BytesIO())
        reader.finish(len(raw))


@pytest.mark.parametrize("cut", [0, 511, 512, 513, 1023, 1535, 2047, 10239])
def test_truncated_archive_rejected(cut):
    c = codec()
    raw = archive([("a", b"x")])[:cut]
    reader = c.TarReader(io.BytesIO(raw), 10240, 17)
    with pytest.raises(c.TarError):
        reader.header()
        reader.body(1, io.BytesIO())
        reader.finish(10240)


@pytest.mark.parametrize("suffix", [b"\0", b"x", bytes(512), bytes(10240)])
def test_extra_trailing_bytes_rejected_with_single_byte_probe(suffix):
    c = codec()
    raw = archive([("a", b"x")])
    source = ShortSource(raw + suffix, step=512)
    reader = c.TarReader(source, len(raw), 512)
    reader.header()
    reader.body(1, io.BytesIO())
    with pytest.raises(c.TarError):
        reader.finish(len(raw))
    assert source.raw.tell() == len(raw) + 1
    assert source.requests[-1] == 1


def test_budgets_count_headers_padding_and_footer_before_overrun():
    c = codec()
    raw = archive([("a", b"x")])
    source = ShortSource(raw, step=512)
    reader = c.TarReader(source, 511, 512)
    with pytest.raises(c.TarError):
        reader.header()
    assert source.raw.tell() == 0
    source = ShortSource(raw, step=512)
    reader = c.TarReader(source, 1023, 512)
    reader.header()
    with pytest.raises(c.TarError):
        reader.body(1, io.BytesIO())
    assert source.raw.tell() == 512
    sink = io.BytesIO()
    writer = c.TarWriter(sink, 10240, 10240, 512)
    with pytest.raises(c.TarError):
        writer.add("a", 9216, [])
    assert sink.tell() == 0


def test_total_size_mismatch_and_operations_after_finish_rejected():
    c = codec()
    writer = c.TarWriter(io.BytesIO(), 20480, 20480, 512)
    with pytest.raises(c.TarError):
        writer.finish()
    writer = c.TarWriter(io.BytesIO(), 10240, 10240, 512)
    assert writer.finish() == 10240
    with pytest.raises(c.TarError):
        writer.finish()
    with pytest.raises(c.TarError):
        writer.add("a", 0, [])
    raw = archive([])
    reader = c.TarReader(io.BytesIO(raw), len(raw), 512)
    with pytest.raises(c.TarError):
        reader.finish(20480)
    reader = c.TarReader(io.BytesIO(raw), len(raw), 512)
    assert reader.finish(len(raw)) == len(raw)
    with pytest.raises(c.TarError):
        reader.finish(len(raw))
    with pytest.raises(c.TarError):
        reader.header()
