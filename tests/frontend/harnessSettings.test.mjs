import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/HarnessSettings.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('HarnessSettings.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declaration = parsed.statements.find(node =>
  ts.isFunctionDeclaration(node) && node.name?.text === 'HarnessSettings');
assert.ok(declaration);
const { outputText } = ts.transpileModule(
  declaration.getText(parsed).replace('export default ', '') + '\nHarnessSettings;', {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  });

const settle = () => new Promise(resolve => setImmediate(resolve));

test('Pi built-in custom configuration uses provider form without an install instruction', async () => {
  const view=setup({runtime:'pi'});view.render();await settle();
  const tree=view.render();
  const provider=nodes(tree,'bundled-provider')[0];
  assert.ok(provider);
  assert.equal(provider.props.runtime,'pi');
  assert.equal(provider.props.version,'1.4.0');
  assert.ok(nodes(tree,'settings-select').some(n=>n.props.label==='工具权限'));
});

test('Pi central subscription saves while Pi is unavailable for legacy subscriptions', async () => {
  const view=setup({runtime:'pi',source:'subscription',backend:'central'});view.render();await settle();view.render();await settle();
  let tree=view.render();
  const engine=nodes(tree,'settings-select').find(n=>n.props.label==='执行引擎');
  assert.ok(engine.props.options.some(n=>n.value==='pi'));
  nodes(tree,'settings-select').find(n=>n.props.label==='订阅模型').props.onChange('central-text');
  tree=view.render();
  assert.equal(nodes(tree,'button').find(n=>text(n)==='保存底座').props.disabled,false);
  nodes(tree,'button').find(n=>text(n)==='保存底座').props.onClick();await settle();
  assert.equal(view.saves[0].runtime,'pi');
  assert.equal(view.saves[0].route.subscription_backend,'central');
  nodes(view.render(),'settings-select').find(n=>n.props.label==='订阅渠道').props.onChange('legacy');
  view.render();await settle();tree=view.render();
  assert.equal(nodes(tree,'button').find(n=>text(n)==='保存底座').props.disabled,true);
  assert.ok(!nodes(tree,'settings-select').find(n=>n.props.label==='执行引擎').props.options.some(n=>n.value==='pi'));
});

test('settings defer environment probes and wait for the actual runtime before loading models', () => {
  const panel = readFileSync(new URL('../../web/frontend/src/components/SettingsPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /const \[harness, setHarness\] = useState\(''\)/);
  assert.match(panel, /if \(sec === 'env'\) void refreshEnv\(\)/);
  assert.match(panel, /if \(!harness\) return;/);
  assert.match(panel, /if \(harness !== 'openclaw'\) return;/);
});

function setup({ runtime = 'openclaw', locked = false, installed = true, saveError = '', modelError = '',
  settingsErrors = [], origin, version, source = 'custom', backend, gatewayReady = true, catalogPromise } = {}) {
  const state = [];
  let cursor = 0;
  const effects = [];
  let effectCursor = 0;
  const settingsRequests = [];
  const changes = [];
  const saves = [];
  const selections = [];
  const settingsChanges = [];
  const modelRequests = [];
  const settings = {
    runtime,
    service_source: source, subscription_model: 'platform-alias', subscription_backend: backend,
    models: { codex: 'codex-model', opencode: 'provider/model' },
    sandbox: 'workspace-write',
    locked,
    agents: ['pi', 'api', 'openclaw', 'opencode', 'codex', 'acp'].map(id => ({
      id, label: id === 'api' ? '仅聊天模式' : id,
      installed: ['api', 'openclaw'].includes(id) || installed, install_hint: `install ${id}`,
      ...(id === 'codex' ? { origin, version } : id === 'pi' ? { origin:'bundled', version:'1.4.0', subscription_backends:['central'] } : {}),
    })),
  };
  const component = runInNewContext(outputText, {
    React: {
      Fragment: 'fragment',
      createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat() }),
    },
    SettingsSelect: 'settings-select',
    platformModels: async () => catalogPromise || ({models:[{id:'platform-alias'}],ready:gatewayReady,reason:gatewayReady?'':'gateway_not_configured'}),
    centralModels: async () => catalogPromise || ({models:[{id:'central-text',name:'中央测试模型',test_only:true}],ready:gatewayReady,reason:gatewayReady?'':'execution_disabled'}),
    BundledProviderSettings: 'bundled-provider',
    useState: initial => {
      const index = cursor++;
      if (!(index in state)) state[index] = initial;
      return [state[index], value => { state[index] = typeof value === 'function' ? value(state[index]) : value; }];
    },
    useRef: initial => {
      const index = cursor++;
      if (!(index in state)) state[index] = { current: initial };
      return state[index];
    },
    useEffect: (callback, dependencies) => {
      const index = effectCursor++;
      const previous = effects[index];
      if (!previous || dependencies.some((value, position) => value !== previous.dependencies[position])) {
        previous?.cleanup?.();
        effects[index] = { dependencies, cleanup: callback() };
      }
    },
    fetchHarnessSettings: async () => {
      const index = settingsRequests.length;
      settingsRequests.push(index);
      if (settingsErrors[index]) throw new Error(settingsErrors[index]);
      return settings;
    },
    fetchHarnessModels: async runtime => {
      modelRequests.push(runtime);
      if (modelError) throw new Error(modelError);
      return { runtime, models: [{ id: 'another-model', label: 'Another model', default: true }] };
    },
    saveHarnessSettings: async (runtime, model, sandbox, codexAutoApprove, route) => {
      saves.push({ runtime, model, sandbox, ...(codexAutoApprove !== undefined ? { codexAutoApprove } : {}), ...(route?.service_source === 'subscription' ? { route } : {}) });
      if (saveError) throw new Error(saveError);
      return { ...settings, ...route, runtime, models: { ...settings.models, [runtime]: model }, sandbox,
        codex_auto_approve: codexAutoApprove ?? false };
    },
  });
  const onChange = value => changes.push(value);
  const onSelect = value => selections.push(value);
  const onSettingsChange = value => settingsChanges.push(value);
  return {
    changes,
    saves,
    selections,
    settingsChanges,
    modelRequests,
    settingsRequests,
    render() {
      cursor = 0;
      effectCursor = 0;
      const tree = component({ onChange, onSelect, onSettingsChange });
      return tree;
    },
  };
}

