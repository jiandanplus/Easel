"""Synthetic offline archive security behavior."""
import hashlib
import io
import os
import tarfile
from dataclasses import replace

import pytest

from services.platform.scripts.av_sources import safe_archive as av


def make(tmp_path, entries=None, compression=""):
    p = tmp_path / "source.tar"
    with tarfile.open(p, "w" + compression, format=tarfile.USTAR_FORMAT) as t:
        for name, data, kind, mode in entries or [("src", b"", tarfile.DIRTYPE, 0o755), ("src/a", b"hello", tarfile.REGTYPE, 0o755)]:
            i = tarfile.TarInfo(name); i.type = kind; i.mode = mode
            i.size = len(data) if kind == tarfile.REGTYPE else 0
            i.linkname = "outside" if kind in (tarfile.LNKTYPE, tarfile.SYMTYPE) else ""
            t.addfile(i, io.BytesIO(data) if i.size else None)
    return p


def pins(p):
    b = p.read_bytes()
    return dict(archive_size=len(b), sha256=hashlib.sha256(b).hexdigest(), limits=av.ArchiveLimits())


@pytest.mark.parametrize("compression", ["", ":gz", ":bz2", ":xz"])
def test_roundtrip(tmp_path, compression):
    p = make(tmp_path, compression=compression); before = p.read_bytes()
    m = av.inspect_archive(p, **pins(p))
    assert m["file_count"] == 1 and m["directory_count"] == 1
    assert m["entries"][1]["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert m["entries"][1]["mode"] == 0o755
    d = tmp_path / "out"
    assert av.extract_archive(p, d, **pins(p)) == m
    assert (d / "src/a").read_bytes() == b"hello"
    assert (d.stat().st_mode & 0o777) == 0o700
    assert ((d / "src/a").stat().st_mode & 0o777) == 0o600
    assert p.read_bytes() == before


@pytest.mark.parametrize("field,value", [("archive_size", 1), ("sha256", "0" * 64)])
def test_bad_pin(tmp_path, field, value):
    p = make(tmp_path); args = pins(p); args[field] = value
    with pytest.raises(av.ArchiveError): av.extract_archive(p, tmp_path / "out", **args)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("name", ["/abs", "../escape", "src/../escape", "a//b", "a/./b"])
def test_bad_paths(tmp_path, name):
    p = make(tmp_path, [(name, b"x", tarfile.REGTYPE, 0o644)])
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


@pytest.mark.parametrize("kind", [tarfile.LNKTYPE, tarfile.SYMTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME])
def test_special_members(tmp_path, kind):
    p = make(tmp_path, [("src/a", b"", kind, 0o644)])
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


@pytest.mark.parametrize("mode", [0o4644, 0o2644])
def test_privileged_mode(tmp_path, mode):
    p = make(tmp_path, [("a", b"x", tarfile.REGTYPE, mode)])
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


@pytest.mark.parametrize("entries", [[("a", b"x", tarfile.REGTYPE, 0o644)] * 2, [("a", b"x", tarfile.REGTYPE, 0o644), ("a/b", b"x", tarfile.REGTYPE, 0o644)]])
def test_conflicts(tmp_path, entries):
    p = make(tmp_path, entries)
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


@pytest.mark.parametrize("limit,value", [("max_archive_bytes", 10), ("max_expanded_bytes", 1000), ("max_file_bytes", 4), ("max_total_file_bytes", 4), ("max_members", 1), ("max_depth", 1), ("max_metadata_bytes", 512)])
def test_limits(tmp_path, limit, value):
    p = make(tmp_path, compression=":gz"); args = pins(p)
    args["limits"] = replace(args["limits"], **{limit: value})
    with pytest.raises(av.ArchiveError): av.extract_archive(p, tmp_path / "out", **args)
    assert not (tmp_path / "out").exists()


def test_truncated_preserves_source(tmp_path):
    p = make(tmp_path); p.write_bytes(p.read_bytes()[:700]); before = p.read_bytes()
    with pytest.raises(av.ArchiveError): av.extract_archive(p, tmp_path / "out", **pins(p))
    assert p.read_bytes() == before and not (tmp_path / "out").exists()


def test_existing_and_symlinks(tmp_path):
    p = make(tmp_path); args = pins(p); d = tmp_path / "out"; d.mkdir()
    (d / "keep").write_text("keep")
    with pytest.raises(av.ArchiveError): av.extract_archive(p, d, **args)
    assert (d / "keep").read_text() == "keep"
    s = tmp_path / "link"; s.symlink_to(p)
    with pytest.raises(av.ArchiveError): av.inspect_archive(s, **args)
    parent = tmp_path / "parent"; parent.symlink_to(d, target_is_directory=True)
    with pytest.raises(av.ArchiveError): av.extract_archive(p, parent / "new", **args)
    with pytest.raises(av.ArchiveError): av.extract_archive(p, parent, **args)


def test_source_changes_before_materialization(tmp_path, monkeypatch):
    p = make(tmp_path); args = pins(p)
    original = av._validate_source
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        if calls == 2: p.write_bytes(b"changed")
        return original(path)
    monkeypatch.setattr(av, "_validate_source", changed)
    with pytest.raises(av.ArchiveError): av.extract_archive(p, tmp_path / "out", **args)
    assert not (tmp_path / "out").exists()


def test_normalized_collision(tmp_path):
    p = make(tmp_path, [("A", b"x", tarfile.REGTYPE, 0o644), ("a", b"x", tarfile.REGTYPE, 0o644)])
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


def test_source_parent_symlink(tmp_path):
    real = tmp_path / "real"; real.mkdir(); p = make(real)
    link = tmp_path / "linked"; link.symlink_to(real, target_is_directory=True)
    with pytest.raises(av.ArchiveError): av.inspect_archive(link / p.name, **pins(p))


def test_embedded_nul_name(tmp_path):
    p = make(tmp_path); data = bytearray(p.read_bytes())
    data[:8] = b"a\0hidden"
    data[148:156] = b"        "
    data[148:156] = ("%06o\0 " % sum(data[:512])).encode()
    p.write_bytes(data)
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


def test_nonregular_source(tmp_path):
    directory = tmp_path / "directory"; directory.mkdir()
    with pytest.raises(av.ArchiveError):
        av.inspect_archive(directory, archive_size=0, sha256="0" * 64, limits=av.ArchiveLimits())


def test_truncated_compression(tmp_path):
    p = make(tmp_path, compression=":gz"); p.write_bytes(p.read_bytes()[:-5])
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


# Independent-review regressions: each uses only local synthetic data.
def test_private_destination_parent_required(tmp_path):
    p = make(tmp_path)
    shared = tmp_path / "shared"; shared.mkdir(mode=0o755)
    with pytest.raises(av.ArchiveError):
        av.extract_archive(p, shared / "out", **pins(p))
    assert not (shared / "out").exists()


@pytest.mark.parametrize("mode", [0o755, 0o700])
def test_root_ordinary_directory_replacement_rejected(tmp_path, monkeypatch, mode):
    p = make(tmp_path)
    other = tmp_path / "other"; other.mkdir(mode=mode)
    (other / "sentinel").write_bytes(b"keep")
    original_open = av.os.open
    swapped = False
    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "out" and flags & os.O_DIRECTORY and not swapped:
            swapped = True
            (tmp_path / "out").rename(tmp_path / "parked")
            other.rename(tmp_path / "out")
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(av.os, "open", swap)
    with pytest.raises(av.ArchiveError):
        av.extract_archive(p, tmp_path / "out", **pins(p))
    assert swapped
    assert (tmp_path / "out/sentinel").read_bytes() == b"keep"
    assert not (tmp_path / "out/src").exists()
    assert list((tmp_path / "parked").iterdir()) == []


@pytest.mark.parametrize("field", ["comment", "name", "extra"])
def test_gzip_container_metadata_budget(tmp_path, field):
    import gzip
    raw = make(tmp_path).read_bytes()
    encoded = bytearray(gzip.compress(raw, mtime=0))
    extra = b"x" * 1025
    encoded[3] = {"comment": 16, "name": 8, "extra": 4}[field]
    optional = len(extra).to_bytes(2, "little") + extra if field == "extra" else extra + b"\0"
    p = tmp_path / "with-header.gz"; p.write_bytes(encoded[:10] + optional + encoded[10:])
    args = pins(p); args["limits"] = replace(args["limits"], max_metadata_bytes=1024)
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **args)


