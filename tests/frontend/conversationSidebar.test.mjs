import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const componentFile = new URL('../../web/frontend/src/components/ConversationSidebar.tsx', import.meta.url);

function nodes(tree, type) {
  if (!tree || typeof tree !== 'object') return [];
  return [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(child => nodes(child, type))];
}
function text(tree) {
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
  return tree && typeof tree === 'object' ? (tree.children || []).map(text).join('') : '';
}
function event(overrides = {}) {
  return { nativeEvent: {}, stopPropagation() {}, preventDefault() {}, ...overrides };
}
function action(tree, title) { return nodes(tree, 'button').find(button => button.props.title === title); }
function selections(tree) { return nodes(tree, 'button').filter(button => button.props.className?.includes('session-select')); }

function setup(props = {}) {
  assert.ok(existsSync(componentFile), 'the independent conversation sidebar must exist');
  const source = readFileSync(componentFile, 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  });
  const slots = [], effects = [], calls = [];
  let cursor = 0, pendingEffects = [], focusCount = 0;
  const React = {
    createElement: (type, attributes, ...children) => ({ type, props: attributes || {}, children: children.flat(Infinity) }),
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useEffect(callback, dependencies) {
      const index = cursor++, previous = effects[index];
      if (!previous || dependencies.some((value, position) => value !== previous.dependencies[position])) {
        pendingEffects.push(() => {
          previous?.cleanup?.();
          effects[index] = { dependencies, cleanup: callback() };
        });
      }
    },
  };
  const exports = {};
  runInNewContext(outputText, { exports, React, require: name => name === 'react' ? React : {} });
  const base = {
    sessions: [], activeSessionId: null, mobileOpen: false,
    onSessionSelect: id => calls.push(['select', id]),
    onSessionDelete: id => calls.push(['delete', id]),
    onSessionRename: (id, title) => calls.push(['rename', id, title]),
    onSessionArchive: (id, archived) => calls.push(['archive', id, archived]),
    onNewChat: () => calls.push(['new']), onClose: () => calls.push(['close']), ...props,
  };
  return {
    calls,
    render(overrides = {}) {
      cursor = 0; pendingEffects = [];
      const tree = exports.default({ ...base, ...overrides });
      for (const button of nodes(tree, 'button')) {
        if (button.props.ref) button.props.ref.current = { focus: () => focusCount++ };
      }
      for (const effect of pendingEffects) effect();
      return tree;
    },
    focused: () => focusCount,
  };
}

const populated = { id: 'populated', title: '已有对话', messages: [{ role: 'user', content: '合成历史' }] };
const empty = { id: 'empty', title: '空对话', messages: [] };
const archived = { id: 'archived', title: '已归档对话', archived: true, messages: [] };

test('history keeps populated and active empty conversations visible while archived entries are collapsed', () => {
  const view = setup({ sessions: [populated, empty, archived], activeSessionId: empty.id });
  const tree = view.render();
  assert.equal(tree.type, 'aside');
  assert.equal(tree.props['aria-label'], '对话历史');
  assert.equal(tree.props.id, 'session-history');
  assert.deepEqual(selections(tree).map(text), ['已有对话', '空对话']);
  assert.match(text(tree), /已归档.*1/);
  assert.deepEqual(selections(view.render({ activeSessionId: populated.id })).map(text), ['已有对话']);
});

test('choosing a conversation and creating a new one delegate once and close the mobile list', () => {
  const view = setup({ sessions: [populated] });
  const tree = view.render();
  selections(tree)[0].props.onClick(event());
  action(tree, '新建对话').props.onClick();
  assert.deepEqual(view.calls, [['select', 'populated'], ['close'], ['new'], ['close']]);
});

test('archive expansion reveals archived conversations and forwards the correct restore action', () => {
  const view = setup({ sessions: [archived] });
  let tree = view.render();
  const toggle = nodes(tree, 'button').find(button => button.props.className === 'archived-header');
  assert.equal(toggle.props['aria-expanded'], false);
  toggle.props.onClick(); tree = view.render();
  assert.deepEqual(selections(tree).map(text), ['已归档对话']);
  action(tree, '取消归档').props.onClick(event());
  assert.deepEqual(view.calls, [['archive', 'archived', false]]);
});

test('archive and delete preserve the existing application callbacks without selecting the conversation', () => {
  const view = setup({ sessions: [populated] });
  const tree = view.render();
  action(tree, '归档').props.onClick(event());
  action(tree, '删除').props.onClick(event());
  assert.deepEqual(view.calls, [['archive', 'populated', true], ['delete', 'populated']]);
});

test('Escape cancels a rename even when the old input blur callback subsequently fires', () => {
  const view = setup({ sessions: [populated] });
  action(view.render(), '重命名').props.onClick(event());
  const input = nodes(view.render(), 'input')[0];
  input.props.onChange(event({ target: { value: '不应保存' } }));
  input.props.onKeyDown(event({ key: 'Escape' }));
  input.props.onBlur();
  assert.deepEqual(view.calls, []);
  assert.equal(nodes(view.render(), 'input').length, 0);
});

test('Enter submits the latest rename only once before a stale blur callback', () => {
  const view = setup({ sessions: [populated] });
  action(view.render(), '重命名').props.onClick(event());
  const input = nodes(view.render(), 'input')[0];
  input.props.onChange(event({ target: { value: '新的标题' } }));
  input.props.onKeyDown(event({ key: 'Enter' }));
  input.props.onBlur();
  assert.deepEqual(view.calls, [['rename', 'populated', '新的标题']]);
});

test('Chinese input composition Enter does not prematurely commit the title', () => {
  const view = setup({ sessions: [populated] });
  action(view.render(), '重命名').props.onClick(event());
  const input = nodes(view.render(), 'input')[0];
  input.props.onChange(event({ target: { value: '中文输入' } }));
  input.props.onKeyDown(event({ key: 'Enter', nativeEvent: { isComposing: true } }));
  assert.deepEqual(view.calls, []);
  assert.equal(nodes(view.render(), 'input').length, 1);
  input.props.onBlur();
  assert.deepEqual(view.calls, [['rename', 'populated', '中文输入']]);
});

test('a normal blur commits once and a duplicate blur cannot repeat the operation', () => {
  const view = setup({ sessions: [populated] });
  action(view.render(), '重命名').props.onClick(event());
  const input = nodes(view.render(), 'input')[0];
  input.props.onChange(event({ target: { value: '失焦保存' } }));
  input.props.onBlur(); input.props.onBlur();
  assert.deepEqual(view.calls, [['rename', 'populated', '失焦保存']]);
});

test('opening the mobile list focuses its close button and Escape closes only an open list', () => {
  const view = setup();
  const closed = view.render();
  closed.props.onKeyDown(event({ key: 'Escape' }));
  assert.deepEqual(view.calls, []);
  const open = view.render({ mobileOpen: true });
  assert.match(open.props.className, /mobile-open/);
  assert.equal(view.focused(), 1);
  open.props.onKeyDown(event({ key: 'Escape' }));
  assert.deepEqual(view.calls, [['close']]);
  const close = nodes(open, 'button').find(button => button.props.className === 'conversation-history-close');
  close.props.onClick();
  assert.deepEqual(view.calls, [['close'], ['close']]);
});