test('parent receives persisted service source on read and successful save, never on draft selection', async () => {
  const view = setup({ runtime: 'codex', origin: 'bundled' }); view.render(); await settle();
  assert.equal(view.settingsChanges.length, 1);
  assert.equal(view.settingsChanges[0].service_source, 'custom');
  nodes(view.render(), 'button').find(node => text(node) === '订阅服务').props.onClick();
  view.render(); await settle();
  let tree = view.render();
  nodes(tree, 'settings-select').find(node => node.props.label === '订阅模型').props.onChange('central-text');
  tree = view.render();
  assert.equal(view.settingsChanges.length, 1);
  nodes(tree, 'button').find(node => text(node) === '保存底座').props.onClick(); await settle();
  assert.equal(view.settingsChanges.length, 2);
  assert.equal(view.settingsChanges[1].service_source, 'subscription');
  assert.equal(view.settingsChanges[1].subscription_backend, 'central');
});

test('failed save leaves parent on its persisted service source', async () => {
  const view = setup({ runtime: 'codex', saveError: 'rejected' }); view.render(); await settle();
  nodes(view.render(), 'button').find(node => text(node) === '订阅服务').props.onClick();
  view.render(); await settle();
  nodes(view.render(), 'settings-select').find(node => node.props.label === '订阅模型').props.onChange('central-text');
  nodes(view.render(), 'button').find(node => text(node) === '保存底座').props.onClick(); await settle();
  assert.equal(view.settingsChanges.length, 1);
  assert.equal(view.settingsChanges[0].service_source, 'custom');
  assert.match(text(view.render()), /保存失败/);
});

test('new explicit subscription selects central while an existing unversioned subscription stays legacy', async () => {
  const fresh = setup({ runtime: 'codex' }); fresh.render(); await settle();
  nodes(fresh.render(), 'button').find(node => text(node) === '订阅服务').props.onClick();
  let tree = fresh.render(); await settle(); tree = fresh.render();
  let backend = nodes(tree, 'settings-select').find(node => node.props.label === '订阅渠道');
  assert.equal(backend.props.value, 'central');
  const models = nodes(tree, 'settings-select').find(node => node.props.label === '订阅模型');
  assert.ok(models.props.options.some(option => option.value === 'central-text'));
  models.props.onChange('central-text'); tree = fresh.render();
  nodes(tree, 'button').find(node => text(node) === '保存底座').props.onClick(); await settle();
  assert.equal(fresh.saves[0].route.subscription_backend, 'central');
  const existing = setup({ source: 'subscription', runtime: 'codex' }); existing.render(); await settle(); existing.render(); await settle();
  backend = nodes(existing.render(), 'settings-select').find(node => node.props.label === '订阅渠道');
  assert.equal(backend.props.value, 'legacy');
});
test('changing subscription backend clears the prior model and does not save until explicit selection', async () => {
  const view = setup({ source: 'subscription', runtime: 'codex', backend: 'legacy' }); view.render(); await settle(); view.render(); await settle();
  const backend = nodes(view.render(), 'settings-select').find(node => node.props.label === '订阅渠道');
  backend.props.onChange('central'); let tree = view.render(); await settle(); tree = view.render();
  assert.equal(nodes(tree, 'settings-select').find(node => node.props.label === '订阅模型').props.value, '');
  assert.equal(nodes(tree, 'button').find(node => text(node) === '保存底座').props.disabled, true);
  assert.equal(view.saves.length, 0);
});

