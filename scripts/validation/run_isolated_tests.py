"""Run source tests with fresh writable data and tracked, public seed resources only."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--pytest-path')
args, pytest_args = parser.parse_known_args()
for key in list(os.environ):
    if any(x in key.upper() for x in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CODEX', 'OPENAI', 'ANTHROPIC', 'EASEL')):
        os.environ.pop(key, None)
sys.path.insert(0, str(ROOT))
from scripts.packaging.build_macos import selected
with tempfile.TemporaryDirectory(prefix='easel-isolated-tests-') as temporary:
    root = Path(temporary)
    workspace, home = root/'workspace', root/'home'
    workspace.mkdir(); home.mkdir()
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    for relative in tracked:
        if not relative or relative.split('/')[0] not in {'skills', 'scripts', 'agents'} or not selected(relative): continue
        source = ROOT/relative
        if source.is_symlink() or not source.is_file(): continue
        destination = workspace/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    os.environ.update(HOME=str(home), EASEL_WORKSPACE_ROOT=str(workspace), EASEL_RESOURCE_ROOT=str(ROOT), HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9', NO_PROXY='127.0.0.1,localhost')
    sys.path.insert(0, str(ROOT))
    if args.pytest_path: sys.path.append(args.pytest_path)
    import pytest
    raise SystemExit(pytest.main(pytest_args))
