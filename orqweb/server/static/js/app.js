// Estado da página, API e DOM. A lógica pura está em format.js, classify.js e events.js.
import { esc, left, ago, md } from './format.js';
import { firstLine, originLabel, permissionBody, toolName } from './classify.js';
import { reduce, collectAnswers } from './events.js';

const T = new URLSearchParams(location.search).get('t') || '';
const H = { 'X-Orq-Token': T, 'Content-Type': 'application/json' };
const $ = (id) => document.getElementById(id);
const ST = {
  working: 'trabalhando…',
  waiting: 'esperando você',
  permission: 'pede permissão',
  error: 'erro',
  starting: 'abrindo…',
  closed: 'fechado',
};

// ---------------------------------------------------------------- infra
function toast(m) {
  const t = $('toast');
  t.textContent = m;
  t.style.display = 'block';
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.style.display = 'none'), 4000);
}
async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: H, body: JSON.stringify(body) } : { headers: H });
  const j = await r.json().catch(() => ({}));
  if (j.err) toast(j.err);
  return j;
}
const agentApi = (label, op, body = {}) => api(`/api/agent/${encodeURIComponent(label)}/${op}`, body);

// ---------------------------------------------------------------- cota
let LAB = {};
let COL = {}; // cor por conta, vinda do accounts.json (ou da paleta padrão)
const colorOf = (acc) => COL[acc] || 'var(--mute)';
function meter(u, k) {
  if (u[k] == null) return '';
  const p = u[k],
    m = u[k + '_pace'] || 0,
    d = p - m;
  return `<div class="urow"><span>${k}</span><div class="meter ${d >= 15 ? 'far' : d > 0 ? 'over' : ''}">
    <i style="width:${Math.min(100, p)}%"></i><b style="left:${Math.min(99, m)}%"></b></div>
    <span class="pct">${Math.round(p)}%</span><span>${left(u[k + '_left'])}</span></div>`;
}
function renderUsage(us) {
  $('usage').innerHTML = Object.entries(us)
    .map(
      ([n, u]) => `<div class="ucard" style="--acc:${colorOf(n)}"><div class="name"><span><i class="sw" style="background:${colorOf(n)}"></i>${esc(LAB[n] || n)}</span>${
        u.err ? '<span class="small bad">erro</span>' : ''
      }</div>${u.err ? `<div class="small mute">${esc(u.err).slice(0, 80)}</div>` : meter(u, '5h') + meter(u, '7d')}</div>`,
    )
    .join('');
}

// ---------------------------------------------------------------- abas
const tabs = {}; // label -> {a, el, seq, es, pending, chips, perms, live, buf, follow}
let cur = null;
let agents = [];
const hostOf = (l) => l.split('@').pop();

