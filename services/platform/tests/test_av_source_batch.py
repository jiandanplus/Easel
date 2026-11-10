"""Offline batch tests: all fetch calls use explicit fakes, never sockets."""
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from av_sources.bounded_fetch import HttpLimits, _SOURCES, _new_directory, _new_file
from av_sources.smart_http import SourceTransportError, pkt_line


def module():
    name = "av_sources.source_batch"
    assert importlib.util.find_spec(name) is not None, "shared-budget coordinator is missing"
    return importlib.import_module(name)


class Clock:
    def __init__(self):
        self.value = 10.0
    def __call__(self):
        return self.value


class Fetch:
    def __init__(self, clock=None, mutation=None, failure=None):
        self.calls = []
        self.clock = clock
        self.mutation = mutation
        self.failure = failure
    def __call__(self, source, destination, limits):
        self.calls.append((source, Path(destination), limits))
        fd, absolute = _new_directory(destination)
        try:
            if self.failure:
                with _new_file(fd, 'partial.bin') as sink:
                    sink.write(b'partial')
                raise self.failure
            url, oid = _SOURCES[source]
            advert = (pkt_line(b'# service=git-upload-pack\n') + b'0000'
                      + pkt_line((oid + ' HEAD\0side-band-64k ofs-delta shallow\n').encode()) + b'0000')
            pack = struct.pack('>4sII', b'PACK', 2, 1) + b'fake-object'
            pack += hashlib.sha1(pack).digest()
            for name, content in [('advertisement.bin', advert), ('source.pack', pack)]:
                with _new_file(fd, name) as sink:
                    sink.write(content)
            body = len(advert) + len(pack) + 40
            receipt = dict(source_id=source, repository_url=url, commit_oid=oid,
                           destination=absolute, request_count=2, response_body_bytes=body,
                           total_input_bytes=body + 30, metadata_bytes=len(advert) + 30,
                           http_raw_input_metered=False,
                           advertisement_sha256=hashlib.sha256(advert).hexdigest(),
                           pack=dict(response_body_bytes=len(pack)+40, pack_bytes=len(pack),
                                     pack_sha256=hashlib.sha256(pack).hexdigest(),
                                     pack_checksum_sha1_verified=True, objects_declared=1,
                                     objects_verified=False, progress_bytes=0, shallow=[oid], packets=4),
                           transport_injected_for_tests=True, source_identity_verified=False,
                           build_inputs_adopted=False, os_resource_limits_verified=False)
            if self.mutation:
                self.mutation(receipt, Path(absolute))
            if self.clock:
                self.clock.value += 3
            return receipt
        finally:
            os.close(fd)


@pytest.fixture
def parent(tmp_path):
    tmp_path.chmod(0o700)
    return tmp_path


def test_shared_limits_and_fixed_deadline(parent):
    clock = Clock(); fetch = Fetch(clock)
    limits = HttpLimits(batch_seconds=12)
    result = module().fetch_sources(parent/'batch', limits, _fetch=fetch, _clock=clock)
    assert [c[0] for c in fetch.calls] == list(_SOURCES)
    first, second = [c[2] for c in fetch.calls]
    assert first.batch_seconds == 12 and second.batch_seconds == 9
    for key, count in [('total_body_bytes', 'response_body_bytes'),
                       ('total_input_bytes', 'total_input_bytes'),
                       ('metadata_bytes', 'metadata_bytes'), ('max_requests', 'request_count')]:
        assert getattr(second, key) == getattr(limits, key) - result['sources'][0][count]
    assert result['status'] == 'candidate'
    assert result['clock_injected_for_tests'] is True
    assert result['transport_injected_for_tests'] is True
    assert result['total_input_bytes'] == sum(s['total_input_bytes'] for s in result['sources'])
    assert result['source_identity_verified'] is False
    assert json.loads((parent/'batch'/'batch-receipt.json').read_text()) == result
    for p in (parent/'batch').rglob('*'):
        assert p.stat().st_mode & 0o777 == (0o700 if p.is_dir() else 0o600)


