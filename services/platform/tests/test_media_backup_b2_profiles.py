import pytest
from platform_service.media_backup_b2.contracts import B2Error

def test_profiles_exact_separate_migration_and_table_inventory():
    from platform_service.media_backup_b2.profiles import PUBLIC_016, LOCAL_020, validate_inventory
    assert len(PUBLIC_016.migrations)==16 and len(LOCAL_020.migrations)==20
    assert 'media_work' not in PUBLIC_016.tables and 'media_work' in LOCAL_020.tables
    validate_inventory(PUBLIC_016,list(PUBLIC_016.migrations),list(PUBLIC_016.tables))
    with pytest.raises(B2Error):validate_inventory(PUBLIC_016,list(LOCAL_020.migrations),list(LOCAL_020.tables))
    with pytest.raises(B2Error):validate_inventory(PUBLIC_016,list(PUBLIC_016.migrations),list(PUBLIC_016.tables)+['unknown_writer'])

def test_logto_profile_is_exact_public_image_and_native_enums():
    from platform_service.media_backup_b2.profiles import LOGTO_145,validate_identity_inventory
    assert LOGTO_145.name=='logto-1.45.0' and len(LOGTO_145.tables)==79 and len(LOGTO_145.enums)==8
    assert LOGTO_145.image_sha256=='097cd47689546461edb4637fb6eee60b9ab6d3d4c08ebb20bc44a1b1809b82c0'
    validate_identity_inventory(LOGTO_145,list(LOGTO_145.tables),[[name,list(labels)] for name,labels in LOGTO_145.enums])
    with pytest.raises(B2Error):validate_identity_inventory(LOGTO_145,list(LOGTO_145.tables)+['unknown'],[[name,list(labels)] for name,labels in LOGTO_145.enums])


@pytest.mark.parametrize('field,row',[
    ('columns',[]),('columns',None),('columns',{'private-marker':'value'}),
    ('constraints',[]),('functions',[]),('indexes',[]),('policies',[]),
    ('triggers',['public']),('types',['public','name']),
    ('relation_options',[]),('sequence_definitions',[]),('rules',[]),
    ('operators',[]),('collations',[]),
])
def test_malformed_catalog_is_a_bounded_validation_failure(field,row):
    from platform_service.media_backup_b2.profiles import PUBLIC_016,validate_profile_catalog
    fields=('columns','constraints','functions','enums','indexes','policies',
            'triggers','types','relation_options','sequence_definitions','rules',
            'operators','collations','casts','extensions','event_triggers')
    catalog={name:[] for name in fields};catalog[field]=[row]
    with pytest.raises(B2Error) as failure:
        validate_profile_catalog(PUBLIC_016,catalog)
    assert 'private-marker' not in str(failure.value)