function tabOrder() {
  const live = agents.filter((a) => a.state !== 'closed').map((a) => a.label); // sdk e terminal (este só para converter)
  for (const l in tabs) if (!live.includes(l) && tabs[l].el) live.push(l);
  return live;
}
function renderTabs() {
  const nav = $('tabs');
  const order = tabOrder();
  nav.innerHTML =
    order
      .map((l, i) => {
        const a = agents.find((x) => x.label === l) || tabs[l]?.a || { label: l };
        const sdk = a.kind === 'sdk';
        const tail = sdk
          ? `${i < 9 ? `<span class="kbd">⌘${i + 1}</span>` : ''}<span class="x" data-x="${esc(l)}" title="encerrar agente">✕</span>`
          : a.tmux
            ? `<span class="cv" data-cv="${esc(l)}" title="converter: fecha o claude do terminal e reabre a conversa aqui">⇄</span>`
            : `<span class="small mute" title="painel da grade antiga do orq, sem tmux próprio">grade</span>`;
        const col = colorOf(a.account);
        const tip = `${a.dir || ''}\nconta ${LAB[a.account] || a.account || '?'}`;
        return `<div class="tab ${a.state || ''} ${sdk ? '' : 'term'} ${l === cur ? 'on' : ''}" data-l="${esc(l)}" title="${esc(tip)}" style="--acc:${col}">
        <span class="dot"></span><span class="meta"><b>${esc(l.split('@')[0])}</b> · ${esc(hostOf(l))}${sdk ? '' : ' · <i>terminal</i>'}</span><i class="sw" style="background:${col}" title="${esc(LAB[a.account] || a.account || '')}"></i>${tail}</div>`;
      })
      .join('') + `<div class="tab plus" id="plus" title="abrir projeto">+</div>`;
  nav.querySelectorAll('.tab[data-l]').forEach(
    (el) =>
      (el.onclick = (e) => {
        if (e.target.dataset.x) return askStop(e.target.dataset.x);
        if (e.target.dataset.cv) return convertAgent(e.target.dataset.cv);
        show(el.dataset.l);
      }),
  );
  $('plus').onclick = openPicker;
  if (!cur && order.length) show(order[0]);
}
function askStop(l) {
  const t = tabs[l];
  if (t && t._confirm) {
    agentApi(l, 'stop').then(() => toast('encerrando ' + l));
    t._confirm = false;
    return;
  }
  if (t) t._confirm = true;
  toast('clique no ✕ de novo para encerrar ' + l.split('@')[0]);
  setTimeout(() => t && (t._confirm = false), 4000);
}
function tab(l) {
  if (!tabs[l]) tabs[l] = { a: null, el: null, seq: 0, es: null, pending: [], chips: {}, perms: {}, live: null, buf: '', follow: true };
  return tabs[l];
}
function show(l) {
  const ag = agents.find((x) => x.label === l);
  if (ag && ag.kind !== 'sdk') return showTerminal(l, ag);
  cur = l;
  const t = tab(l);
  t.a = ag || t.a || { label: l };
  if (!t.el) {
    t.el = document.createElement('div');
    t.el.className = 'chat';
    t.el.innerHTML = '<div class="inner"></div>';
    t.el.addEventListener('scroll', () => (t.follow = t.el.scrollHeight - t.el.scrollTop - t.el.clientHeight < 80), {
      passive: true,
    });
    $('chats').appendChild(t.el);
  }
  document.querySelectorAll('.chat').forEach((e) => e.classList.toggle('on', e === t.el));
  $('empty').style.display = 'none';
  for (const k in tabs)
    if (k !== l && tabs[k].es) {
      tabs[k].es.close();
      tabs[k].es = null;
    }
  $('composer').hidden = false;
  if (!t.es) stream(l);
  renderTabs();
  status();
  scrollEnd(t);
  $('input').focus();
}
function showTerminal(l, a) {
  cur = l;
  const t = tab(l);
  t.a = a;
  if (!t.el) {
    t.el = document.createElement('div');
    t.el.className = 'chat';
    $('chats').appendChild(t.el);
  }
  t.el.innerHTML = `<div class="inner"><div class="perm term"><div class="t">Sessão do claude no terminal</div>
    <div class="small mute">tmux <code>${esc(a.tmux || '?')}</code> · <i class="sw" style="background:${colorOf(a.account)}"></i>${esc(LAB[a.account] || a.account || '?')} · <code>${esc(a.dir || '')}</code>${
      a.msg ? ` · ${esc(a.msg)}` : ''
    }</div>
    <p>A conversa dela não passa pela página. <b>Converter</b> dá <code>/exit</code> nela (ou Ctrl-C, se não responder) e reabre a mesma conversa aqui, como agente da página. Nada se perde: o Claude Code grava cada mensagem em disco.</p>
    <div class="btns">${
      a.tmux
        ? '<button class="ok">⇄ Converter para a página</button>'
        : '<span class="small mute">painel da grade antiga do orq: feche-o no terminal (q no menu)</span>'
    }</div></div></div>`;
  const ok = t.el.querySelector('.ok');
  if (ok) ok.onclick = () => convertAgent(l);
  document.querySelectorAll('.chat').forEach((e) => e.classList.toggle('on', e === t.el));
  $('empty').style.display = 'none';
  $('composer').hidden = true;
  for (const k in tabs)
    if (tabs[k].es) {
      tabs[k].es.close();
      tabs[k].es = null;
    }
  renderTabs();
}
async function convertAgent(l) {
  toast(`convertendo ${l.split('@')[0]}… (fecha o claude do terminal e reabre aqui)`);
  const j = await api('/api/agent/convert', { label: l });
  if (!j.label) return;
  toast(`${j.label} convertido${j.how ? ` (fechado por ${j.how})` : ''}`);
  for (const k of [l, j.label]) {
    if (tabs[k]) {
      tabs[k].el?.remove();
      delete tabs[k];
    }
  }
  cur = null;
  await refresh();
  show(j.label);
}
function status() {
  const t = tabs[cur];
  const a = t && (agents.find((x) => x.label === cur) || t.a);
  const st = a ? a.state : '';
  $('cstate').innerHTML = a
    ? `<i class="sw" style="background:${colorOf(a.account)}"></i>${esc(LAB[a.account] || a.account || '')} · ${esc(ST[st] || st)}${
        a.msg && st !== 'working' ? ' · ' + esc(a.msg) : ''
      }`
    : '';
  $('stop').hidden = st !== 'working';
}
function setState(t, st, msg) {
  if (!t.a) t.a = { label: cur };
  t.a.state = st;
  if (msg !== undefined) t.a.msg = msg;
  const a = agents.find((x) => x.label === t.a.label);
  if (a) {
    a.state = st;
    if (msg !== undefined) a.msg = msg;
  }
  if (t === tabs[cur]) {
    status();
    renderTabs();
  }
}
function scrollEnd(t) {
  if (!t || !t.el || t.follow === false) return;
  t.el.scrollTop = t.el.scrollHeight;
  clearTimeout(t._sc);
  t._sc = setTimeout(() => t.follow !== false && (t.el.scrollTop = t.el.scrollHeight), 150);
}
function add(t, html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  const n = d.firstElementChild;
  t.el.querySelector('.inner').appendChild(n);
  scrollEnd(t);
  return n;
}