function nodes(tree, type) {
  if (!tree || typeof tree !== 'object') return [];
  return [
    ...(tree.type === type ? [tree] : []),
    ...(tree.children || []).flatMap(child => nodes(child, type)),
  ];
}

function text(tree) {
  if (typeof tree === 'string') return tree;
  if (!tree || typeof tree !== 'object') return '';
  return (tree.children || []).map(text).join('');
}

test('changing harness is local until save and retains its model override', async () => {
  const view = setup();
  view.render();
  await settle();
  let tree = view.render();
  assert.equal(nodes(tree, 'settings-select')[0].props.options.length, 6);
  nodes(tree, 'settings-select')[0].props.onChange('codex');
  tree = view.render();
  assert.equal(nodes(tree, 'input')[0].props.value, 'codex-model');
  assert.deepEqual(view.changes, ['openclaw']);
  assert.equal(view.saves.length, 0);
  nodes(tree, 'settings-select').find(node => node.props.label === 'Codex 沙箱').props.onChange('read-only');
  nodes(tree, 'input')[0].props.onChange({ target: { value: 'custom-model' } });
  tree = view.render();
  nodes(tree, 'button')[0].props.onClick();
  assert.equal(nodes(view.render(), 'button')[0].props.disabled, true);
  await settle();
  assert.deepEqual(view.saves, [{ runtime: 'codex', model: 'custom-model', sandbox: 'read-only', codexAutoApprove: false }]);
  assert.deepEqual(view.changes, ['openclaw', 'codex']);
  assert.match(text(view.render()), /已保存.*新建对话后生效/);
});

test('Codex permanent approval defaults off, saves explicitly and stays exclusive to Codex', async () => {
  const view = setup({ runtime: 'codex' });
  view.render();
  await settle();
  let tree = view.render();
  const toggle = nodes(tree, 'button').find(node => node.props.role === 'switch');
  assert.equal(toggle.props['aria-checked'], false);
  assert.match(text(tree), /删除、系统修改及执行脚本仍需确认/);
  toggle.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, 'button').find(node => node.props.role === 'switch').props['aria-checked'], true);
  nodes(tree, 'button')[0].props.onClick();
  await settle();
  assert.equal(view.saves[0].codexAutoApprove, true);
  nodes(view.render(), 'settings-select')[0].props.onChange('opencode');
  assert.equal(nodes(view.render(), 'button').some(node => node.props.role === 'switch'), false);
  nodes(view.render(), 'button')[0].props.onClick();
  await settle();
  assert.equal(view.saves[1].codexAutoApprove, undefined);
});

test('environment-locked harness cannot be changed or saved', async () => {
  const view = setup({ locked: true });
  view.render();
  await settle();
  const tree = view.render();
  assert.equal(nodes(tree, 'settings-select')[0].props.disabled, true);
  assert.equal(nodes(tree, 'button')[0].props.disabled, true);
  assert.match(text(tree), /执行引擎已锁定/);
});

test('settings can retry a failed read without saving or silently changing the engine', async () => {
  const view = setup({ settingsErrors: ['temporary connection failure'] });
  view.render();
  await settle();
  let tree = view.render();
  assert.match(text(tree), /读取失败：.*temporary connection failure/);
  assert.equal(nodes(tree, 'button')[0].props.disabled, true);
  const reload = nodes(tree, 'button').find(node => text(node) === '重新读取');
  assert.ok(reload);
  assert.equal(reload.props.disabled, false);
  reload.props.onClick();
  view.render();
  await settle();
  tree = view.render();
  assert.equal(view.settingsRequests.length, 2);
  assert.equal(nodes(tree, 'settings-select')[0].props.disabled, false);
  assert.deepEqual(view.changes, ['openclaw']);
  assert.deepEqual(view.saves, []);
  assert.doesNotMatch(text(tree), /读取失败/);
});

