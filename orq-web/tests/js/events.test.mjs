import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reduce, collectAnswers, peerBody, sentMsgId } from '../../orqweb/server/static/js/events.js';

const user = (content, extra = {}) => ({ type: 'user', message: { role: 'user', content }, ...extra });
const assistant = (blocks, extra = {}) => ({ type: 'assistant', message: { role: 'assistant', content: blocks }, ...extra });
const kinds = (actions) => actions.map((a) => a.kind);

test('aviso do supervisor vira sys; permission_answered vira ação própria com estado working', () => {
  assert.deepEqual(reduce({ type: 'orq', level: 'error', text: 'caiu' }), [{ kind: 'sys', level: 'error', text: 'caiu' }]);
  const [a] = reduce({ type: 'orq', subtype: 'permission_answered', request_id: 'r1', behavior: 'deny' });
  assert.equal(a.kind, 'permissionAnswered');
  assert.equal(a.requestId, 'r1');
  assert.equal(a.behavior, 'deny');
  assert.equal(a.state, 'working');
});

test('mensagem do usuário digitada vira balão', () => {
  assert.deepEqual(reduce(user('oi', { origin: { kind: 'human' } })), [{ kind: 'bubble', text: 'oi', pending: false }]);
});

test('eco de envio pendente casa e consome o pendente', () => {
  const ctx = { pending: ['oi'] };
  assert.deepEqual(reduce(user('oi'), ctx), [{ kind: 'bubble', text: 'oi', pending: true }]);
  assert.deepEqual(ctx.pending, []);
});

test('mensagem injetada (cron) vira machine com a origem', () => {
  assert.deepEqual(reduce(user('### Aviso\ncorpo', { orq_origin: 'injected', origin: { kind: 'cron' } })), [
    { kind: 'machine', text: '### Aviso\ncorpo', origin: 'cron' },
  ]);
  // sem carimbo, no replay, sem marcas: também máquina
  assert.equal(reduce(user('x', { replay: true }))[0].kind, 'machine');
  // no replay, humano digitado: balão
  assert.equal(reduce(user('x', { replay: true, origin: { kind: 'human' }, promptSource: 'typed' }))[0].kind, 'bubble');
});

test('interrupção e registros internos', () => {
  assert.deepEqual(reduce(user('[Request interrupted by user]')), [{ kind: 'interrupted' }]);
  assert.deepEqual(reduce(user('<command-name>/exit</command-name>')), [{ kind: 'ignore' }]);
  assert.deepEqual(reduce(user('x', { isMeta: true })), [{ kind: 'ignore' }]);
});

test('texto colado é limpo antes de virar balão', () => {
  const [a] = reduce(user('veja <pasted_content id="1">isto</pasted_content>', { origin: { kind: 'human' } }));
  assert.equal(a.text, 'veja 📋 isto');
});

test('tool_result em mensagem user vira toolResult, texto concatenado e truncado', () => {
  const e = user([
    { type: 'tool_result', tool_use_id: 'tu1', content: [{ type: 'text', text: 'a' }, { type: 'text', text: 'b' }] },
    { type: 'tool_result', tool_use_id: 'tu2', content: 'x'.repeat(7000), is_error: true },
  ]);
  const acts = reduce(e);
  assert.deepEqual(acts[0], { kind: 'toolResult', toolUseId: 'tu1', text: 'a\nb', isError: false });
  assert.equal(acts[1].text.length, 6000);
  assert.equal(acts[1].isError, true);
});

test('assistente: texto e tool_use, com estado working só ao vivo', () => {
  const acts = reduce(assistant([{ type: 'thinking', thinking: 'x' }, { type: 'text', text: 'olá' }, { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }]));
  assert.deepEqual(kinds(acts), ['text', 'toolUse']);
  assert.equal(acts[0].state, 'working');
  assert.equal(acts[1].summary, 'ls');
  const replayed = reduce(assistant([{ type: 'text', text: 'antigo' }], { replay: true }));
  assert.equal(replayed[0].state, undefined);
  assert.deepEqual(reduce(assistant([{ type: 'thinking' }])), [{ kind: 'ignore' }]);
});

test('stream_event: início e delta de texto; outros ignorados', () => {
  assert.deepEqual(reduce({ type: 'stream_event', event: { type: 'content_block_start', content_block: { type: 'text' } } }), [{ kind: 'streamStart' }]);
  assert.deepEqual(reduce({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'ab' } } }), [
    { kind: 'streamDelta', text: 'ab' },
  ]);
  assert.deepEqual(reduce({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'input_json_delta' } } }), [{ kind: 'ignore' }]);
});

test('permissão comum e AskUserQuestion', () => {
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

test('result: sucesso vira waiting, erro vira error com texto', () => {
  assert.deepEqual(reduce({ type: 'result', subtype: 'success' }), [{ kind: 'result', isError: false, text: '', state: 'waiting', msg: '' }]);
  const [r] = reduce({ type: 'result', is_error: true, result: 'estourou a cota' });
  assert.equal(r.state, 'error');
  assert.equal(r.text, 'estourou a cota');
});

test('tipos desconhecidos são ignorados sem erro', () => {
  assert.deepEqual(reduce({ type: 'rate_limit_event' }), [{ kind: 'ignore' }]);
  assert.deepEqual(reduce({ type: 'system', subtype: 'init' }), [{ kind: 'ignore' }]);
});

test('collectAnswers junta escolhas e exige todas', () => {
  const qs = [{ question: 'A?' }, { question: 'B?' }];
  assert.deepEqual(collectAnswers(qs, [['x'], ['y', 'z']]), { 'A?': 'x', 'B?': 'y, z' });
  assert.equal(collectAnswers(qs, [['x'], []]), null);
  assert.equal(collectAnswers(qs, [['x'], ['']]), null);
});

test('mensagem de outra sessão (peer) vira wireIn com o texto limpo e quem mandou', () => {
  const wrapped = 'Another Claude session sent a message:\n<cross-session-message from-name="exe">\nPONG\n</cross-session-message>\n\nThis came…';
  const [a] = reduce(user(wrapped, { orq_origin: 'agent', origin: { kind: 'peer', name: 'exe-laptop', body: 'PONG', msg_id: 'm1' } }));
  assert.deepEqual(a, { kind: 'wireIn', from: 'exe-laptop', text: 'PONG', msgId: 'm1', state: 'working' });
  // no replay do histórico só vem origin (sem carimbo) e sem body: o texto sai do embrulho
  const [r] = reduce(user(wrapped, { replay: true, origin: { kind: 'peer', name: 'exe' } }));
  assert.equal(r.kind, 'wireIn');
  assert.equal(r.text, 'PONG');
  assert.equal(r.state, undefined);
});

test('SendMessage vira wireOut em vez de toolUse', () => {
  const acts = reduce(assistant([{ type: 'tool_use', id: 't9', name: 'SendMessage', input: { to: 'exe-laptop', summary: 'delegar', message: 'faça X' } }]));
  assert.deepEqual(acts, [{ kind: 'wireOut', id: 't9', to: 'exe-laptop', summary: 'delegar', message: 'faça X', state: 'working' }]);
});

test('peerBody e sentMsgId', () => {
  assert.equal(peerBody('<cross-session-message from="x">\n  olá\n</cross-session-message>'), 'olá');
  assert.equal(peerBody('sem embrulho'), 'sem embrulho');
  assert.equal(sentMsgId('{"success":true,"message":"→ exe","msg_id":"abc"}'), 'abc');
  assert.equal(sentMsgId('{"success":false,"msg_id":"abc"}'), '');
  assert.equal(sentMsgId('não é json'), '');
});
