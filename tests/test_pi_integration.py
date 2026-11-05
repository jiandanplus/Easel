"""Pi selection, source authority and existing-user compatibility."""
import asyncio
import importlib.util
import json

import pytest

from easel.harnesses import store
from easel.harnesses.base import HarnessError, RunRequest
from tests.test_agent_harnesses import sandbox


@pytest.fixture(autouse=True)
def isolated_engine_environment(monkeypatch, tmp_path):
    for key in ('EASEL_AGENT_RUNTIME', 'EASEL_CHAT_TRANSPORT', 'EASEL_RUNTIME_MANIFEST', 'EASEL_PI_RUNTIME_MANIFEST'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('EASEL_OPENCLAW_STATE_DIR', str(tmp_path/'absent-openclaw'))


def test_clean_workspace_defaults_pi_and_never_writes_settings(tmp_path):
    assert store.settings(tmp_path/'outputs', {})['runtime'] == 'pi'
    assert not (tmp_path/'outputs').exists()


def test_pi_install_default_and_preserve_explicit_settings(tmp_path):
    from easel.installation import initialize_runtime
    assert initialize_runtime(tmp_path, 'pi') == 'pi'
    path=tmp_path/'outputs/_harness/config.json'
    store.write_json(path, {'runtime':'codex','models':{'codex':'my-model'},'future':True})
    before=path.read_bytes()
    assert initialize_runtime(tmp_path, 'pi') == 'codex'
    assert path.read_bytes() == before


def test_explicit_legacy_config_and_env_retain_engine(tmp_path):
    store.write_json(tmp_path/'outputs/_harness/config.json', {'runtime':'api'})
    assert store.settings(tmp_path/'outputs', {})['runtime'] == 'api'
    assert store.settings(tmp_path/'outputs', {'EASEL_AGENT_RUNTIME':'codex'})['runtime'] == 'codex'
    assert store.settings(tmp_path/'fresh', {'EASEL_CHAT_TRANSPORT':'http'})['runtime'] == 'openclaw'
    assert store.settings(tmp_path/'fresh', {'EASEL_CHAT_TRANSPORT':'api'})['runtime'] == 'api'


def test_explicit_subscription_capabilities():
    assert importlib.util.find_spec('easel.harnesses.capabilities') is not None
    from easel.harnesses.capabilities import supports_subscription
    assert supports_subscription('pi','central')
    assert not supports_subscription('pi','legacy')
    assert supports_subscription('codex','central') and supports_subscription('codex','legacy')
    assert not supports_subscription('openclaw','central')
    assert not supports_subscription('pi','untrusted')


def test_pi_context_uses_accepted_central_scope(tmp_path, monkeypatch):
    from easel.platform.central import CentralClient
    from easel.service_contracts import task_context
    scope={'user_id':'u','tenant_id':'t','product_id':'easel','billing_account_id':'w','session_id':'s','authorization_epoch':0}
    monkeypatch.setattr(CentralClient,'authorize',lambda self, model: {'account_id':'u','platform_url':'http://127.0.0.1:9011','scope':scope})
    config={'runtime':'pi','models':{},'service_source':'subscription','execution_target':'local','subscription_backend':'central','subscription_model':'central-model'}
    result=task_context(config,root=tmp_path)
    assert result.runtime == 'pi' and result.central_scope == scope and result.invocation_key
    assert result.model == 'central-model' and result.account_id == 'u'
    with pytest.raises(HarnessError):task_context({**config,'subscription_backend':'legacy'},root=tmp_path)


def test_pi_custom_model_is_saved_provider_authority(tmp_path):
    from easel.harnesses.provider import save_provider
    from easel.service_contracts import task_context
    save_provider(tmp_path,base_url='https://provider.invalid/v1',model='custom-real',api_key='SYNTHETIC_PRIVATE',secret_action='replace')
    config={'runtime':'pi','models':{'pi':''},'service_source':'custom','execution_target':'local'}
    assert task_context(config,root=tmp_path).model == 'custom-real'
    assert store.settings(tmp_path/'outputs',{})['models']['pi'] == 'custom-real'
    with pytest.raises(HarnessError):task_context({**config,'models':{'pi':'different'}},root=tmp_path)


def test_pi_model_catalog_keeps_central_metadata(tmp_path, monkeypatch):
    from easel.harnesses.models import available_models
    from easel.platform.central import CentralClient
    row={'id':'central-model','name':'Model','test_only':True,'limits':{'max_output_tokens':128}}
    monkeypatch.setattr(CentralClient,'models',lambda self:{'models':[row]})
    request=RunRequest('p',tmp_path,{},service_source='subscription',subscription_backend='central',model='central-model')
    result=asyncio.run(available_models('pi',request))
    assert result[0]['id']=='central-model' and result[0]['test_only'] and result[0]['limits']==row['limits']


def test_pi_cannot_reinterpret_old_codex_session(tmp_path):
    store.bind(tmp_path,'chat','codex','old-native')
    before=store.binding_path(tmp_path,'chat').read_bytes()
    with pytest.raises(HarnessError,match='其他 Agent'):store.bind(tmp_path,'chat','pi','new-native')
    assert store.binding_path(tmp_path,'chat').read_bytes()==before


def test_api_accepts_pi_central_and_masks_custom_key(sandbox, monkeypatch):
    import web.app as web
    monkeypatch.setattr(web.harnesses,'describe',lambda runtime:{'id':runtime,'installed':True,'origin':'bundled','subscription_backends':['central'] if runtime=='pi' else ['central','legacy'] if runtime=='codex' else []})
    payload={'runtime':'pi','service_source':'subscription','execution_target':'local','subscription_backend':'central','subscription_model':'central-model'}
    response=sandbox.post('/api/settings/harness',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['runtime']=='pi'
    assert sandbox.post('/api/settings/harness',json={**payload,'subscription_backend':'legacy'}).status_code==409
    response=sandbox.post('/api/settings/harness/provider',json={'base_url':'https://provider.invalid/v1','model':'custom-model','api_key':'SYNTHETIC_PROVIDER_PRIVATE','secret_action':'replace'})
    assert response.status_code==200,response.text
    assert 'SYNTHETIC_PROVIDER_PRIVATE' not in response.text
    assert 'SYNTHETIC_PROVIDER_PRIVATE' not in sandbox.get('/api/settings/harness/provider').text


def test_installed_pi_provider_preview_is_available_before_engine_save(sandbox, monkeypatch):
    import web.app as web
    from easel.harnesses import runtime
    from types import SimpleNamespace
    store.write_json(web.OUTPUTS_DIR / '_harness/config.json', {'runtime': 'api'})
    monkeypatch.setattr(web.harnesses, 'describe', lambda selected: {'id': selected, 'installed': selected == 'pi', 'origin': 'bundled' if selected == 'pi' else 'external', 'subscription_backends': ['central'] if selected == 'pi' else []})
    monkeypatch.setattr(runtime, 'resolve_codex', lambda: SimpleNamespace(origin='external'))
    preview = sandbox.get('/api/settings/harness/provider')
    assert preview.status_code == 200, preview.text
    assert preview.json()['key_present'] is False
    assert store.read_json(web.OUTPUTS_DIR / '_harness/config.json')['runtime'] == 'api'
    selected = sandbox.post('/api/settings/harness', json={'runtime': 'pi', 'service_source': 'custom', 'execution_target': 'local'})
    assert selected.status_code == 200, selected.text
    saved = sandbox.post('/api/settings/harness/provider', json={'base_url': 'https://provider.invalid/v1', 'model': 'preview-model', 'api_key': 'SYNTHETIC_PREVIEW_SECRET', 'secret_action': 'replace'})
    assert saved.status_code == 200, saved.text
    assert saved.json()['configured']
    assert 'SYNTHETIC_PREVIEW_SECRET' not in saved.text


def test_pi_central_readiness_accepts_authorized_model(sandbox,monkeypatch):
    import web.app as web
    from easel.platform.central import CentralClient
    monkeypatch.setattr(CentralClient,'status',lambda self:{'configured':True,'authenticated':True})
    monkeypatch.setattr(CentralClient,'models',lambda self:{'models':[{'id':'m','test_only':False}]})
    monkeypatch.setattr(web.harnesses,'describe',lambda runtime:{'installed':True})
    result=asyncio.run(web._subscription_readiness({'runtime':'pi','execution_target':'local','subscription_backend':'central','subscription_model':'m'}))
    assert result['ready'] and result['detail']=='中央文本执行可用'


@pytest.mark.parametrize('configured',[False,True])
def test_pi_custom_bootstrap_uses_local_provider_not_gateway(sandbox,monkeypatch,configured):
    import web.app as web
    from easel.harnesses.provider import save_provider
    if configured:save_provider(web.PROJECT_ROOT,base_url='https://provider.invalid/v1',model='m',api_key='SYNTHETIC_PRIVATE',secret_action='replace')
    monkeypatch.setattr(web,'_harness_settings',lambda:{'runtime':'pi','service_source':'custom'})
    monkeypatch.setattr(web.harnesses,'describe',lambda runtime:{'installed':True,'origin':'bundled'})
    monkeypatch.setattr(web,'check_gateway',lambda:pytest.fail('Pi must not need an OpenClaw gateway'))
    result=sandbox.get('/api/settings/bootstrap').json()
    assert result['modelConfigured'] is configured and result['routeOk'] is configured and result['authVerified'] is False
    assert '供应商' in result['routeDetail']
    assert 'SYNTHETIC_PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('installed,configured,expected', [(True,False,'内置 Pi 供应商未配置'),(False,False,'内置 Pi 运行时不可用'),(True,True,'内置 Pi 供应商已配置')])
def test_pi_status_distinguishes_provider_setup_and_missing_runtime(sandbox,monkeypatch,installed,configured,expected):
    import web.app as web
    from easel.harnesses.provider import save_provider
    store.write_json(web.OUTPUTS_DIR/'_harness/config.json',{'runtime':'pi','service_source':'custom'})
    if configured:save_provider(web.PROJECT_ROOT,base_url='https://provider.invalid/v1',model='m',api_key='SYNTHETIC_STATUS_PRIVATE',secret_action='replace')
    monkeypatch.setattr(web.harnesses,'describe',lambda runtime:{'installed':installed,'origin':'bundled'})
    result=sandbox.get('/api/status').json()
    assert result['gateway'] is bool(installed and configured)
    assert expected in result['runtime_detail']
    assert 'CLI' not in result['runtime_detail'] and 'SYNTHETIC_STATUS_PRIVATE' not in json.dumps(result)


def test_pi_status_subscription_keeps_its_own_nonsecret_readiness_detail(sandbox,monkeypatch):
    import web.app as web
    store.write_json(web.OUTPUTS_DIR/'_harness/config.json',{'runtime':'pi','service_source':'subscription','subscription_backend':'central'})
    async def readiness(_):return {'ready':False,'detail':'请先登录中央账号'}
    monkeypatch.setattr(web,'_subscription_readiness',readiness)
    result=sandbox.get('/api/status').json()
    assert result['gateway'] is False and result['runtime_detail']=='请先登录中央账号'
    assert '供应商' not in result['runtime_detail']


@pytest.mark.parametrize('backend,runtime', [('central', 'pi'), ('legacy', 'codex')])
@pytest.mark.parametrize('configured', [False, True])
def test_subscription_setup_survives_missing_auth_without_claiming_readiness(sandbox, monkeypatch, backend, runtime, configured):
    import web.app as web
    from easel.platform.central import CentralClient
    from easel.platform.client import PlatformClient

    client = CentralClient if backend == 'central' else PlatformClient
    monkeypatch.setattr(client, 'status', lambda self: {'configured': configured, 'authenticated': False})
    monkeypatch.setattr(client, 'models', lambda self: pytest.fail('must not load models before login'))
    monkeypatch.setattr(client, 'call', lambda *args, **kwargs: pytest.fail('must not load grants before login'))
    config = {'runtime': runtime, 'service_source': 'subscription', 'subscription_backend': backend,
              'execution_target': 'local', 'subscription_model': ''}
    monkeypatch.setattr(web, '_harness_settings', lambda: config)
    state = asyncio.run(web._subscription_readiness(config))
    assert state.get('configured') is configured
    assert state['authenticated'] is False and state['ready'] is False
    bootstrap = sandbox.get('/api/settings/bootstrap').json()
    assert bootstrap['modelConfigured'] is configured
    assert bootstrap['envKeyOk'] is False and bootstrap['routeOk'] is False
    assert bootstrap['gatewayUp'] is False and bootstrap['authVerified'] is False
    assert sandbox.get('/api/status').json()['gateway'] is False


@pytest.mark.parametrize('backend,runtime', [('central', 'pi'), ('legacy', 'codex')])
@pytest.mark.parametrize('catalog_state', ['ready', 'missing-model', 'unavailable'])
def test_configured_subscription_catalog_readiness_does_not_force_local_setup(sandbox, monkeypatch, backend, runtime, catalog_state):
    import web.app as web
    from easel.platform.central import CentralClient
    from easel.platform.client import PlatformClient

    client = CentralClient if backend == 'central' else PlatformClient
    monkeypatch.setattr(client, 'status', lambda self: {'configured': True, 'authenticated': True})
    monkeypatch.setattr(web.harnesses, 'describe', lambda runtime: {'installed': True})

    def models(self):
        if catalog_state == 'unavailable':
            raise HarnessError('目录暂不可用')
        return {'ready': True, 'models': [{'id': 'm', 'test_only': False}]}

    monkeypatch.setattr(client, 'models', models)
    if backend == 'legacy':
        monkeypatch.setattr(client, 'call', lambda *args, **kwargs: {
            'entitlements': [{'status': 'active', 'models': ['m'], 'remaining_tokens': 10}]})
    config = {'runtime': runtime, 'service_source': 'subscription', 'subscription_backend': backend,
              'execution_target': 'local', 'subscription_model': '' if catalog_state == 'missing-model' else 'm'}
    monkeypatch.setattr(web, '_harness_settings', lambda: config)
    ready = catalog_state == 'ready'
    state = asyncio.run(web._subscription_readiness(config))
    assert state['ready'] is ready
    bootstrap = sandbox.get('/api/settings/bootstrap').json()
    assert bootstrap['modelConfigured'] is True
    assert bootstrap['envKeyOk'] is True
    assert bootstrap['routeOk'] is ready and bootstrap['gatewayUp'] is ready
    assert bootstrap['authVerified'] is False
    assert sandbox.get('/api/status').json()['gateway'] is ready
