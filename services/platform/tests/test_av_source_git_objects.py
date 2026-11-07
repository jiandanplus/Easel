"""Offline tests using actual Git object framing, without Git or a database."""
import hashlib
import importlib
import json
from dataclasses import replace

import pytest


def api():
    try:
        return importlib.import_module('services.platform.scripts.av_sources.git_objects')
    except ModuleNotFoundError:
        pytest.fail('Git raw object verifier has not been implemented')


class Objects:
    def __init__(self):
        self.objects = {}
        self.calls = []

    def add(self, kind, raw):
        oid = hashlib.sha1(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        self.objects[oid] = (kind, raw)
        return oid

    def tree(self, entries):
        return self.add('tree', b''.join(mode.encode() + b' ' + name + b'\0' + bytes.fromhex(oid)
                                       for mode, name, oid in entries))

    def commit(self, tree, suffix=b''):
        raw = (b'tree ' + tree.encode() + b'\nauthor A <a@b> 1234567890 +0530\n'
               b'committer B <b@c> 1234567891 -0730\n' + suffix + b'\nmessage\n\xff\n')
        return self.add('commit', raw)

    def read(self, oid):
        self.calls.append(oid)
        return self.objects[oid]


def simple():
    obj = Objects()
    blob = obj.add('blob', b'hello\n')
    tree = obj.tree([('100644', b'file', blob)])
    return obj, blob, tree, obj.commit(tree)


def test_complete_manifest_preserves_identity_and_special_entries():
    m = api()
    obj = Objects()
    blob = obj.add('blob', b'hello\n')
    link = obj.add('blob', b'../../outside')
    subtree = obj.tree([('100755', b'run', blob)])
    gitlink = 'ab' * 20
    tree = obj.tree([('100644', b'a.c', blob), ('40000', b'a', subtree),
                     ('120000', b'link', link), ('160000', b'sub', gitlink)])
    commit = obj.commit(tree)
    result = m.verify_commit_tree(commit, obj.read)
    assert result['commit_oid'] == commit
    assert result['tree_oid'] == tree
    assert result['commit_sha256'] == hashlib.sha256(obj.objects[commit][1]).hexdigest()
    entries = {e['path']: e for e in result['entries']}
    assert entries['a']['kind'] == 'tree'
    assert entries['a/run'] == {'path': 'a/run', 'mode': '100755', 'kind': 'blob',
                                'oid': blob, 'size': 6, 'sha256': hashlib.sha256(b'hello\n').hexdigest()}
    assert entries['link']['kind'] == 'symlink'
    assert entries['link']['sha256'] == hashlib.sha256(b'../../outside').hexdigest()
    assert result['gitlinks'] == [{'path': 'sub', 'mode': '160000', 'oid': gitlink}]
    assert gitlink not in obj.calls
    json.dumps(result)


@pytest.mark.parametrize('corruption', ['blob', 'tree', 'commit', 'type', 'missing'])
def test_rejects_corrupted_identity_chain(corruption):
    m = api()
    obj, blob, tree, commit = simple()
    if corruption == 'type':
        obj.objects[blob] = ('tree', b'hello\n')
    elif corruption == 'missing':
        del obj.objects[blob]
    else:
        oid = {'blob': blob, 'tree': tree, 'commit': commit}[corruption]
        kind, raw = obj.objects[oid]
        obj.objects[oid] = (kind, raw + b'!')
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(commit, obj.read)


@pytest.mark.parametrize('raw', [b'100644 x\0' + b'\1' * 19, b'100644 x', b'0100644 x\0' + b'\1' * 20,
                                  b'100664 x\0' + b'\1' * 20, b'040000 x\0' + b'\1' * 20])
def test_rejects_noncanonical_or_truncated_tree(raw):
    m = api()
    obj = Objects()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(obj.commit(obj.add('tree', raw)), obj.read)


@pytest.mark.parametrize('names', [[b'a', b'a'], [b'b', b'a'], [b'../x'], [b'/x'], [b'.git'],
                                  [b'..'], [b'.'], [b'a\\b'], [b'C:'], [b'\xff'],
                                  ['é'.encode(), 'e\u0301'.encode()], [b'A', b'a']])
def test_rejects_unsafe_or_colliding_paths(names):
    m = api()
    obj = Objects()
    blob = obj.add('blob', b'x')
    tree = obj.tree([('100644', name, blob) for name in names])
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(obj.commit(tree), obj.read)


def test_git_directory_suffix_sort_is_required():
    m = api()
    obj = Objects()
    tree = obj.tree([('40000', b'a', obj.tree([])), ('100644', b'a.c', obj.add('blob', b'x'))])
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(obj.commit(tree), obj.read)


@pytest.mark.parametrize('suffix', [b'tree ' + b'a' * 40 + b'\n', b'badheader\n', b' continuation\n'])
def test_invalid_commit_headers(suffix):
    m = api()
    obj = Objects()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(obj.commit(obj.tree([]), suffix), obj.read)


@pytest.mark.parametrize('raw', [b'tree ' + b'a' * 40 + b'\n', b'\nmsg',
                                  b'tree ' + b'A' * 40 + b'\n\nmsg', b'tree ' + b'a' * 40 + b'\0\n\nmsg'])
def test_incomplete_commit(raw):
    m = api()
    obj = Objects()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(obj.add('commit', raw), obj.read)


@pytest.mark.parametrize('field,value', [('max_members', 0), ('max_blob_bytes', 5),
                                        ('max_total_bytes', 5), ('max_metadata_bytes', 10),
                                        ('max_object_bytes', 10)])
def test_budgets(field, value):
    m = api()
    obj, _, _, commit = simple()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(commit, obj.read, limits=replace(m.Limits(), **{field: value}))


def test_depth_limit_and_repeated_blob_counting():
    m = api()
    obj = Objects()
    blob = obj.add('blob', b'xxxx')
    subtree = obj.tree([('100644', b'a', blob), ('100644', b'b', blob)])
    root = obj.tree([('40000', b'dir', subtree)])
    commit = obj.commit(root)
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(commit, obj.read, limits=replace(m.Limits(), max_depth=1))
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(commit, obj.read, limits=replace(m.Limits(), max_total_bytes=7))


def test_wrong_expected_type_even_when_hash_valid():
    m = api()
    obj = Objects()
    tree = obj.tree([('100644', b'x', obj.tree([]))])
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(obj.commit(tree), obj.read)


def test_valid_commit_extra_headers_and_binary_message():
    m = api()
    obj = Objects()
    tree = obj.tree([])
    commit = obj.commit(tree, b'gpgsig -----BEGIN PGP SIGNATURE-----\n data\n -----END PGP SIGNATURE-----\n')
    assert m.verify_commit_tree(commit, obj.read)['commit_oid'] == commit


@pytest.mark.parametrize('headers', [
    b'committer B <b@c> 1 +0000\nauthor A <a@b> 1 +0000',
    b'author A <a@b> 1 +0000\nparent ' + b'a' * 40 + b'\ncommitter B <b@c> 1 +0000',
    b'parent ' + b'a' * 40 + b'\nparent ' + b'a' * 40 + b'\nauthor A <a@b> 1 +0000\ncommitter B <b@c> 1 +0000',
])
def test_noncanonical_required_header_order(headers):
    m = api()
    obj = Objects()
    raw = b'tree ' + obj.tree([]).encode() + b'\n' + headers + b'\n\nmessage'
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(obj.add('commit', raw), obj.read)


@pytest.mark.parametrize('result', [None, ['blob', b'x'], ('blob', bytearray(b'x')), ('tree', b'x'), ('blob',)])
def test_loader_contract_rejected(result):
    m = api()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree('a' * 40, lambda oid: result)


@pytest.mark.parametrize('field', ['max_loaded_bytes', 'max_path_bytes'])
def test_additional_budgets(field):
    m = api()
    obj, _, _, commit = simple()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(commit, obj.read, limits=replace(m.Limits(), **{field: 0}))


def test_exact_budget_boundaries_and_valid_parent_reference():
    m = api()
    obj, blob, tree, _ = simple()
    raw = (b'tree ' + tree.encode() + b'\nparent ' + b'a' * 40 +
           b'\nauthor A <a@b> 1 +0000\ncommitter B <b@c> 1 +0000\n\nmessage')
    commit = obj.add('commit', raw)
    total = sum(len(obj.objects[oid][1]) for oid in (commit, tree, blob))
    result = m.verify_commit_tree(commit, obj.read, limits=replace(
        m.Limits(), max_members=1, max_depth=1, max_blob_bytes=6,
        max_total_bytes=6, max_loaded_bytes=total, max_path_bytes=4))
    assert result['counts'] == {'members': 1, 'content_bytes': 6, 'loaded_bytes': total}
    assert 'a' * 40 not in obj.calls


@pytest.mark.parametrize('oid', ['A' * 40, 'a' * 39, 'a' * 41, None, 123])
def test_invalid_commit_oid(oid):
    m = api()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree(oid, lambda oid: pytest.fail('must not call loader'))


@pytest.mark.parametrize('changes', [{'max_depth': 129}, {'max_members': -1}, {'max_blob_bytes': True}])
def test_invalid_limits(changes):
    m = api()
    with pytest.raises(m.GitObjectError):
        m.verify_commit_tree('a' * 40, lambda oid: pytest.fail('must not call loader'),
                             limits=replace(m.Limits(), **changes))


@pytest.mark.parametrize('mode', ['40000', '100644', '100755', '120000', '160000'])
def test_rejects_null_tree_entry_oid_before_loading(mode):
    m = api()
    obj = Objects()
    null = '0' * 40
    tree = obj.tree([(mode, b'entry', null)])
    with pytest.raises(m.GitObjectError, match='null'):
        m.verify_commit_tree(obj.commit(tree), obj.read)
    assert null not in obj.calls


def test_rejects_null_target_commit_before_loading():
    m = api()
    with pytest.raises(m.GitObjectError, match='null'):
        m.verify_commit_tree('0' * 40, lambda oid: pytest.fail('must not load null OID'))


@pytest.mark.parametrize('header', [b'tree', b'parent'])
def test_rejects_null_commit_reference_before_loading(header):
    m = api()
    obj = Objects()
    null = '0' * 40
    tree = null if header == b'tree' else obj.tree([])
    parent = b'parent ' + null.encode() + b'\n' if header == b'parent' else b''
    raw = (b'tree ' + tree.encode() + b'\n' + parent +
           b'author A <a@b> 0 +0000\ncommitter B <b@c> 0 +0000\n\nmessage')
    with pytest.raises(m.GitObjectError, match='reference'):
        m.verify_commit_tree(obj.add('commit', raw), obj.read)
    assert null not in obj.calls


@pytest.mark.parametrize('identity', [b'author', b'committer'])
@pytest.mark.parametrize('epoch', [b'-1', b'-0'])
def test_rejects_negative_identity_epoch(identity, epoch):
    m = api()
    obj = Objects()
    author = epoch if identity == b'author' else b'0'
    committer = epoch if identity == b'committer' else b'0'
    raw = (b'tree ' + obj.tree([]).encode() + b'\nauthor A <a@b> ' + author +
           b' +0000\ncommitter B <b@c> ' + committer + b' -0730\n\nmessage')
    with pytest.raises(m.GitObjectError, match='timestamp'):
        m.verify_commit_tree(obj.add('commit', raw), obj.read)


def test_accepts_zero_identity_epoch_preserving_raw_commit():
    m = api()
    obj = Objects()
    raw = (b'tree ' + obj.tree([]).encode() +
           b'\nauthor A <a@b> 0 +0530\ncommitter B <b@c> 0 -0730\n\nmessage\n')
    commit = obj.add('commit', raw)
    result = m.verify_commit_tree(commit, obj.read)
    assert result['commit_sha256'] == hashlib.sha256(raw).hexdigest()
