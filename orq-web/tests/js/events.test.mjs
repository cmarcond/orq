import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reduce, collectAnswers, peerBody, sentMsgId } from '../../orqweb/server/static/js/events.js';

const user = (content, extra = {}) => ({ type: 'user', message: { role: 'user', content }, ...extra });
const assistant = (blocks, extra = {}) => ({ type: 'assistant', message: { role: 'assistant', content: blocks }, ...extra });
const kinds = (actions) => actions.map((a) => a.kind);

test('a supervisor notice becomes sys; permission_answered becomes its own action with state working', () => {
  assert.deepEqual(reduce({ type: 'orq', level: 'error', text: 'crashed' }), [{ kind: 'sys', level: 'error', text: 'crashed' }]);
  const [a] = reduce({ type: 'orq', subtype: 'permission_answered', request_id: 'r1', behavior: 'deny' });
  assert.equal(a.kind, 'permissionAnswered');
  assert.equal(a.requestId, 'r1');
  assert.equal(a.behavior, 'deny');
  assert.equal(a.state, 'working');
});

test('a typed user message becomes a bubble', () => {
  assert.deepEqual(reduce(user('oi', { origin: { kind: 'human' } })), [{ kind: 'bubble', text: 'oi', pending: false }]);
});

test('the echo of a pending send matches and consumes the pending one', () => {
  const ctx = { pending: ['oi'] };
  assert.deepEqual(reduce(user('oi'), ctx), [{ kind: 'bubble', text: 'oi', pending: true }]);
  assert.deepEqual(ctx.pending, []);
});

test('an injected message (cron) becomes machine with the origin', () => {
  assert.deepEqual(reduce(user('### Notice\nbody', { orq_origin: 'injected', origin: { kind: 'cron' } })), [
    { kind: 'machine', text: '### Notice\nbody', origin: 'cron' },
  ]);
  // no stamp, on replay, no marks: also machine
  assert.equal(reduce(user('x', { replay: true }))[0].kind, 'machine');
  // on replay, typed by a human: bubble
  assert.equal(reduce(user('x', { replay: true, origin: { kind: 'human' }, promptSource: 'typed' }))[0].kind, 'bubble');
});

test('interruption and internal records', () => {
  assert.deepEqual(reduce(user('[Request interrupted by user]')), [{ kind: 'interrupted' }]);
  assert.deepEqual(reduce(user('<command-name>/exit</command-name>')), [{ kind: 'ignore' }]);
  assert.deepEqual(reduce(user('x', { isMeta: true })), [{ kind: 'ignore' }]);
});

test('pasted text is cleaned before becoming a bubble', () => {
  const [a] = reduce(user('see <pasted_content id="1">this</pasted_content>', { origin: { kind: 'human' } }));
  assert.equal(a.text, 'see 📋 this');
});

test('tool_result in a user message becomes toolResult, text concatenated and truncated', () => {
  const e = user([
    { type: 'tool_result', tool_use_id: 'tu1', content: [{ type: 'text', text: 'a' }, { type: 'text', text: 'b' }] },
    { type: 'tool_result', tool_use_id: 'tu2', content: 'x'.repeat(7000), is_error: true },
  ]);
  const acts = reduce(e);
  assert.deepEqual(acts[0], { kind: 'toolResult', toolUseId: 'tu1', text: 'a\nb', isError: false });
  assert.equal(acts[1].text.length, 6000);
  assert.equal(acts[1].isError, true);
});

test('assistant: text and tool_use, with state working only when live', () => {
  const acts = reduce(assistant([{ type: 'thinking', thinking: 'x' }, { type: 'text', text: 'hello' }, { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }]));
  assert.deepEqual(kinds(acts), ['text', 'toolUse']);
  assert.equal(acts[0].state, 'working');
  assert.equal(acts[1].summary, 'ls');
  const replayed = reduce(assistant([{ type: 'text', text: 'old' }], { replay: true }));
  assert.equal(replayed[0].state, undefined);
  assert.deepEqual(reduce(assistant([{ type: 'thinking' }])), [{ kind: 'ignore' }]);
});

