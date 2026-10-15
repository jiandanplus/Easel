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


def test_seed_does_not_overwrite_symlinked_user_file(tmp_path):
    resource, workspace=tmp_path/'app',tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('old')
    seed_workspace(resource,workspace)
    profile=workspace/'BIO'; profile.write_text('old')
    (workspace/'skills/a').unlink(); (workspace/'skills/a').symlink_to(profile)
    (resource/'seed/skills/a').write_text('new')
    seed_workspace(resource,workspace)
    assert profile.read_text()=='old'


def test_control_directory_symlink_rejected(tmp_path):
    resource,workspace=tmp_path/'app',tmp_path/'data'
    resource.mkdir(); workspace.mkdir(); outside=tmp_path/'outside'; outside.mkdir()
    (workspace/'.easel-portable').symlink_to(outside)
    with pytest.raises(ValueError): prepare_workspace(resource,workspace)
    assert list(outside.iterdir())==[]


@pytest.mark.parametrize("value", [{"skills/a": None}, {"junk": "bad"}, {"../escape": "0"*64}, {"/absolute": "0"*64}, {"skills/a\x00": "0"*64}, {"skills/a\\b": "0"*64}])
def test_invalid_seed_schema_preserves_bytes(tmp_path, value):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('new')
    state = workspace/'.easel-portable/seed.json'; state.parent.mkdir(parents=True)
    original = json.dumps(value); state.write_text(original)
    with pytest.raises(ValueError): seed_workspace(resource, workspace)
    assert state.read_text() == original
    assert not (workspace/'skills/a').exists()


def test_seed_interrupted_copy_preserves_destination(tmp_path, monkeypatch):
    import easel.portable as portable
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    source = resource/'seed/skills/a'; source.write_text('old')
    seed_workspace(resource, workspace); source.write_text('new')
    def broken(src, dst):
        Path(dst).write_text('partial'); raise OSError('disk full')
    monkeypatch.setattr(portable.shutil, 'copy2', broken)
    with pytest.raises(OSError): seed_workspace(resource, workspace)
    assert (workspace/'skills/a').read_text() == 'old'


def test_seed_recovers_after_metadata_save_failure(tmp_path, monkeypatch):
    import easel.portable as portable
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    source = resource/'seed/skills/a'; source.write_text('old')
    seed_workspace(resource, workspace); source.write_text('new')
    original = portable.atomic_json
    monkeypatch.setattr(portable, 'atomic_json', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError): seed_workspace(resource, workspace)
    monkeypatch.setattr(portable, 'atomic_json', original)
    seed_workspace(resource, workspace); source.write_text('newer')
    seed_workspace(resource, workspace)
    assert (workspace/'skills/a').read_text() == 'newer'


@pytest.mark.parametrize('name', ['seed.json', 'venv.json', 'instance.json', 'launcher.lock'])
@pytest.mark.parametrize('dangling', [False, True])
def test_individual_control_symlinks_rejected(tmp_path, name, dangling):
    from easel.portable import safe_state
    workspace = tmp_path/'data'; state = workspace/'.easel-portable'; state.mkdir(parents=True)
    outside = tmp_path/'foreign'
    if not dangling: outside.write_text('foreign')
    (state/name).symlink_to(outside)
    with pytest.raises(ValueError): safe_state(workspace)
    assert not outside.exists() if dangling else outside.read_text() == 'foreign'


def test_builder_rejects_extra_and_mismatched_payload(tmp_path):
    from scripts.packaging.build_macos import verify_payload
    payload = tmp_path/'payload'; payload.mkdir(); (payload/'a').write_text('ok')
    from easel.portable import digest
    records = {'a': digest(payload/'a')}
    verify_payload(payload, records)
    (payload/'extra.pth').write_text('unsafe')
    with pytest.raises(ValueError): verify_payload(payload, records)


def test_builder_metadata_uses_tracked_authority(tmp_path):
    from scripts.packaging.build_macos import verify_metadata
    authoritative, prepared = tmp_path/'tracked', tmp_path/'prepared'
    authoritative.mkdir(); prepared.mkdir()
    (authoritative/'pins').write_text('pinned'); (prepared/'pins').write_text('different')
    with pytest.raises(ValueError): verify_metadata(prepared, authoritative, ['pins'])


@pytest.mark.parametrize('suffix', ['', 'event: error\ndata: "failed"\n\n', 'event: done\ndata: {}\n\nevent: done\ndata: {}\n\n'])
def test_sse_proof_rejects_incomplete_or_error(suffix):
    from scripts.validation.verify_portable import assert_sse_success
    stream = 'event: token\ndata: "MARKER"\n\n' + suffix
    with pytest.raises(AssertionError): assert_sse_success(stream, 'MARKER')


def test_sse_proof_requires_marker_and_successful_done():
    from scripts.validation.verify_portable import assert_sse_success
    stream = 'event: token\ndata: "MARKER"\n\nevent: done\ndata: {}\n\n'
    assert assert_sse_success(stream, 'MARKER') == {'token': 1, 'done': 1}
    with pytest.raises(AssertionError): assert_sse_success(stream, 'OTHER')


def test_seed_existing_directory_is_preserved(tmp_path):
    resource, workspace = tmp_path/'app', tmp_path/'data'
    (resource/'seed/skills').mkdir(parents=True)
    (resource/'seed/skills/a').write_text('seed')
    (workspace/'skills/a').mkdir(parents=True)
    seed_workspace(resource, workspace)
    assert (workspace/'skills/a').is_dir()
