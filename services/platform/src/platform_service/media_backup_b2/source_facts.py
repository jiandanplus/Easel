"""Raw canonical source observations, independently re-derived archive closure."""
import hashlib
import re
from media_backup.contract import validate_plan, registry_digest, ClosureError
from .contracts import B2Error,require,validate_hash
from .bundles import (canonical_bytes,strict_json,decode_bundle,item_metadata,
                      MAX_FACTS_BYTES,MAX_BUNDLE_BYTES,MAX_BUNDLES_BYTES)
from .profiles import PROFILES,LOGTO_145,validate_identity_inventory,validate_profile_catalog

DB_FILES=frozenset({'db/central.dump','db/identity.dump','db/central.roles','db/identity.roles'})
SCOPE=('user_id','tenant_id','product_id','billing_account_id')
FACT_KEYS={'format','profile','snapshots','schema_sha256','schema','dumps','configs','rows','physical','sequences','roles','workers','snapshot_projections','dump_schema','source_owner','maintenance_snapshot'}

_COLS=r'[a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*'

def validate_raw_relations(rows,catalog):
    """Evaluate fixed-profile FK/PK/UNIQUE definitions without executing SQL.

    Callers validate the complete catalog pin first. This deliberately handles a
    small closed grammar, never treats archived SQL as a command or authority.
    """
    try:
        require(type(rows) is dict and type(catalog) is dict)
        indexes={}
        for schema,table,_,definition in catalog['constraints']:
            if schema!='public':continue
            require(table in rows)
            if definition.startswith(('PRIMARY KEY','UNIQUE')):
                match=re.fullmatch(r'(PRIMARY KEY|UNIQUE)( NULLS NOT DISTINCT)? \(('+_COLS+r')\)',definition,re.ASCII)
                require(match is not None);columns=match[3].split(', ');seen=set()
                for row in rows[table]:
                    values=[row[col] for col in columns]
                    if match[1]=='PRIMARY KEY':require(all(value is not None for value in values))
                    elif match[2] is None and any(value is None for value in values):continue
                    key=canonical_bytes(values);require(key not in seen);seen.add(key)
            elif definition.startswith('FOREIGN KEY'):
                match=re.fullmatch(r'FOREIGN KEY \(('+_COLS+r')\) REFERENCES (?:(public)\.)?([a-z_][a-z0-9_]*)\(('+_COLS+r')\)((?: (?:MATCH (?:FULL|SIMPLE)|ON (?:UPDATE|DELETE) (?:NO ACTION|RESTRICT|CASCADE|SET NULL|SET DEFAULT)|DEFERRABLE|NOT DEFERRABLE|INITIALLY (?:DEFERRED|IMMEDIATE)))*)',definition,re.ASCII)
                require(match is not None)
                columns=match[1].split(', ');target=match[3];parents=match[4].split(', ')
                require(target in rows and len(columns)==len(parents))
                selector=(target,tuple(parents))
                if selector not in indexes:indexes[selector]={canonical_bytes([row[col] for col in parents]) for row in rows[target]}
                for row in rows[table]:
                    values=[row[col] for col in columns]
                    if any(value is None for value in values):
                        if 'MATCH FULL' in match[5]:require(all(value is None for value in values))
                        continue
                    require(canonical_bytes(values) in indexes[selector])
        return None
    except (KeyError,TypeError,ValueError,AttributeError,RecursionError):raise B2Error() from None

def _by(rows,key):
    require(type(rows) is list and len(rows)<=100000)
    result={}
    for row in rows:
        require(type(row) is dict and key in row and row[key] not in result)
        result[row[key]]=row
    return result

