// Estado da página, API e DOM. A lógica pura está em format.js, classify.js e events.js.
import { esc, left, ago, md, withAttachments, fmtBytes } from './format.js';
import { firstLine, originLabel, permissionBody, toolName } from './classify.js';
import { reduce, collectAnswers, sentMsgId } from './events.js';

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

// ---------------------------------------------------------------- abas e níveis
// Um nível (professor, arquiteto…) é um agente com `parent` = rótulo do agente logo abaixo. A aba é
// sempre a do agente base; o chat mostrado (`cur`) é um rótulo da cadeia, por padrão o do topo.
const tabs = {}; // label -> {a, el, seq, es, pending, chips, perms, live, buf, follow, wires}
let cur = null;
let agents = [];
const hostOf = (l) => l.split('@').pop();
const LEVEL_COLORS = { professor: '#2b6cb0', arquiteto: '#805ad5', executor: '#b7791f' };
const sessionName = (l) => l.replace(/[^a-zA-Z0-9_-]/g, '-'); // = config.session_name
const live = (a) => a && a.state !== 'closed';
const agentOf = (l) => agents.find((x) => x.label === l);
const isLevel = (a) => Boolean(a && a.parent && live(agentOf(a.parent)));
function levelsAbove(label) {
  const out = [];
  let c = label;
  for (;;) {
    const n = agents.find((a) => a.parent === c && live(a));
    if (!n || out.includes(n.label) || n.label === label) break;
    out.push(n.label);
    c = n.label;
  }
  return out;
}
function baseOf(label) {
  let a = agentOf(label);
  const seen = new Set();
  while (a && isLevel(a) && !seen.has(a.label)) {
    seen.add(a.label);
    a = agentOf(a.parent);
  }
  return a ? a.label : label;
}
const chainOf = (base) => [base, ...levelsAbove(base)]; // de baixo para cima
const levelName = (l) => agentOf(l)?.level || (isLevel(agentOf(l)) ? l.split('@')[0].split('~').pop() : 'executor');
const levelColor = (l) => LEVEL_COLORS[levelName(l)] || 'var(--mute)';
const curBase = () => (cur ? baseOf(cur) : null);

