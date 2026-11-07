import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const jsx = (type, props) => ({ type, props: props || {} });
const nodes = (tree, type) => !tree || typeof tree !== 'object' ? [] : [
  ...(tree.type === type ? [tree] : []),
  ...[tree.props.children].flat(Infinity).flatMap(child => nodes(child, type)),
];
const text = tree => tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object'
  ? [tree.props.children].flat(Infinity).map(text).join('') : String(tree);
const settle = () => new Promise(resolve => setImmediate(resolve));

// Exercise the actual ChatPage callback and MessageBubble action with an isolated
// navigator and hook state; no browser, OS clipboard, timers, or service requests.
function replyView(clipboard, role = 'assistant') {
  const componentState = new Map();
  const timers = [];
  let state, cursor;
  const React = {
    useState(initial) {
      const index = cursor++;
      if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
      const values = state;
      return [values[index], value => { values[index] = typeof value === 'function' ? value(values[index]) : value; }];
    },
    useRef: value => ({ current: value }),
    useEffect() {},
    useMemo: fn => fn(),
  };
  const draft = { scopeKey: 'scope', sessionId: 'chat', text: '', attachments: [], persisted: true };
  const modules = new Map();
  function load(name) {
    if (modules.has(name)) return modules.get(name);
    const exports = {};
    modules.set(name, exports);
    const source = readFileSync(new URL(`../../web/frontend/src/components/${name}.tsx`, import.meta.url), 'utf8');
    runInNewContext(ts.transpileModule(source, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText, {
      exports,
      require: id => id === 'react' ? React : id === 'react/jsx-runtime' ? { jsx, jsxs: jsx }
        : id === './MessageBubble' ? load('MessageBubble')
        : id.endsWith('/useComposerDraft') ? { useComposerDraft: () => ({ draft }) }
        : id.endsWith('/sanitize') ? { renderMarkdown: value => value }
        : id.endsWith('/linkifyOutputs') ? { linkifyOutputs: value => value, externalizeMediaLinks: value => value }
        : id.endsWith('/artifacts') ? { extractArtifacts: html => ({ html, artifacts: [], archives: [] }) }
        : id.endsWith('/deliverables') ? { mergeVerifiedArtifacts: () => [] }
        : { __esModule: true, default: id },
      navigator: { clipboard },
      DOMParser: class { parseFromString() { return { querySelectorAll: () => [] }; } },
      setTimeout: callback => { timers.push(callback); },
      Error,
    });
    return exports;
  }
  function renderComponent(component, props) {
    if (!componentState.has(component)) componentState.set(component, []);
    state = componentState.get(component); cursor = 0;
    return component(props);
  }
  const ChatPage = load('ChatPage').default;
  const MessageBubble = load('MessageBubble').default;
  const page = renderComponent(ChatPage, {
    apiDirect: true, draftScopeKey: 'scope', draftStore: {},
    session: { id: 'chat', messages: [{ role, content: '回复正文\n第二行' }] },
  });
  const bubble = nodes(page, MessageBubble)[0];
  assert.ok(bubble, 'the completed message has a bubble');
  const bubbleTree = renderComponent(MessageBubble, bubble.props);
  // ActionBar is private: reach it through the actual MessageBubble render tree.
  const findAction = tree => !tree || typeof tree !== 'object' ? undefined
    : tree.props.actions ? tree : [tree.props.children].flat(Infinity).map(findAction).find(Boolean);
  const actionBar = findAction(bubbleTree);
  assert.ok(actionBar, 'the message exposes its copy action');
  return {
    render: () => renderComponent(actionBar.type, actionBar.props),
    expireSuccess: () => timers.splice(0).forEach(callback => callback()),
  };
}

for (const role of ['user', 'assistant']) {
  test(`${role} message reports copy failure when the clipboard API is unavailable`, async () => {
    for (const clipboard of [undefined, {}]) {
      const view = replyView(clipboard, role);
      nodes(view.render(), 'button')[0].props.onClick();
      await settle();
      assert.match(text(view.render()), /复制失败/);
      assert.doesNotMatch(text(view.render()), /已复制/);
    }
  });
}

test('reply copy rejection stays recoverable and a later successful retry clears the error', async () => {
  let denied = true;
  const writes = [];
  const view = replyView({ writeText: async value => {
    writes.push(value);
    if (denied) throw new Error('permission denied');
  } });
  nodes(view.render(), 'button')[0].props.onClick();
  await settle();
  assert.match(text(view.render()), /复制失败/);
  assert.doesNotMatch(text(view.render()), /已复制/);
  denied = false;
  nodes(view.render(), 'button')[0].props.onClick();
  await settle();
  assert.match(text(view.render()), /已复制/);
  assert.doesNotMatch(text(view.render()), /复制失败/);
  assert.deepEqual(writes, ['回复正文\n第二行', '回复正文\n第二行']);
});

test('reply shows copied only after the exact text finishes writing and then resets its label', async () => {
  let finish;
  const writes = [];
  const view = replyView({ writeText: value => {
    writes.push(value);
    return new Promise(resolve => { finish = resolve; });
  } });
  nodes(view.render(), 'button')[0].props.onClick();
  await settle();
  assert.deepEqual(writes, ['回复正文\n第二行']);
  assert.doesNotMatch(text(view.render()), /已复制|复制失败/);
  finish();
  await settle();
  assert.match(text(view.render()), /已复制/);
  view.expireSuccess();
  assert.equal(text(view.render()), '复制');
});
