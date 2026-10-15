import json
from pathlib import Path
import pytest
from easel.portable import seed_workspace, prepare_workspace, verify_inventory


def test_seed_preserves_user_changes_and_updates_untouched(tmp_path):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('one')
    seed_workspace(resource, workspace)
    (workspace/'skills/a').write_text('user')
    (resource/'seed/skills/a').write_text('two')
    seed_workspace(resource, workspace)
    assert (workspace/'skills/a').read_text() == 'user'
    (resource/'seed/skills/b').write_text('one')
    seed_workspace(resource, workspace)
    (resource/'seed/skills/b').write_text('two')
    seed_workspace(resource, workspace)
    assert (workspace/'skills/b').read_text() == 'two'


def test_overlap_and_foreign_venv_fail_before_seed(tmp_path):
    resource = tmp_path/'app'; resource.mkdir()
    with pytest.raises(ValueError): prepare_workspace(resource, resource/'data')
    workspace = tmp_path/'data'; workspace.mkdir(); (workspace/'.venv').mkdir()
    with pytest.raises(ValueError): prepare_workspace(resource, workspace)
    assert not (workspace/'.easel-portable').exists()


def test_malformed_seed_state_preserved(tmp_path):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    resource.mkdir(); (workspace/'.easel-portable').mkdir(parents=True)
    state = workspace/'.easel-portable/seed.json'; state.write_text('{bad')
    with pytest.raises(ValueError): seed_workspace(resource, workspace)
    assert state.read_text() == '{bad'


def test_inventory_rejects_tamper_and_escape(tmp_path):
    (tmp_path/'file').write_text('bad')
    (tmp_path/'build-inventory.json').write_text(json.dumps({'file':'0'*64}))
    with pytest.raises(ValueError): verify_inventory(tmp_path)
    (tmp_path/'build-inventory.json').write_text(json.dumps({'../outside':'0'*64}))
    with pytest.raises(ValueError): verify_inventory(tmp_path)


def test_builder_uses_resource_allowlist():
    from scripts.packaging.build_macos import selected
    assert selected('skills/shared/scripts/model_registry.py')
    assert selected('easel/portable.py')
    for path in ['profiles/private/BIO.md','.env','outputs/key.json','skills/a/.env','skills/a/.token_cache.json','skills/a/node_modules/a.js','scripts/validation/verify_codex_gateway.py','untracked-credentials.json']:
        assert not selected(path), path