function tabOrder() {
  // sdk e terminal (este só para converter); agente fechado, sumido ou nível de outro não ocupa aba
  return agents.filter((a) => live(a) && !isLevel(a)).map((a) => a.label);
}
function renderTabs() {
  const nav = $('tabs');
  const order = tabOrder();
  const onTab = curBase();
  // uma linha por máquina: o rótulo da máquina à esquerda, as abas dela na linha, e um + que já abre ali
  const hosts = [];
  for (const l of order) if (!hosts.includes(hostOf(l))) hosts.push(hostOf(l));
  if (!hosts.includes('laptop')) hosts.unshift('laptop');
  let i = 0;
  nav.innerHTML = hosts
    .map((h) => {
      const row = order
        .filter((l) => hostOf(l) === h)
        .map((l) => {
          const a = agentOf(l) || tabs[l]?.a || { label: l };
          const sdk = a.kind === 'sdk';
          const n = i++;
          const lv = levelsAbove(l);
          const tail = sdk
            ? `${lv.length ? `<span class="lv" title="${esc(lv.map(levelName).reverse().join(' → '))} → executor">⇅${lv.length}</span>` : ''}${n < 9 ? `<span class="kbd">⌘${n + 1}</span>` : ''}<span class="x" data-x="${esc(l)}" title="encerrar agente">✕</span>`
            : a.tmux
              ? `<span class="cv" data-cv="${esc(l)}" title="converter: fecha o claude do terminal e reabre a conversa aqui">⇄</span>`
              : `<span class="small mute" title="painel da grade antiga do orq, sem tmux próprio">grade</span>`;
          const col = colorOf(a.account);
          const tip = `${a.dir || ''}\nconta ${LAB[a.account] || a.account || '?'}`;
          // o ponto da aba segue o estado do nível que está no chat (o topo), não só o do base
          const shown = lv.length && l === onTab ? agentOf(cur) || a : lv.length ? agentOf(lv[lv.length - 1]) || a : a;
          return `<div class="tab ${shown.state || ''} ${sdk ? '' : 'term'} ${l === onTab ? 'on' : ''}" data-l="${esc(l)}" title="${esc(tip)}" style="--acc:${col}">
          <span class="dot"></span><span class="meta"><b>${esc(l.split('@')[0])}</b>${sdk ? '' : ' · <i>terminal</i>'}</span><i class="sw" style="background:${col}" title="${esc(LAB[a.account] || a.account || '')}"></i>${tail}</div>`;
        })
        .join('');
      return `<div class="trow"><span class="host" title="máquina">${esc(h)}</span><div class="tline">${row}<div class="tab plus" data-host="${esc(h)}" title="abrir projeto em ${esc(h)}">+</div></div></div>`;
    })
    .join('');
  nav.querySelectorAll('.tab[data-l]').forEach(
    (el) =>
      (el.onclick = (e) => {
        if (e.target.dataset.x) return askStop(e.target.dataset.x);
        if (e.target.dataset.cv) return convertAgent(e.target.dataset.cv);
        show(el.dataset.l);
      }),
  );
  nav.querySelectorAll('.tab.plus').forEach((el) => (el.onclick = () => openPicker(el.dataset.host)));
  if (!cur && order.length) show(order[0]);
}
function askStop(l) {
  const t = tabs[l];
  if (t && t._confirm) {
    const lv = levelsAbove(l);
    (lv.length ? agentApi(l, 'downgrade') : Promise.resolve()).then(() => agentApi(l, 'stop')).then(() => toast('encerrando ' + l));
    t._confirm = false;
    return;
  }
  if (t) t._confirm = true;
  toast('clique no ✕ de novo para encerrar ' + l.split('@')[0] + (levelsAbove(l).length ? ' e seus níveis' : ''));
  setTimeout(() => t && (t._confirm = false), 4000);
}
function tab(l) {
  if (!tabs[l]) tabs[l] = { a: null, el: null, seq: 0, es: null, pending: [], chips: {}, perms: {}, live: null, buf: '', follow: true };
  return tabs[l];
}
function ensureEl(t) {
  if (t.el) return;
  t.el = document.createElement('div');
  t.el.className = 'chat';
  t.el.innerHTML = '<div class="inner"></div>';
  t.el.addEventListener('scroll', () => (t.follow = t.el.scrollHeight - t.el.scrollTop - t.el.clientHeight < 80), {
    passive: true,
  });
  $('chats').appendChild(t.el);
}
/** Mostra a aba de `l`. `exact` mostra esse rótulo mesmo que seja um nível ou um base com níveis. */
function show(l, exact = false) {
  const ag = agentOf(l);
  if (ag && ag.kind !== 'sdk') return showTerminal(l, ag);
  const base = baseOf(l);
  const chain = chainOf(base);
  cur = exact && chain.includes(l) ? l : chain[chain.length - 1];
  for (const k of chain) {
    const t = tab(k);
    t.a = agentOf(k) || t.a || { label: k };
    ensureEl(t);
  }
  const t = tabs[cur];
  document.querySelectorAll('.chat').forEach((e) => e.classList.toggle('on', e === t.el));
  $('empty').style.display = 'none';
  for (const k in tabs)
    if (!chain.includes(k) && tabs[k].es) {
      tabs[k].es.close();
      tabs[k].es = null;
    }
  $('composer').hidden = false;
  for (const k of chain) if (!tabs[k].es) stream(k); // todos os níveis ficam ligados: os fios vêm de todos
  renderTabs();
  renderLevels(base);
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
  for (const k in tabs) {
    if (tabs[k].es) {
      tabs[k].es.close();
      tabs[k].es = null;
    }
  }
  renderTabs();
  renderLevels(null);
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
function dropClosed() {
  // abas cujo agente fechou (✕, /exit, claude saiu, downgrade) ou sumiu da lista: fecham o stream e saem
  const liveLabels = new Set(agents.filter(live).map((a) => a.label));
  const base = curBase();
  for (const l of Object.keys(tabs)) {
    const t = tabs[l];
    const gone = !liveLabels.has(l) || t.a?.state === 'closed';
    if (!gone) continue;
    if (t.es) t.es.close();
    t.el?.remove();
    delete tabs[l];
    if (cur === l) cur = null;
  }
  if (!cur && base && liveLabels.has(base)) show(base);
  else if (!cur) {
    $('composer').hidden = true;
    $('empty').style.display = '';
    renderLevels(null);
  }
}
function status() {
  const t = tabs[cur];
  const a = t && (agentOf(cur) || t.a);
  const st = a ? a.state : '';
  const lv = cur && (isLevel(a) || levelsAbove(cur).length) ? ` · <b style="color:${levelColor(cur)}">${esc(levelName(cur))}</b>` : '';
  $('cstate').innerHTML = a
    ? `<i class="sw" style="background:${colorOf(a.account)}"></i>${esc(LAB[a.account] || a.account || '')}${lv} · ${esc(ST[st] || st)}${
        a.msg && st !== 'working' ? ' · ' + esc(a.msg) : ''
      }`
    : '';
  $('stop').hidden = st !== 'working';
  const base = curBase();
  const has = base && levelsAbove(base).length;
  $('lvl').hidden = !base || !(agentOf(base)?.kind === 'sdk');
  $('lvl').textContent = has ? '⇅ níveis' : '⇅ subir nível';
  $('lvl').title = has ? 'mostrar/esconder o painel dos níveis' : 'subir um professor (ou mais níveis) acima deste agente';
}
function setState(t, st, msg) {
  if (!t.a) t.a = { label: cur };
  t.a.state = st;
  if (msg !== undefined) t.a.msg = msg;
  const a = agentOf(t.a.label);
  if (a) {
    a.state = st;
    if (msg !== undefined) a.msg = msg;
  }
  const base = curBase();
  if (base && chainOf(base).includes(t.a.label)) {
    status();
    renderTabs();
    renderLevelCards(base);
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

// ---------------------------------------------------------------- painel de níveis e fios
// Fica na aba base: cartões (um por nível, do topo para baixo, clicáveis para ver o chat cru daquele
// nível) e a lista de fios: cada SendMessage que sai de um nível, marcado "entregue" quando chega no outro.
function wiresOf(base) {
  const t = tab(base);
  if (!t.wires) {
    t.wires = { el: document.createElement('div'), byId: {}, byMsg: {} };
    t.wires.el.className = 'wires';
  }
  return t.wires;
}
function renderLevels(base) {
  const main = $('chats');
  const panel = $('levels');
  const lv = base ? levelsAbove(base) : [];
  if (!base || !lv.length) {
    panel.hidden = true;
    main.classList.remove('withlevels');
    return;
  }
  panel.hidden = false;
  main.classList.add('withlevels');
  panel.innerHTML = `<div class="lhead"><b>níveis</b><button id="down" class="ghost" title="encerra os níveis de cima; a aba volta a falar com o executor">↓ desfazer</button></div>
    <div class="lcards"></div><div class="lhead"><b>fios</b><span class="small mute">mensagens entre os níveis</span></div>`;
  panel.appendChild(wiresOf(base).el);
  panel.querySelector('#down').onclick = () => askDowngrade(base);
  renderLevelCards(base);
}
function renderLevelCards(base) {
  const box = $('levels').querySelector('.lcards');
  if (!box) return;
  const chain = chainOf(base);
  box.innerHTML = chain
    .slice()
    .reverse()
    .map((l) => {
      const a = agentOf(l) || tabs[l]?.a || {};
      const st = a.state || '';
      return `<div class="lcard ${st} ${l === cur ? 'on' : ''}" data-l="${esc(l)}" style="--lc:${levelColor(l)}" title="${esc(l)}: ver o chat cru deste nível">
        <span class="dot"></span><b>${esc(levelName(l))}</b><span class="small mute">${esc(ST[st] || st)}${a.msg && st !== 'working' ? ' · ' + esc(a.msg).slice(0, 40) : ''}</span></div>`;
    })
    .join('');
  box.querySelectorAll('.lcard').forEach((el) => (el.onclick = () => show(el.dataset.l, true)));
}
function askDowngrade(base) {
  const t = tab(base);
  if (t._down) {
    t._down = false;
    agentApi(base, 'downgrade').then((j) => {
      if (!j.stopped) return;
      toast(`níveis encerrados: ${j.stopped.map(levelName).join(', ')}`);
      for (const l of j.stopped) {
        const a = agentOf(l);
        if (a) a.state = 'closed';
        if (tabs[l]) tabs[l].a = { ...(tabs[l].a || { label: l }), state: 'closed' };
      }
      refresh().then(() => show(base));
    });
    return;
  }
  t._down = true;
  toast('clique de novo em ↓ desfazer para encerrar os níveis (o executor continua)');
  setTimeout(() => (t._down = false), 4000);
}
/** Um fio: SendMessage saindo de `from` (wireOut) ou chegando (wireIn); a mesma mensagem vira "entregue". */
function wire(t, a) {
  const base = baseOf(t.a?.label || cur);
  const w = wiresOf(base);
  const from = t.a?.label || cur;
  if (a.kind === 'wireOut') {
    if (w.byId[a.id]) return;
    const to = a.to.startsWith('uds:') ? 'resposta' : a.to;
    const el = document.createElement('div');
    el.className = 'wire out';
    el.style.setProperty('--lc', levelColor(from));
    el.innerHTML = `<div class="wh"><b>${esc(levelName(from))}</b><span>→ ${esc(to)}</span><span class="st" title="enviado">…</span></div>${
      a.summary ? `<div class="sum">${esc(a.summary)}</div>` : ''
    }<pre></pre>`;
    el.querySelector('pre').textContent = a.message;
    el.querySelector('.wh').onclick = () => el.classList.toggle('open');
    w.byId[a.id] = el;
    w.el.appendChild(el);
  } else if (a.kind === 'sent') {
    const el = w.byId[a.id];
    if (!el) return;
    el.querySelector('.st').textContent = '✓';
    if (a.msgId) w.byMsg[a.msgId] = el;
  } else if (a.kind === 'wireIn') {
    const sent = a.msgId && w.byMsg[a.msgId];
    if (sent) {
      sent.classList.add('delivered');
      sent.querySelector('.st').textContent = '✓✓';
      sent.querySelector('.st').title = 'entregue em ' + levelName(from);
      return;
    }
    const el = document.createElement('div');
    el.className = 'wire in';
    el.style.setProperty('--lc', levelColor(from));
    el.innerHTML = `<div class="wh"><span>${esc(a.from)} →</span><b>${esc(levelName(from))}</b><span class="st">✓✓</span></div><pre></pre>`;
    el.querySelector('pre').textContent = a.text;
    el.querySelector('.wh').onclick = () => el.classList.toggle('open');
    w.el.appendChild(el);
  }
  w.el.scrollTop = w.el.scrollHeight;
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
      setTimeout(() => (cur === l || chainOf(curBase() || '').includes(l)) && !t.es && stream(l), 3000);
    }
  };
}

function apply(t, a) {
  if (a.state) setState(t, a.state, a.msg);
  switch (a.kind) {
    case 'sys':
      add(t, `<div class="sys ${esc(a.level)}">${esc(a.text)}</div>`);
      if (/agente encerrado|claude saiu/.test(a.text)) {
        setState(t, 'closed', '');
        setTimeout(refresh, 1500);
      }
      return;
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
    case 'wireIn': {
      // no chat deste nível: um chip "de <quem>"; no painel: entrega do fio
      const ch = add(
        t,
        `<div class="chip peer"><div class="head"><b>de ${esc(a.from)}</b><span class="sum">${esc(firstLine(a.text))}</span><span class="st">▸</span></div><pre></pre></div>`,
      );
      ch.querySelector('pre').textContent = a.text;
      ch.querySelector('.head').onclick = () => ch.classList.toggle('open');
      wire(t, a);
      return;
    }
    case 'wireOut': {
      if (t.live && !t.live.textContent.trim()) {
        t.live.remove();
        t.live = null;
      }
      const to = a.to.startsWith('uds:') ? 'resposta' : a.to;
      const ch = add(
        t,
        `<div class="chip run peer"><div class="head"><b>→ ${esc(to)}</b><span class="sum">${esc(a.summary || firstLine(a.message))}</span><span class="st">…</span></div><pre></pre></div>`,
      );
      ch.querySelector('pre').textContent = a.message;
      ch.querySelector('.head').onclick = () => ch.classList.toggle('open');
      ch.dataset.wire = '1';
      t.chips[a.id] = ch;
      wire(t, a);
      return;
    }
    case 'toolResult': {
      const ch = t.chips[a.toolUseId];
      if (!ch) return;
      ch.classList.remove('run');
      ch.classList.add(a.isError ? 'err' : 'ok');
      ch.querySelector('.st').textContent = a.isError ? '✗' : '✓';
      if (ch.dataset.wire) {
        const msgId = sentMsgId(a.text);
        wire(t, { kind: 'sent', id: a.toolUseId, msgId });
        if (!a.isError && msgId) return; // o JSON do envio não interessa; o chip mantém a mensagem
      }
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

// ---------------------------------------------------------------- anexos
let queue = []; // arquivos escolhidos, ainda não enviados (File[])
function renderFiles() {
  const box = $('files');
  box.hidden = !queue.length;
  box.innerHTML = queue
    .map((f, i) => `<span class="fchip" title="${esc(f.name)}">📄 ${esc(f.name)} <small>${fmtBytes(f.size)}</small><b data-i="${i}" title="remover">✕</b></span>`)
    .join('');
  box.querySelectorAll('b[data-i]').forEach((b) => (b.onclick = () => { queue.splice(+b.dataset.i, 1); renderFiles(); }));
}
function addFiles(list) {
  for (const f of list) if (f && f.size >= 0) queue.push(f);
  renderFiles();
  $('input').focus();
}
async function uploadAll(label) {
  const paths = [];
  for (const f of queue) {
    toast(`enviando ${f.name}…`);
    const r = await fetch(`/api/agent/${encodeURIComponent(label)}/upload`, {
      method: 'POST',
      headers: { 'X-Orq-Token': T, 'X-Filename': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' },
      body: f,
    });
    const j = await r.json().catch(() => ({}));
    if (j.err) {
      toast(`falhou ${f.name}: ${j.err}`);
      return null;
    }
    paths.push(j.path);
  }
  return paths;
}
$('attach').onclick = () => $('fileinput').click();
$('fileinput').onchange = (e) => { addFiles(e.target.files); e.target.value = ''; };
for (const ev of ['dragenter', 'dragover']) document.addEventListener(ev, (e) => { if (e.dataTransfer?.types?.includes('Files')) { e.preventDefault(); document.body.classList.add('drag'); } });
for (const ev of ['dragleave', 'drop']) document.addEventListener(ev, (e) => { if (ev === 'drop' || e.relatedTarget === null) document.body.classList.remove('drag'); });
document.addEventListener('drop', (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); if (cur) addFiles(e.dataTransfer.files); else toast('abra uma aba antes de soltar arquivos'); } });

// ---------------------------------------------------------------- composer
async function send() {
  const typed = $('input').value.trim();
  if ((!typed && !queue.length) || !cur) return;
  const t = tabs[cur];
  let paths = [];
  if (queue.length) {
    paths = await uploadAll(cur);
    if (!paths) return; // falhou: mantém texto e fila para tentar de novo
    queue = [];
    renderFiles();
  }
  const text = withAttachments(typed, paths);
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
$('lvl').onclick = () => {
  const base = curBase();
  if (!base) return;
  if (levelsAbove(base).length) {
    $('chats').classList.toggle('showlevels');
    return;
  }
  openLevelPicker(base);
};
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key >= '1' && e.key <= '9') {
    const el = [...document.querySelectorAll('.tab[data-l]')][+e.key - 1];
    if (el) {
      e.preventDefault();
      show(el.dataset.l);
    }
  }
});

// ---------------------------------------------------------------- abrir projeto / subir nível
function picker(title, hint, rowsHtml) {
  $('picker').hidden = false;
  $('ptitle').textContent = title;
  $('phint').innerHTML = hint;
  $('pnewrow').hidden = !rowsHtml ? false : true;
  $('plist').innerHTML = rowsHtml || 'carregando…';
}
async function openPicker(host) {
  picker(host ? `Abrir projeto em ${host}` : 'Abrir projeto', 'Projetos recentes em todas as máquinas. Ou uma pasta nova: <code>maquina:/caminho</code>');
  $('pnew').value = host ? `${host}:~/` : '';
  const s = await api('/api/projects');
  $('plist').innerHTML =
    (s.items || [])
      .filter((p) => !host || p.host === host)
      .map(
        (p) => `<div class="p" data-p='${esc(JSON.stringify({ host: p.host, cwd: p.cwd, account: p.account }))}'>
      <span>${esc(p.host)}</span><span class="path" title="${esc(p.cwd)}">${esc(p.short || p.cwd)}</span>
      <span class="mute">${esc(p.account || '')}</span><span class="mute small">${ago(p.mtime)}</span><span class="ttl">${esc(p.title || '')}</span></div>`,
      )
      .join('') || '<div class="mute">nenhum projeto recente</div>';
  $('plist').querySelectorAll('.p').forEach((el) => (el.onclick = () => openAgent(JSON.parse(el.dataset.p), 'resume')));
}
async function openLevelPicker(base) {
  picker(
    `Níveis sobre ${base.split('@')[0]}`,
    'Sobe um nível de cima (professor etc.) na mesma máquina, pasta e conta. A aba passa a falar com ele; o agente atual vira o executor e recebe o trabalho pelo SendMessage.',
    '…',
  );
  const s = await api('/api/pipelines');
  $('plist').innerHTML =
    (s.items || [])
      .map(
        (p) => `<div class="p lvp" data-id="${esc(p.id)}"><span class="path"><b>${esc(p.titulo)}</b></span><span class="mute small">${esc(
          p.niveis.join(' → '),
        )} → executor</span><span class="ttl">${esc(p.descricao)}</span></div>`,
      )
      .join('') || '<div class="mute">nenhuma receita em orqweb/pipelines</div>';
  $('plist').querySelectorAll('.p').forEach((el) => (el.onclick = () => upgrade(base, el.dataset.id)));
}
async function upgrade(base, pipeline) {
  $('picker').hidden = true;
  toast(`subindo níveis sobre ${base.split('@')[0]}…`);
  const j = await agentApi(base, 'upgrade', { pipeline });
  if (!j.top) return;
  toast(`${j.levels.map(levelName).reverse().join(' → ')} → executor; a aba fala com ${levelName(j.top)}`);
  await refresh();
  show(base);
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
      if (t.a?.state === 'closed') a.state = 'closed'; // encerrado pela página: a máquina pode atrasar
      else if (t.es && a.state !== 'closed') {
        // a aba sabe o estado pelos eventos; a consulta às máquinas atrasa até 15 s
        a.state = t.a?.state || a.state;
        a.msg = t.a?.msg ?? a.msg;
      }
      t.a = a;
    }
    dropClosed();
    const base = curBase();
    if (base && agentOf(base)?.kind === 'sdk') {
      // níveis que apareceram (upgrade feito noutra janela, ou recém-subidos) entram na cadeia ligada
      for (const k of chainOf(base)) {
        const t = tab(k);
        t.a = agentOf(k) || t.a;
        ensureEl(t);
        if (!t.es) stream(k);
      }
      renderLevels(base);
    }
    renderTabs();
    status();
  } catch (_) {
    /* servidor fora; tenta no próximo ciclo */
  }
}
refresh();
setInterval(refresh, 5000);
