import hashlib, os
import pytest
from platform_service.media_backup_b2.contracts import B2Error

def test_bundle_roundtrip_and_forbidden_paths():
    from platform_service.media_backup_b2.bundles import encode_bundle, decode_bundle, config_item
    item=config_item('maintenance', b'private synthetic configuration', 501, 20, 0o600)
    raw=encode_bundle('central',[item], source_facts={'synthetic':True})
    assert decode_bundle(raw,'central')['items']==[item]
    with pytest.raises(B2Error): config_item('/etc/passwd',b'x',501,20,0o600)
    with pytest.raises(B2Error): decode_bundle(raw+b' ','central')
    with pytest.raises(B2Error): decode_bundle(raw.replace(b'"uid":501',b'"uid":true'),'central')
    with pytest.raises(B2Error): decode_bundle(raw.replace(b'"kind":"central"',b'"kind":"central","kind":"central"'),'central')

def test_original_fd_drift(tmp_path):
    from platform_service.media_backup_b2.bundles import ConfigInputs
    path=tmp_path/'maintenance';path.write_bytes(b'one');path.chmod(0o600)
    root=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with ConfigInputs(root,{'central':{'maintenance':'maintenance'},'identity':{},'media':{}},owner=(os.getuid(),os.getgid())) as inputs:
            assert inputs.items('central')[0]['size']==3
            path.write_bytes(b'two')
            with pytest.raises(B2Error): inputs.verify()
    finally:os.close(root)

def test_output_same_bytes_new_path_inode_rejects(tmp_path):
    from platform_service.media_backup_b2.capture import fixed_identity_inventory,verify_fixed_identities
    root=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    fd=os.open('central.dump',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600,dir_fd=root)
    try:
        os.write(fd,b'actual descriptor bytes, no PG proof implied')
        files={'db/central.dump':fd};before=fixed_identity_inventory(root,files)
        (tmp_path/'central.dump').rename(tmp_path/'original.dump')
        replacement=tmp_path/'central.dump';replacement.write_bytes(os.pread(fd,1000,0));replacement.chmod(0o600)
        with pytest.raises(B2Error):verify_fixed_identities(root,files,before)
    finally:os.close(fd);os.close(root)

def test_missing_original_config_failure_is_sanitized(tmp_path):
    from platform_service.media_backup_b2.bundles import ConfigInputs
    tmp_path.chmod(0o700);root=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with pytest.raises(B2Error) as error:
            ConfigInputs(root,{'central':{'maintenance':'synthetic-do-not-expose'},'identity':{},'media':{}},owner=(os.getuid(),os.getgid()))
        assert str(error.value)=='invalid_backup'
    finally:os.close(root)
