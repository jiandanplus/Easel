import copy,hashlib
import pytest
from platform_service.media_backup_b2.contracts import B2Error


@pytest.mark.parametrize('identity_kind',['public','logto'])
@pytest.mark.parametrize('column',[[],['public'],['public','users']])
def test_short_identity_catalog_rows_are_rejected_without_index_error(identity_kind,column):
    from platform_service.media_backup_b2.profiles import PUBLIC_016,LOGTO_145
    from platform_service.media_backup_b2.source_facts import FACT_KEYS,validate_source_facts
    from platform_service.media_backup_b2.bundles import canonical_bytes
    identity_profile=LOGTO_145 if identity_kind=='logto' else PUBLIC_016
    catalog={'columns':[column]}
    schema={'migrations':[list(row) for row in PUBLIC_016.migrations],
            'relations':{},'identity':{'profile':identity_profile.name,
                'relations':catalog,
                'image_sha256':LOGTO_145.image_sha256 if identity_kind=='logto' else None,
                'public_sql':[list(row) for row in LOGTO_145.public_sql] if identity_kind=='logto' else []}}
    facts=dict.fromkeys(FACT_KEYS)
    facts.update(format='easel-source-facts-v1',profile=PUBLIC_016.name,
        source_owner=[0,0],snapshots={'central':'central','identity':'identity','cross_database_atomic':False},
        schema=schema,schema_sha256=hashlib.sha256(canonical_bytes(schema)).hexdigest(),
        rows={table:[] for table in PUBLIC_016.tables})
    with pytest.raises(B2Error):validate_source_facts(canonical_bytes(facts))

def _asset_rows():
    scope={'user_id':'u','tenant_id':'t','product_id':'p','billing_account_id':'b','session_id':'s','authorization_epoch':1}
    inv={'id':'inv','scope':scope,**{k:v for k,v in scope.items() if k!='authorization_epoch'},'funding_mode':'test','model':'m','capability':'image.generate'}
    blob={'storage_key':'a'*32,'kind':'asset','scope':scope,'user_id':'u','tenant_id':'t','billing_account_id':'b','invocation_id':'inv','job_id':None,'attempt_id':None,'sha256':'b'*64,'size':3,'mime':'image/png','properties':{},'expires_at':'2026-10-31T00:00:00+00:00','state':'attached','attached_id':'asset','purged_at':None,'deleted_at':None,'gc_token':None,'gc_lease_until':None}
    asset={'id':'asset','storage_key':'a'*32,'invocation_id':'inv','scope':scope,'user_id':'u','tenant_id':'t','billing_account_id':'b','product_id':'p','sha256':'b'*64,'size':3,'mime':'image/png','properties':{},'expires_at':'2026-10-31T00:00:00+00:00','purged_at':None}
    rows={'media_blobs':[blob],'media_invocations':[inv],'media_assets':[asset],'media_artifacts':[],'media_jobs':[],'credit_reservations':[], 'identity_sessions':[{'id':'s',**scope}],'billing_accounts':[{'id':'b','tenant_id':'t'}]}
    physical=[{'key':'a'*32,'sha256':'b'*64,'size':3,'uid':501,'gid':20,'mode':0o600}]
    return rows,physical

def test_registry_uses_raw_job_scope_and_billing():
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=_asset_rows()
    blobs,refs=derive_registry(rows,physical)
    assert blobs[0]['operation_id']=='invocation:inv'
    bad=copy.deepcopy(rows);bad['media_blobs'][0]['billing_account_id']='other'
    with pytest.raises(B2Error):derive_registry(bad,physical)
    with pytest.raises(B2Error):derive_registry(rows,physical+[{**physical[0],'key':'c'*32}])
    bad=copy.deepcopy(rows);bad['media_blobs'][0]['state']='gc_pending'
    with pytest.raises(B2Error):derive_registry(bad,physical)

def test_deleted_history_retains_old_gc_token_and_lease_without_becoming_active():
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,_=_asset_rows();blob=rows['media_blobs'][0]
    blob.update(state='deleted',purged_at='2026-10-29T00:00:00+00:00',deleted_at='2026-10-29T00:00:01+00:00',gc_token='historical-claim',gc_lease_until='2026-10-29T00:01:00+00:00')
    rows['media_assets'][0]['purged_at']=blob['purged_at']
    blobs,_=derive_registry(rows,[],source_owner=(501,20))
    assert blobs[0]['present'] is False and blobs[0]['state']=='deleted' and blobs[0]['uid']==501

def test_facts_and_bundle_cross_binding_is_not_digest_only():
    from platform_service.media_backup_b2.source_facts import verify_archived_bundles
    with pytest.raises(B2Error):verify_archived_bundles({},[],{})