@pytest.mark.parametrize('error', [SourceTransportError('secret diagnostic'), KeyboardInterrupt(), SystemExit(9)])
def test_failure_stops_preserves_unknown(parent, error):
    fetch = Fetch(failure=error)
    with pytest.raises(type(error)) as caught:
        module().fetch_sources(parent/'batch', _fetch=fetch, _clock=Clock())
    assert caught.value is error and len(fetch.calls) == 1
    r = json.loads((parent/'batch'/'failure-receipt.json').read_text())
    assert r['status'] == 'failed' and r['unobserved_source'] == 'libplacebo'
    assert r['in_flight_counters'] is None and r['accounting_complete'] is False
    assert 'secret' not in json.dumps(r)
    assert (parent/'batch'/'libplacebo'/'partial.bin').read_bytes() == b'partial'


@pytest.mark.parametrize('key,value', [('source_id','other'), ('repository_url','https://evil.invalid'),
    ('commit_oid','0'*40), ('destination','/tmp/elsewhere'), ('request_count',True),
    ('request_count',0), ('response_body_bytes',-1), ('total_input_bytes',True),
    ('metadata_bytes',99999999), ('source_identity_verified',True),
    ('build_inputs_adopted',1), ('os_resource_limits_verified',True),
    ('dns_ip_egress_verified',True), ('http_raw_input_metered',True),
    ('transport_injected_for_tests',False), ('advertisement_sha256','0'*64)])
def test_malicious_receipt(parent, key, value):
    fetch=Fetch(mutation=lambda r,p: r.update({key:value}))
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch', _fetch=fetch, _clock=Clock())
    assert len(fetch.calls)==1


@pytest.mark.parametrize('key,value', [('pack_bytes',True), ('pack_sha256','0'*64),
    ('objects_verified',True), ('pack_checksum_sha1_verified',1), ('objects_declared',0),
    ('packets',True), ('progress_bytes',-1), ('shallow',['0'*40]), ('response_body_bytes',99999999)])
def test_malicious_pack_receipt(parent,key,value):
    fetch=Fetch(mutation=lambda r,p: r['pack'].update({key:value}))
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch', _fetch=fetch, _clock=Clock())
    assert len(fetch.calls)==1


@pytest.mark.parametrize('value', [float('nan'),float('inf'),-float('inf'),True,'10'])
def test_invalid_clock(parent,value):
    fetch=Fetch()
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch', _fetch=fetch, _clock=lambda:value)
    assert not fetch.calls


def test_clock_backwards_and_expiry(parent):
    for label, new_time in [('backward',9), ('expired',610)]:
        clock=Clock()
        def change(r,p): clock.value=new_time
        fetch=Fetch(mutation=change)
        with pytest.raises(SourceTransportError):
            module().fetch_sources(parent/label, _fetch=fetch, _clock=clock)
        assert len(fetch.calls)==1


@pytest.mark.parametrize('limit', ['max_requests','total_body_bytes','total_input_bytes','metadata_bytes'])
def test_exhaustion_does_not_start_second(parent,limit):
    sample=Fetch()
    # Probe only the synthetic fixture, never real transport.
    receipt=sample('libplacebo',parent/'sample',HttpLimits())
    val={'max_requests':2,'total_body_bytes':receipt['response_body_bytes'],
         'total_input_bytes':receipt['total_input_bytes'],'metadata_bytes':receipt['metadata_bytes']}[limit]
    fetch=Fetch()
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch',replace(HttpLimits(),**{limit:val}),_fetch=fetch,_clock=Clock())
    assert len(fetch.calls)==1


