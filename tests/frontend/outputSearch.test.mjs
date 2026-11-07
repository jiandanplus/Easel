import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
let filterLibraryNodes;
try {
  const source = readFileSync(new URL('../../web/frontend/src/lib/outputSearch.ts', import.meta.url), 'utf8');
  const module = { exports: {} };
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  runInNewContext(outputText, { exports: module.exports });
  filterLibraryNodes = module.exports.filterLibraryNodes;
} catch (error) { if (error.code !== 'ENOENT') throw error; }
const nodes = [{ name: 'draft.md', meta: { title: 'AI 学习笔记' } }, { name: '秋日旅行.png' }];
test('library search exposes the real filter', () => assert.equal(typeof filterLibraryNodes, 'function'));
test('empty search retains all current folder nodes', () => assert.equal(filterLibraryNodes(nodes, '  ').length, 2));
test('search matches displayed project titles and ignores ASCII case', () => assert.equal(filterLibraryNodes(nodes, ' ai ')[0], nodes[0]));
test('search matches Chinese filenames', () => assert.equal(filterLibraryNodes(nodes, '旅行')[0], nodes[1]));
test('unmatched search is empty and never mutates source nodes', () => { assert.equal(filterLibraryNodes(nodes, '音乐').length, 0); assert.equal(nodes.length, 2); });
