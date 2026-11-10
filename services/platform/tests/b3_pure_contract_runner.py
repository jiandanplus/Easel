"""Standalone stdlib runner; ordinary test imports remain usable by pytest."""
import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/platform_service/media_backup_b3"
PURE_NAMES = ("platform_service.media_backup_b3.contracts",
              "platform_service.media_backup_b3.compare")
TEST_NAMES = ("test_media_backup_b3_contract", "test_media_backup_b3_compare")
PARENT_NAMES = ("platform_service", "platform_service.media_backup_b3")
LOADS = tuple(zip(PURE_NAMES, (PACKAGE / "contracts.py", PACKAGE / "compare.py")))
LOADS += tuple((name, ROOT / "tests" / (name + ".py")) for name in TEST_NAMES)
FILES = tuple(path for _, path in LOADS) + (Path(__file__).resolve(),)


def validate_source(source, filename):
    """Allow only stdlib and exact pure/test-helper dependency names."""
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                assert (filename == "compare.py" and node.level == 1
                        and node.module == "contracts"), (filename, node.module)
                continue
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert (name in PURE_NAMES or name == TEST_NAMES[0]
                    or name.split(".")[0] in sys.stdlib_module_names), (filename, name)


def assert_clean_modules(modules):
    """Never replace existing product, B2, legacy alias, or test modules."""
    conflicts = [name for name in modules
                 if name == "platform_service" or name.startswith("platform_service.")
                 or name in TEST_NAMES or name.startswith(("_b3_", "_b2_"))]
    if conflicts:
        raise RuntimeError("standalone runner requires clean namespaces: " + repr(sorted(conflicts)))


def main():
    assert_clean_modules(sys.modules)
    assert all(path.is_file() for path in FILES), "missing B3 pure contract files"
    for path in FILES:
        validate_source(path.read_text(), path.name)
    # Empty paths prevent discovery or execution of actual product __init__ files.
    for name in PARENT_NAMES:
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package
    sys.modules[PARENT_NAMES[0]].media_backup_b3 = sys.modules[PARENT_NAMES[1]]
    loaded = []
    for name, path in LOADS:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        if name in PURE_NAMES:
            setattr(sys.modules[PARENT_NAMES[1]], name.rsplit(".", 1)[1], module)
        loaded.append(module)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in loaded[2:])
    assert suite.countTestCases() == 32
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