@pytest.mark.parametrize('kind', ['exists','symlink','public','dotdot'])
def test_unsafe_destination(parent,kind):
    destination=parent/'batch'
    if kind=='exists': destination.mkdir(mode=0o700)
    if kind=='symlink': destination.symlink_to(parent, target_is_directory=True)
    if kind=='public': parent.chmod(0o755)
    if kind=='dotdot': destination=parent/'..'/'batch'
    fetch=Fetch()
    with pytest.raises(SourceTransportError):
        module().fetch_sources(destination,_fetch=fetch,_clock=Clock())
    assert not fetch.calls


@pytest.mark.parametrize('field,value', [('metadata_bytes',True), ('total_body_bytes',0),
    ('total_input_bytes',-1), ('max_requests',1), ('response_body_bytes',67108865),
    ('batch_seconds',601), ('request_seconds',True), ('batch_seconds',float('nan'))])
def test_invalid_limits_never_fetch(parent,field,value):
    fetch=Fetch()
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch',replace(HttpLimits(),**{field:value}),_fetch=fetch,_clock=Clock())
    assert not fetch.calls and not (parent/'batch').exists()


def test_response_cap_is_per_response_and_remaining_is_clipped(parent):
    fetch=Fetch()
    result=module().fetch_sources(parent/'batch',HttpLimits(response_body_bytes=200),_fetch=fetch,_clock=Clock())
    assert result['sources'][0]['response_body_bytes']>200
    assert all(call[2].response_body_bytes==200 for call in fetch.calls)


@pytest.mark.parametrize('filename', ['source.pack','advertisement.bin'])
@pytest.mark.parametrize('mutation', ['corrupt','symlink','hardlink','public','missing'])
def test_file_replacement_is_rejected(parent,filename,mutation):
    def damage(r,p):
        target=p/filename
        if mutation=='corrupt':
            target.write_bytes(b'corrupt')
        if mutation=='symlink':
            target.unlink(); target.symlink_to(p/'other')
        if mutation=='hardlink':
            os.link(target,p/'other')
        if mutation=='public': target.chmod(0o644)
        if mutation=='missing': target.unlink()
    fetch=Fetch(mutation=damage)
    with pytest.raises((SourceTransportError,OSError)):
        module().fetch_sources(parent/'batch',_fetch=fetch,_clock=Clock())
    assert len(fetch.calls)==1
    assert json.loads((parent/'batch'/'failure-receipt.json').read_text())['accounting_complete'] is False


def test_second_source_failure_keeps_validated_prefix(parent):
    success=Fetch(); failure=Fetch(failure=SourceTransportError('private raw error'))
    def fetch(source,destination,limits):
        return (success if source=='libplacebo' else failure)(source,destination,limits)
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch',_fetch=fetch,_clock=Clock())
    r=json.loads((parent/'batch'/'failure-receipt.json').read_text())
    assert [s['source_id'] for s in r['sources']]==['libplacebo']
    assert r['request_count']==2 and r['unobserved_source']=='vulkan_headers'
    assert r['accounting_complete'] is False and not (parent/'batch'/'batch-receipt.json').exists()
    assert (parent/'batch'/'libplacebo-receipt.json').exists()