test('stream_event: text start and delta; others ignored', () => {
  assert.deepEqual(reduce({ type: 'stream_event', event: { type: 'content_block_start', content_block: { type: 'text' } } }), [{ kind: 'streamStart' }]);
  assert.deepEqual(reduce({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'ab' } } }), [
    { kind: 'streamDelta', text: 'ab' },
  ]);
  assert.deepEqual(reduce({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'input_json_delta' } } }), [{ kind: 'ignore' }]);
});

test('ordinary permission and AskUserQuestion', () => {
  const req = { type: 'control_request', request_id: 'r', request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'ls' } } };
  const [p] = reduce(req);
  assert.equal(p.kind, 'permission');
  assert.equal(p.state, 'permission');
  assert.equal(p.msg, 'Bash');
  const ask = { ...req, request: { subtype: 'can_use_tool', tool_name: 'AskUserQuestion', input: { questions: [{ question: 'q?' }] } } };
  assert.equal(reduce(ask)[0].kind, 'ask');
  assert.equal(reduce({ ...req, replay: true })[0].state, undefined);
  assert.equal(reduce({ ...req, replay: true })[0].replayed, true);
  assert.equal(reduce(req)[0].replayed, undefined);
  assert.deepEqual(reduce({ type: 'control_request', request: { subtype: 'interrupt' } }), [{ kind: 'ignore' }]);
});

test('result: success becomes waiting, an error becomes error with text', () => {
  assert.deepEqual(reduce({ type: 'result', subtype: 'success' }), [{ kind: 'result', isError: false, text: '', state: 'waiting', msg: '' }]);
  const [r] = reduce({ type: 'result', is_error: true, result: 'quota exceeded' });
  assert.equal(r.state, 'error');
  assert.equal(r.text, 'quota exceeded');
});

test('unknown types are ignored without error', () => {
  assert.deepEqual(reduce({ type: 'rate_limit_event' }), [{ kind: 'ignore' }]);
  assert.deepEqual(reduce({ type: 'system', subtype: 'init' }), [{ kind: 'ignore' }]);
});

test('collectAnswers joins the choices and requires all of them', () => {
  const qs = [{ question: 'A?' }, { question: 'B?' }];
  assert.deepEqual(collectAnswers(qs, [['x'], ['y', 'z']]), { 'A?': 'x', 'B?': 'y, z' });
  assert.equal(collectAnswers(qs, [['x'], []]), null);
  assert.equal(collectAnswers(qs, [['x'], ['']]), null);
});

test('a message from another session (peer) becomes wireIn with the clean text and who sent it', () => {
  const wrapped = 'Another Claude session sent a message:\n<cross-session-message from-name="exe">\nPONG\n</cross-session-message>\n\nThis came…';
  const [a] = reduce(user(wrapped, { orq_origin: 'agent', origin: { kind: 'peer', name: 'exe-laptop', body: 'PONG', msg_id: 'm1' } }));
  assert.deepEqual(a, { kind: 'wireIn', from: 'exe-laptop', text: 'PONG', msgId: 'm1', state: 'working' });
  // on history replay only origin comes (no stamp) and no body: the text comes out of the wrapper
  const [r] = reduce(user(wrapped, { replay: true, origin: { kind: 'peer', name: 'exe' } }));
  assert.equal(r.kind, 'wireIn');
  assert.equal(r.text, 'PONG');
  assert.equal(r.state, undefined);
});

test('SendMessage becomes wireOut instead of toolUse', () => {
  const acts = reduce(assistant([{ type: 'tool_use', id: 't9', name: 'SendMessage', input: { to: 'exe-laptop', summary: 'delegate', message: 'do X' } }]));
  assert.deepEqual(acts, [{ kind: 'wireOut', id: 't9', to: 'exe-laptop', summary: 'delegate', message: 'do X', state: 'working' }]);
});

test('peerBody and sentMsgId', () => {
  assert.equal(peerBody('<cross-session-message from="x">\n  hello\n</cross-session-message>'), 'hello');
  assert.equal(peerBody('no wrapper'), 'no wrapper');
  assert.equal(sentMsgId('{"success":true,"message":"→ exe","msg_id":"abc"}'), 'abc');
  assert.equal(sentMsgId('{"success":false,"msg_id":"abc"}'), '');
  assert.equal(sentMsgId('not json'), '');
});