// ---------------------------------------------------------------- stream
function stream(l) {
  const t = tabs[l];
  const es = new EventSource(`/api/agent/${encodeURIComponent(l)}/stream?since=${t.seq}&t=${encodeURIComponent(T)}`);
  t.es = es;
  es.onmessage = (ev) => {
    let e;
    try {
      e = JSON.parse(ev.data);
    } catch (_) {
      return;
    }
    if (e.type !== 'stream_event') t.seq++;
    for (const a of reduce(e, t)) apply(t, a);
  };
  es.onerror = () => {
    es.close();
    if (t.es === es) {
      t.es = null;
      setTimeout(() => cur === l && !t.es && stream(l), 3000);
    }
  };
}

function apply(t, a) {
  if (a.state) setState(t, a.state, a.msg);
  switch (a.kind) {
    case 'sys':
      return add(t, `<div class="sys ${esc(a.level)}">${esc(a.text)}</div>`);
    case 'interrupted':
      return add(t, `<div class="sys">interrompido por você</div>`);
    case 'permissionAnswered': {
      const p = t.perms[a.requestId];
      if (!p) return;
      p.classList.add('done');
      const ask = p.classList.contains('ask');
      const txt = a.behavior === 'allow' ? (ask ? 'respondido' : 'permitido') : ask ? 'cancelado' : 'negado';
      p.querySelector('.btns').innerHTML = `<span class="small mute">${txt}</span>`;
      return;
    }
    case 'bubble': {
      if (a.pending) {
        const el = t.el.querySelector('.me.pending');
        if (el) return el.classList.remove('pending');
      }
      return add(t, `<div class="me">${esc(a.text)}</div>`);
    }
    case 'machine': {
      const ch = add(
        t,
        `<div class="chip sysm"><div class="head"><b>${esc(originLabel(a.origin))}</b><span class="sum">${esc(
          firstLine(a.text),
        )}</span><span class="st">▸</span></div><pre></pre></div>`,
      );
      ch.querySelector('pre').textContent = a.text;
      ch.querySelector('.head').onclick = () => ch.classList.toggle('open');
      return;
    }
    case 'toolResult': {
      const ch = t.chips[a.toolUseId];
      if (!ch) return;
      ch.classList.remove('run');
      ch.classList.add(a.isError ? 'err' : 'ok');
      ch.querySelector('.st').textContent = a.isError ? '✗' : '✓';
      ch.querySelector('pre').textContent = a.text || '(sem saída)';
      return;
    }
    case 'text': {
      if (t.live) {
        t.live.classList.remove('live');
        t.live.innerHTML = md(a.text);
        t.live = null;
      } else add(t, `<div class="ai">${md(a.text)}</div>`);
      return;
    }
    case 'toolUse': {
      if (t.live && !t.live.textContent.trim()) {
        t.live.remove();
        t.live = null;
      }
      const ch = add(
        t,
        `<div class="chip run"><div class="head"><b>${esc(toolName(a.name))}</b><span class="sum">${esc(
          a.summary,
        )}</span><span class="st">…</span></div><pre></pre></div>`,
      );
      ch.querySelector('.head').onclick = () => ch.classList.toggle('open');
      t.chips[a.id] = ch;
      return;
    }
    case 'streamStart':
      if (!t.live) t.live = add(t, `<div class="ai live"></div>`);
      t.buf = '';
      return;
    case 'streamDelta':
      if (!t.live) {
        t.live = add(t, `<div class="ai live"></div>`);
        t.buf = '';
      }
      t.buf += a.text;
      t.live.textContent = t.buf;
      return scrollEnd(t);
    case 'permission':
      return permCard(t, a);
    case 'ask':
      return askForm(t, a);
    case 'result': {
      if (t.live) {
        t.live.classList.remove('live');
        if (!t.live.textContent.trim()) t.live.remove();
        t.live = null;
      }
      if (a.isError) add(t, `<div class="sys error">${esc(a.text)}</div>`);
      return;
    }
    default:
      return;
  }
}

