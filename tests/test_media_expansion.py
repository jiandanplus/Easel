"""M4 uses synthetic central definitions; no commercial calls."""
from copy import deepcopy
import pytest
from tests.test_central_media_host import definition


def expanded(capability='audio.speech'):
    row=definition();row.update(id='fixture-'+capability,capability=capability)
    row['parameters']={'type':'object','properties':{'response_format':{'type':'string','enum':['wav']}},'required':['response_format'],'additionalProperties':False}
    row['rate']['meters']=[{'unit':'unicode_scalar','basis':'verified_input_v1','numerator':'3','denominator':'2'}, {'unit':'audio_ms','basis':'verified_delivery_v1','numerator':'7','denominator':'3'}]
    row['limits'].update(max_artifact_bytes=67108864,max_units={'unicode_scalar':500,'audio_ms':60000},output_media={'mime_types':['audio/wav'],'max_files':1,'max_duration_ms':60000,'max_channels':2,'max_sample_rate':48000,'require_audio':True})
    return row


def test_catalog_expansion_preserves_all_exact_meters_and_schema(tmp_path):
    import httpx
    from tests.test_central_media_host import Fixture,setup
    fixture=Fixture();row=expanded()
    client,_,_=setup(tmp_path,lambda r:httpx.Response(200,json={'models':[row]}) if r.url.path.endswith('/media-models') else fixture(r))
    assert client.catalog()['models']==[row]


@pytest.mark.parametrize('mutation',[lambda r:r['parameters']['properties'].update(supplier_url={'type':'string','maxLength':100}),lambda r:r['rate']['meters'][0].update(unit='usd'),lambda r:r['limits']['output_media'].update(mime_types=['audio/unknown']),lambda r:r['limits'].update(max_artifact_bytes=67108865)])
def test_unsupported_model_not_advertised(tmp_path,mutation):
    import httpx
    from tests.test_central_media_host import Fixture,setup
    fixture=Fixture();row=expanded();mutation(row)
    client,_,_=setup(tmp_path,lambda r:httpx.Response(200,json={'models':[row]}) if r.url.path.endswith('/media-models') else fixture(r))
    assert client.catalog()['models']==[]


def test_records_keep_image_turn_file_and_independent_capability_buckets(tmp_path):
    from easel.platform.media_records import MediaRecords,digest,MediaRecordError
    records=MediaRecords(tmp_path);base=dict(owner=digest('owner'),scope={},session_id='s',turn_id='t',tool_id='image',model='fixture',body_hash=digest('body'))
    image,_=records.claim(**base)
    speech,_=records.claim(**{**base,'tool_id':'speech'},capability='audio.speech')
    assert image['turn_key']==speech['turn_key'] and image['operation_key']!=speech['operation_key']
    assert len(records.rows(base['owner'],image['turn_key'])[0])==2
    assert records.owned(base['owner'],operation_key=speech['operation_key'])==speech
    for capability in ['image.edit','image.variation','image.generate']:
        with pytest.raises(MediaRecordError):records.claim(**{**base,'tool_id':'new'},capability=capability)
    with pytest.raises(MediaRecordError):records.claim(**{**base,'tool_id':'retry'},capability='audio.speech')
    assert records.claim(**{**base,'tool_id':'speech'},capability='audio.speech')==(speech,False)


