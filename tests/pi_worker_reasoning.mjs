/** Actual pinned Pi SDK -> loopback Responses wire proof. No commercial calls. */
import assert from 'node:assert/strict';
import http from 'node:http';
import { pathToFileURL } from 'node:url';

const directory = process.argv[2];
const { runAgent } = await import(pathToFileURL(directory + '/worker.mjs'));
let active, requests = [];
const server = http.createServer(async (req, res) => {
  try {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks));
    requests.push(body);
    assert.equal(req.url, '/v1/responses');
    assert.equal(req.headers.authorization, 'Bearer noncommercial-local-fixture');
    const tool = active.tools && requests.length === 1;
    const item = tool
      ? { type: 'function_call', id: 'fc_owned', call_id: 'call_owned', name: 'read_file', arguments: '{"path":"fixture.txt"}', status: 'completed' }
      : { type: 'message', id: 'msg_owned', role: 'assistant', content: [{ type: 'output_text', text: 'fixture-ok', annotations: [] }], status: 'completed' };
    const response = { id: 'resp_owned_' + requests.length, object: 'response', model: body.model,
      status: 'completed', output: [item], usage: { input_tokens: 3, output_tokens: 2, total_tokens: 5 } };
    const events = [
      { type: 'response.created', response: { ...response, status: 'in_progress', output: [] } },
      { type: 'response.output_item.added', output_index: 0, item: tool ? { ...item, arguments: '' } : { ...item, content: [] } },
      ...(tool ? [{ type: 'response.function_call_arguments.delta', output_index: 0, item_id: item.id, delta: item.arguments }]
        : [{ type: 'response.content_part.added', output_index: 0, content_index: 0, item_id: item.id, part: { type: 'output_text', text: '', annotations: [] } },
          { type: 'response.output_text.delta', output_index: 0, content_index: 0, item_id: item.id, delta: 'fixture-ok' }]),
      { type: 'response.output_item.done', output_index: 0, item },
      { type: 'response.completed', response },
    ];
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.end(events.map(e => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join(''));
  } catch { res.writeHead(500); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const baseUrl = `http://127.0.0.1:${server.address().port}/v1`;
const scenarios = [
  { name: 'unknown-default', efforts: [], effort: '' },
  { name: 'capable-default', efforts: ['low', 'high'], effort: '' },
  ...['minimal', 'low', 'medium', 'high', 'xhigh', 'max'].map(effort => ({ name: effort, efforts: [effort], effort })),
  { name: 'tools-high', efforts: ['low', 'high'], effort: 'high', tools: true },
];
try {
  for (active of scenarios) {
    requests = [];
    const events = [], tools = [];
    await runAgent({ type: 'run', id: 'owned', session_id: 'pi:00000000-0000-4000-8000-000000000000',
      system_prompt: 'Owned synthetic baseline', prompt: 'synthetic test', messages: [],
      api_key: 'noncommercial-local-fixture', tools_enabled: !!active.tools,
      reasoning_effort: active.effort,
      model: { id: 'selected-' + active.name, baseUrl, maxTokens: 128, contextWindow: 8192, reasoningEfforts: active.efforts } },
    { async send(value) { events.push(value); }, onAbort() {}, async tool(value) {
      tools.push(value); return { content: [{ type: 'text', text: 'owned fixture content' }], details: { ok: true } };
    } });
    assert.equal(requests.length, active.tools ? 2 : 1, active.name + ': request count');
    assert.ok(events.some(e => e.type === 'done'), active.name + ': no success');
    for (const body of requests) {
      assert.equal(body.model, 'selected-' + active.name);
      if (active.effort) assert.equal(body.reasoning?.effort, active.effort, active.name + ': wire reasoning');
      else assert.ok(!Object.hasOwn(body, 'reasoning'), active.name + ': default must omit reasoning');
    }
    if (active.tools) assert.equal(tools.length, 1);
    console.log(JSON.stringify({ scenario: active.name, requests: requests.length, tools: tools.length, wire_verified: true }));
  }
  for (const invalid of [
    { reasoning_effort: 'high', reasoningEfforts: [] },
    { reasoning_effort: 'bogus', reasoningEfforts: ['bogus'] },
    { reasoning_effort: 'low', reasoningEfforts: ['high'] },
  ]) {
    requests = [];
    await assert.rejects(runAgent({ type: 'run', id: 'owned', session_id: 'pi:00000000-0000-4000-8000-000000000000',
      system_prompt: 'Owned', prompt: 'synthetic', messages: [], api_key: 'noncommercial-local-fixture', tools_enabled: false,
      reasoning_effort: invalid.reasoning_effort,
      model: { id: 'invalid', baseUrl, maxTokens: 128, contextWindow: 8192, reasoningEfforts: invalid.reasoningEfforts } },
    { async send() {}, onAbort() {} }), /invalid_reasoning/);
    assert.equal(requests.length, 0);
  }
  console.log(JSON.stringify({ invalid_cases: 3, upstream_requests: 0, rejected: true }));
} finally { await new Promise(resolve => server.close(resolve)); }
