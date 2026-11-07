import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
export const deliverableHelpers = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/deliverables.ts', import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports: deliverableHelpers });
export const requireDeliverables = name => {
  if (name === './deliverables') return deliverableHelpers;
  throw new Error(`Unexpected harness dependency: ${name}`);
};
