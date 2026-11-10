import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function pick(file, names) {
  const source = readFileSync(new URL(`../../web/frontend/src/components/${file}`, import.meta.url), 'utf8');
  const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const chosen = parsed.statements.filter(node => (ts.isVariableStatement(node) && node.declarationList.declarations.some(d => names.includes(d.name.getText(parsed))))
    || (ts.isFunctionDeclaration(node) && names.includes(node.name?.text)));
  const code = chosen.map(node => node.getText(parsed).replace(/^export /, '')).join('\n') + `\n({${names.join(',')}})`;
  return runInNewContext(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, { navigator: { platform: 'MacIntel' } });
}

test('settings sidebar has the six groups in the requested order', () => {
  const { SETTINGS_GROUPS } = pick('SettingsPanel.tsx', ['SETTINGS_GROUPS']);
  assert.deepEqual(JSON.parse(JSON.stringify(SETTINGS_GROUPS.map(group => [group.label, group.items.map(item => item.label)]))), [
    ['账号', ['个人中心', '外观', '快捷键', '数据统计']],
    ['订阅与账单', ['套餐', '用量', '余额', '账单']],
    ['智能体', ['AI 服务商', '技能', '连接器']],
    ['连接', ['聊天平台', '账号管理']],
    ['运行资源', ['设备', '运行环境']],
    ['其他', ['回收站', '数据存储']],
  ]);
  const ids = SETTINGS_GROUPS.flatMap(group => group.items.map(item => item.id));
  assert.equal(new Set(ids).size, ids.length);
  const icons = readFileSync(new URL('../../web/frontend/src/components/settingsNavIcons.tsx', import.meta.url), 'utf8');
  for (const id of ids) assert.match(icons, new RegExp(`\\b${id}: <Svg>`), `icon for ${id}`);
});

test('storage sizes and shortcut list read naturally', () => {
  const { formatBytes, SHORTCUTS } = pick('SettingsPages.tsx', ['formatBytes', 'mod', 'SHORTCUTS']);
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(250 * 1024 * 1024), '250 MB');
  assert.equal(formatBytes(3.2 * 1024 ** 3), '3.2 GB');
  const all = SHORTCUTS.flatMap(group => group.items);
  assert.ok(all.some(([keys, label]) => keys === '⌘ ,' && label === '打开设置'));
  assert.ok(all.some(([keys, label]) => keys === '⌘ N' && label === '新建对话'));
});

test('profile shows a letter when there is no avatar', () => {
  const { initialOf } = pick('ProfileSettings.tsx', ['initialOf']);
  assert.equal(initialOf('jiandan'), 'J');
  assert.equal(initialOf('进击的晴明'), '进');
  assert.equal(initialOf(''), '优');
  assert.equal(initialOf(null), '优');
});

test('plan page converts points, prices and periods exactly', () => {
  const { pointsText, yuan, perMillion, periodOf, sellable, ratioOf, yearlySaving } = pick('PlanSettings.tsx', ['pointsText', 'yuan', 'perMillion', 'periodOf', 'sellable', 'ratioOf', 'yearlySaving']);
  assert.equal(ratioOf({ credits: '5000000000' }, { credits: '1000000000' }), '5');
  assert.equal(ratioOf({ credits: '2500000000' }, { credits: '1000000000' }), '2.5');
  assert.equal(yearlySaving('19440', '1800'), 10);  // ¥194.4 a year against ¥18 × 12.
  assert.equal(yearlySaving('21600', '1800'), null);
  assert.equal(pointsText('1000000000'), '1,000');
  assert.equal(pointsText('99970000'), '99.97');
  assert.equal(pointsText('500000'), '0.5');
  assert.equal(pointsText(null), '—');
  assert.equal(yuan('9900'), '99');
  assert.equal(yuan('216000'), '2,160');
  assert.equal(yuan('1990'), '19.90');
  assert.equal(perMillion({ numerator: '1', denominator: '2' }), '0.5');
  assert.equal(perMillion({ numerator: '3', denominator: '2' }), '1.5');
  assert.equal(perMillion({ numerator: '16', denominator: '1' }), '16');
  assert.equal(perMillion({ numerator: 'x', denominator: '1' }), '—');
  assert.deepEqual(JSON.parse(JSON.stringify([periodOf(30), periodOf(31), periodOf(365), periodOf(7)])), ['month', 'month', 'year', 'once']);
  assert.equal(sellable({ id: 'plan-a', test_only: false }), true);
  assert.equal(sellable({ id: 'easel-owner-allowance-v1', test_only: false }), false);
  assert.equal(sellable({ id: 'plan-t', test_only: true }), false);
});
