"""Incho's published n/lyric parameters through the actual owned Pi SDK."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('params,calls', [({'n':1,'lyric':'合成歌词'},1),({'n':2},1),
    ({'n':0},0),({'n':3},0),({'n':True},1),({'lyric':'x'*3001},0),({'unknown':'value'},0)])
def test_actual_owned_pi_sdk_projects_incho_schema_and_preserves_host_type_boundary(tmp_path,params,calls):
    payload=os.environ.get('EASEL_PI_TEST_PAYLOAD')
    if not payload:pytest.skip('owned Pi runtime required for Incho SDK acceptance')
    from test_media_contracts import definition
    from easel.platform.media_contracts import public_definition
    row=definition('music.generate');row.update(adapter='newapi_incho_v1',readiness='test')
    row['rate']['meters']=[{'unit':'audio_clip','basis':'verified_delivery_v1','numerator':'3','denominator':'2'}]
    row['limits'].update(max_assets=0,max_units={'audio_clip':2},output_media={'mime_types':['audio/mpeg'],
        'max_files':2,'max_duration_ms':1000,'max_channels':2,'max_sample_rate':48000,'require_audio':True})
    row['parameters']={'type':'object','properties':{'n':{'type':'integer','minimum':1,'maximum':2},
        'lyric':{'type':'string','maxLength':3000}},'required':[],'additionalProperties':False}
    projected=public_definition(row)
    root=Path(__file__).resolve().parents[3];directory=Path(payload).resolve()
    shutil.copy2(root/'runtime/pi/worker.mjs',tmp_path/'worker.mjs')
    (tmp_path/'node_modules').symlink_to(directory/'node_modules',target_is_directory=True)
    node=directory/'node/bin/node'
    if not node.is_file():node=directory/'node'
    args={'model':row['id'],'capability':'music.generate','prompt':'synthetic music','parameters':params}
    # Pi's SDK may pass n=true to the host; it is not an authorization boundary.
    # The actual Python host/ASGI test separately proves zero POST for this type.
    result=subprocess.run([str(node),str(root/'tests/pi_incho_worker_fixture.mjs'),str(tmp_path),
        json.dumps(projected),json.dumps(args),str(calls)],capture_output=True,text=True,timeout=15,
        env={'PATH':'/usr/bin:/bin'})
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['calls']==calls
