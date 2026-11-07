import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const module = {};
const jsx = (type, props) => ({type, props: props || {}});
runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/EnvBoard.tsx', import.meta.url), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText, {exports: module, require: id => id === 'react/jsx-runtime' ? {jsx, jsxs: jsx} : {}});
const tools = [
  {id: 'node', name: 'Node.js', group: 'rm', desc: 'rm 运行时 · 建议 24 LTS', state: 'ok', version: 'v22.20.0'},
  {id: 'ffmpeg', name: 'ffmpeg', group: 'rm', desc: 'full build · lut3d / nlmeans 等滤镜齐', state: 'fail', detail: '磁盘空间不足'},
  {id: 'python', name: 'Python', group: 'rm', desc: '3.11+ · 工具与门脚本运行用', state: 'missing'},
  {id: 'fw', name: 'faster-whisper', group: 'rm', desc: 'GPU 转录（cu121）', state: 'missing'},
  {id: 'model', name: 'Whisper large-v3 模型', group: 'rm', desc: '约 3.1GB · hf-mirror 直拉 · 不入包', state: 'missing', big: true},
  {id: 'rmdeps', name: 'Remotion 工程依赖', group: 'rm', desc: '蓝本内置 · npm ci 还原（镜像加速）', state: 'no_dir'},
  {id: 'shell', name: 'Chrome Headless Shell', group: 'rm', desc: '渲染用 · 随 Remotion 下载', state: 'missing'},
  {id: 'biliup', name: 'biliup', group: 'pub', desc: 'B站投稿 CLI · python 包装', state: 'missing'},
  {id: 'pw', name: 'Playwright Chromium', group: 'pub', desc: '小红书 / 快手 / 知乎 发布链浏览器', state: 'missing'},
  {id: 'cft', name: 'Chrome for Testing', group: 'pub', desc: '登录态浏览器 · 平台登录用', state: 'missing'},
  {id: 'pylibs', name: '常用 Python 库', group: 'common', desc: 'Pillow · edge-tts · pdfplumber · requests', state: 'missing'},
];
function render(overrides = {}) {
  return module.default({tools, python: '/fixture/python', loading: false, error: '', jobs: {}, anyRunning: false, onRefresh() {}, onInstall: async () => {}, onInstallMany: async () => {}, ...overrides});
}
function nodes(tree, predicate) {return !tree || typeof tree !== 'object' ? [] : [...(predicate(tree) ? [tree] : []), ...[tree.props?.children].flat(Infinity).flatMap(x => nodes(x, predicate))];}
function text(tree) {return tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? [tree.props?.children].flat(Infinity).map(text).join('') : String(tree);}
const button = (tree, label) => nodes(tree, n => n.type === 'button' && text(n) === label)[0];
const card = (tree, id) => nodes(tree, n => n.props['data-tool'] === id)[0];
const specs = tree => nodes(tree, n => n.props.className === 'card-desc');

test('environment cards retain objective sizes and versions without internal implementation copy', () => {
  const tree = render();
  assert.equal(nodes(tree, n => n.props['data-tool']).length, 11);
  assert.match(text(tree), /就绪 1 \/ 11/);
  assert.match(text(card(tree, 'node')), /v22.20.0/);
  assert.match(text(card(tree, 'node')), /24 LTS/);
  assert.match(text(card(tree, 'python')), /3.11\+/);
  assert.match(text(card(tree, 'model')), /约 3.1GB/);
  assert.doesNotMatch(text(tree), /rm 运行时|建议|full build|lut3d|nlmeans|GPU 转录|cu121|hf-mirror|直拉|不入包|蓝本内置|镜像加速|python 包装|Pillow|发布与读回|按需/);
  assert.equal(specs(card(tree, 'ffmpeg')).length, 0);
  assert.equal(specs(card(tree, 'fw')).length, 0);
  const resized = render({tools: [{...tools[4], desc: '约 5.2GB · 新镜像实现说明'}]});
  assert.match(text(resized), /约 5.2GB/); assert.doesNotMatch(text(resized), /3.1GB|新镜像实现说明/);
});

test('single, group and all install actions keep their original IDs and eligibility', () => {
  const installs = [], batches = []; let refreshed = 0;
  const tree = render({onInstall: async id => installs.push(id), onInstallMany: async ids => batches.push(Array.from(ids)), onRefresh: () => refreshed++});
  button(card(tree, 'model'), '安装').props.onClick();
  button(card(tree, 'ffmpeg'), '重试').props.onClick();
  button(card(tree, 'node'), '重装').props.onClick();
  assert.deepEqual(installs, ['model', 'ffmpeg', 'node']);
  assert.equal(button(card(tree, 'rmdeps'), '需目录').props.disabled, true);
  assert.match(text(card(tree, 'rmdeps')), /需要指定工程目录/);
  button(tree, '全部安装').props.onClick();
  const groups = nodes(tree, n => n.props.className === 'grp');
  button(groups[0], '整组安装').props.onClick();
  assert.deepEqual(batches[0], tools.filter(t => !['ok', 'no_dir'].includes(t.state)).map(t => t.id));
  assert.deepEqual(batches[1], ['ffmpeg', 'python', 'fw', 'model', 'shell']);
  button(tree, '重新检测').props.onClick(); assert.equal(refreshed, 1);
});

test('real install errors, running progress and the verification transition remain visible', () => {
  const tree = render({anyRunning: true, error: '环境体检失败：连接中断', jobs: {fw: {state: 'running', line: '下载 48% · 1.5GB / 3.1GB'}, ffmpeg: {state: 'fail', line: '', detail: '下载失败：磁盘空间不足'}}});
  assert.match(text(tree), /环境体检失败：连接中断/);
  assert.match(text(card(tree, 'ffmpeg')), /下载失败：磁盘空间不足/);
  assert.equal(card(tree, 'ffmpeg').props['data-state'], 'fail');
  assert.match(text(card(tree, 'fw')), /下载 48% · 1.5GB \/ 3.1GB/);
  assert.equal(card(tree, 'fw').props['data-state'], 'busy');
  assert.equal(button(tree, '全部安装').props.disabled, true);
  assert.equal(button(tree, '重新检测').props.disabled, true);
  assert.equal(button(card(tree, 'ffmpeg'), '重试').props.disabled, true);
  const checking = render({jobs: {model: {state: 'ok', line: '安装完成'}}});
  assert.match(text(card(checking, 'model')), /校验中/);
  assert.equal(card(checking, 'model').props['data-state'], 'busy');
  assert.equal(card(checking, 'model').props['data-state'] === 'ok', false);
});