test('uninstalled native harness cannot be saved and does not show Codex sandbox for ACP', async () => {
  const view = setup({ installed: false });
  view.render();
  await settle();
  nodes(view.render(), 'settings-select')[0].props.onChange('acp');
  const tree = view.render();
  assert.equal(nodes(tree, 'button')[0].props.disabled, true);
  assert.equal(nodes(tree, 'settings-select').length, 1);
  assert.match(text(tree), /未检测到 acp/);
});

test('engine options retain missing harnesses with configuration previews and installation hints', async () => {
  const view = setup({ installed: false });
  view.render();
  await settle();
  assert.deepEqual(nodes(view.render(), 'settings-select')[0].props.options.map(option => option.value),
    ['pi', 'api', 'openclaw', 'opencode', 'codex', 'acp']);
  const option = nodes(view.render(), 'settings-select')[0].props.options.find(item => item.value === 'opencode');
  assert.match(option.label, /未安装 \/ 未配置/);
  nodes(view.render(), 'settings-select')[0].props.onChange('opencode');
  const preview = view.render();
  assert.match(text(preview), /未检测到 opencode/);
  assert.deepEqual(view.changes, ['openclaw']);
  assert.equal(nodes(preview, 'input')[0].props.value, 'provider/model');
  assert.equal(nodes(preview, 'button')[0].props.disabled, true);
  const missing = setup({ runtime: 'codex', installed: false });
  missing.render();
  await settle();
  assert.deepEqual(nodes(missing.render(), 'settings-select')[0].props.options.map(option => option.value),
    ['pi', 'api', 'openclaw', 'opencode', 'codex', 'acp']);
  assert.equal(nodes(missing.render(), 'button')[0].props.disabled, true);
});
test('failed harness save shows error without switching the active runtime', async () => {
  const view = setup({ saveError: 'active task' });
  view.render();
  await settle();
  nodes(view.render(), 'settings-select')[0].props.onChange('opencode');
  nodes(view.render(), 'button')[0].props.onClick();
  await settle();
  assert.match(text(view.render()), /保存失败：.*active task/);
  assert.deepEqual(view.changes, ['openclaw']);
  assert.equal(nodes(view.render(), 'button')[0].props.disabled, false);
});

test('harness form reuses settings input styling and model controls', async () => {
  const view = setup();
  view.render();
  await settle();
  nodes(view.render(), 'settings-select')[0].props.onChange('codex');
  const tree = view.render();
  assert.equal(nodes(tree, 'input')[0].props.className, 'mock');
  assert.equal(nodes(tree, 'select').length, 0);
  assert.ok(nodes(tree, 'settings-select').some(node => node.props.label === 'Codex 沙箱'));
});

test('Pi is the loading default and a persisted chat-only choice restores its controls', async () => {
  const view = setup({ runtime: 'api' });
  assert.equal(nodes(view.render(), 'settings-select')[0].props.value, 'pi');
  await settle();
  const tree = view.render();
  assert.equal(nodes(tree, 'settings-select')[0].props.options[0].value, 'pi');
  assert.equal(nodes(tree, 'settings-select')[0].props.options.find(option=>option.value==='api').label, '仅聊天模式');
  assert.equal(nodes(tree, 'settings-select').length, 1);
  assert.equal(nodes(tree, 'input').length, 0);
  assert.deepEqual(view.changes, ['api']);
});

test('chat-only can be saved without any agent CLI and preserves the active engine until save', async () => {
  const view = setup({ installed: false });
  view.render();
  await settle();
  nodes(view.render(), 'settings-select')[0].props.onChange('api');
  let tree = view.render();
  assert.equal(nodes(tree, 'button')[0].props.disabled, false);
  assert.equal(nodes(tree, 'input').length, 0);
  assert.deepEqual(view.changes, ['openclaw']);
  nodes(tree, 'button')[0].props.onClick();
  await settle();
  tree = view.render();
  assert.deepEqual(view.changes, ['openclaw', 'api']);
  assert.equal(view.saves[0].runtime, 'api');
  assert.equal(nodes(tree, 'settings-select')[0].props.value, 'api');
});

test('OpenClaw selection previews its API configuration without changing the active engine', async () => {
  const view = setup({ runtime: 'codex' });
  view.render();
  await settle();
  nodes(view.render(), 'settings-select')[0].props.onChange('openclaw');
  assert.deepEqual(view.selections, ['codex', 'openclaw']);
  assert.deepEqual(view.changes, ['codex']);
  assert.match(text(view.render()), /切换待保存/);
});

