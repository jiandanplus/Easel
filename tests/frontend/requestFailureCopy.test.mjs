import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const helperFile = new URL('../../web/frontend/src/lib/requestFailureCopy.ts', import.meta.url);
const helpers = {};
if (existsSync(helperFile)) runInNewContext(ts.transpileModule(readFileSync(helperFile, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, {exports: helpers});
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const handlers = [];
function visit(node) {if (ts.isArrowFunction(node) && node.parameters[0]?.name.getText(ast) === 'err') handlers.push(node); ts.forEachChild(node, visit);}
visit(ast);
const diagnostic = 'Pi 模型或运行时执行失败，请检查供应商设置或中央执行状态';
for (const [i, handler] of handlers.entries()) test(`new SSE failure ${i + 1} uses concise known copy and preserves existing text and unknown errors`, () => {
  for (const message of [diagnostic, '网络连接失败，错误 503']) {
    const prior = {role: 'assistant', content: '原有历史 Pi 供应商设置'};
    const history = [prior];
    const code = ts.transpileModule(`(${handler.getText(ast)})(failure)`, {compilerOptions: {target: ts.ScriptTarget.ES2022}}).outputText;
    runInNewContext(code, {...helpers, failure: {message}, submission: null, typingBuf: {current: {}}, streamAcc: {current: {session: {content: '已有部分回复', steps: []}}}, sessionId: 'session', turnId: 'turn', appendAssistant: (_, value) => history.push(value), clearStream() {}, sessionStorage: {removeItem() {}}});
    assert.equal(history[0], prior); assert.equal(history[0].content, '原有历史 Pi 供应商设置');
    assert.equal(history[1].content, `已有部分回复\n\n${message === diagnostic ? '本次请求未完成，请查看请求状态' : message}`);
    assert.equal(history[1].taskState, 'unknown');
  }
});
test('both new stream error paths are exercised', () => assert.equal(handlers.length, 2));
