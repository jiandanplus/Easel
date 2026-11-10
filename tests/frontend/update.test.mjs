import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/update.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { updateView, restarted } = await import('data:text/javascript,' + encodeURIComponent(code));

const base = { managed: true, version: 37, state: 'idle', error: '', received: 0, available: null, busy: false, last: null };
const available = { version: 38, notes: '修复若干问题', bytes: 130 * 1024 * 1024 };

test('unmanaged install shows version and no action', () => {
  const view = updateView({ ...base, managed: false });
  assert.equal(view.action, null);
  assert.match(view.line, /不支持应用内更新/);
});

test('idle and latest offer a check', () => {
  assert.equal(updateView(base).action, 'check');
  const latest = updateView({ ...base, state: 'latest' });
  assert.equal(latest.action, 'check');
  assert.match(latest.line, /已是最新/);
});

test('available update shows version, size and notes with a download action', () => {
  const view = updateView({ ...base, state: 'available', available });
  assert.equal(view.action, 'download');
  assert.match(view.line, /v38/);
  assert.match(view.line, /130 MB/);
  assert.equal(view.notes, '修复若干问题');
});

test('download progress is a percentage of the signed size', () => {
  const view = updateView({ ...base, state: 'downloading', available, received: available.bytes / 4 });
  assert.equal(view.action, null);
  assert.match(view.line, /25%/);
});

test('ready update applies only when no task is running', () => {
  assert.equal(updateView({ ...base, state: 'ready', available }).disabled, false);
  const busy = updateView({ ...base, state: 'ready', available, busy: true });
  assert.equal(busy.action, 'apply');
  assert.equal(busy.disabled, true);
  assert.match(busy.notice, /任务/);
});

test('failure shows the error and allows retrying the download', () => {
  const view = updateView({ ...base, state: 'failed', available, error: '更新包校验失败' });
  assert.equal(view.action, 'download');
  assert.equal(view.notice, '更新包校验失败');
});

test('a rolled-back previous attempt is reported', () => {
  const view = updateView({ ...base, last: { status: 'rolled_back', from: 37, to: 38 } });
  assert.match(view.notice, /v38/);
  assert.match(view.notice, /v37/);
});

test('restart is detected only when a newer version answers', () => {
  assert.equal(restarted(37, null), false);
  assert.equal(restarted(37, { ...base, version: 37 }), false);
  assert.equal(restarted(37, { ...base, version: 38 }), true);
});