def _financial_lineage(rows,jobs,reservations):
    if not jobs:return {}
    required={'credit_lots','credit_holds','credit_journals','credit_entries','memberships','plans'}
    require(required<=rows.keys())
    lots=_by(rows['credit_lots'],'id');memberships=_by(rows['memberships'],'id');plans=_by(rows['plans'],'id')
    journals=_by(rows['credit_journals'],'id');entries=_by(rows['credit_entries'],'id')
    seen_holds=set();proofs={}
    for hold in rows['credit_holds']:
        key=(hold['reservation_id'],hold['lot_id']);require(key not in seen_holds);seen_holds.add(key)
    for job in jobs.values():
        reservation=reservations[job['reservation_id']]
        holds=[r for r in rows['credit_holds'] if r['reservation_id']==reservation['id']]
        require(holds and sum(r['amount'] for r in holds)==reservation['amount'])
        for hold in holds:
            require(type(hold['amount']) is int and hold['amount']>0 and hold['billing_account_id']==reservation['billing_account_id'])
            lot=lots[hold['lot_id']];membership=memberships[lot['membership_id']];plan=plans[membership['plan_id']]
            require(lot['billing_account_id']==membership['billing_account_id']==reservation['billing_account_id'])
            require(type(lot['test_only']) is bool and type(plan['definition']['test_only']) is bool)
            require(lot['test_only']==plan['definition']['test_only']==(reservation['funding_mode']=='test'))
        related=[r for r in journals.values() if r['source']==reservation['id']]
        require(len([r for r in related if r['kind']=='reserve'])==1)
        terminal='settle' if reservation['status']=='settled' else ('release' if reservation['status']=='released' else None)
        require({r['kind'] for r in related}==({'reserve',terminal} if terminal else {'reserve'}))
        require(len(related)==(2 if terminal else 1))
        related_entries=[]
        for journal in related:
            require(journal['billing_account_id']==reservation['billing_account_id'])
            values=[r for r in entries.values() if r['journal_id']==journal['id']]
            require(len(values)>=2 and all(type(r['delta']) is int and r['delta']!=0 and r['billing_account_id']==reservation['billing_account_id'] for r in values))
            require(sum(r['delta'] for r in values)==0)
            buckets={}
            for row in values:buckets[row['bucket']]=buckets.get(row['bucket'],0)+row['delta']
            if journal['kind']=='reserve':require(buckets=={'available':-reservation['amount'],'held':reservation['amount']})
            else:
                actual=reservation['actual'];require(type(actual) is int and 0<=actual<=reservation['amount'])
                require(set(buckets)<={'held','spent','available','expired'})
                require(buckets.get('held')==-reservation['amount'] and buckets.get('spent',0)==actual)
                require(buckets.get('available',0)>=0 and buckets.get('expired',0)>=0)
                require(buckets.get('available',0)+buckets.get('expired',0)==reservation['amount']-actual)
            related_entries.extend(values)
        proofs[job['id']]={'holds':sorted(holds,key=lambda r:r['lot_id']),
            'lots':[lots[key] for key in sorted({r['lot_id'] for r in holds})],
            'memberships':[memberships[key] for key in sorted({lots[r['lot_id']]['membership_id'] for r in holds})],
            'plans':[plans[key] for key in sorted({memberships[lots[r['lot_id']]['membership_id']]['plan_id'] for r in holds})],
            'journals':sorted(related,key=lambda r:r['id']),'entries':sorted(related_entries,key=lambda r:r['id'])}
    return proofs

def _missing_evidence(rows,blob,jobs,reservations,financial):
    require(blob['kind']=='artifact' and blob['job_id'] in financial)
    job=jobs[blob['job_id']];reservation=reservations[job['reservation_id']]
    require(job['status']==reservation['status']=='settled' and job['actual'] is not None)
    require(job['execution_state'] in {'failed','cancelled'} and job['delivery_state'] in {'unavailable','expired'})
    attempt=_by(rows['media_provider_attempts'],'job_id')[job['id']]
    terminal=_by(rows['media_terminal_evidence'],'job_id')[job['id']]
    receipt=_by(rows['media_receipts'],'job_id')[job['id']]
    require(terminal['terminal_status'] in {'failed','cancelled'} and receipt['actual']==job['actual'])
    require(not any(r['storage_key']==blob['storage_key'] for r in rows.get('media_sources',[])))
    budget=_by(rows['media_storage_budgets'],'job_id')[job['id']]
    require(budget['released_at'] is not None and budget['remaining_bytes']==budget['remaining_files']==0 and budget['scope']==job['scope'])
    for field in ('user_id','tenant_id','billing_account_id'):require(budget[field]==job[field])
    proof={'format':'easel-staged-missing-v1','storage_key':blob['storage_key'],'job':job,
        'reservation':reservation,'attempt':attempt,'terminal':terminal,'receipt':receipt,'budget':budget,'finance':financial[job['id']]}
    return hashlib.sha256(canonical_bytes(proof)).hexdigest()

