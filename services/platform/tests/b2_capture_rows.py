"""Synthetic complete rows for raw capture-closure tests; no DB/barrier proof."""
from copy import deepcopy
from platform_service.media_backup_b2.profiles import PUBLIC_016

AT='2026-10-30T00:00:00+00:00'
LATER='2026-10-31T00:00:00+00:00'
SCOPE={'user_id':'u','tenant_id':'t','product_id':'p','billing_account_id':'b','session_id':'s','authorization_epoch':1}

def artifact_rows(*,terminal=False,missing=False):
    rows={table:[] for table in PUBLIC_016.tables}
    definition={'id':'model','capability':'image.generate','adapter':'newapi_images_v1','provider_id':'provider','provider_version':'v1','rate':{'version':'r1'},'limits':{}}
    rows['users']=[{'id':'u','authorization_epoch':1,'active':True,'created_at':AT}]
    rows['products']=[{'id':'p','enabled':True}]
    rows['tenants']=[{'id':'t','owner_user_id':'u'}]
    rows['tenant_members']=[{'tenant_id':'t','user_id':'u','role':'owner','active':True}]
    rows['billing_accounts']=[{'id':'b','tenant_id':'t'}]
    rows['external_identities']=[{'issuer':'synthetic','subject':'subject','user_id':'u','email':None,'email_verified':False}]
    rows['product_clients']=[{'product_id':'p','client_id':'client','enabled':True}]
    rows['identity_sessions']=[{'id':'s','user_id':'u','product_id':'p','tenant_id':'t','billing_account_id':'b','client_id':'client','authorization_epoch':1,'issuer':'synthetic','subject':'subject','source_transaction_id':None,'created_at':AT,'expires_at':LATER,'revoked_at':None}]
    rows['plans']=[{'id':'plan','name':'synthetic','version':'v1','definition':{'test_only':True},'created_by':'operator','created_at':AT}]
    rows['memberships']=[{'id':'membership','billing_account_id':'b','plan_id':'plan','operator_id':'operator','issuance_key':'fixture','starts_at':AT,'expires_at':LATER,'created_at':AT}]
    rows['media_models']=[{'id':'model','definition':definition,'operator_id':'operator','created_at':AT}]
    rows['media_invocations']=[{'id':'inv','scope':deepcopy(SCOPE),'session_id':'s','user_id':'u','product_id':'p','tenant_id':'t','billing_account_id':'b','invocation_key':'synthetic','model':'model','capability':'image.generate','funding_mode':'test','expires_at':LATER,'created_at':AT,'operation_count':1,'held':0 if terminal else 10,'spent':0}]
    rows['credit_reservations']=[{'id':'reserve','billing_account_id':'b','tenant_id':'t','user_id':'u','product_id':'p','operation':'media','idempotency_key':'job','body_hash':'a'*64,'rate_version':'r1','amount':10,'actual':0 if terminal else None,'status':'settled' if terminal else 'pending_reconciliation','completion_key':'attempt' if terminal else None,'funding_mode':'test','created_at':AT,'updated_at':AT,'created_xid':'2'}]
    rows['media_jobs']=[{'id':'job','invocation_id':'inv','operation_key':'one','request_hash':'a'*64,'request':{'prompt':'synthetic','assets':[]},'input_units':{},'scope':deepcopy(SCOPE),'user_id':'u','product_id':'p','tenant_id':'t','billing_account_id':'b','model':'model','capability':'image.generate','funding_mode':'test','model_definition':definition,'binding_hash':'b'*64,'attempt_id':'attempt','reservation_id':'reserve','status':'settled' if terminal else 'pending_reconciliation','execution_state':'failed' if terminal else 'unknown','delivery_state':'unavailable' if terminal else 'processing','amount':10,'actual':0 if terminal else None,'reason':'synthetic_failure' if terminal else None,'lease_token':None,'lease_until':AT,'dispatched_at':AT,'cancel_requested_at':None,'created_at':AT,'updated_at':AT}]
    rows['media_provider_attempts']=[{'job_id':'job','attempt_id':'attempt','binding_hash':'b'*64,'provider_scope_hash':'c'*64,'provider_request_hash':'d'*64,'upstream_model':'synthetic','request_id':'request','created_at':AT}]
    rows['media_provider_associations']=[{'job_id':'job','kind':'http','attempt_id':'attempt','binding_hash':'b'*64,'provider_scope_hash':'c'*64,'http_request_id':'req_synthetic','http_status':200,'provider_response_id':None,'provider_job_id':None,'created_at':AT}]
    rows['credit_lots']=[{'id':'lot','billing_account_id':'b','membership_id':'membership','issued':100,'available':100 if terminal else 90,'held':0 if terminal else 10,'spent':0,'expired':0,'expires_at':LATER,'test_only':True,'refunded':0,'frozen':False,'created_at':AT}]
    rows['credit_holds']=[{'reservation_id':'reserve','lot_id':'lot','billing_account_id':'b','amount':10}]
    rows['credit_journals']=[{'id':'issue','billing_account_id':'b','kind':'issue','source':'membership','operator_id':'operator','created_at':AT,'created_xid':'1'}, {'id':'reserve-journal','billing_account_id':'b','kind':'reserve','source':'reserve','operator_id':None,'created_at':AT,'created_xid':'2'}]
    entries=[('issue','issuance',-100),('issue','available',100),('reserve-journal','available',-10),('reserve-journal','held',10)]
    if terminal:
        rows['credit_journals'].append({'id':'settle-journal','billing_account_id':'b','kind':'settle','source':'reserve','operator_id':None,'created_at':AT,'created_xid':'3'})
        entries.extend([('settle-journal','held',-10),('settle-journal','available',10)])
        evidence={'job_id':'job','attempt_id':'attempt','binding_hash':'b'*64,'terminal_status':'failed','provider_response_id':None,'quantities':{},'basis_evidence':{},'evidence_hash':'e'*64,'reference_kind':'http_request_id','http_reference_namespace':'openai_x_request_id_v1','http_reference_value':'req_synthetic','created_at':AT}
        rows['media_terminal_evidence']=[evidence]
        rows['media_receipts']=[{key:value for key,value in evidence.items() if key!='binding_hash'}|{'actual':0}]
        rows['media_http_response_references']=[{'job_id':'job','attempt_id':'attempt','binding_hash':'b'*64,'provider_scope_hash':'c'*64,'namespace':'openai_x_request_id_v1','http_request_id':'req_synthetic','response_body_sha256':'e'*64,'created_at':AT}]
    rows['credit_entries']=[{'id':i+1,'journal_id':journal,'billing_account_id':'b','bucket':bucket,'delta':delta} for i,(journal,bucket,delta) in enumerate(entries)]
    common={'scope':deepcopy(SCOPE),'user_id':'u','tenant_id':'t','billing_account_id':'b','invocation_id':'inv','job_id':'job','attempt_id':'attempt','storage_key':'f'*64,'sha256':'1'*64,'size':3,'mime':'image/png','properties':{},'created_at':AT,'expires_at':LATER,'purged_at':None}
    rows['media_blobs']=[common|{'kind':'artifact','state':'staged' if missing else 'attached','attached_id':None if missing else 'artifact','deleted_at':None,'gc_token':None,'gc_lease_until':None}]
    rows['media_artifacts']=[] if missing else [common|{'id':'artifact','product_id':'p'}]
    rows['media_storage_budgets']=[{'job_id':'job','scope':deepcopy(SCOPE),'user_id':'u','tenant_id':'t','billing_account_id':'b','max_bytes':10,'max_files':1,'remaining_bytes':0 if terminal else 7,'remaining_files':0,'created_at':AT,'released_at':AT if terminal else None}]
    rows['schema_migrations']=[{'version':name,'checksum':digest} for name,digest in PUBLIC_016.migrations]
    rows['media_storage_policy']=[{'id':1,'owner_max_bytes':268435456,'owner_max_files':128,'global_max_bytes':2147483648,'global_max_files':4096,'ttl_seconds':86400}]
    physical=[] if missing else [{'key':'f'*64,'size':3,'sha256':'1'*64,'uid':501,'gid':20,'mode':0o600}]
    return rows,physical