for (const runtime of ['codex', 'opencode']) {
  test(`${runtime} loads native models only on request and saves a dropdown selection`, async () => {
    const view = setup({ runtime });
    view.render();
    await settle();
    let tree = view.render();
    assert.equal(view.modelRequests.length, 0);
    nodes(tree, 'button').find(node => text(node) === '拉取模型').props.onClick();
    await settle();
    tree = view.render();
    const dropdown = nodes(tree, 'settings-select').find(node => node.props.label === '原生模型');
    assert.equal(dropdown.props.options[0].value, '');
    assert.equal(dropdown.props.options[1].value, 'another-model');
    dropdown.props.onChange('another-model');
    tree = view.render();
    assert.equal(nodes(tree, 'input')[0].props.value, 'another-model');
    nodes(tree, 'button')[0].props.onClick();
    await settle();
    assert.equal(view.saves[0].model, 'another-model');
    assert.equal(view.saves[0].runtime, runtime);
  });
}

test('native model listing failure preserves the saved model and allows manual input', async () => {
  const view = setup({ runtime: 'codex', modelError: 'CLI unavailable' });
  view.render();
  await settle();
  nodes(view.render(), 'button').find(node => text(node) === '拉取模型').props.onClick();
  await settle();
  const tree = view.render();
  assert.match(text(tree), /读取失败：(Error: )?CLI unavailable/);
  assert.equal(nodes(tree, 'input')[0].props.value, 'codex-model');
  assert.equal(view.saves.length, 0);
});


test('bundled Codex renders provider configuration and version without native model overrides', async () => {
  const view = setup({ runtime: 'codex', origin: 'bundled', version: '0.146.0' });
  view.render(); await settle();
  let tree = view.render();
  const provider = nodes(tree, 'bundled-provider')[0];
  assert.equal(provider.props.version, '0.146.0');
  assert.equal(nodes(tree, 'input').length, 0);
  assert.equal(nodes(tree, 'button').some(node => text(node) === '拉取模型'), false);
  provider.props.onSaved('new-model');
  tree = view.render();
  nodes(tree, 'button')[0].props.onClick(); await settle();
  assert.equal(view.saves[0].model, 'new-model');
});

test('central subscription preview offers Pi and Codex and restores independent custom model', async()=>{
 const view=setup({runtime:'codex',origin:'bundled'});view.render();await settle();
 nodes(view.render(),'button').find(n=>text(n)==='订阅服务').props.onClick();await settle();
 let tree=view.render();assert.equal(nodes(tree,'bundled-provider').length,0);
 assert.ok(nodes(tree,'settings-select').some(n=>n.props.label==='Codex 沙箱'));
 assert.match(text(tree),/云端执行.*暂未开放/);
 assert.ok(nodes(tree,'button').find(n=>text(n)==='云端执行（暂未开放）').props.disabled);
 assert.deepEqual(nodes(tree,'settings-select')[0].props.options.map(x=>x.value),['pi','codex']);
 assert.equal(view.saves.length,0);
 nodes(tree,'button').find(n=>text(n)==='自定义服务').props.onClick();
 tree=view.render();assert.equal(nodes(tree,'bundled-provider').length,1);
 nodes(tree,'button')[0].props.onClick();await settle();assert.equal(view.saves[0].model,'codex-model');
});

test('subscription unavailable gateway shows human reason and source-aware authentication', async()=>{
 const view=setup({runtime:'codex',source:'subscription',gatewayReady:false});view.render();await settle();view.render();await settle();
 const value=text(view.render());assert.match(value,/平台模型网关尚未配置/);assert.doesNotMatch(value,/gateway_not_configured|外部引擎认证沿用/);assert.equal(nodes(view.render(),'bundled-provider').length,0);
});


test('leaving pending subscription catalog restores custom native model loading', async () => {
 let resolve; const catalogPromise = new Promise(done => { resolve = done; });
 const view = setup({runtime:'codex',catalogPromise}); view.render(); await settle();
 nodes(view.render(),'button').find(n=>text(n)==='订阅服务').props.onClick(); view.render();
 nodes(view.render(),'button').find(n=>text(n)==='自定义服务').props.onClick(); view.render();
 assert.equal(nodes(view.render(),'button').find(n=>text(n)==='拉取模型')?.props.disabled,false);
 resolve({models:[],ready:true}); await settle();
 nodes(view.render(),'button').find(n=>text(n)==='拉取模型').props.onClick(); await settle();
 assert.deepEqual(view.modelRequests,['codex']);
});
