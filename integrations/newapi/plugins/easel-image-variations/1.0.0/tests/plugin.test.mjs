import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const path = new URL('../plugin.js', import.meta.url);
const plugin = async () => {
  let source;
  try { source = await readFile(path, 'utf8'); }
  catch { assert.fail('The variations plugin is missing'); }
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
};
const file = { ref: 'request_file:image', field: 'image', filename: 'input.png', mimeType: 'image/png', size: 200 };
const request = { body: { kind: 'multipart', fields: { model: ['easel-image-variations'], n: ['2'] }, files: [file] } };
const driver = { baseUrl: 'https://provider.example/v1', authHeader: 'Bearer server-owned', upstreamModel: 'configured-variation-model', publicTaskId: 'task_public', files: [file], requestBody: { n: 2, size: '1024x1024', response_format: 'url' } };

test('uses a private route without claiming the occupied standard endpoint', async () => {
  const p = await plugin();
  assert.equal(p.meta.routes[0].path, '/easel/openai/v1/images/variations');
  assert.equal(p.meta.protocols, undefined);
});
test('decodes multipart input into an explicit task intent', async () => {
  const p = await plugin();
  assert.deepEqual(p.native.create(request), { kind: 'submit', model: 'easel-image-variations', action: 'variations', requestBody: { n: 2, size: '1024x1024', response_format: 'url' } });
});
test('rejects unsupported bodies, parameters, duplicates and oversize input', async () => {
  const p = await plugin();
  assert.throws(() => p.native.create({ body: { kind: 'json', value: {} } }), /multipart/);
  for (const fields of [{ n: ['0'] }, { n: ['1', '2'] }, { size: ['9:16'] }, { mask: ['unexpected'] }, { url: ['http://localhost/'] }]) {
    assert.throws(() => p.native.create({ body: { ...request.body, fields: { ...request.body.fields, ...fields } } }));
  }
  assert.throws(() => p.native.create({ body: { ...request.body, files: [{ ...file, size: 4194305 }] } }), /image/);
});
test('rebuilds multipart using only host file references and channel credentials', async () => {
  const p = await plugin();
  const result = p.buildSubmitRequest(driver);
  assert.equal(result.url, 'https://provider.example/v1/images/variations');
  assert.equal(result.headers.Authorization, 'Bearer server-owned');
  assert.equal(result.bodyType, 'multipart');
  assert.deepEqual(result.parts[0], { name: 'image', fileRef: file.ref });
  assert.deepEqual(result.parts[1], { name: 'model', value: 'configured-variation-model' });
  assert.equal(result.headers['Content-Type'], undefined);
  assert.throws(() => p.buildSubmitRequest({ ...driver, baseUrl: 'https://provider.example/v1?redirect=evil' }), /base URL/);
});
test('requires a real successful result and reports actual returned image count', async () => {
  const p = await plugin();
  const body = { created: 1, data: [{ url: 'https://cdn.example/one.png' }] };
  const r = p.parseSubmitResponse(driver, { statusCode: 200, body });
  assert.equal(r.immediate.status, 'SUCCESS');
  assert.equal(r.taskId, 'task_public');
  assert.deepEqual(p.extractUsageOnSubmit(driver, r.taskData), { images: 1 });
  assert.deepEqual(p.native.result({}, { status: 'SUCCESS', data: r.taskData }), body);
  assert.throws(() => p.parseSubmitResponse(driver, { statusCode: 200, body: { error: { message: 'secret' } } }), /no usable images/);
  assert.throws(() => p.parseSubmitResponse(driver, { statusCode: 429, body }), /upstream request failed/);
});
test('never describes terminal JSON tasks as an asynchronous polling protocol', async () => {
  const p = await plugin();
  assert.throws(() => p.buildQueryRequest({}), /immediate/);
  assert.deepEqual(p.parseTaskResult({}, {}), { status: 'UNKNOWN' });
  assert.throws(() => p.native.result({}, { status: 'IN_PROGRESS', data: {} }), /not successful/);
  assert.deepEqual(p.extractUsage({ requestBody: { n: 3 } }), { images: 3 });
});