def _job_lineage(rows,invs,jobs):
    attempts=_by(rows.get('media_provider_attempts',[]),'job_id')
    evidence=_by(rows.get('media_terminal_evidence',[]),'job_id')
    receipts=_by(rows.get('media_receipts',[]),'job_id')
    models=_by(rows.get('media_models',[]),'id')
    associations={}
    for row in rows.get('media_provider_associations',[]):
        pair=(row['job_id'],row['kind']);require(pair not in associations);associations[pair]=row
        attempt=attempts[row['job_id']]
        for field in ('attempt_id','binding_hash','provider_scope_hash'):require(row[field]==attempt[field])
    for row in attempts.values():
        job=jobs[row['job_id']]
        for field in ('attempt_id','binding_hash'):require(row[field]==job[field])
    for job in jobs.values():
        require(type(job['amount']) is int and job['amount']>0)
        require(job['actual'] is None or (type(job['actual']) is int and 0<=job['actual']<=job['amount']))
        require(job['status'] in {'reserved','dispatched','pending_reconciliation','settled','released'})
        require(job['model'] in models and job['model_definition']==models[job['model']]['definition'])
        require(job['model_definition']['capability']==job['capability'])
        if job['status']=='reserved':require(job['actual'] is None and job['dispatched_at'] is None and job['execution_state']=='queued')
        elif job['status']=='released':require(job['actual']==0 and job['dispatched_at'] is None and job['execution_state']=='cancelled')
        else:require(job['dispatched_at'] is not None and job['id'] in attempts)
        if job['status']=='settled':
            require(job['actual'] is not None and job['id'] in evidence and job['id'] in receipts)
        else:require(job['id'] not in receipts)
    for row in evidence.values():
        job=jobs[row['job_id']]
        for field in ('attempt_id','binding_hash'):require(row[field]==job[field])
        require(row['terminal_status']==job['execution_state'] and row['terminal_status'] in {'completed','failed','cancelled'})
        require((job['id'],'http') in associations)
        reference=row.get('reference_kind','object_id')
        if reference=='object_id':
            response=associations.get((job['id'],'response'))
            require(response is not None and row['provider_response_id']==response['provider_response_id'])
        elif reference=='http_request_id':
            require(any(ref['job_id']==job['id'] and ref['namespace']==row['http_reference_namespace'] and ref['http_request_id']==row['http_reference_value'] and ref['response_body_sha256']==row['evidence_hash'] for ref in rows.get('media_http_response_references',[])))
        else:require(False)
    for row in receipts.values():
        job=jobs[row['job_id']];terminal=evidence[row['job_id']]
        require(row['actual']==job['actual'] and row['attempt_id']==job['attempt_id'])
        for field in ('quantities','basis_evidence','evidence_hash','terminal_status','provider_response_id'):
            require(row[field]==terminal[field])
        for field in ('reference_kind','http_reference_namespace','http_reference_value'):
            require(row.get(field)==terminal.get(field))
    for row in rows.get('media_http_response_references',[]):
        attempt=attempts[row['job_id']]
        for field in ('attempt_id','binding_hash','provider_scope_hash'):require(row[field]==attempt[field])
    for row in rows.get('media_protocol_facts',[]):
        job=jobs[row['job_id']]
        for field in ('attempt_id','binding_hash'):require(row[field]==job[field])
        require(row['namespace']==job['model_definition']['adapter'])
    work=_by(rows.get('media_work',[]),'job_id')
    observations=_by(rows.get('media_observations',[]),'job_id')
    for row in work.values():
        job=jobs[row['job_id']];association=associations[(row['job_id'],'job')]
        for field in ('attempt_id','binding_hash','provider_scope_hash','provider_job_id'):require(row[field]==association[field])
        require(row['adapter']==job['model_definition']['adapter'])
        # Outstanding work leases are actual unknown children, never clean drain.
        require(row['lease_token'] is None and row['lease_until'] is None)
    for row in observations.values():
        parent=work[row['job_id']]
        for field in ('attempt_id','binding_hash','provider_job_id'):require(row[field]==parent[field])
        manifest=[{field:source[field] for field in ('source_id','kind','mime')} for source in sorted(rows.get('media_sources',[]),key=lambda s:s['source_id']) if source['job_id']==row['job_id']]
        require(row['manifest']==manifest)
    for row in rows.get('media_sources',[]):
        require(row['job_id'] in observations)
        matching=[b for b in rows['media_blobs'] if b['storage_key']==row['storage_key']]
        if not matching:
            # A durable URL/content manifest can precede blob staging. Preserve
            # that known pending state without inventing a registry/file row.
            require(row['metadata'] is None and row['artifact_id'] is None and work[row['job_id']]['phase'] in {'query','download'})
        else:require(len(matching)==1 and matching[0]['kind']=='artifact' and matching[0]['job_id']==row['job_id'])
        if row['artifact_id'] is not None:
            artifact=next(a for a in rows['media_artifacts'] if a['id']==row['artifact_id'])
            require(artifact['job_id']==row['job_id'] and artifact['storage_key']==row['storage_key'])
            require(row['metadata'] is not None)
            for field in ('sha256','size','mime','properties'):require(row['metadata'][field]==artifact[field])
    for inv in invs.values():
        if all(field in inv for field in ('operation_count','held','spent')):
            members=[j for j in jobs.values() if j['invocation_id']==inv['id']]
            require(inv['operation_count']==len(members))
            require(inv['held']==sum(j['amount'] for j in members if j['status'] in {'reserved','dispatched','pending_reconciliation'}))
            require(inv['spent']==sum(j['actual'] for j in members if j['status']=='settled'))