def test_raw_pending_job_rejects_false_provider_attempt_and_billing():
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows()
    assert derive_registry(rows,physical,source_owner=(501,20))[0][0]["operation_id"]=="job:job"
    changed=copy.deepcopy(rows);changed["media_provider_attempts"][0]["attempt_id"]="other-attempt"
    with pytest.raises(B2Error):derive_registry(changed,physical,source_owner=(501,20))
    changed=copy.deepcopy(rows);changed["credit_reservations"][0]["billing_account_id"]="other-wallet"
    with pytest.raises(B2Error):derive_registry(changed,physical,source_owner=(501,20))

def test_pending_with_recorded_terminal_evidence_keeps_unknown_actual():
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows();closed,_=artifact_rows(terminal=True)
    rows["media_jobs"][0].update(execution_state="failed",delivery_state="unavailable")
    rows["media_terminal_evidence"]=closed["media_terminal_evidence"]
    rows["media_http_response_references"]=closed["media_http_response_references"]
    rows["media_storage_budgets"]=closed["media_storage_budgets"]
    assert derive_registry(rows,physical,source_owner=(501,20))
    assert rows["media_jobs"][0]["actual"] is None and rows["credit_reservations"][0]["status"]=="pending_reconciliation"

def test_pending_async_manifest_before_blob_staging_is_preserved():
    from b2_capture_rows import artifact_rows,AT,LATER
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,_=artifact_rows();rows['media_blobs']=[];rows['media_artifacts']=[]
    rows['media_storage_budgets'][0].update(remaining_bytes=10,remaining_files=1)
    rows['media_models'][0]['definition']['adapter']='newapi_videos_v1'
    rows['media_provider_associations'].append({'job_id':'job','kind':'job','attempt_id':'attempt','binding_hash':'b'*64,'provider_scope_hash':'c'*64,'provider_job_id':'upstream-job','http_status':None,'http_request_id':None,'provider_response_id':None,'created_at':AT})
    rows['media_work']=[{'job_id':'job','attempt_id':'attempt','binding_hash':'b'*64,'provider_scope_hash':'c'*64,'provider_job_id':'upstream-job','adapter':'newapi_videos_v1','phase':'download','due_at':AT,'deadline':LATER,'lease_token':None,'lease_until':None,'polls':1,'failures':0,'last_safe_code':None,'created_at':AT,'updated_at':AT}]
    rows['media_observations']=[{'job_id':'job','attempt_id':'attempt','binding_hash':'b'*64,'provider_job_id':'upstream-job','state':'completed','quantities':{},'usage_hash':'2'*64,'response_hash':'3'*64,'manifest':[{'source_id':'output','kind':'url','mime':'video/mp4'}],'created_at':AT}]
    rows['media_sources']=[{'job_id':'job','source_id':'output','kind':'url','location':'https://example.invalid/synthetic','mime':'video/mp4','storage_key':'f'*64,'artifact_key':'future-artifact','metadata':None,'artifact_id':None,'created_at':AT,'updated_at':AT}]
    assert derive_registry(rows,[],source_owner=(501,20))==([],[])

def test_failed_staged_missing_preserves_nonzero_actual():
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows(terminal=True,missing=True)
    for table in ("media_jobs","credit_reservations","media_receipts"):rows[table][0]["actual"]=3
    rows["media_invocations"][0]["spent"]=3
    rows["credit_lots"][0].update(available=97,spent=3)
    rows["credit_entries"][-1]["delta"]=7
    rows["credit_entries"].append({"id":7,"journal_id":"settle-journal","billing_account_id":"b","bucket":"spent","delta":3})
    assert derive_registry(rows,physical,source_owner=(501,20))[0][0]["missing_evidence_sha256"]
    assert rows["media_jobs"][0]["actual"]==3

def test_artifact_raw_complete_financial_lineage_and_job_operation():
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows();blobs,refs=derive_registry(rows,physical,source_owner=(501,20))
    assert blobs[0]['operation_id']=='job:job' and refs[0]['operation_id']=='job:job'

