import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const compilerOptions = {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX};
const names = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/skillDisplayNames.ts', import.meta.url), 'utf8'), {compilerOptions}).outputText, {exports: names});
const skills = [...new Set([...Object.keys(names.SKILL_DISPLAY_NAMES), 'video-production'])].map((name, i) => ({
  name, description: `内部触发规则 marker-${i} 当用户说生成内容`,
  layer: ['discover', 'plan', 'produce', 'publish', 'attribute', 'general'][i % 6],
  needsApi: i < 2, apiConfigured: i === 1,
}));
function viewSkills() {
  const module = {}, state = [], effects = []; let cursor = 0, effectCursor = 0, fetches = 0;
  const jsx = (type, props) => ({type, props: props || {}});
  const react = {
    useState(initial) {const n = cursor++; if (!(n in state)) state[n] = initial; return [state[n], value => {state[n] = typeof value === 'function' ? value(state[n]) : value;}];},
    useEffect(fn, deps) {const n = effectCursor++; if (!effects[n] || deps.some((x, i) => x !== effects[n].deps[i])) effects[n] = {fn, deps, pending: true};},
    useMemo: fn => fn(),
  };
  runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/SkillPage.tsx', import.meta.url), 'utf8'), {compilerOptions}).outputText, {
    exports: module,
    require: id => id === 'react' ? react : id === 'react/jsx-runtime' ? {jsx, jsxs: jsx} : id.endsWith('/api') ? {fetchSkills: async () => {fetches++; return skills;}} : id.endsWith('/SkillDrawer') ? {__esModule: true, default: 'skill-drawer'} : id.endsWith('/skillDisplayNames') ? names : {},
  });
  return {
    render() {cursor = effectCursor = 0; return module.default({persona: 'creator'});},
    effects() {effects.forEach(e => {if (e.pending) {e.pending = false; e.fn();}});},
    fetches: () => fetches,
  };
}
function nodes(tree, predicate) {return !tree || typeof tree !== 'object' ? [] : [...(predicate(tree) ? [tree] : []), ...[tree.props?.children].flat(Infinity).flatMap(x => nodes(x, predicate))];}
function text(tree) {return tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? [tree.props?.children].flat(Infinity).map(text).join('') : String(tree);}
const cards = tree => nodes(tree, n => n.props.className?.split(' ').includes('skill-card'));
const search = tree => nodes(tree, n => n.type === 'input')[0];
async function ready() {const view = viewSkills(); view.render(); view.effects(); await new Promise(resolve => setImmediate(resolve)); return view;}

test('all catalog entries remain native button actions with real configuration status', async () => {
  const view = await ready(), tree = view.render(), items = cards(tree);
  assert.equal(items.length, skills.length);
  assert.equal(skills.length, 114);
  assert.equal(nodes(tree, n => n.type === 'section').length, 6);
  assert.match(text(tree), /114 个技能 · 1 个待配置/);
  assert.equal(/内部触发规则|当用户说|marker-/.test(text(tree)), false, 'skill cards must not expose internal trigger instructions');
  assert.equal(search(tree).props['aria-label'], '搜索技能');
  for (const card of items) {
    assert.equal(card.type, 'button'); assert.equal(card.props.type, 'button');
    assert.equal(nodes(card, n => ['button', 'a', 'input', 'select', 'textarea'].includes(n.type)).length, 1);
  }
  const missing = items.find(n => text(n).includes(names.displayName(skills[0].name)));
  const configured = items.find(n => text(n).includes(names.displayName(skills[1].name)));
  assert.match(text(missing), /需 API/); assert.doesNotMatch(text(missing), /已配置/);
  assert.match(text(configured), /已配置/); assert.doesNotMatch(text(configured), /需 API/);
  assert.doesNotMatch(text(tree), /内部触发规则|当用户说|marker-/);
  assert.doesNotMatch(text(missing), new RegExp(skills[0].name));
});

test('hidden descriptions, canonical IDs and display names still find the same skill and open its drawer', async () => {
  const view = await ready();
  const target = skills[0];
  for (const query of [target.name, names.displayName(target.name), 'marker-0']) {
    search(view.render()).props.onChange({target: {value: query}});
    const tree = view.render(), items = cards(tree);
    assert.equal(items.length, 1); assert.match(text(items[0]), new RegExp(names.displayName(target.name)));
    items[0].props.onClick();
    const drawer = nodes(view.render(), n => n.type === 'skill-drawer')[0];
    assert.equal(drawer.props.skillName, target.name); assert.equal(drawer.props.persona, 'creator');
    drawer.props.onClose(); assert.equal(nodes(view.render(), n => n.type === 'skill-drawer').length, 0);
  }
  nodes(view.render(), n => n.type === 'button' && text(n) === '清除')[0].props.onClick();
  assert.equal(cards(view.render()).length, skills.length);
  assert.equal(view.fetches(), 1);
});