def derive_registry(rows,physical,*,source_owner=None):
    """Do not infer tenant, billing, attempt or ownership from storage key prefixes."""
    try:
        require(type(rows) is dict)
        if source_owner is not None:
            require(type(source_owner) is tuple and len(source_owner)==2 and all(type(v) is int and 0<=v<2**32 for v in source_owner))
        inventory=_by(physical,'key');registry=_by(rows['media_blobs'],'storage_key')
        invs=_by(rows['media_invocations'],'id');jobs=_by(rows['media_jobs'],'id')
        reservations=_by(rows['credit_reservations'],'id')
        assets=_by(rows['media_assets'],'storage_key');artifacts=_by(rows['media_artifacts'],'storage_key')
        sessions=_by(rows['identity_sessions'],'id');accounts=_by(rows['billing_accounts'],'id')
        require(set(assets)<=set(registry) and set(artifacts)<=set(registry))
        require(not (set(assets)&set(artifacts)))
        require(all(registry[key]['kind']=='asset' for key in assets) and all(registry[key]['kind']=='artifact' for key in artifacts))
        for inv in invs.values():
            scope=inv['scope'];require(type(scope) is dict)
            for field in SCOPE:require(inv[field]==scope[field])
            session=sessions[inv['session_id']]
            for field in SCOPE:require(session[field]==scope[field])
            require(type(scope['authorization_epoch']) is int and session['authorization_epoch']==scope['authorization_epoch'])
            require(scope['session_id']==inv['session_id'])
            require(accounts[inv['billing_account_id']]['tenant_id']==inv['tenant_id'])
        for job in jobs.values():
            inv=invs[job['invocation_id']];reservation=reservations[job['reservation_id']]
            require(job['scope']==inv['scope'])
            for field in SCOPE+('funding_mode',):require(job[field]==inv[field] and reservation[field]==job[field])
            for field in ('status','amount','actual'):require(job[field]==reservation[field])
            require(reservation['operation']=='media' and reservation['idempotency_key']==job['id'] and reservation['body_hash']==job['request_hash'])
            require(reservation['rate_version']==job['model_definition']['rate']['version'])
            for field in ('model','capability'):require(job[field]==inv[field])
        require({r['id'] for r in reservations.values() if r['operation']=='media'}=={j['reservation_id'] for j in jobs.values()})
        _job_lineage(rows,invs,jobs)
        financial=_financial_lineage(rows,jobs,reservations)
        blobs=[];refs=[]
        for key,blob in sorted(registry.items()):
            inv=invs[blob['invocation_id']];scope=inv['scope']
            require(blob['scope']==scope)
            for field in ('user_id','tenant_id','billing_account_id'):require(blob[field]==scope[field])
            require(blob['state'] in {'staged','attached','deleted'})
            if blob['state']!='deleted':require(blob.get('gc_token') is None and blob.get('gc_lease_until') is None)
            if blob['kind']=='asset':
                require(blob['job_id'] is None and blob['attempt_id'] is None)
                operation='invocation:'+inv['id'];relation=assets.get(key)
            else:
                require(blob['kind']=='artifact');job=jobs[blob['job_id']]
                require(job['invocation_id']==inv['id'] and job['attempt_id']==blob['attempt_id'])
                operation='job:'+job['id'];relation=artifacts.get(key)
            if relation is not None:
                for field in SCOPE:require(relation[field]==scope[field])
                require(relation['scope']==scope and relation['invocation_id']==inv['id'])
                for field in ('size','sha256','mime','properties','expires_at','purged_at'):require(relation[field]==blob[field])
                if blob['kind']=='artifact':require(relation['job_id']==blob['job_id'] and relation['attempt_id']==blob['attempt_id'])
            present=key in inventory
            if blob['state']=='attached':require(present and relation is not None and relation['id']==blob['attached_id'] and blob['purged_at'] is None)
            elif blob['state']=='staged':
                require(relation is None and blob['attached_id'] is None and blob['purged_at'] is None)
            else:require(not present and blob['purged_at'] is not None and blob['deleted_at'] is not None)
            if not present:require(source_owner is not None)
            metadata=inventory.get(key,{'uid':source_owner[0] if source_owner else 0,'gid':source_owner[1] if source_owner else 0,'mode':0o600})
            if present:
                require(metadata.keys()=={'key','size','sha256','uid','gid','mode'})
                require(metadata['size']==blob['size'] and metadata['sha256']==blob['sha256'])
                if source_owner is not None:require((metadata['uid'],metadata['gid'])==source_owner)
            row={'key':key,'state':blob['state'],'size':blob['size'],'sha256':blob['sha256'],
                 'uid':metadata['uid'],'gid':metadata['gid'],'mode':metadata['mode'],
                 'tenant_id':scope['tenant_id'],'product_id':scope['product_id'],
                 'operation_id':operation,'present':present,'missing_evidence_sha256':_missing_evidence(rows,blob,jobs,reservations,financial) if blob['state']=='staged' and not present else None}
            blobs.append(row);refs.append({field:row[field] for field in ('key','tenant_id','product_id','operation_id')})
        require(set(inventory)<=set(registry))
        registry_digest(blobs,refs)
        return blobs,refs
    except (KeyError,TypeError,ValueError,AttributeError,ClosureError):raise B2Error() from None

