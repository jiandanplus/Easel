"""Built management assets have reproducible, bounded inventories."""
import importlib.util
import json
from pathlib import Path
import pytest


def module():
    path = Path(__file__).parents[1] / "scripts/build_console.py"
    spec = importlib.util.spec_from_file_location("console_builder_test", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def frontend(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("export default function App() { return null; }")
    (tmp_path / "package.json").write_text('{"name":"console"}')
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":3}')
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / ".env").write_text("secret-never-package")
    (tmp_path / "dist/assets").mkdir(parents=True)
    (tmp_path / "dist/index.html").write_text('<script src="/assets/index-abc.js"></script>')
    (tmp_path / "dist/assets/index-abc.js").write_text("console.log('bounded');")
    return tmp_path


def test_console_build_inventory_omits_env_and_is_repeatable(tmp_path):
    build = module(); root = frontend(tmp_path)
    one = build.write_receipt(root); two = build.write_receipt(root)
    assert one == two and one["schema_version"] == 1
    assert ".env" not in one["source"] and ".env" not in one["files"]
    assert set(one["files"]) == {"index.html", "assets/index-abc.js"}
    assert json.loads((root / "dist/console-assets.json").read_text()) == one


def test_console_build_rejects_unexpected_private_output(tmp_path):
    build = module(); root = frontend(tmp_path)
    (root / "dist/private.env").write_text("secret-never-package")
    with pytest.raises(ValueError):
        build.write_receipt(root)


def test_console_build_rejects_symlink_payload(tmp_path):
    build = module(); root = frontend(tmp_path)
    (root / "dist/assets/linked.js").symlink_to(root / "src/App.tsx")
    with pytest.raises(ValueError):
        build.write_receipt(root)
