"""Explicit clonefile copies: no fallback, hardlinks or source mutation."""
import errno
import importlib.util
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def builder():
    spec = importlib.util.spec_from_file_location("cow_builder", ROOT / "scripts/packaging/build_pi_macos.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree(tmp_path):
    src = tmp_path / "source"
    (src / "sub").mkdir(parents=True)
    (src / "sub/data").write_bytes(b"immutable source bytes")
    (src / "alias").symlink_to("sub/data")
    (src / "sub/data").chmod(0o640)
    os.utime(src / "sub/data", ns=(1700000000000000000, 1700000000000000000))
    return src


def test_default_mode_keeps_copytree_and_bytecode_filter(builder, tmp_path, monkeypatch):
    src = tree(tmp_path)
    (src / "__pycache__").mkdir()
    (src / "__pycache__/ignored.pyc").write_bytes(b"unused")
    monkeypatch.setattr(builder, "clone_regular_file", lambda *args: pytest.fail("default must not clone"), raising=False)
    dst = tmp_path / "default"
    assert builder.copy_runtime_tree(src, dst, symlinks=True, ignore_bytecode=True) == "copytree"
    assert (dst / "alias").is_symlink()
    assert (dst / "alias").read_bytes() == (src / "sub/data").read_bytes()
    assert not (dst / "__pycache__").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="actual Darwin clonefile required")
def test_actual_clone_has_independent_inodes_and_preserved_source(builder, tmp_path):
    src = tree(tmp_path)
    original = (src / "sub/data").read_bytes()
    before = (src / "sub/data").stat()
    dst = tmp_path / "cloned"
    assert builder.copy_runtime_tree(src, dst, copy_on_write=True, symlinks=True) == "clonefile"
    copied = (dst / "sub/data").stat()
    assert copied.st_ino != before.st_ino and copied.st_dev == before.st_dev
    assert copied.st_nlink == before.st_nlink == 1
    assert copied.st_mtime_ns == before.st_mtime_ns
    assert copied.st_mode == before.st_mode
    assert (dst / "alias").readlink() == Path("sub/data")
    (dst / "sub/data").write_bytes(b"changed destination only")
    assert (src / "sub/data").read_bytes() == original
    assert (src / "sub/data").stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("error", [errno.ENOTSUP, errno.EXDEV])
def test_optin_clone_failure_never_falls_back(builder, tmp_path, monkeypatch, error):
    src = tree(tmp_path)
    def fail(*args):
        raise OSError(error, "synthetic clone failure")
    monkeypatch.setattr(builder, "clone_regular_file", fail, raising=False)
    monkeypatch.setattr(builder.shutil, "copytree", lambda *a, **k: pytest.fail("copy fallback forbidden"))
    monkeypatch.setattr(builder.shutil, "copy2", lambda *a, **k: pytest.fail("copy fallback forbidden"))
    with pytest.raises(OSError, match="synthetic clone failure"):
        builder.copy_runtime_tree(src, tmp_path / "failed", copy_on_write=True, symlinks=True)
    assert (src / "sub/data").read_bytes() == b"immutable source bytes"


@pytest.mark.parametrize("kind", ["hardlink", "absolute_link", "directory_link", "source_alias"])
def test_rejects_unsafe_source_before_cloning(builder, tmp_path, monkeypatch, kind):
    src = tree(tmp_path)
    if kind == "hardlink":
        os.link(src / "sub/data", src / "hardlink")
    elif kind == "absolute_link":
        (src / "bad").symlink_to(src / "sub/data")
    elif kind == "directory_link":
        (src / "bad").symlink_to("sub", target_is_directory=True)
    else:
        alias = tmp_path / "source-alias"
        alias.symlink_to(src, target_is_directory=True)
        src = alias
    monkeypatch.setattr(builder, "clone_regular_file", lambda *a: pytest.fail("invalid source cloned"), raising=False)
    with pytest.raises(ValueError, match="link|regular|directory"):
        builder.copy_runtime_tree(src, tmp_path / "unsafe", copy_on_write=True, symlinks=True)
    assert not (tmp_path / "unsafe").exists()


@pytest.mark.parametrize("kind", ["existing", "parent_alias", "overlap"])
def test_rejects_nonfresh_or_aliased_destination(builder, tmp_path, monkeypatch, kind):
    src = tree(tmp_path)
    dst = tmp_path / "dest"
    if kind == "existing":
        dst.mkdir()
    elif kind == "parent_alias":
        parent = tmp_path / "alias-parent"
        parent.symlink_to(tmp_path, target_is_directory=True)
        dst = parent / "dest"
    else:
        dst = src / "dest"
    monkeypatch.setattr(builder, "clone_regular_file", lambda *a: pytest.fail("invalid destination cloned"), raising=False)
    with pytest.raises((ValueError, FileExistsError), match="exist|link|overlap"):
        builder.copy_runtime_tree(src, dst, copy_on_write=True, symlinks=True)


def test_source_changes_during_clone_are_detected(builder, tmp_path, monkeypatch):
    src = tree(tmp_path)
    def mutate(source, destination):
        destination.write_bytes(source.read_bytes())
        source.write_bytes(b"changed source")
    monkeypatch.setattr(builder, "clone_regular_file", mutate, raising=False)
    with pytest.raises(ValueError, match="source|Source"):
        builder.copy_runtime_tree(src, tmp_path / "changed", copy_on_write=True, symlinks=True)


def test_budget_keeps_extra_200_mib_and_fails_before_writing(builder, tmp_path, monkeypatch):
    remaining = 250 * 1024**2
    usage = type(builder.shutil.disk_usage(tmp_path))(0, 0, remaining + 200 * 1024**2 - 1)
    monkeypatch.setattr(builder.shutil, "disk_usage", lambda p: usage)
    with pytest.raises(OSError, match="space|budget"):
        builder.require_copy_space(tmp_path, remaining)


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_copy_mode_is_explicit_and_defaults_to_normal(builder, tmp_path, monkeypatch, enabled):
    seen = []
    monkeypatch.setattr(builder, "build", lambda *args, **kwargs: seen.append(kwargs))
    args = ["build_pi_macos.py", "--inputs", str(tmp_path), "--pi-root", str(tmp_path), "--output", str(tmp_path / "out"), "--version", "38"]
    if enabled:
        args.append("--copy-on-write")
    monkeypatch.setattr(sys, "argv", args)
    builder.main()
    assert seen == [{"copy_on_write": enabled, "version": 38}]