@pytest.mark.parametrize('fault',['attempt','scope','billing','omitted_registry','duplicate_registry','duplicate_relation','cross_kind_relation','metadata_mime','metadata_expiry','hold_omitted','hold_amount','hold_wallet','journal_omitted','journal_unbalanced','journal_wallet','session','unknown_gc'])
def test_artifact_raw_lineage_mismatch_rejects(fault):
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows()
    if fault=='attempt':rows['media_artifacts'][0]['attempt_id']='other'
    elif fault=='scope':rows['media_jobs'][0]['scope']={**rows['media_jobs'][0]['scope'],'product_id':'other'}
    elif fault=='billing':rows['credit_reservations'][0]['billing_account_id']='other'
    elif fault=='omitted_registry':rows['media_blobs']=[]
    elif fault=='duplicate_registry':rows['media_blobs'].append(copy.deepcopy(rows['media_blobs'][0]))
    elif fault=='duplicate_relation':rows['media_artifacts'].append(copy.deepcopy(rows['media_artifacts'][0]))
    elif fault=='cross_kind_relation':rows['media_assets']=[{k:v for k,v in rows['media_artifacts'][0].items() if k not in {'job_id','attempt_id'}}]
    elif fault=='metadata_mime':rows['media_artifacts'][0]['mime']='image/jpeg'
    elif fault=='metadata_expiry':rows['media_artifacts'][0]['expires_at']='2026-10-30T00:01:00+00:00'
    elif fault=='hold_omitted':rows['credit_holds']=[]
    elif fault=='hold_amount':rows['credit_holds'][0]['amount']=9
    elif fault=='hold_wallet':rows['credit_holds'][0]['billing_account_id']='other'
    elif fault=='journal_omitted':rows['credit_journals']=[r for r in rows['credit_journals'] if r['kind']!='reserve']
    elif fault=='journal_unbalanced':rows['credit_entries'][2]['delta']=-9
    elif fault=='journal_wallet':rows['credit_journals'][1]['billing_account_id']='other'
    elif fault=='session':rows['identity_sessions'][0]['authorization_epoch']=2
    elif fault=='unknown_gc':rows['media_blobs'][0]['gc_lease_until']='2026-10-30T00:01:00+00:00'
    with pytest.raises(B2Error):derive_registry(rows,physical,source_owner=(501,20))

def test_staged_missing_requires_complete_failed_attempt_terminal_finance():
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows(terminal=True,missing=True)
    blobs,_=derive_registry(rows,physical,source_owner=(501,20))
    assert blobs[0]['state']=='staged' and blobs[0]['present'] is False and len(blobs[0]['missing_evidence_sha256'])==64
    for table in ('media_terminal_evidence','media_receipts','media_provider_attempts','credit_holds'):
        bad=copy.deepcopy(rows);bad[table]=[]
        with pytest.raises(B2Error):derive_registry(bad,physical,source_owner=(501,20))

@pytest.mark.parametrize('fault',['pending','completed','receipt_actual','terminal_attempt','attempt_binding','active_budget','omitted_budget','asset','unknown_gc','attached','duplicate_hold','duplicate_entry'])
def test_staged_missing_incomplete_or_unknown_evidence_rejects(fault):
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows(terminal=True,missing=True)
    if fault=='pending':
        rows['media_jobs'][0].update(status='pending_reconciliation',actual=None)
        rows['credit_reservations'][0].update(status='pending_reconciliation',actual=None,completion_key=None)
        rows['media_invocations'][0]['held']=10;rows['media_receipts']=[]
        rows['credit_journals']=[r for r in rows['credit_journals'] if r['kind']!='settle']
        rows['credit_entries']=[r for r in rows['credit_entries'] if r['journal_id']!='settle-journal']
    elif fault=='completed':
        rows['media_jobs'][0]['execution_state']='completed'
        rows['media_terminal_evidence'][0]['terminal_status']=rows['media_receipts'][0]['terminal_status']='completed'
    elif fault=='receipt_actual':rows['media_receipts'][0]['actual']=1
    elif fault=='terminal_attempt':rows['media_terminal_evidence'][0]['attempt_id']='other'
    elif fault=='attempt_binding':rows['media_provider_attempts'][0]['binding_hash']='9'*64
    elif fault=='active_budget':rows['media_storage_budgets'][0]['released_at']=None
    elif fault=='omitted_budget':rows['media_storage_budgets']=[]
    elif fault=='asset':rows['media_blobs'][0].update(kind='asset',job_id=None,attempt_id=None)
    elif fault=='unknown_gc':rows['media_blobs'][0]['gc_token']='unknown'
    elif fault=='attached':rows['media_blobs'][0].update(state='attached',attached_id='artifact')
    elif fault=='duplicate_hold':rows['credit_holds'].append(copy.deepcopy(rows['credit_holds'][0]))
    elif fault=='duplicate_entry':rows['credit_entries'].append(copy.deepcopy(rows['credit_entries'][0]))
    with pytest.raises(B2Error):derive_registry(rows,physical,source_owner=(501,20))

def test_purged_artifact_and_historical_gc_are_absent():
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.source_facts import derive_registry
    rows,physical=artifact_rows(terminal=True)
    for table in ('media_blobs','media_artifacts'):
        rows[table][0].update(expires_at='2026-10-29T00:00:00+00:00',purged_at='2026-10-29T00:01:00+00:00')
    rows['media_blobs'][0].update(state='deleted',deleted_at='2026-10-29T00:01:01+00:00',gc_token='old',gc_lease_until='2026-10-29T00:01:59+00:00')
    blobs,_=derive_registry(rows,[],source_owner=(501,20))
    assert blobs[0]['present'] is False and blobs[0]['state']=='deleted'
    with pytest.raises(B2Error):derive_registry(rows,physical,source_owner=(501,20))