@pytest.mark.parametrize("budget,value", [("max_file_bytes", 1), ("max_total_file_bytes", 1), ("max_members", 0), ("max_metadata_bytes", 511)])
def test_tar_limits_stop_before_member_payload_is_spooled(tmp_path, monkeypatch, budget, value):
    p = make(tmp_path, [("payload", b"z" * 4096, tarfile.REGTYPE, 0o644)], compression=":gz")
    args = pins(p); args["limits"] = replace(args["limits"], **{budget: value})
    original_temp = av.tempfile.TemporaryFile
    written = []
    class CountWrites:
        def __init__(self, wrapped): self.wrapped = wrapped; self.count = 0; written.append(self)
        def write(self, data): self.count += len(data); return self.wrapped.write(data)
        def __getattr__(self, name): return getattr(self.wrapped, name)
    monkeypatch.setattr(av.tempfile, "TemporaryFile", lambda *a, **k: CountWrites(original_temp(*a, **k)))
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **args)
    assert len(written) == 2
    assert written[1].count <= 512, "tar quota was applied after member payload was expanded/spooled"
    assert all(t.wrapped.closed for t in written)


def test_original_source_symlink_dotdot_rejected(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    alias = tmp_path / "alias"; alias.symlink_to(real, target_is_directory=True)
    p = make(tmp_path)
    with pytest.raises(av.ArchiveError): av.inspect_archive(str(alias) + "/../source.tar", **pins(p))


def test_corrupt_deflate_archive_error_and_explicit_temp_cleanup(tmp_path, monkeypatch):
    p = tmp_path / "bad.gz"; p.write_bytes(bytes.fromhex("1f8b0800000000000003") + b"\x07" + bytes(8))
    original_temp = av.tempfile.TemporaryFile
    opened = []
    def capture(*a, **k):
        f = original_temp(*a, **k); opened.append(f); return f
    monkeypatch.setattr(av.tempfile, "TemporaryFile", capture)
    try:
        with pytest.raises(av.ArchiveError): av.extract_archive(p, tmp_path / "out", **pins(p))
    finally:
        assert all(f.closed for f in opened), "failure leaked an open temporary resource"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("compression", [":gz", ":bz2", ":xz"])
def test_compression_structure_shares_tar_metadata_budget(tmp_path, compression):
    p = make(tmp_path, [("a", b"x", tarfile.REGTYPE, 0o644)], compression=compression)
    args = pins(p); args["limits"] = replace(args["limits"], max_metadata_bytes=512)
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **args)


