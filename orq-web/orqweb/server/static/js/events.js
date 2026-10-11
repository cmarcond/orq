// Redutor puro: um evento do stream do agente -> lista de ações de interface.
// Não toca no DOM, por isso roda nos testes do Node. app.js aplica as ações.
//
// Ações: {kind, ...}:
//   ignore | sys{level,text} | permissionAnswered{requestId,behavior} | interrupted
//   bubble{text,pending} | machine{text,origin} | toolResult{toolUseId,text,isError}
//   text{text} | toolUse{id,name,summary} | streamStart | streamDelta{text}
//   permission{requestId,request} | ask{requestId,request} | result{isError,text}
// Cada ação pode trazer `state` ('working' | 'permission' | 'waiting' | 'error') e `msg`.

import { clean, internal, isMachine, toolSummary } from './classify.js';

const blocks = (e) => {
  const c = e.message?.content;
  return Array.isArray(c) ? c : [];
};

/** ctx.pending: textos enviados pela página ainda sem eco. É mutado quando um eco casa. */
export function reduce(e, ctx = { pending: [] }) {
  const t = e.type;
  if (t === 'orq') return reduceOrq(e);
  if (t === 'user') return reduceUser(e, ctx);
  if (t === 'assistant') return reduceAssistant(e);
  if (t === 'stream_event') return reduceStream(e);
  if (t === 'control_request' && e.request?.subtype === 'can_use_tool') return reducePermission(e);
  if (t === 'result') {
    const isError = Boolean(e.is_error);
    const text = isError ? String(e.result || e.subtype || 'erro').slice(0, 300) : '';
    return [{ kind: 'result', isError, text, state: isError ? 'error' : 'waiting', msg: text.slice(0, 80) }];
  }
  return [{ kind: 'ignore' }];
}

function reduceOrq(e) {
  if (e.subtype === 'permission_answered')
    return [{ kind: 'permissionAnswered', requestId: e.request_id, behavior: e.behavior, state: 'working', msg: '' }];
  return [{ kind: 'sys', level: e.level || 'info', text: e.text || '' }];
}

function reduceUser(e, ctx) {
  if (e.isMeta) return [{ kind: 'ignore' }];
  const c = e.message?.content;
  const texts = typeof c === 'string' ? [c] : blocks(e).filter((b) => b.type === 'text').map((b) => b.text);
  const out = [];
  for (const raw of texts) {
    if (internal(raw)) continue;
    const text = clean(raw);
    if (!text) continue;
    const i = e.replay ? -1 : ctx.pending.findIndex((x) => clean(x) === text);
    if (i >= 0) {
      ctx.pending.splice(i, 1);
      out.push({ kind: 'bubble', text, pending: true });
    } else if (/^\s*\[Request interrupted/i.test(text)) out.push({ kind: 'interrupted' });
    else if (isMachine(e) || /^\s*\[/.test(text)) out.push({ kind: 'machine', text, origin: e.origin?.kind });
    else out.push({ kind: 'bubble', text, pending: false });
  }
  for (const b of blocks(e))
    if (b.type === 'tool_result') {
      const s = typeof b.content === 'string' ? b.content : (b.content || []).map((x) => x.text || '').join('\n');
      out.push({ kind: 'toolResult', toolUseId: b.tool_use_id, text: s.slice(0, 6000), isError: Boolean(b.is_error) });
    }
  return out.length ? out : [{ kind: 'ignore' }];
}

function reduceAssistant(e) {
  const out = [];
  for (const b of blocks(e)) {
    if (b.type === 'text') out.push({ kind: 'text', text: b.text });
    else if (b.type === 'tool_use') out.push({ kind: 'toolUse', id: b.id, name: b.name, summary: toolSummary(b.input) });
  }
  if (!e.replay && out.length) out[0].state = 'working';
  return out.length ? out : [{ kind: 'ignore' }];
}

function reduceStream(e) {
  const ev = e.event || {};
  if (ev.type === 'content_block_start' && ev.content_block?.type === 'text') return [{ kind: 'streamStart' }];
  if (ev.type === 'content_block_delta' && ev.delta?.type === 'text_delta') return [{ kind: 'streamDelta', text: ev.delta.text }];
  return [{ kind: 'ignore' }];
}

function reducePermission(e) {
  const r = e.request;
  const ask = r.tool_name === 'AskUserQuestion' && Array.isArray(r.input?.questions);
  const a = { kind: ask ? 'ask' : 'permission', requestId: e.request_id, request: r };
  if (!e.replay) {
    a.state = 'permission';
    a.msg = r.tool_name;
  }
  return [a];
}

/** Respostas de um formulário AskUserQuestion -> {pergunta: "opção, opção"}; null se faltou alguma. */
export function collectAnswers(questions, picked) {
  const answers = {};
  for (let i = 0; i < questions.length; i++) {
    const chosen = (picked[i] || []).filter(Boolean);
    if (!chosen.length) return null;
    answers[questions[i].question] = chosen.join(', ');
  }
  return answers;
}