def encode_source_facts(raw_rows,dump_roles,original_config_items,snapshot_binding,sequence_observations,*,profile,schema,roles,workers,physical,projections,dump_schema,source_owner,maintenance_snapshot):
    value={'format':'easel-source-facts-v1','profile':profile.name,
           'snapshots':snapshot_binding,'schema_sha256':hashlib.sha256(canonical_bytes(schema)).hexdigest(),
           'schema':schema,'dumps':dump_roles,'configs':original_config_items,'rows':raw_rows,
           'physical':physical,'sequences':sequence_observations,'roles':roles,'workers':workers,'snapshot_projections':projections,'dump_schema':dump_schema,'source_owner':list(source_owner),'maintenance_snapshot':maintenance_snapshot}
    raw=canonical_bytes(value);validate_source_facts(raw);return raw

def validate_source_facts(raw):
    try:
        value=strict_json(raw,MAX_FACTS_BYTES)
        require(type(value) is dict and value.keys()==FACT_KEYS and value['format']=='easel-source-facts-v1')
        require(value['profile'] in PROFILES);profile=PROFILES[value['profile']]
        require(type(value['source_owner']) is list and len(value['source_owner'])==2 and all(type(v) is int and 0<=v<2**32 for v in value['source_owner']))
        require(type(value['snapshots']) is dict and value['snapshots'].keys()=={'central','identity','cross_database_atomic'})
        require(value['snapshots']['cross_database_atomic'] is False)
        for key in ('central','identity'):require(type(value['snapshots'][key]) is str and value['snapshots'][key])
        validate_hash(value['schema_sha256']);require(hashlib.sha256(canonical_bytes(value['schema'])).hexdigest()==value['schema_sha256'])
        require(type(value['rows']) is dict and value['rows'].keys()==set(profile.tables))
        require(type(value['schema']) is dict and value['schema'].keys()=={'migrations','relations','identity'})
        require(value['schema']['migrations']==[list(row) for row in profile.migrations])
        identity=value['schema']['identity']
        require(type(identity) is dict and identity.keys()=={'profile','relations','image_sha256','public_sql'})
        if identity['profile']==LOGTO_145.name:
            require(identity['image_sha256']==LOGTO_145.image_sha256 and identity['public_sql']==[list(row) for row in LOGTO_145.public_sql])
            validate_profile_catalog(LOGTO_145,identity['relations'])
            identity_tables=sorted({r[1] for r in identity['relations']['columns'] if r[0]=='public' and r[2] in {'r','p'}})
            validate_identity_inventory(LOGTO_145,identity_tables,identity['relations']['enums'])
        else:
            require(identity['profile'] in PROFILES and identity['image_sha256'] is None and identity['public_sql']==[])
            validate_profile_catalog(PROFILES[identity['profile']],identity['relations'])
            require(sorted({r[1] for r in identity['relations']['columns'] if r[0]=='public' and r[2] in {'r','p'}})==list(PROFILES[identity['profile']].tables))
        relations=value['schema']['relations']
        require(type(relations) is dict and relations.keys()=={'columns','constraints','functions','enums','triggers','indexes','policies','namespaces','default_acls','database','types','relation_options','sequence_definitions','rules','operators','collations','casts','extensions','event_triggers'})
        validate_profile_catalog(profile,relations)
        from .maintenance_snapshot import validate_snapshot,validate_worker_binding
        maintenance=validate_snapshot(value['maintenance_snapshot'],snapshot=value['snapshots']['central'],catalog=relations)
        control=maintenance['tables']['control'][0]
        require(control['state']=='CAPTURING' and control['window_id']==value['workers']['window_id'])
        require(control['binding']['generation_digest']==value['workers']['generation_digest'])
        validate_worker_binding(maintenance,value['workers'])
        columns={name:set() for name in profile.tables}
        for row in relations['columns']:
            require(type(row) is list and len(row)==17)
            if row[0]=='public' and row[2] in {'r','p'}:
                require(row[1] in columns)
                if row[6] is not None:columns[row[1]].add(row[6])
        require(all(columns.values()))
        for name,items in value['rows'].items():
            require(type(items) is list and len(items)<=100000)
            require(all(type(row) is dict and row.keys()==columns[name] for row in items))
        validate_raw_relations(value['rows'],relations)
        require(sorted((r['version'],r['checksum']) for r in value['rows']['schema_migrations'])==sorted(profile.migrations))
        require(type(value['dumps']) is list and len(value['dumps'])==4 and {r['name'] for r in value['dumps']}==DB_FILES)
        require(type(value['configs']) is dict and value['configs'].keys()=={'central','identity','media'})
        require('maintenance' in {i['name'] for i in value['configs']['central']})
        require(type(value['workers']) is dict and value['workers'].keys()=={'generation_digest','window_id','host_id','boot_id','process_start','domain','registry','registry_sha256'})
        validate_hash(value['workers']['generation_digest'])
        worker=value['workers'];validate_hash(worker['registry_sha256'])
        require(type(worker['registry']) is list and hashlib.sha256(canonical_bytes(worker['registry'])).hexdigest()==worker['registry_sha256'])
        domain=worker['domain']
        require(type(domain) is dict and domain.keys()=={'control_dev','control_ino','control_fs_sha256','control_namespace_sha256','source_dev','source_ino','source_fs_sha256','source_namespace_sha256'})
        for name,item in domain.items():
            if name.endswith('_sha256'):validate_hash(item)
            else:require(type(item) is int and item>=0)
        for row in worker['registry']:
            require(type(row) is dict and row.keys()=={'worker_id','generation','identity','active','domain'})
            require(row['active'] is True and row['domain']==domain)
            require(row['identity']['worker_id']==row['worker_id'] and row['identity']['generation']==row['generation'])
            require(row['identity']['host_id']==worker['host_id'] and row['identity']['boot_id']==worker['boot_id'])
        require(type(value['dump_schema']) is dict and value['dump_schema'].keys()=={'central','identity'})
        for kind in ('central','identity'):
            ddl=value['dump_schema'][kind]
            require(type(ddl) is dict and ddl.keys()=={'snapshot','sha256','size'} and ddl['snapshot']==value['snapshots'][kind])
            validate_hash(ddl['sha256']);require(type(ddl['size']) is int and 0<ddl['size']<=8*1024**2)
            observed=value['sequences'][kind];require(observed.keys()=={'before','dump','after'})
            require(canonical_bytes(observed['before'])==canonical_bytes(observed['dump'])==canonical_bytes(observed['after']))
            require(all(row['schema']=='public' for row in observed['before']))
            role=value['roles'][kind];require(role.keys()=={'before','during','after'})
            require(canonical_bytes(role['before'])==canonical_bytes(role['during'])==canonical_bytes(role['after']))
            # Actual role export digest is derivable from raw role observations,
            # not a freestanding digest assertion in source facts.
            record=next(r for r in value['dumps'] if r['name']=='db/'+kind+'.roles')
            exported=canonical_bytes({'format':'easel-roles-v1','inventory':role['before']})
            require(record['size']==len(exported) and record['sha256']==hashlib.sha256(exported).hexdigest())
        projections=value['snapshot_projections'];require(type(projections) is dict and projections.keys()=={'central','identity'})
        require(set(projections['central'])=={'public.'+name for name in profile.tables})
        identity_profile=LOGTO_145 if identity['profile']==LOGTO_145.name else PROFILES[identity['profile']]
        require(set(projections['identity'])=={'public.'+name for name in identity_profile.tables})
        for inventory in projections.values():
            require(type(inventory) is dict and len(inventory)<=1000)
            for row in inventory.values():
                require(type(row) is dict and row.keys()=={'columns','count','rows_sha256'})
                require(type(row['count']) is int and 0<=row['count']<=100000)
                require(type(row['columns']) is list and len(row['columns'])<=512 and len(set(row['columns']))==len(row['columns']))
                validate_hash(row['rows_sha256'])
        for table,items in value['rows'].items():require(projections['central']['public.'+table]['count']==len(items))
        derive_registry(value['rows'],value['physical'],source_owner=tuple(value['source_owner']))
        return value
    except (KeyError,TypeError,ValueError,AttributeError):raise B2Error() from None