// ---------------------------------------------------------------- permissões
function permCard(t, a) {
  const r = a.request;
  const p = add(
    t,
    `<div class="perm"><div class="t">Permissão: ${esc(r.display_name || r.tool_name)}</div>${
      r.description ? `<div class="small mute">${esc(r.description)}</div>` : ''
    }<pre>${esc(permissionBody(r.input))}</pre><div class="btns"><button data-ok="1">Permitir</button><button data-ok="0">Negar</button></div></div>`,
  );
  t.perms[a.requestId] = p;
  p.querySelectorAll('button').forEach(
    (bt) =>
      (bt.onclick = () =>
        agentApi(t.a.label, 'permission', { request_id: a.requestId, allow: bt.dataset.ok === '1', input: r.input })),
  );
}

function askForm(t, a) {
  const r = a.request,
    inp = r.input,
    qs = inp.questions;
  const opt = (q, i, label, inner) =>
    `<label class="opt"><input type="${q.multiSelect ? 'checkbox' : 'radio'}" name="q${i}" value="${esc(label)}"><span>${inner}</span></label>`;
  const p = add(
    t,
    `<div class="perm ask"><div class="t">O agente pergunta</div>${qs
      .map(
        (q, i) => `<div class="q"><div class="qh">${esc(q.header || '')}</div><div class="qt">${esc(q.question)}</div><div class="opts">${(
          q.options || []
        )
          .map((o) => opt(q, i, o.label, `<b>${esc(o.label)}</b>${o.description ? `<small>${esc(o.description)}</small>` : ''}`))
          .join('')}${opt(q, i, '__other__', '<input type="text" class="otxt" placeholder="Outro…">')}</div></div>`,
      )
      .join('')}<div class="btns"><button class="ok">Responder</button><button class="no">Cancelar</button></div></div>`,
  );
  t.perms[a.requestId] = p;
  p.querySelectorAll('.otxt').forEach((tx) =>
    tx.addEventListener('focus', () => {
      const o = tx.closest('.opt').querySelector('input[type=radio],input[type=checkbox]');
      if (o) o.checked = true;
    }),
  );
  p.querySelector('.ok').onclick = () => {
    const picked = qs.map((_, i) =>
      [...p.querySelectorAll(`input[name=q${i}]:checked`)].map((x) =>
        x.value === '__other__' ? x.closest('.opt').querySelector('.otxt').value.trim() : x.value,
      ),
    );
    const answers = collectAnswers(qs, picked);
    if (!answers) return toast('responda todas as perguntas');
    agentApi(t.a.label, 'permission', { request_id: a.requestId, allow: true, input: { ...inp, answers } });
  };
  p.querySelector('.no').onclick = () => agentApi(t.a.label, 'permission', { request_id: a.requestId, allow: false });
}

