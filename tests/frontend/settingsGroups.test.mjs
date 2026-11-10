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
  const { pointsText, yuan, perMillion, periodOfOffer, sellable, ratioOf, yearlySaving } = pick('PlanSettings.tsx', ['pointsText', 'yuan', 'perMillion', 'periodOfOffer', 'sellable', 'ratioOf', 'yearlySaving']);
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
  assert.deepEqual([periodOfOffer({ period: 'year' }), periodOfOffer({ period: 'month' }), periodOfOffer({})].join(','), 'year,month,once');
  assert.equal(sellable({ id: 'plan-a', test_only: false }), true);
  assert.equal(sellable({ id: 'easel-owner-allowance-v1', test_only: false }), false);
  assert.equal(sellable({ id: 'plan-t', test_only: true }), false);
});

test('usage page sorts charges into kinds and sums points per day exactly', () => {
  const u = pick('UsageSettings.tsx', ['kindOf', 'chargedMicro', 'finePoints', 'toDate', 'dayKey', 'dailySpend', 'tokenTone', 'untilText']);
  assert.equal(u.kindOf({ operation: 'execution' }), 'text');
  assert.equal(u.kindOf({ operation: 'media', capability: 'image.generate' }), 'image');
  assert.equal(u.kindOf({ operation: 'media', capability: 'audio.speech' }), 'voice');
  assert.equal(u.kindOf({ operation: 'media', capability: 'audio.transcribe' }), 'transcribe');
  assert.equal(u.chargedMicro({ status: 'settled', actual: '1600', amount: '9000' }), 1600n);
  assert.equal(u.chargedMicro({ status: 'held', actual: null, amount: '9000' }), 9000n);
  assert.equal(u.chargedMicro({ status: 'waived', actual: '5', amount: '9000' }), 0n);
  assert.equal(u.finePoints(1600n), '0.0016');
  assert.equal(u.finePoints(2080000n), '2.08');
  assert.equal(u.finePoints(50n), '<0.0001');
  const now = new Date(2026, 10, 10, 12);
  const days = u.dailySpend([{ created_at: new Date(2026, 10, 9, 9).toISOString(), status: 'settled', actual: '700' }, { created_at: new Date(2026, 10, 9, 20).toISOString(), status: 'settled', actual: '300' }], 3, now);
  assert.deepEqual(days.map(d => `${d.day}:${d.micro}`).join(','), '2026-11-08:0,2026-11-09:1000,2026-11-10:0');
  assert.deepEqual([u.tokenTone(1465), u.tokenTone(56754), u.tokenTone(219064)].join(','), 'low,mid,high');
  assert.equal(u.untilText(new Date(now.getTime() + (2 * 24 + 5) * 3600000).toISOString(), now.getTime()), '2 天 5 小时后到期');
  assert.equal(u.untilText(new Date(now.getTime() - 1000).toISOString(), now.getTime()), '已到期');
});

test('balance page sorts grants into active, used up and expired', () => {
  const b = pick('BalanceSettings.tsx', ['grantState', 'remainingShare']);
  const now = Date.parse('2026-11-10T00:00:00Z');
  const base = { issued: '1000000000', available: '400000000', revoked: false, expires_at: '2026-12-01T00:00:00Z' };
  assert.equal(b.grantState(base, now), 'active');
  assert.equal(b.grantState({ ...base, available: '0' }, now), 'used');
  assert.equal(b.grantState({ ...base, expires_at: '2026-11-01T00:00:00Z' }, now), 'expired');
  assert.equal(b.grantState({ ...base, revoked: true }, now), 'expired');
  assert.equal(b.remainingShare(base), 40);
  assert.equal(b.remainingShare({ ...base, issued: '0' }), 0);
});

test('billing history shows short order states', () => {
  const { orderStatus } = pick('BillingPage.tsx', ['orderStatus']);
  const label = order => orderStatus(order).text;
  assert.equal(label({ status: 'paid', credit_state: 'granted' }), '已支付');
  assert.equal(label({ status: 'paid', credit_state: 'pending' }), '入账中');
  assert.equal(label({ status: 'pending' }), '待支付');
  assert.equal(label({ status: 'closed' }), '已关闭');
  assert.equal(label({ status: 'weird' }), '待确认');
});