def test_destination_dotdot_cannot_hide_symlink(tmp_path):
    p = make(tmp_path)
    real = tmp_path / "real"; real.mkdir()
    alias = tmp_path / "alias"; alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(av.ArchiveError):
        av.extract_archive(p, str(alias) + "/../out", **pins(p))
    assert not (tmp_path / "out").exists()



def test_gnu_ordinary_header_roundtrip_without_prefix_interpretation(tmp_path):
    p = tmp_path / "gnu.tar"
    with tarfile.open(p, "w:xz", format=tarfile.GNU_FORMAT) as t:
        d = tarfile.TarInfo("ffmpeg-9.0.2/"); d.type = tarfile.DIRTYPE; t.addfile(d)
        f = tarfile.TarInfo("ffmpeg-9.0.2/a"); f.size = 2; t.addfile(f, io.BytesIO(b"ok"))
    m = av.extract_archive(p, tmp_path / "out", **pins(p))
    assert m["file_count"] == 1 and m["directory_count"] == 1
    assert (tmp_path / "out/ffmpeg-9.0.2/a").read_bytes() == b"ok"



def test_foreign_owner_parent_is_rejected_before_destination_creation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    p = make(tmp_path); parent_ino = tmp_path.stat().st_ino
    original_fstat = av.os.fstat
    def foreign(fd):
        s = original_fstat(fd)
        if s.st_ino == parent_ino:
            return SimpleNamespace(st_mode=s.st_mode, st_uid=s.st_uid + 1)
        return s
    monkeypatch.setattr(av.os, "fstat", foreign)
    with pytest.raises(av.ArchiveError, match="effective-UID-owned"):
        av.extract_archive(p, tmp_path / "out", **pins(p))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode", [0o700, 0o755])
def test_member_parent_ordinary_directory_replacement_rejected(tmp_path, monkeypatch, mode):
    p = make(tmp_path)
    other = tmp_path / "other"; other.mkdir(mode=mode)
    (other / "sentinel").write_bytes(b"keep")
    original_open = av.os.open; swapped = False
    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "src" and flags & os.O_DIRECTORY and not swapped:
            swapped = True
            (tmp_path / "out/src").rename(tmp_path / "parked")
            other.rename(tmp_path / "out/src")
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(av.os, "open", swap)
    with pytest.raises(av.ArchiveError): av.extract_archive(p, tmp_path / "out", **pins(p))
    assert swapped and (tmp_path / "out/src/sentinel").read_bytes() == b"keep"
    assert not (tmp_path / "out/src/a").exists()
    assert list((tmp_path / "parked").iterdir()) == []


@pytest.mark.parametrize("compression,module,factory", [
    (":gz", "zlib", "decompressobj"), (":bz2", "bz2", "BZ2Decompressor"), (":xz", "lzma", "LZMADecompressor")])
