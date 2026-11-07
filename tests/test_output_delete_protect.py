"""内容库删除不得清掉 _sessions 等系统顶层。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "web"))


from easel.routes import outputs as output_routes  # noqa: E402
import app as web  # noqa: E402


def test_underscore_sessions_and_profile_build_are_protected():
    root = web.OUTPUTS_DIR.resolve()
    assert output_routes._is_protected(root / "_sessions" / "abc.json") is True
    assert output_routes._is_protected(root / "_profile_build" / "job.json") is True
    assert output_routes._is_protected(root / "_login" / "xhs.json") is True
    assert output_routes._is_protected(root / "_debug" / "trace.log") is True
    assert output_routes._is_protected(root / "analytics" / "x.json") is True


def test_normal_project_dir_is_not_protected():
    root = web.OUTPUTS_DIR.resolve()
    assert output_routes._is_protected(root / "my-paper" / "note.md") is False
    assert output_routes._is_protected(root / "视频草稿" / "a.mp4") is False