class MultimediaFixture:
    def __init__(self,capability='audio.speech'):
        from tests.test_central_media_host import Fixture
        import io,wave
        from hashlib import sha256
        self.base=Fixture();self.row=expanded(capability);self.capability=capability
        stream=io.BytesIO()
        with wave.open(stream,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\0\0'*8000)
        self.raw=stream.getvalue()
        self.base.artifact.update(mime='audio/wav',size=len(self.raw),sha256=sha256(self.raw).hexdigest(),properties={'format':'WAV','duration_ms':1000,'channels':1,'sample_rate':8000})
        self.base.job.update(model=self.row['id'],capability=capability)
        self.queued=False
    def __call__(self,request):
        import httpx
        if request.url.path.endswith('/media-models'):
            self.base.calls.append(request);return httpx.Response(200,json={'models':[self.row]})
        if request.url.path.endswith('/content'):
            self.base.calls.append(request);return httpx.Response(200,content=self.raw,headers={'content-type':self.base.artifact['mime'],'etag':'"'+self.base.artifact['sha256']+'"'})
        if self.queued and request.url.path=='/v2/media/jobs':
            self.base.calls.append(request);return httpx.Response(202,json={**self.base.job,'artifacts':[],'execution_state':'queued','delivery_state':'processing','status':'dispatched','financial_state':'dispatched','actual':None})
        return self.base(request)


def test_speech_creates_and_downloads_original_owned_wave(tmp_path):
    from tests.test_central_media_host import setup
    fixture=MultimediaFixture();_,host,_=setup(tmp_path,fixture)
    result=host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.speech','prompt':'hello','parameters':{'response_format':'wav'}},'speech')
    assert result['ok'],result
    saved=host.execute('download_media_artifact',{'job_id':fixture.base.job_id,'artifact_id':fixture.base.artifact_id,'path':'outputs/speech.wav'},'save')
    assert saved['ok'],saved
    assert (tmp_path/'outputs/speech.wav').read_bytes()==fixture.raw


def test_async_queued_job_is_returned_without_polling_or_reposting(tmp_path):
    from tests.test_central_media_host import setup
    fixture=MultimediaFixture();fixture.queued=True;_,host,_=setup(tmp_path,fixture)
    result=host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.speech','prompt':'hello','parameters':{'response_format':'wav'}},'speech')
    assert result['ok'] and result['job']['execution_state']=='queued'
    assert len([r for r in fixture.base.calls if r.url.path=='/v2/media/jobs'])==1


class UploadFixture(MultimediaFixture):
    def __init__(self,capability='audio.transcribe'):
        from uuid import uuid4
        super().__init__(capability)
        self.row['limits'].update(max_assets=1,input_media=self.row['limits'].pop('output_media'))
        self.row['parameters']={'type':'object','properties':{},'required':[],'additionalProperties':False}
        self.asset={**self.base.artifact,'id':str(uuid4())};self.sample=self.raw;self.upload_timeout=False
        self.voice={'id':str(uuid4()),'state':'ready','name':'Fixture','language':'zh','expires_at':'2027-10-27T00:00:00+00:00'}
        if capability=='audio.transcribe':
            from hashlib import sha256
            self.raw=b'{"text":"hello"}'
            self.base.artifact.update(mime='application/json',size=len(self.raw),sha256=sha256(self.raw).hexdigest(),properties={'unicode_scalar':5})
        else:
            self.row['rate']['meters']=[{'unit':'registration','basis':'verified_delivery_v1','numerator':'1','denominator':'1'}]
            self.row['limits']['max_units']={'registration':1}
            self.base.job.update(artifacts=[],voice=self.voice)
    def __call__(self,request):
        import httpx
        if request.url.path=='/v2/media/assets':
            self.base.calls.append(request)
            assert request.content==self.sample
            assert request.headers['authorization']=='Bearer MEDIA-TASK-TOKEN-SECRET'
            if self.upload_timeout:raise httpx.ReadTimeout('synthetic',request=request)
            return httpx.Response(200,json=self.asset)
        if request.url.path.startswith('/v2/media/voices/'):
            self.base.calls.append(request);return httpx.Response(200,json=self.voice)
        return super().__call__(request)


def test_transcription_freezes_and_uploads_sample_without_local_path_then_saves_json(tmp_path):
    import json
    from tests.test_central_media_host import setup
    fixture=UploadFixture();(tmp_path/'sample.wav').write_bytes(fixture.sample)
    client,host,_=setup(tmp_path,fixture)
    args={'model':fixture.row['id'],'capability':'audio.transcribe','assets':['sample.wav']}
    result=host.execute('create_media',args,'transcribe')
    assert result['ok'],result
    post=next(r for r in fixture.base.calls if r.url.path=='/v2/media/jobs')
    assert json.loads(post.content)['assets']==[fixture.asset['id']]
    assert b'sample.wav' not in post.content
    assert host.execute('download_media_artifact',{'job_id':fixture.base.job_id,'artifact_id':fixture.base.artifact_id,'path':'outputs/transcript.json'},'download')['ok']
    assert (tmp_path/'outputs/transcript.json').read_bytes()==fixture.raw
    assert host.execute('create_media',args,'transcribe')['ok']
    assert len([r for r in fixture.base.calls if r.url.path=='/v2/media/assets'])==1


def test_upload_unknown_never_submits_or_reuploads_on_replay(tmp_path):
    from tests.test_central_media_host import setup
    fixture=UploadFixture();fixture.upload_timeout=True;(tmp_path/'sample.wav').write_bytes(fixture.sample)
    _,host,_=setup(tmp_path,fixture);args={'model':fixture.row['id'],'capability':'audio.transcribe','assets':['sample.wav']}
    first=host.execute('create_media',args,'transcribe')
    assert not first['ok'] and first['local_status']=='upload_unknown'
    host.execute('create_media',args,'transcribe')
    assert len([r for r in fixture.base.calls if r.method=='POST'])==2
    assert not any(r.url.path=='/v2/media/jobs' for r in fixture.base.calls)


@pytest.mark.parametrize('path',['.env','runtime/sample.wav','outputs/_platform/sample.wav','../sample.wav','linked.wav'])
def test_untrusted_assets_rejected_before_any_post(tmp_path,path):
    from tests.test_central_media_host import setup
    fixture=UploadFixture();(tmp_path/'sample.wav').write_bytes(fixture.sample);(tmp_path/'linked.wav').symlink_to(tmp_path/'sample.wav')
    _,host,_=setup(tmp_path,fixture)
    assert not host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.transcribe','assets':[path]},'bad')['ok']
    assert not any(r.method=='POST' for r in fixture.base.calls)


def test_enrollment_publishes_only_actual_ready_owned_voice(tmp_path):
    from tests.test_central_media_host import setup
    fixture=UploadFixture('voice.enroll');(tmp_path/'sample.wav').write_bytes(fixture.sample);_,host,_=setup(tmp_path,fixture)
    result=host.execute('create_media',{'model':fixture.row['id'],'capability':'voice.enroll','assets':['sample.wav']},'enroll')
    assert result['ok'] and result['job']['voice']==fixture.voice


def test_same_tool_id_in_distinct_buckets_has_distinct_operation_key(tmp_path):
    from easel.platform.media_records import MediaRecords,digest
    records=MediaRecords(tmp_path);kwargs=dict(owner=digest('o'),scope={},session_id='s',turn_id='t',tool_id='same',model='m',body_hash=digest('b'))
    first,_=records.claim(**kwargs,capability='audio.speech');second,_=records.claim(**kwargs,capability='video.generate')
    assert first['operation_key']!=second['operation_key']


@pytest.mark.parametrize('capability',['video.generate','music.generate','voice.synthesize'])
def test_other_capabilities_use_exact_schema_and_return_original_async_job(tmp_path,capability):
    import httpx
    from uuid import uuid4
    from tests.test_central_media_host import setup
    fixture=MultimediaFixture(capability);fixture.queued=True
    if capability=='video.generate':
        fixture.row['limits']['output_media'].update(mime_types=['video/mp4'],max_pixels=1048576)
        fixture.row['parameters']={'type':'object','properties':{'seconds':{'type':'integer','minimum':1,'maximum':5},'size':{'type':'string','enum':['1024x1024']}},'required':['seconds','size'],'additionalProperties':False}
        params={'seconds':5,'size':'1024x1024'}
    elif capability=='music.generate':
        fixture.row['parameters']={'type':'object','properties':{'is_instrumental':{'type':'boolean'},'format':{'type':'string','enum':['wav']}},'required':['format'],'additionalProperties':False}
        params={'format':'wav','is_instrumental':True}
    else:params={'response_format':'wav'}
    voice={'id':str(uuid4()),'name':'fixture','language':'zh','state':'ready','expires_at':'2027-10-27T00:00:00+00:00'}
    def handler(request):
        if request.url.path.startswith('/v2/media/voices/'):
            fixture.base.calls.append(request);return httpx.Response(200,json=voice)
        return fixture(request)
    _,host,_=setup(tmp_path,handler)
    args={'model':fixture.row['id'],'capability':capability,'prompt':'hello','parameters':params}
    if capability=='voice.synthesize':args['voice_id']=voice['id']
    result=host.execute('create_media',args,'call')
    assert result['ok'],result
    assert result['job']['execution_state']=='queued'
    assert not host.execute('create_media',args,'different-tool')['ok']
    assert len([r for r in fixture.base.calls if r.url.path=='/v2/media/jobs'])==1


@pytest.mark.parametrize('mutation',[lambda m:m.update(mime='image/png',properties={'format':'PNG','width':1024,'height':1024}),lambda m:m.update(size=67108865),lambda m:m['properties'].update(provider_voice_id='private')])
def test_speech_malformed_or_cross_capability_artifacts_never_report_delivered(tmp_path,mutation):
    from tests.test_central_media_host import setup
    fixture=MultimediaFixture();mutation(fixture.base.artifact);_,host,_=setup(tmp_path,fixture)
    assert not host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.speech','prompt':'hello','parameters':{'response_format':'wav'}},'call')['ok']


def test_upload_mismatch_stops_before_generation(tmp_path):
    from tests.test_central_media_host import setup
    fixture=UploadFixture();fixture.asset['sha256']='0'*64;(tmp_path/'sample.wav').write_bytes(fixture.sample)
    _,host,_=setup(tmp_path,fixture)
    result=host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.transcribe','assets':['sample.wav']},'call')
    assert not result['ok'] and result['local_status']=='upload_unknown'
    assert not any(r.url.path=='/v2/media/jobs' for r in fixture.base.calls)


@pytest.mark.parametrize('fault',['bytes','header','etag','extension','stop','logout'])
def test_audio_download_integrity_and_late_publication_fence(tmp_path,monkeypatch,fault):
    import httpx
    from tests.test_central_media_host import setup,ORIGIN
    import easel.platform.media as media
    fixture=MultimediaFixture()
    def handler(r):
        response=fixture(r)
        if r.url.path.endswith('/content'):
            if fault=='bytes':return httpx.Response(200,content=b'broken',headers=response.headers)
            if fault=='header':response.headers['content-type']='text/html'
            if fault=='etag':response.headers['etag']='"wrong"'
        return response
    client,host,_=setup(tmp_path,handler)
    args={'model':fixture.row['id'],'capability':'audio.speech','prompt':'hello','parameters':{'response_format':'wav'}}
    assert host.execute('create_media',args,'call')['ok']
    if fault in {'stop','logout'}:
        save=media._save_media
        def late(*args,**kwargs):
            if fault=='stop':host.stop()
            else:
                with client.central.store.locked():client.central.store.write({'version':2,'url':ORIGIN})
            return save(*args,**kwargs)
        monkeypatch.setattr(media,'_save_media',late)
    path='outputs/wrong.png' if fault=='extension' else 'outputs/audio.wav'
    result=host.execute('download_media_artifact',{'job_id':fixture.base.job_id,'artifact_id':fixture.base.artifact_id,'path':path},'download')
    assert not result['ok'] and not (tmp_path/path).exists()


def test_voice_language_may_be_null_and_enroll_then_synthesize_has_two_original_jobs(tmp_path):
    import httpx,json
    from uuid import uuid4
    from tests.test_central_media_host import setup
    enroll=UploadFixture('voice.enroll');enroll.voice['language']=None
    synth=MultimediaFixture('voice.synthesize');synth.base.job_id=str(uuid4());synth.base.job['id']=synth.base.job_id
    (tmp_path/'sample.wav').write_bytes(enroll.sample)
    def handler(r):
        if r.url.path.endswith('/media-models'):return httpx.Response(200,json={'models':[enroll.row,synth.row]})
        if r.url.path=='/v2/media/jobs' and json.loads(r.content)['capability']=='voice.synthesize':return synth(r)
        return enroll(r)
    client,host,_=setup(tmp_path,handler)
    first=host.execute('create_media',{'model':enroll.row['id'],'capability':'voice.enroll','assets':['sample.wav']},'enroll')
    assert first['ok'],first
    second=host.execute('create_media',{'model':synth.row['id'],'capability':'voice.synthesize','voice_id':first['job']['voice']['id'],'prompt':'hello','parameters':{'response_format':'wav'}},'synthesize')
    assert second['ok'],second
    assert first['operation_key']!=second['operation_key'] and first['job']['id']!=second['job']['id']
    rows=list((tmp_path/'outputs/_harness/central-media/operations').glob('turn-*.json'))
    assert len(rows)==2 and all('SECRET' not in r.read_text() and 'sample.wav' not in r.read_text() for r in rows)


def test_stop_during_job_read_cannot_return_late_artifacts(tmp_path):
    from tests.test_central_media_host import setup
    fixture=MultimediaFixture();_,host,_=setup(tmp_path,fixture)
    assert host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.speech','prompt':'hello','parameters':{'response_format':'wav'}},'call')['ok']
    fixture.base.hook=lambda r:host.stop() if r.url.path=='/v2/media/jobs/'+fixture.base.job_id else None
    result=host.execute('get_media_job',{'job_id':fixture.base.job_id},'query')
    assert not result['ok'] and 'job' not in result


@pytest.mark.parametrize('capability',['image.edit','image.variation'])
def test_image_input_capabilities_share_original_image_bucket(tmp_path,capability):
    import httpx,json
    from uuid import uuid4
    from tests.test_central_media_host import Fixture,setup,definition
    fixture=Fixture();row=definition();row.update(id='fixture-'+capability,capability=capability);row['limits']['max_assets']=1
    fixture.job.update(model=row['id'],capability=capability)
    asset={**fixture.artifact,'id':str(uuid4())};(tmp_path/'input.png').write_bytes(fixture.raw)
    def handler(r):
        if r.url.path.endswith('/media-models'):return httpx.Response(200,json={'models':[row]})
        if r.url.path=='/v2/media/assets':fixture.calls.append(r);return httpx.Response(200,json=asset)
        return fixture(r)
    client,host,_=setup(tmp_path,handler)
    args={'model':row['id'],'capability':capability,'assets':['input.png'],'prompt':'edit'}
    first=host.execute('create_media',args,'edit');assert first['ok'],first
    assert not host.execute('create_media',args,'second')['ok']
    posts=[r for r in fixture.calls if r.url.path=='/v2/media/jobs']
    assert len(posts)==1 and json.loads(posts[0].content)['assets']==[asset['id']]
    records=list((tmp_path/'outputs/_harness/central-media/operations').glob('turn-*.json'))
    assert len(records)==1 and records[0].stem.count('-')==1


@pytest.mark.parametrize('kind,mime,suffix,format_', [('video','video/mp4','.mp4','MP4'),('audio','audio/mpeg','.mp3','MP3'),('audio','audio/ogg','.ogg','OGG'),('audio','audio/mp4','.m4a','MP4')])
def test_original_av_artifacts_save_with_hash_and_matching_container(tmp_path,kind,mime,suffix,format_):
    import subprocess,shutil
    from hashlib import sha256
    from contextlib import nullcontext
    from easel.platform.media_files import save
    ffmpeg=shutil.which('ffmpeg');assert ffmpeg,'Existing ffmpeg required only for controlled test fixture generation'
    path=tmp_path/('source'+suffix)
    source=['-f','lavfi','-i','color=c=blue:s=32x32:r=10:d=0.5','-c:v','mpeg4','-an'] if kind=='video' else ['-f','lavfi','-i','sine=frequency=440:duration=0.5','-c:a',{'audio/mpeg':'libmp3lame','audio/ogg':'libopus','audio/mp4':'aac'}[mime]]
    subprocess.run([ffmpeg,'-nostdin','-v','error','-threads','1',*source,str(path)],check=True,capture_output=True,timeout=10)
    raw=path.read_bytes();props={'format':format_,'duration_ms':500}
    props.update({'width':32,'height':32} if kind=='video' else {'channels':1,'sample_rate':48000 if mime=='audio/ogg' else 44100})
    metadata={'mime':mime,'size':len(raw),'sha256':sha256(raw).hexdigest(),'properties':props}
    result=save(tmp_path,'outputs/final'+suffix,raw,metadata,publish_guard=nullcontext)
    assert (tmp_path/result).read_bytes()==raw
    with pytest.raises(FileExistsError):save(tmp_path,'outputs/final'+suffix,raw,metadata,publish_guard=nullcontext)


def test_legacy_record_without_capability_remains_readable_and_unchanged(tmp_path):
    import json
    from easel.platform.media_records import MediaRecords,digest
    records=MediaRecords(tmp_path);owner=digest('o')
    row,_=records.claim(owner=owner,scope={},session_id='s',turn_id='t',tool_id='old',model='m',body_hash=digest('b'))
    file=next((tmp_path/'outputs/_harness/central-media/operations').glob('turn-*.json'))
    legacy=json.loads(file.read_text());del legacy['capability'];file.write_text(json.dumps(legacy));original=file.read_bytes()
    found=records.owned(owner,operation_key=row['operation_key'])
    assert found['capability']=='image.generate'
    records.update(found,local_status='unknown')
    assert file.read_bytes()==original
    assert records.rows(owner,row['turn_key'])[0][0]['capability']=='image.generate'


def test_image_artifact_keeps_original_twenty_mib_limit():
    from easel.platform.media import _artifact
    from tests.test_central_media_host import Fixture
    artifact=Fixture().artifact;artifact['size']=20*1024*1024+1
    with pytest.raises(ValueError):_artifact(artifact)


@pytest.mark.parametrize('headers',[{'content-length':'1'},{'content-encoding':'gzip'}])
def test_download_rejects_inconsistent_transport_headers(tmp_path,headers):
    from tests.test_central_media_host import setup
    fixture=MultimediaFixture()
    def handler(r):
        response=fixture(r)
        if r.url.path.endswith('/content'):response.headers.update(headers)
        return response
    _,host,_=setup(tmp_path,handler)
    assert host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.speech','prompt':'hello','parameters':{'response_format':'wav'}},'call')['ok']
    result=host.execute('download_media_artifact',{'job_id':fixture.base.job_id,'artifact_id':fixture.base.artifact_id,'path':'outputs/audio.wav'},'save')
    assert not result['ok'] and not (tmp_path/'outputs/audio.wav').exists()


def test_renewed_same_owner_can_read_and_download_but_old_turn_cannot_cancel(tmp_path):
    import httpx
    from tests.test_central_media_host import Fixture,setup,MODEL,ORIGIN
    from tests.test_central_platform_client import auth
    fixture=Fixture();client,host,_=setup(tmp_path,fixture)
    assert host.execute('create_media',{'model':MODEL,'prompt':'fixture'},'image')['ok']
    fresh=auth();fresh['scope']['session_id']=fresh['session_id']='renewed-session'
    with client.central.store.locked():client.central.store.write({'version':2,'url':ORIGIN,'auth':fresh})
    def handler(r):
        if r.url.path=='/v2/me':return httpx.Response(200,json={'account':fresh['account'],'scope':fresh['scope']})
        return fixture(r)
    client.central.transport=httpx.MockTransport(handler)
    assert host.execute('get_media_job',{'job_id':fixture.job_id},'read')['ok']
    assert host.execute('download_media_artifact',{'job_id':fixture.job_id,'artifact_id':fixture.artifact_id,'path':'outputs/recovered.png'},'download')['ok']
    assert not host.execute('cancel_media_job',{'job_id':fixture.job_id},'cancel')['ok']
    assert not any(r.url.path.endswith('/cancel') for r in fixture.calls)
    from dataclasses import replace
    from easel.platform.media import MediaHost
    current=MediaHost(replace(host.request,central_scope=fresh['scope']),client=client)
    result=current.execute('cancel_media_job',{'job_id':fixture.job_id},'current-cancel')
    assert result['ok'] and result['job']['financial_state']=='pending_reconciliation' and result['job']['actual'] is None


def test_logout_after_atomic_save_suppresses_late_link(tmp_path,monkeypatch):
    from tests.test_central_media_host import setup,ORIGIN
    fixture=MultimediaFixture();client,host,_=setup(tmp_path,fixture)
    assert host.execute('create_media',{'model':fixture.row['id'],'capability':'audio.speech','prompt':'hello','parameters':{'response_format':'wav'}},'call')['ok']
    update=host.records.update
    def logout(record,**changes):
        result=update(record,**changes)
        if changes.get('local_status')=='downloaded':
            with client.central.store.locked():client.central.store.write({'version':2,'url':ORIGIN})
        return result
    monkeypatch.setattr(host.records,'update',logout)
    result=host.execute('download_media_artifact',{'job_id':fixture.base.job_id,'artifact_id':fixture.base.artifact_id,'path':'outputs/audio.wav'},'save')
    assert not result['ok'] and 'path' not in result and 'job' not in result


def test_catalog_projects_rate_contract_without_nested_private_fields(tmp_path):
    import httpx,json
    from tests.test_central_media_host import Fixture,setup
    fixture=Fixture();row=expanded();expected=deepcopy(row)
    row['rate'].update(provider_id='synthetic-private-provider',owner={'user_id':'synthetic-private-owner'},internal_note='nonpublic')
    client,host,_=setup(tmp_path,lambda r:httpx.Response(200,json={'models':[row]}) if r.url.path.endswith('/media-models') else fixture(r))
    assert client.catalog()['models']==[expected]
    assert host.models()==[expected]
    assert 'synthetic-private' not in json.dumps(host.models())
