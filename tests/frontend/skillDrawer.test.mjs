import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const compilerOptions = {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX};
const names = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/skillDisplayNames.ts', import.meta.url), 'utf8'), {compilerOptions}).outputText, {exports: names});
function viewDrawer({needsApi = false, failure = ''} = {}) {
  const module = {}, state = [], effects = [], refs = [], calls = [], saves = [];
  let cursor = 0, effectCursor = 0, refCursor = 0, configured = false, closed = 0, refreshed = 0;
  const jsx = (type, props) => ({type, props: props || {}});
  const react = {
    useState(initial) {const n = cursor++; if (!(n in state)) state[n] = initial; return [state[n], value => {state[n] = typeof value === 'function' ? value(state[n]) : value;}];},
    useRef(initial) {return refs[refCursor++] ||= {current: initial};},
    useEffect(fn, deps) {const n = effectCursor++; if (!effects[n] || deps.some((x, i) => x !== effects[n].deps[i])) effects[n] = {fn, deps, pending: true};},
    useMemo: fn => fn(),
  };
  const detail = () => ({name: 'video-production', layer: 'produce', description: '当用户说走视频产线时使用，和兄弟 SKILL 的区别', body: '# 原始agent指令\n本 SKILL 必须经过质量门与人工点头', needsApi, apiConfigured: configured,
    apiSpec: needsApi ? {label: '示例服务', settings: [{env: 'DEMO_PROVIDER', label: '优先接口', configured: false, choices: ['demo']}], providers: [{id: 'demo', name: '示例接口', keys: [{env: 'DEMO_API_KEY', label: '业务密钥', required: true, secret: true, configured, choices: []}]}]} : null});
  const api = {
    fetchSkillDetail: async () => detail(),
    saveEnv: async updates => {saves.push(updates); configured = true;},
    executeSkill: async (...args) => {calls.push(args); if (failure) throw new Error(failure); return {response: '已生成成片与交付文件'};},
  };
  runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/SkillDrawer.tsx', import.meta.url), 'utf8'), {compilerOptions}).outputText, {exports: module,
    require: id => id === 'react' ? react : id === 'react/jsx-runtime' ? {jsx, jsxs: jsx} : id.endsWith('/api') ? api : id.endsWith('/skillDisplayNames') ? names : {renderMarkdown: value => `<p>${value}</p>`},
    setTimeout: () => 0, Error,
  });
  return {
    calls, saves, closed: () => closed, refreshed: () => refreshed,
    render() {cursor = effectCursor = refCursor = 0; return module.default({skillName: 'video-production', persona: 'creator', onClose: () => closed++, onConfigured: () => refreshed++});},
    effects() {effects.forEach(e => {if (e.pending) {e.pending = false; e.fn();}});},
  };
}
function nodes(tree, predicate) {return !tree || typeof tree !== 'object' ? [] : [...(predicate(tree) ? [tree] : []), ...[tree.props?.children].flat(Infinity).flatMap(x => nodes(x, predicate))];}
function text(tree) {return tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? (tree.props?.dangerouslySetInnerHTML?.__html || '') + [tree.props?.children].flat(Infinity).map(text).join('') : String(tree);}
const button = (tree, label) => nodes(tree, n => n.type === 'button' && text(n) === label)[0];
const input = tree => nodes(tree, n => n.type === 'textarea')[0];
async function ready(options) {const view = viewDrawer(options); view.render(); view.effects(); await new Promise(resolve => setImmediate(resolve)); return view;}

test('skill drawer keeps its user title, category and runnable input without agent metadata or raw instructions', async () => {
  const view = await ready(), tree = view.render();
  assert.match(text(tree), /整片视频制作/); assert.match(text(tree), /制作/);
  assert.doesNotMatch(text(tree), /video-production|当用户说|兄弟 SKILL|原始agent指令|质量门|人工点头/);
  assert.ok(input(tree)); assert.equal(button(tree, '执行').props.disabled, true);
  input(tree).props.onChange({target: {value: '  包装这条原片  '}});
  assert.equal(button(view.render(), '执行').props.disabled, false);
  await button(view.render(), '执行').props.onClick();
  assert.deepEqual(Array.from(view.calls[0]), ['video-production', '包装这条原片', 'creator']);
  assert.match(text(view.render()), /已生成成片与交付文件/);
  nodes(view.render(), n => n.type === 'button' && n.props.title === '关闭')[0].props.onClick();
  assert.equal(view.closed(), 1);
});

test('missing API configuration still blocks execution and saving the exact key refreshes readiness', async () => {
  const view = await ready({needsApi: true});
  input(view.render()).props.onChange({target: {value: '包装原片'}});
  assert.match(text(view.render()), /需配置 API/);
  assert.equal(button(view.render(), '执行').props.disabled, true);
  assert.equal(view.calls.length, 0);
  assert.match(text(view.render()), /请先配置接口/);
  assert.doesNotMatch(text(view.render()), /任选一个服务商|该 SKILL|DEMO_PROVIDER/);
  assert.ok(nodes(view.render(), n => n.type === 'option' && text(n) === '请选择 优先接口').length);
  assert.equal(nodes(view.render(), n => n.type === 'input' && n.props.type === 'password')[0].props.placeholder, '请输入 业务密钥');
  nodes(view.render(), n => n.type === 'input' && n.props.type === 'password')[0].props.onChange({target: {value: 'fixture-value'}});
  await button(view.render(), '保存配置').props.onClick();
  assert.equal(view.saves[0].DEMO_API_KEY, 'fixture-value'); assert.equal(view.refreshed(), 1);
  assert.match(text(view.render()), /已配置/);
  assert.equal(button(view.render(), '执行').props.disabled, false);
  await button(view.render(), '执行').props.onClick(); assert.equal(view.calls.length, 1);
});

test('execution failure remains visible and the request can be edited and retried', async () => {
  const view = await ready({failure: '原片文件不存在'});
  input(view.render()).props.onChange({target: {value: '第一条原片'}});
  await button(view.render(), '执行').props.onClick();
  assert.match(text(view.render()), /原片文件不存在/);
  input(view.render()).props.onChange({target: {value: '第二条原片'}});
  assert.equal(button(view.render(), '执行').props.disabled, false);
  await button(view.render(), '执行').props.onClick();
  assert.equal(view.calls[1][1], '第二条原片');
});
