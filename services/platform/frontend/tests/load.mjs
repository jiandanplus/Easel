import { createRequire } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { resolve, dirname } from 'node:path';
const local = createRequire(new URL('../package.json', import.meta.url));
const shared = createRequire(new URL('../../../../web/frontend/package.json', import.meta.url));
export const dependency = name => { try { return local(name); } catch { return shared(name); } };
const ts = dependency('typescript');
export function load(name, overrides = {}) {
  const path = new URL('../src/' + name, import.meta.url).pathname;
  const cache = new Map();
  function compile(file) {
    if (!existsSync(file)) return {};
    if (cache.has(file)) return cache.get(file);
    const exports = {}; cache.set(file, exports);
    const code = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    runInNewContext(code, { exports, require: name => overrides[name] ?? (name.startsWith('.') ? compile(resolve(dirname(file), name + (existsSync(resolve(dirname(file), name + '.ts')) ? '.ts' : '.tsx'))) : dependency(name)), URLSearchParams, URL, AbortController, Headers, Response, Error, Date, Promise, BigInt, JSON, console, setTimeout, clearTimeout, crypto: { randomUUID: () => 'generated-key' } });
    return exports;
  }
  return compile(path);
}