def verify_archived_bundles(bundles,fixed_records,plan):
    """Called only after actual archive member SHA/size streaming validation."""
    try:
        require(type(bundles) is dict and bundles.keys()=={'config/central','config/identity','config/media'})
        require(all(type(v) is bytes and len(v)<=MAX_BUNDLE_BYTES for v in bundles.values()))
        require(sum(map(len,bundles.values()))<=MAX_BUNDLES_BYTES)
        normalized=validate_plan(plan);require(type(fixed_records) is list)
        records=_by(fixed_records,'name');expected=_by(normalized['files'],'name')
        require(records==expected)
        parsed={kind:decode_bundle(bundles['config/'+kind],kind) for kind in ('central','identity','media')}
        central=parsed['central'];raw=canonical_bytes(central['source_facts']);facts=validate_source_facts(raw)
        digest=hashlib.sha256(raw).hexdigest()
        require(digest==central['source_facts_sha256']==normalized['snapshots']['source_sha256'])
        require(facts['schema_sha256']==normalized['snapshots']['schema_sha256'])
        for kind in ('central','identity'):
            require(facts['snapshots'][kind]==normalized['snapshots'][kind])
        require(_by(facts['dumps'],'name')=={name:records[name] for name in DB_FILES})
        for kind,bundle in parsed.items():
            require(item_metadata(bundle['items'])==facts['configs'][kind])
            record=records['config/'+kind];raw_bundle=bundles['config/'+kind]
            require(record['size']==len(raw_bundle) and record['sha256']==hashlib.sha256(raw_bundle).hexdigest())
        blobs,refs=derive_registry(facts['rows'],facts['physical'],source_owner=tuple(facts['source_owner']))
        require(blobs==normalized['blobs'] and refs==normalized['references'])
        return {'source_facts_sha256':digest,'registry_sha256':normalized['registry_sha256'],
                'raw_registry_count':len(blobs),'profile':facts['profile']}
    except (KeyError,TypeError,ValueError,AttributeError,ClosureError):raise B2Error() from None