// ---------------------------------------------------------------- composer
async function send() {
  const text = $('input').value.trim();
  if (!text || !cur) return;
  const t = tabs[cur];
  $('input').value = '';
  $('input').style.height = 'auto';
  t.pending.push(text);
  t.follow = true;
  add(t, `<div class="me pending">${esc(text)}</div>`);
  setState(t, 'working', '');
  const j = await agentApi(cur, 'send', { text });
  if (j.err) t.el.querySelector('.me.pending')?.remove();
}
$('send').onclick = send;
$('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
  if (e.key === 'Escape' && cur) agentApi(cur, 'interrupt');
});
$('input').addEventListener('input', (e) => {
  e.target.style.height = 'auto';
  e.target.style.height = Math.min(e.target.scrollHeight, window.innerHeight * 0.4) + 'px';
});
$('stop').onclick = () => cur && agentApi(cur, 'interrupt');
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key >= '1' && e.key <= '9') {
    const el = [...document.querySelectorAll('.tab[data-l]')][+e.key - 1];
    if (el) {
      e.preventDefault();
      show(el.dataset.l);
    }
  }
});

// ---------------------------------------------------------------- abrir projeto
async function openPicker() {
  $('picker').hidden = false;
  $('plist').innerHTML = 'carregando…';
  const s = await api('/api/projects');
  $('plist').innerHTML =
    (s.items || [])
      .map(
        (p) => `<div class="p" data-p='${esc(JSON.stringify({ host: p.host, cwd: p.cwd, account: p.account }))}'>
      <span>${esc(p.host)}</span><span class="path" title="${esc(p.cwd)}">${esc(p.short || p.cwd)}</span>
      <span class="mute">${esc(p.account || '')}</span><span class="mute small">${ago(p.mtime)}</span><span class="ttl">${esc(p.title || '')}</span></div>`,
      )
      .join('') || '<div class="mute">nenhum projeto recente</div>';
  $('plist').querySelectorAll('.p').forEach((el) => (el.onclick = () => openAgent(JSON.parse(el.dataset.p), 'resume')));
}
async function openAgent(p, mode) {
  $('picker').hidden = true;
  toast(`abrindo ${p.cwd.split('/').pop() || p.cwd} em ${p.host}…`);
  const j = await api('/api/agent/open', { ...p, mode });
  if (j.label) {
    toast(`${j.label} · conta ${j.acc} (${j.why})`);
    await refresh();
    show(j.label);
  }
}
$('pclose').onclick = () => ($('picker').hidden = true);
$('picker').onclick = (e) => e.target === $('picker') && ($('picker').hidden = true);
$('popen').onclick = () => {
  const m = $('pnew').value.trim().match(/^([\w.-]+):(.+)$/);
  if (!m) return toast('use maquina:/caminho');
  openAgent({ host: m[1], cwd: m[2], account: null }, 'new');
};

// ---------------------------------------------------------------- estado geral
async function refresh() {
  try {
    const s = await api('/api/state');
    if (s.err) return;
    LAB = s.labels;
    COL = s.colors || {};
    renderUsage(s.usage);
    agents = s.agents;
    for (const a of agents) {
      const t = tabs[a.label];
      if (!t) continue;
      if (t.es && a.state !== 'closed') {
        // a aba sabe o estado pelos eventos; a consulta às máquinas atrasa até 15 s
        a.state = t.a?.state || a.state;
        a.msg = t.a?.msg ?? a.msg;
      }
      t.a = a;
    }
    renderTabs();
    status();
  } catch (_) {
    /* servidor fora; tenta no próximo ciclo */
  }
}
refresh();
setInterval(refresh, 5000);
