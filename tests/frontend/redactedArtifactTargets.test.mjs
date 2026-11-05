import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { deliverableHelpers } from './deliverablesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript'), { marked } = require('marked');
marked.setOptions({ gfm: true, breaks: true });

// DOM boundary fixture only: real marked produces HTML; the browser's anchor
// attribute/removeAttribute API is modeled here. This is not browser acceptance.
const attr = /\s+(href|target|rel|download)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi;
const decodeEntities = text => text.replace(/&#(x[0-9a-f]+|\d+);/gi, (_, value) => String.fromCodePoint(value[0].toLowerCase() === 'x' ? parseInt(value.slice(1), 16) : Number(value)))
  .replace(/&(?:lbrack|lsqb);/gi, '[').replace(/&(?:rbrack|rsqb);/gi, ']').replace(/&amp;/gi, '&');
class HTMLBoundary {
  parseFromString(html) {
    const parts = html.split(/(<a\b[^>]*>)/gi);
    const nodes = parts.flatMap((part, index) => /^<a\b/i.test(part) ? [{
      getAttribute(name) {
        return [...parts[index].matchAll(attr)].find(match => match[1].toLowerCase() === name)?.slice(2).find(value => value !== undefined) ?? null;
      },
      removeAttribute(name) { parts[index] = parts[index].replace(attr, (whole, key) => key.toLowerCase() === name ? '' : whole); },
      setAttribute(name, value) {
        this.removeAttribute(name); parts[index] = parts[index].replace(/>$/, ` ${name}="${value}">`);
      },
    }] : []);
    for (const node of nodes) {
      const get = node.getAttribute;
      node.getAttribute = name => { const value = get(name); return value === null ? null : decodeEntities(value); };
    }
    return { querySelectorAll: () => nodes.filter(node => node.getAttribute('href') !== null),
      body: { get innerHTML() { return parts.join(''); } } };
  }
}
const links = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/linkifyOutputs.ts', import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText,
  { exports: links, DOMParser: HTMLBoundary, TextDecoder, require: () => ({ mediaUrl: path => '/api/media/' + path }) });
const base = 'http://127.0.0.1:17868/api/media/proof/tool-check.tx';
const forms = [
  `${base}[已隐藏]`, `<${base}[已隐藏]>`, `[下载](${base}[已隐藏])`,
  `<a href="${base}[已隐藏]">下载</a>`, `<a HREF='${base}%5B%E5%B7%B2%E9%9A%90%E8%97%8F%5D'>下载</a>`,
  `<a href="${base}&#91;已隐藏&#93;">下载</a>`,
  `<a href="${base}%FF%5B%E5%B7%B2%E9%9A%90%E8%97%8F%5D">下载</a>`,
];
for (const streaming of [false, true]) for (const [index, input] of forms.entries()) {
  test(`actual marked HTML disables redacted artifact target ${index}, streaming=${streaming}`, () => {
    const body = streaming ? links.disableRedactedOutputLinks(input) : links.linkifyOutputs(input);
    const html = links.externalizeMediaLinks(marked.parse(body));
    assert.equal(new HTMLBoundary().parseFromString(html).querySelectorAll('a[href]').length, 0, html);
    assert.ok(html.includes('下载') || html.includes('tool-check.tx'), 'visible original body survives');
  });
}
test('HTML boundary retains ordinary links and exact separately verified delivery reference', () => {
  const html = links.externalizeMediaLinks(marked.parse('[文件](/api/media/proof/tool-check.txt) [外链](https://example.invalid/ok)'));
  const targets = new HTMLBoundary().parseFromString(html).querySelectorAll('a[href]').map(node => node.getAttribute('href'));
  assert.deepEqual(targets, ['/api/media/proof/tool-check.txt', 'https://example.invalid/ok']);
  assert.match(html, /target="_blank"/);
  const item = { version: 1, session_id: 'owned', turn_id: 'turn', path: 'outputs/proof/tool-check.txt', name: 'tool-check.txt', size: 25, sha256: 'a'.repeat(64) };
  assert.equal(deliverableHelpers.deliverableDownloadUrl(deliverableHelpers.verifiedArtifactReference(item, 'owned', 'turn')), '/api/media/proof/tool-check.txt');
});

for (const streaming of [false, true]) test(`MessageBubble actual marked pipeline keeps exact verified card, streaming=${streaming}`, () => {
  const exports = {}, jsx = (type, props) => ({ type, props: props || {} });
  runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/MessageBubble.tsx', import.meta.url), 'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText,
    { exports, DOMParser: HTMLBoundary, require: id => id === 'react' ? { useMemo: fn => fn(), useState: value => [value, () => {}] }
      : id === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : id.endsWith('/deliverables') ? deliverableHelpers
      : id.endsWith('/linkifyOutputs') ? links : id.endsWith('/sanitize') ? { renderMarkdown: value => marked.parse(value) }
      : id.endsWith('/artifacts') ? { extractArtifacts: html => ({ html, artifacts: [], archives: [] }) }
      : id.endsWith('/DeliverableActions') ? { __esModule: true, default: 'actions' } : {} });
  const content = `<${base}[已隐藏]>`;
  const message = { role: 'assistant', content, turnId: 'turn', verifiedArtifacts: [{ version: 1, session_id: 'owned', turn_id: 'turn',
    path: 'outputs/proof/tool-check.txt', name: 'tool-check.txt', size: 25, sha256: 'a'.repeat(64) }] };
  const tree = exports.default({ message, sessionId: 'owned', isStreaming: streaming });
  const nodes = value => !value || typeof value !== 'object' ? [] : [value, ...[value.props?.children].flat(Infinity).flatMap(nodes)];
  const html = nodes(tree).find(node => node.props?.dangerouslySetInnerHTML)?.props.dangerouslySetInnerHTML.__html;
  assert.equal(new HTMLBoundary().parseFromString(html).querySelectorAll('a[href]').length, 0);
  assert.equal(nodes(tree).find(node => node.type === 'actions')?.props.reference.path, 'proof/tool-check.txt');
  assert.equal(message.content, content);
});