def test_receipt_write_failure_preserves_original_exception(parent,monkeypatch):
    m=module(); error=KeyboardInterrupt(); fetch=Fetch(failure=error)
    def fail(*args): raise OSError('private disk error')
    monkeypatch.setattr(m,'_write',fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        m.fetch_sources(parent/'batch',_fetch=fetch,_clock=Clock())
    assert caught.value is error
    assert any('Failure receipt' in note for note in error.__notes__)
    assert len(fetch.calls)==1 and not (parent/'batch'/'batch-receipt.json').exists()


@pytest.mark.parametrize('receipt_name', ['libplacebo-receipt.json','batch-receipt.json'])
def test_write_failure_never_returns_success(parent,monkeypatch,receipt_name):
    m=module(); write=m._write; fetch=Fetch()
    def fail(fd,name,receipt):
        if name==receipt_name: raise OSError('disk full')
        return write(fd,name,receipt)
    monkeypatch.setattr(m,'_write',fail)
    with pytest.raises(OSError):
        m.fetch_sources(parent/'batch',_fetch=fetch,_clock=Clock())
    assert json.loads((parent/'batch'/'failure-receipt.json').read_text())['status']=='failed'
    assert len(fetch.calls)==(1 if receipt_name.startswith('libplacebo') else 2)


def test_durable_write_deadline_checked_before_success(parent,monkeypatch):
    m=module(); write=m._write; clock=Clock(); fetch=Fetch()
    def late(fd,name,receipt):
        result=write(fd,name,receipt)
        if name=='batch-receipt.json': clock.value=610
        return result
    monkeypatch.setattr(m,'_write',late)
    with pytest.raises(SourceTransportError,match='deadline'):
        m.fetch_sources(parent/'batch',_fetch=fetch,_clock=clock)
    # Persisted candidate is an orphan observation, never a final gate/adoption marker.
    assert json.loads((parent/'batch'/'batch-receipt.json').read_text())['status']=='candidate'
    failure=json.loads((parent/'batch'/'failure-receipt.json').read_text())
    assert failure['status']=='failed' and failure['accounting_complete'] is True
    assert len(fetch.calls)==2


def test_no_next_fetch_after_first_receipt_fsync_expires(parent,monkeypatch):
    m=module(); write=m._write; clock=Clock(); fetch=Fetch()
    def late(fd,name,receipt):
        result=write(fd,name,receipt)
        clock.value=610
        return result
    monkeypatch.setattr(m,'_write',late)
    with pytest.raises(SourceTransportError,match='deadline'):
        m.fetch_sources(parent/'batch',_fetch=fetch,_clock=clock)
    assert len(fetch.calls)==1


def test_unknown_receipt_fields_rejected(parent):
    fetch=Fetch(mutation=lambda r,p:r.update({'private_debug':'do not serialize'}))
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch',_fetch=fetch,_clock=Clock())
    assert 'private_debug' not in (parent/'batch'/'failure-receipt.json').read_text()



def test_dispatch_rechecks_clock_after_handoff_validation(parent,monkeypatch):
    m=module(); validate=m._validate_limits; clock=Clock(); fetch=Fetch(); calls=0
    def expire(limits):
        nonlocal calls
        validate(limits); calls+=1
        if calls==2: clock.value=610
    monkeypatch.setattr(m,'_validate_limits',expire)
    with pytest.raises(SourceTransportError,match='deadline'):
        m.fetch_sources(parent/'batch',_fetch=fetch,_clock=clock)
    assert not fetch.calls



def test_tight_total_clips_each_response_limit(parent):
    fetch=Fetch()
    result=module().fetch_sources(parent/'batch',HttpLimits(total_body_bytes=600,total_input_bytes=700),
                                  _fetch=fetch,_clock=Clock())
    first,second=[call[2] for call in fetch.calls]
    assert first.response_body_bytes==600
    assert second.response_body_bytes==600-result['sources'][0]['response_body_bytes']
    assert second.request_seconds<=first.request_seconds


def test_one_remaining_request_cannot_start_second(parent):
    fetch=Fetch()
    with pytest.raises(SourceTransportError,match='exhausted'):
        module().fetch_sources(parent/'batch',HttpLimits(max_requests=3),_fetch=fetch,_clock=Clock())
    assert len(fetch.calls)==1


@pytest.mark.parametrize('field', ['response_body_bytes','total_input_bytes','metadata_bytes','pack'])
def test_missing_receipt_fields_fail_closed(parent,field):
    fetch=Fetch(mutation=lambda r,p:r.pop(field))
    with pytest.raises(SourceTransportError):
        module().fetch_sources(parent/'batch',_fetch=fetch,_clock=Clock())
    assert len(fetch.calls)==1