def test_file_quota_stops_actual_decoder_output_at_header(tmp_path, monkeypatch, compression, module, factory):
    p = make(tmp_path, [("payload", b"z" * 4096, tarfile.REGTYPE, 0o644)], compression=compression)
    args = pins(p); args["limits"] = replace(args["limits"], max_file_bytes=1)
    namespace = getattr(av, module); original = getattr(namespace, factory); emitted = []
    class Observe:
        def __init__(self, *a, **k): self.real = original(*a, **k)
        def decompress(self, *a, **k):
            data = self.real.decompress(*a, **k); emitted.append(len(data)); return data
        def __getattr__(self, name): return getattr(self.real, name)
    monkeypatch.setattr(namespace, factory, Observe)
    with pytest.raises(av.ArchiveError, match="file budget"): av.inspect_archive(p, **args)
    assert sum(emitted) == 512


@pytest.mark.parametrize("compression", [":gz", ":bz2", ":xz"])
def test_concatenated_empty_compression_stream_explicitly_rejected(tmp_path, compression):
    import bz2, gzip, lzma
    p = make(tmp_path, compression=compression)
    compressor = {":gz": gzip.compress, ":bz2": bz2.compress, ":xz": lzma.compress}[compression]
    p.write_bytes(p.read_bytes() + compressor(b""))
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **pins(p))


def test_gnu_sparse_layout_rejected_even_with_regular_type(tmp_path):
    p = make(tmp_path)
    data = bytearray(p.read_bytes()); data[257:265] = b"ustar  \0"; data[386] = 49
    data[148:156] = b"        "; data[148:156] = ("%06o\0 " % sum(data[:512])).encode()
    p.write_bytes(data)
    with pytest.raises(av.ArchiveError, match="GNU extended/sparse"): av.inspect_archive(p, **pins(p))



@pytest.mark.parametrize("budget,value", [("max_metadata_bytes", 18), ("max_members", 0)])
def test_exhausted_tar_header_budget_consumes_only_detection_byte(tmp_path, monkeypatch, budget, value):
    import gzip
    p = make(tmp_path)
    p.write_bytes(gzip.compress(p.read_bytes(), mtime=0))  # no optional gzip fields: 18 structure bytes
    args = pins(p); args["limits"] = replace(args["limits"], **{budget: value})
    original = av.zlib.decompressobj; emitted = []
    class Observe:
        def __init__(self, *a, **k): self.real = original(*a, **k)
        def decompress(self, *a, **k):
            data = self.real.decompress(*a, **k); emitted.append(len(data)); return data
        def __getattr__(self, name): return getattr(self.real, name)
    monkeypatch.setattr(av.zlib, "decompressobj", Observe)
    with pytest.raises(av.ArchiveError): av.inspect_archive(p, **args)
    assert sum(emitted) <= 1, "header budget must stop before reading another complete header"



def test_default_metadata_accepts_actual_member_count_shape(tmp_path):
    p = tmp_path / "member-shape.tar.xz"
    with tarfile.open(p, "w:xz", format=tarfile.GNU_FORMAT) as t:
        for n in range(126):
            d = tarfile.TarInfo("src/d%03d/" % n); d.type = tarfile.DIRTYPE; t.addfile(d)
        for n in range(10399):
            f = tarfile.TarInfo("src/f%05d" % n); f.size = 1; t.addfile(f, io.BytesIO(b"x"))
    m = av.inspect_archive(p, **pins(p))
    assert (m["file_count"], m["directory_count"]) == (10399, 126)
    args = pins(p); args["limits"] = replace(args["limits"], max_metadata_bytes=4 * 1024 * 1024)
    with pytest.raises(av.ArchiveError, match="metadata budget"): av.inspect_archive(p, **args)


@pytest.mark.parametrize("budget", [4 * 1024 * 1024, 8 * 1024 * 1024])
def test_gzip_large_comment_rejected_at_explicit_old_and_new_default_metadata_boundary(tmp_path, budget):
    import gzip
    raw = make(tmp_path).read_bytes(); encoded = bytearray(gzip.compress(raw, mtime=0)); encoded[3] = 16
    p = tmp_path / "large-comment.gz"
    p.write_bytes(encoded[:10] + b"x" * (budget + 1) + b"\0" + encoded[10:])
    args = pins(p)
    if budget == 4 * 1024 * 1024:
        args["limits"] = replace(args["limits"], max_metadata_bytes=budget)
    else:
        assert args["limits"].max_metadata_bytes == budget
    with pytest.raises(av.ArchiveError, match="metadata budget"): av.inspect_archive(p, **args)
