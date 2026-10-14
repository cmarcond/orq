// Estado da página, API e DOM. A lógica pura está em format.js, classify.js e events.js.
import { esc, left, ago, md, withAttachments, fmtBytes, fileKind } from './format.js';
import { firstLine, originLabel, permissionBody, toolName, toolSummary } from './classify.js';
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
  quota: 'sem cota: trocando de conta…',
};

// ---------------------------------------------------------------- notificações
// Título da aba com quantos agentes esperam você (permissão, pergunta, resposta pronta), e notificação
// do navegador quando a página não está visível. O sino no topo pede a permissão uma vez.
const attn = new Set(); // rótulos que precisam de você desde a última vez que olhou
// Bipe curto pela própria página (o som da notificação do sistema nem sempre toca). O AudioContext nasce
// no clique do sino: o navegador só libera áudio depois de um gesto na página.
let audio = null;
function beep() {
  try {
    if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume();
    const t0 = audio.currentTime;
    for (const [f, at] of [[880, 0], [1320, 0.12]]) {
      const o = audio.createOscillator();
      const g = audio.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t0 + at);
      g.gain.exponentialRampToValueAtTime(0.25, t0 + at + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.11);
      o.connect(g).connect(audio.destination);
      o.start(t0 + at);
      o.stop(t0 + at + 0.12);
    }
  } catch (_) {
    /* sem áudio */
  }
}
function updateTitle() {
  document.title = attn.size ? `(${attn.size}) orq` : 'orq';
}
function needsYou(label, why, body) {
  if (label === cur && !document.hidden) return; // você está olhando
  attn.add(label);
  updateTitle();
  if (!document.hidden) return;
  beep(); // mesmo sem permissão de notificação (em http o navegador nunca dá): o bipe basta
  if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    try {
      const n = new Notification(`${label.split('@')[0]}: ${why}`, { body: (body || '').slice(0, 120), tag: label });
      n.onclick = () => {
        window.focus();
        show(label, true);
        n.close();
      };
    } catch (_) {
      /* navegador sem suporte */
    }
  }
}
function sawIt(label) {
  if (attn.delete(label)) updateTitle();
}
document.addEventListener('visibilitychange', () => !document.hidden && cur && sawIt(cur));
function testNotify() {
  beep();
  try {
    new Notification('orq: teste', { body: 'se você vê isto, as notificações do navegador funcionam', tag: 'orq-teste' });
    toast('notificação de teste enviada; se nada apareceu, confira Ajustes do Sistema → Notificações → seu navegador');
  } catch (e) {
    toast('o navegador recusou a notificação: ' + e.message);
  }
}
function askNotify() {
  // Fora de https/localhost o navegador recusa sem perguntar; o clique ao menos destrava o áudio do bipe.
  if (!window.isSecureContext) {
    beep();
    return toast('notificações do navegador só em https ou localhost; nesta página (http) ficam o bipe e a contagem no título — bipe ligado');
  }
  if (typeof Notification === 'undefined') return toast('este navegador não tem notificações');
  if (Notification.permission === 'granted') return testNotify();
  Notification.requestPermission().then((p) => {
    if (p === 'granted') return testNotify();
    beep();
    toast('notificações recusadas pelo navegador (libere no cadeado da barra de endereço); o bipe fica ligado');
  });
}

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
function totalCard(t) {
  // consolidado da semana da frota (`_total` do usage-all), no formato das contas: barra do usado médio com
  // o ritmo médio; à direita, quando zera (∞ = com os resets, não zera nesta semana); queima 1,0× = no ritmo
  if (!t) return '';
  const d = t['7d'] - t['7d_pace'];
  const burn = t['7d_burn'] ?? 0;
  const bcls = burn < 0.9 ? 'ok' : burn <= 1.15 ? 'warn' : 'bad';
  const runs = t['7d_runs_out'] != null ? left(t['7d_runs_out']) : '∞';
  return `<div class="ucard total" title="semana da frota: barra = usado médio, ┃ = ritmo médio; à direita, quando zera (∞ = não zera com os resets); queima 1,0× = no ritmo das janelas"><div class="name"><span>TOTAL · semana</span><span class="burn ${bcls}">queima ${burn.toFixed(1).replace('.', ',')}×</span></div>
    <div class="urow"><span>7d</span><div class="meter ${d >= 15 ? 'far' : d > 0 ? 'over' : ''}"><i style="width:${Math.min(100, t['7d'])}%"></i><b style="left:${Math.min(99, t['7d_pace'])}%"></b></div>
    <span class="pct">${Math.round(t['7d'])}%</span><span>${runs}</span></div>
    <div class="small mute">${Math.round(t['7d_free'])}% livre na média das ${t.accounts} contas</div></div>`;
}
function renderUsage(us) {
  $('usage').innerHTML =
    `<span class="tools"><button id="sets" class="ghost" title="conjuntos: abrir vários agentes de uma vez (cada um com prompt próprio, com ou sem professor), salvar as abas abertas como conjunto ou enviar um JSON pronto">⊞ conjuntos</button><button id="bell" class="ghost" title="notificações do navegador quando um agente pedir permissão ou terminar; clique para testar">🔔</button></span>` +
    totalCard(us._total) +
    Object.entries(us)
    .filter(([n]) => !n.startsWith('_'))
    .map(
      ([n, u]) => `<div class="ucard" style="--acc:${colorOf(n)}"><div class="name"><span><i class="sw" style="background:${colorOf(n)}"></i>${esc(LAB[n] || n)}</span>${
        u.err ? '<span class="small bad">erro</span>' : ''
      }</div>${u.err ? `<div class="small mute">${esc(u.err).slice(0, 80)}</div>` : meter(u, '5h') + meter(u, '7d')}</div>`,
    )
    .join('');
  $('bell').onclick = askNotify;
  $('sets').onclick = openSetsPicker;
  const tot = $('usage').querySelector('.ucard.total');
  if (tot) tot.onclick = () => $('usage').classList.toggle('open');
}

// ---------------------------------------------------------------- abas e níveis
// Um nível (professor, arquiteto…) é um agente com `parent` = rótulo do agente logo abaixo. A aba é
// sempre a do agente base; o chat mostrado (`cur`) é um rótulo da cadeia, por padrão o do topo.
const tabs = {}; // label -> {a, el, seq, es, pending, chips, perms, live, buf, follow, wires}
let cur = null;
let agents = [];
let links = []; // vínculos entre agentes (orq-agent links), de todas as máquinas
const samePair = (lk, a, b) => (lk.local === a && lk.remote === b) || (lk.local === b && lk.remote === a);
const dropLink = (a, b) => (links = links.filter((lk) => !samePair(lk, a, b)));
/** Vínculos vivos de `label`: [{other, host, kind, local, remote}]. */
function linksOf(label) {
  return links
    .filter((lk) => lk.up && (lk.local === label || lk.remote === label))
    .map((lk) => ({ ...lk, other: lk.local === label ? lk.remote : lk.local }));
}
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
/** Destinatário de um SendMessage para mostrar: nome, ou, se for um endereço `uds:`, o nível logo acima
 *  de quem mandou (é para quem uma resposta vai), senão "resposta". */
function wireTo(from, to) {
  if (!to.startsWith('uds:')) return to;
  const chain = chainOf(baseOf(from));
  const i = chain.indexOf(from);
  return i >= 0 && i + 1 < chain.length ? `↑ ${levelName(chain[i + 1])}` : 'resposta';
}

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
          const nl = linksOf(l);
          const tail = sdk
            ? `${lv.length ? `<span class="lv" title="${esc(lv.map(levelName).reverse().join(' → '))} → executor">⇅${lv.length}</span>` : ''}${nl.length ? `<span class="lv lk" title="vinculado a ${esc(nl.map((k) => k.other).join(', '))}">🔗${nl.length}</span>` : ''}${n < 9 ? `<span class="kbd">⌘${n + 1}</span>` : ''}<span class="x" data-x="${esc(l)}" title="encerrar agente">✕</span>`
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
  sawIt(cur);
  $('input').focus();
  loadFiles(base);
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
  const st8 = a && a.set ? ` · <span title="aberto pelo conjunto ${esc(a.set)}">⊞ ${esc(a.set)}</span>` : '';
  $('cstate').innerHTML = a
    ? `<i class="sw" style="background:${colorOf(a.account)}"></i>${esc(LAB[a.account] || a.account || '')}${lv}${st8} · ${esc(ST[st] || st)}${
        a.msg && st !== 'working' ? ' · ' + esc(a.msg) : ''
      }`
    : '';
  $('stop').hidden = st !== 'working';
  const base = curBase();
  const has = base && levelsAbove(base).length;
  $('lvl').hidden = !base || !(agentOf(base)?.kind === 'sdk');
  $('out').hidden = $('lvl').hidden;
  $('lnk').hidden = $('lvl').hidden;
  const nl = base ? linksOf(base).length : 0;
  $('lnk').innerHTML = nl ? `🔗 ${nl}` : '🔗<span class="lbl"> vincular</span>';
  $('lnk').title = nl ? 'vínculos deste agente (clique para ver; vincular a mais um pelo painel)' : 'vincular a outro agente da página (qualquer máquina): um pode pedir coisas ao outro pelo SendMessage';
  outBadge(base);
  $('lvl').innerHTML = `⇅<span class="lbl"> ${has ? 'níveis' : 'subir nível'}</span>`;
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
  const panel = $('levels');
  const lv = base ? levelsAbove(base) : [];
  if (lv.length) {
    panel.innerHTML = `<div class="lhead"><b>níveis</b><button id="down" class="ghost" title="encerra os níveis de cima; a aba volta a falar com o executor">↓ desfazer</button></div>
    <div class="lcards"></div><div class="lhead"><b>fios</b><span class="small mute">mensagens entre os níveis</span></div>`;
    panel.appendChild(wiresOf(base).el);
    panel.querySelector('#down').onclick = () => askDowngrade(base);
    renderLevelCards(base);
  } else panel.innerHTML = '';
  renderSide(base);
}

// ---------------------------------------------------------------- painel lateral: níveis ou arquivos
// O painel à direita da aba base mostra os níveis (sempre que existem) ou a lista de arquivos que o agente
// compartilhou (📂 abre). Só uma vista por vez; as abinhas no topo trocam.
const SIDE = { view: 'levels', files: false }; // vista pedida e se a lista de arquivos está aberta
function renderSide(base) {
  const main = $('chats');
  const side = $('side');
  const hasLv = Boolean(base && levelsAbove(base).length);
  const nLinks = base ? linksOf(base).length : 0;
  const avail = { levels: hasLv, links: nLinks > 0, files: Boolean(base && SIDE.files) };
  const view = !base ? null : avail[SIDE.view] ? SIDE.view : hasLv ? 'levels' : nLinks ? 'links' : null;
  $('levels').hidden = view !== 'levels';
  $('links').hidden = view !== 'links';
  $('outbox').hidden = view !== 'files';
  if (!view) {
    side.hidden = true;
    main.classList.remove('withside');
    return;
  }
  side.hidden = false;
  main.classList.add('withside');
  const n = tabs[base]?.files?.items.length || 0;
  side.querySelector('.stabs').innerHTML =
    (hasLv ? `<button data-v="levels" class="${view === 'levels' ? 'on' : ''}">⇅ níveis</button>` : '') +
    (nLinks ? `<button data-v="links" class="${view === 'links' ? 'on' : ''}">🔗 vínculos <span class="n">${nLinks}</span></button>` : '') +
    `<button data-v="files" class="${view === 'files' ? 'on' : ''}">📂 arquivos${n ? ` <span class="n">${n}</span>` : ''}</button>` +
    (view === 'files' ? '<button class="ghost sx" title="fechar a lista de arquivos">✕</button>' : '');
  side.querySelectorAll('.stabs [data-v]').forEach((b) => {
    b.onclick = () => {
      SIDE.view = b.dataset.v;
      if (SIDE.view === 'files') SIDE.files = true;
      renderSide(base);
    };
  });
  const sx = side.querySelector('.sx');
  if (sx)
    sx.onclick = () => {
      SIDE.files = false;
      SIDE.view = 'levels';
      main.classList.remove('showside');
      renderSide(base);
    };
  if (view === 'files') renderOutbox(base);
  if (view === 'links') renderLinks(base);
  if (view === 'levels' && !$('levels').contains(wiresOf(base).el)) $('levels').appendChild(wiresOf(base).el);
}
/** Vista dos vínculos: um cartão por agente ligado a `base` (clique abre a aba dele; ✕ desfaz) e os fios. */
function renderLinks(base) {
  const box = $('links');
  const items = linksOf(base);
  box.innerHTML = `<div class="lhead"><b>vínculos</b><button class="ghost more" title="vincular a mais um agente">+ vincular</button></div>
    <div class="lcards">${items
      .map((lk) => {
        const a = agentOf(lk.other) || { label: lk.other };
        const st = a.state || '';
        return `<div class="lcard link ${st}" data-l="${esc(lk.other)}" title="${esc(lk.other)}: abrir a aba dele"><div class="lrow"><span class="dot"></span><b>${esc(lk.other.split('@')[0])}</b><span class="small mute">${esc(hostOf(lk.other))}${lk.kind === 'ssh' ? ' · ssh' : ''} · ${esc(ST[st] || st)}</span><button class="ghost ul" data-u="${esc(lk.other)}" title="desfazer o vínculo">✕</button></div></div>`;
      })
      .join('')}</div><div class="lhead"><b>fios</b><span class="small mute">pedidos e respostas pelo SendMessage</span></div>`;
  box.appendChild(wiresOf(base).el);
  box.querySelector('.more').onclick = () => openLinkPicker(base);
  box.querySelectorAll('.lcard').forEach((el) => {
    el.onclick = (e) => {
      if (e.target.closest('button')) return;
      show(el.dataset.l);
    };
  });
  box.querySelectorAll('.ul').forEach((bt) => (bt.onclick = () => askUnlink(base, bt.dataset.u)));
}
function askUnlink(base, other) {
  const t = tab(base);
  if (t._unlink === other) {
    t._unlink = null;
    agentApi(base, 'unlink', { to: other }).then((j) => {
      if (!j.ok) return;
      toast(`vínculo com ${other.split('@')[0]} desfeito`);
      dropLink(base, other);
      renderSide(base);
      renderTabs();
      status();
    });
    return;
  }
  t._unlink = other;
  toast(`clique de novo em ✕ para desfazer o vínculo com ${other.split('@')[0]}`);
  setTimeout(() => (t._unlink = null), 4000);
}
/** Escolhe outro agente da página (qualquer máquina) para vincular a `base`. */
function openLinkPicker(base) {
  const linked = new Set(linksOf(base).map((lk) => lk.other));
  const chain = new Set(chainOf(base));
  const rows = agents.filter((a) => live(a) && a.kind === 'sdk' && a.label !== base && !linked.has(a.label) && !chain.has(a.label) && !isLevel(a));
  picker(
    `Vincular ${base.split('@')[0]} a…`,
    'Os dois passam a se ver no ListAgents e a pedir coisas um ao outro pelo SendMessage (em máquinas diferentes, o orq estica o socket por ssh). Cada um recebe uma nota dizendo como falar com o outro.',
    rows.length
      ? rows
          .map(
            (a) => `<div class="p" data-l="${esc(a.label)}"><span class="mute">${esc(hostOf(a.label))}</span><span class="path"><b>${esc(a.label.split('@')[0])}</b> <span class="mute">${esc(a.dir || '')}</span></span><span><i class="sw" style="background:${colorOf(a.account)}"></i>${esc(LAB[a.account] || a.account || '')}</span><span class="small mute">${esc(ST[a.state] || a.state || '')}</span></div>`,
          )
          .join('')
      : '<div class="mute">Nenhum outro agente da página para vincular. Abra outro projeto no <b>+</b> antes.</div>',
  );
  $('plist').querySelectorAll('.p').forEach((el) => {
    el.onclick = async () => {
      const to = el.dataset.l;
      $('picker').hidden = true;
      toast(`vinculando ${base.split('@')[0]} a ${to.split('@')[0]}…${hostOf(base) !== hostOf(to) ? ' (túnel ssh)' : ''}`);
      const j = await agentApi(base, 'link', { to });
      if (!j.ok) return;
      if (j.link) {
        dropLink(base, to);
        links.push(j.link);
      }
      toast(`${base.split('@')[0]} ⇄ ${to.split('@')[0]} vinculados${(j.note_errors || []).length ? ' (a nota não chegou a um deles)' : ''}`);
      SIDE.view = 'links';
      renderSide(base);
      renderTabs();
      status();
    };
  });
}
const fileUrl = (base, name, dl = false) =>
  `/api/agent/${encodeURIComponent(base)}/file?name=${encodeURIComponent(name)}${dl ? '&dl=1' : ''}${T ? `&t=${encodeURIComponent(T)}` : ''}`;
function filesOf(base) {
  const t = tab(base);
  if (!t.files) t.files = { items: [], loaded: false, known: null, fresh: new Set(), dir: '', err: null, busy: false };
  return t.files;
}
/** Lê a _outbox/ do agente base. Arquivo que não estava na leitura anterior vira aviso e destaque. */
async function loadFiles(base) {
  if (!base || agentOf(base)?.kind !== 'sdk') return;
  const fl = filesOf(base);
  if (fl.busy) return;
  fl.busy = true;
  try {
    const r = await fetch(`/api/agent/${encodeURIComponent(base)}/files`, { headers: H });
    if (r.status === 404) {
      fl.busy = false; // o servidor ainda não conhece o agente (acabou de abrir): fica para a próxima
      return;
    }
    const j = await r.json().catch(() => ({ err: 'resposta inválida' }));
    fl.err = j.err || null;
    fl.dir = j.dir || fl.dir;
    if (j.items) {
      fl.items = j.items;
      const key = (f) => `${f.name}@${f.mtime}@${f.size}`;
      const now = new Set(fl.items.map(key));
      if (fl.known) {
        const novo = fl.items.filter((f) => !fl.known.has(key(f)));
        if (novo.length) {
          novo.forEach((f) => fl.fresh.add(f.name));
          toast(`📂 ${base.split('@')[0]}: ${novo.length === 1 ? `novo arquivo ${novo[0].name}` : `${novo.length} arquivos novos`}`);
        }
      }
      fl.known = now;
    }
    fl.loaded = true;
  } catch (_) {
    /* servidor fora; fica a lista anterior */
  }
  fl.busy = false;
  if (base === curBase()) {
    outBadge(base);
    renderSide(base);
  }
}
function outBadge(base) {
  const fl = base && tabs[base]?.files;
  const n = fl?.items.length || 0;
  $('out').innerHTML = `📂${n ? ` <span class="n">${n}</span>` : ''}`;
  $('out').classList.toggle('new', Boolean(fl?.fresh.size));
}
function renderOutbox(base) {
  const box = $('outbox');
  const fl = filesOf(base);
  const dir = fl.dir || `${agentOf(base)?.dir || ''}/_outbox`;
  const head = `<div class="lhead"><b>arquivos</b><span class="small mute"><code title="${esc(dir)}">_outbox/</code> <button class="ghost rf" title="atualizar a lista">↻</button></span></div>`;
  let body;
  if (!fl.loaded) body = '<div class="small mute">carregando…</div>';
  else if (fl.err && !fl.items.length) body = `<div class="small bad">${esc(fl.err)}</div>`;
  else if (!fl.items.length)
    body = `<div class="fempty"><div>📂</div><p>Nada compartilhado ainda.</p><p class="small">Peça ao agente para copiar o arquivo para <code>_outbox/</code> na pasta do projeto; ele aparece aqui para ver ou baixar.</p></div>`;
  else
    body = `<div class="flist">${fl.items
      .map((f) => {
        const k = fileKind(f.name);
        return `<div class="frow ${fl.fresh.has(f.name) ? 'fresh' : ''}" data-n="${esc(f.name)}" title="${k.view ? 'ver' : 'baixar'} ${esc(f.name)}"><span class="fico">${k.icon}</span><div class="fmeta"><b>${esc(f.name)}</b><span class="small mute">${fmtBytes(f.size)} · ${ago(f.mtime)}</span></div><a class="fdl" href="${fileUrl(base, f.name, true)}" download="${esc(f.name)}" title="baixar">⬇</a></div>`;
      })
      .join('')}</div>`;
  box.innerHTML = head + body;
  box.querySelector('.rf').onclick = () => loadFiles(base);
  box.querySelectorAll('.frow').forEach((el) => {
    el.onclick = (e) => {
      if (e.target.closest('a')) return;
      const f = fl.items.find((x) => x.name === el.dataset.n);
      if (f) openView(base, f);
    };
  });
  fl.fresh.clear(); // visto: o destaque fica até a próxima leitura
  outBadge(base);
}
/** Prévia de um arquivo: imagem, PDF (visualizador do navegador), vídeo, áudio, texto/markdown; o resto baixa. */
function openView(base, f) {
  const k = fileKind(f.name);
  if (!k.view) {
    const a = document.createElement('a');
    a.href = fileUrl(base, f.name, true);
    a.download = f.name;
    a.click();
    return;
  }
  const v = $('view');
  const url = fileUrl(base, f.name);
  v.hidden = false;
  v.querySelector('.vname').textContent = f.name;
  v.querySelector('.vsize').textContent = fmtBytes(f.size);
  v.querySelector('.vdl').href = fileUrl(base, f.name, true);
  const body = v.querySelector('.vbody');
  if (k.view === 'image') body.innerHTML = `<img src="${url}" alt="${esc(f.name)}">`;
  else if (k.view === 'pdf') body.innerHTML = `<iframe src="${url}" title="${esc(f.name)}"></iframe>`;
  else if (k.view === 'video') body.innerHTML = `<video src="${url}" controls autoplay></video>`;
  else if (k.view === 'audio') body.innerHTML = `<audio src="${url}" controls autoplay></audio>`;
  else if (f.size > 2 * 1024 * 1024) body.innerHTML = '<p class="small mute">texto grande demais para mostrar aqui; baixe.</p>';
  else {
    body.innerHTML = '<div class="small mute">carregando…</div>';
    fetch(url, { headers: H })
      .then((r) => r.text())
      .then((txt) => {
        if (v.hidden || v.querySelector('.vname').textContent !== f.name) return;
        if (k.md) body.innerHTML = `<div class="vmd">${md(txt)}</div>`;
        else {
          body.innerHTML = '<pre></pre>';
          body.querySelector('pre').textContent = txt;
        }
      })
      .catch(() => (body.innerHTML = '<p class="small bad">não consegui ler o arquivo</p>'));
  }
}
function closeView() {
  const v = $('view');
  v.hidden = true;
  v.querySelector('.vbody').innerHTML = ''; // para o vídeo e solta a conexão
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
      const pend = st === 'permission' && tabs[l]?.pend;
      // permissão pendente num nível que não está no chat: responde aqui mesmo (pergunta abre o chat dele)
      const ask = pend
        ? pend.ask
          ? `<div class="lask"><span class="small">pergunta: ${esc((pend.request.input?.questions || [])[0]?.question || '').slice(0, 80)}</span><button class="go">responder</button></div>`
          : `<div class="lask"><code>${esc(toolName(pend.request.tool_name))} ${esc(toolSummary(pend.request.input)).slice(0, 80)}</code><span class="lbtns"><button data-ok="1">Permitir</button><button data-ok="0">Negar</button></span></div>`
        : '';
      return `<div class="lcard ${st} ${l === cur ? 'on' : ''}" data-l="${esc(l)}" style="--lc:${levelColor(l)}" title="${esc(l)}: ver o chat cru deste nível">
        <div class="lrow"><span class="dot"></span><b>${esc(levelName(l))}</b><span class="small mute">${esc(ST[st] || st)}${a.msg && st !== 'working' ? ' · ' + esc(a.msg).slice(0, 40) : ''}</span></div>${ask}</div>`;
    })
    .join('');
  box.querySelectorAll('.lcard').forEach((el) => {
    const l = el.dataset.l;
    el.onclick = (e) => {
      if (e.target.closest('button')) return;
      show(l, true);
    };
    el.querySelectorAll('.lbtns button').forEach((bt) => {
      bt.onclick = () => {
        const pend = tabs[l]?.pend;
        if (!pend) return;
        agentApi(l, 'permission', { request_id: pend.requestId, allow: bt.dataset.ok === '1', input: pend.request.input });
      };
    });
    const go = el.querySelector('.go');
    if (go) go.onclick = () => show(l, true);
  });
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
    const to = wireTo(from, a.to);
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
  const lab = t.a?.label || cur;
  if (a.kind === 'permission' && !a.replayed) needsYou(lab, 'pede permissão', toolName(a.request.tool_name) + ' ' + toolSummary(a.request.input));
  else if (a.kind === 'ask' && !a.replayed) needsYou(lab, 'pergunta', (a.request.input?.questions || [])[0]?.question);
  else if (a.kind === 'result' && !t.a?.replaying) needsYou(lab, a.isError ? 'erro' : 'terminou', a.text || t.lastText || '');
  if (a.kind === 'text') t.lastText = a.text;
  if (a.kind === 'result' && !t.a?.replaying) setTimeout(() => loadFiles(baseOf(lab)), 800); // a rodada pode ter deixado arquivo na _outbox
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
      if (t.pend?.requestId === a.requestId) t.pend = null;
      renderLevelCards(baseOf(t.a?.label || cur));
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
      const to = wireTo(t.a?.label || cur, a.to);
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
      if (!a.replayed) t.pend = { requestId: a.requestId, request: a.request, ask: false };
      permCard(t, a);
      return renderLevelCards(baseOf(t.a?.label || cur));
    case 'ask':
      if (!a.replayed) t.pend = { requestId: a.requestId, request: a.request, ask: true };
      askForm(t, a);
      return renderLevelCards(baseOf(t.a?.label || cur));
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
    if (SIDE.view !== 'levels') {
      SIDE.view = 'levels';
      renderSide(base);
      $('chats').classList.add('showside');
    } else $('chats').classList.toggle('showside');
    return;
  }
  openLevelPicker(base);
};
$('lnk').onclick = () => {
  const base = curBase();
  if (!base) return;
  if (linksOf(base).length) {
    SIDE.view = 'links';
    $('chats').classList.add('showside');
    renderSide(base);
    return;
  }
  openLinkPicker(base);
};
$('out').onclick = () => {
  const base = curBase();
  if (!base) return;
  const open = SIDE.files && SIDE.view === 'files';
  SIDE.files = !open;
  SIDE.view = open ? 'levels' : 'files';
  $('chats').classList.toggle('showside', !open);
  renderSide(base);
  if (!open) loadFiles(base);
};
$('view').querySelector('.vx').onclick = closeView;
$('view').onclick = (e) => e.target === $('view') && closeView();
setInterval(() => loadFiles(curBase()), 30000);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('view').hidden) return closeView();
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
  if (agentOf(base) && !agentOf(base).name) {
    // aberto antes de os agentes subirem com -n: sem nome o SendMessage não o acha. Reabre na mesma conversa.
    $('plist').innerHTML = `<div class="mute">Este agente abriu sem nome e os níveis não conseguiriam falar com ele.
      <b>Reabrir</b> fecha e reabre a mesma conversa, já com nome (leva uns segundos).</div>
      <div class="prow"><button class="ok">↻ Reabrir com nome</button></div>`;
    $('plist').querySelector('.ok').onclick = async () => {
      $('picker').hidden = true;
      toast(`reabrindo ${base.split('@')[0]}…`);
      const j = await agentApi(base, 'reopen');
      if (!j.label) return;
      if (tabs[base]) {
        tabs[base].el?.remove();
        delete tabs[base];
      }
      cur = null;
      await refresh();
      show(base);
      toast(`${base.split('@')[0]} reaberto com nome; agora ⇅ subir nível`);
    };
    return;
  }
  const s = await api('/api/pipelines');
  $('plist').innerHTML =
    (s.items || [])
      .map(
        (p) => {
          const nl = p.multi ? linksOf(base).length : 0;
          const tail = p.multi ? (nl ? ` → ${nl + 1} executores (este + ${nl} vinculados)` : ' → executor (nenhum vinculado ainda: 🔗 antes)') : ' → executor';
          return `<div class="p lvp" data-id="${esc(p.id)}"><span class="path"><b>${esc(p.titulo)}</b></span><span class="mute small">${esc(
            p.niveis.join(' → '),
          )}${esc(tail)}</span><span class="ttl">${esc(p.descricao)}</span></div>`;
        },
      )
      .join('') || '<div class="mute">nenhuma receita em orqweb/pipelines</div>';
  $('plist').querySelectorAll('.p').forEach((el) => (el.onclick = () => upgrade(base, el.dataset.id)));
}
async function upgrade(base, pipeline) {
  $('picker').hidden = true;
  toast(`subindo níveis sobre ${base.split('@')[0]}…`);
  const j = await agentApi(base, 'upgrade', { pipeline });
  if (!j.top) return;
  SIDE.view = 'levels'; // o painel mostra os níveis recém-subidos, mesmo que estivesse nos vínculos
  const nl = (j.links || []).length;
  const le = Object.keys(j.link_errors || {}).length;
  toast(`${j.levels.map(levelName).reverse().join(' → ')} → ${nl ? `${nl + 1} executores` : 'executor'}; a aba fala com ${levelName(j.top)}${le ? ` (${le} vínculo(s) falharam)` : ''}`);
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
// ---------------------------------------------------------------- conjuntos
// ⊞ no topo: um conjunto é um JSON (em ~/.config/orq-web/conjuntos, no servidor) com vários agentes para
// abrir de uma vez, cada um com prompt e tarefa próprios, com ou sem um professor líder. O servidor abre numa
// thread; o andamento chega pelo /api/state (conjuntos_abrindo) e vira toast aqui.
const SETS = { seen: {} }; // id -> última etapa vista, para avisar cada passo uma vez
const setApi = (id, op, body) => api(`/api/conjuntos/${encodeURIComponent(id)}${op ? '/' + op : ''}`, body);
async function openSetsPicker() {
  picker(
    'Conjuntos',
    'Vários agentes de uma vez, cada um com prompt e tarefa próprios, com ou sem um professor líder. Clique para abrir; ⬇ baixa o JSON; ✕ apaga. Se algum agente do conjunto já estiver aberto, a página recusa e diz quais.',
    '…',
  );
  const s = await api('/api/conjuntos');
  if (s.err) return;
  const rows = (s.items || [])
    .map((c) => {
      const open = c.abertos.length;
      const tag = [`${c.n} agente${c.n === 1 ? '' : 's'}`, c.maquinas.join(', '), c.lider ? '👨‍🏫 professor' : 'independentes', c.permissao]
        .filter(Boolean)
        .join(' · ');
      return `<div class="p setp" data-id="${esc(c.id)}" title="abrir o conjunto ${esc(c.nome)}">
        <span class="path"><b>${esc(c.nome)}</b> <span class="mute small">${esc(tag)}</span></span>
        <span class="small ${open ? 'bad' : 'mute'}">${open ? `${open} já aberto${open > 1 ? 's' : ''}` : ''}</span>
        <span class="sbtns"><button class="ghost" data-dl="${esc(c.id)}" title="baixar o JSON">⬇</button><button class="ghost" data-del="${esc(c.id)}" title="apagar o conjunto">✕</button></span>
        ${c.descricao ? `<span class="ttl">${esc(c.descricao)}</span>` : ''}</div>`;
    })
    .join('');
  $('plist').innerHTML =
    `<div class="prow stools"><button id="setup">📄 enviar JSON</button><button id="setsnap">📷 salvar abas abertas</button><span class="mute small">pasta: <code>${esc(s.dir)}</code></span></div>` +
    (rows || '<div class="mute">nenhum conjunto salvo. Envie um JSON pronto ou fotografe as abas abertas.</div>');
  $('setup').onclick = uploadSet;
  $('setsnap').onclick = snapshotSet;
  $('plist').querySelectorAll('.setp').forEach((el) => {
    el.onclick = (e) => {
      if (e.target.dataset.dl) return downloadSet(e.target.dataset.dl);
      if (e.target.dataset.del) return deleteSet(e.target.dataset.del, el);
      openSet(el.dataset.id);
    };
  });
}
async function openSet(id) {
  const j = await setApi(id, 'open', {});
  if (j.err) return; // o api() já mostrou o motivo (inclusive "já aberto: …")
  $('picker').hidden = true;
  SETS.seen[id] = 'abrindo';
  toast(`⊞ ${j.nome}: abrindo ${j.agentes.map((a) => `${a.label.split('@')[0]} (${LAB[a.acc] || a.acc})`).join(', ')}…`);
}
const SET_STEPS = { abrindo: 'abrindo os agentes…', vínculos: 'vinculando os executores…', professor: 'subindo o professor…', tarefas: 'enviando as tarefas…' };
function setsProgress(jobs) {
  for (const j of jobs || []) {
    const prev = SETS.seen[j.id];
    if (prev === j.etapa) continue;
    SETS.seen[j.id] = j.etapa;
    if (prev === undefined && j.etapa === 'pronto') continue; // terminou antes de esta página existir
    if (j.etapa !== 'pronto') {
      toast(`⊞ ${j.nome}: ${SET_STEPS[j.etapa] || j.etapa}`);
      continue;
    }
    const errs = Object.entries(j.erros || {});
    const n = j.labels.length;
    toast(
      `⊞ ${j.nome}: ${j.feitos.length} de ${n} agente${n === 1 ? '' : 's'} no ar${j.top ? ', com professor' : ''}${
        errs.length ? `; ${errs.length} problema${errs.length > 1 ? 's' : ''}: ${errs.map(([k, v]) => `${k}: ${v}`).join('; ').slice(0, 240)}` : ''
      }`,
    );
    const first = j.feitos.includes(j.labels[0]) ? j.labels[0] : j.feitos[0];
    if (first && agentOf(first)) show(first);
  }
}
async function downloadSet(id) {
  const j = await setApi(id);
  if (j.err) return;
  const blob = new Blob([JSON.stringify(j.conjunto, null, 2) + '\n'], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${id}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function deleteSet(id, el) {
  if (el._confirm) {
    setApi(id, 'delete', {}).then((j) => {
      if (j.err) return;
      toast(`conjunto ${id} apagado`);
      openSetsPicker();
    });
    return;
  }
  el._confirm = true;
  toast(`clique no ✕ de novo para apagar o conjunto ${id}`);
  setTimeout(() => (el._confirm = false), 4000);
}
function uploadSet() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = '.json,application/json';
  inp.onchange = async () => {
    const f = inp.files[0];
    if (!f) return;
    let data;
    try {
      data = JSON.parse(await f.text());
    } catch (e) {
      return toast(`${f.name}: JSON inválido (${e.message})`);
    }
    if (data && typeof data === 'object' && !data.nome) data.nome = f.name.replace(/\.json$/i, '');
    const j = await api('/api/conjuntos', data);
    if (j.err) return;
    toast(`conjunto ${j.id} salvo`);
    openSetsPicker();
  };
  inp.click();
}
async function snapshotSet() {
  const j = await api('/api/conjuntos/snapshot', {});
  if (j.err) return;
  const c = j.conjunto;
  $('phint').innerHTML =
    'As abas abertas como conjunto: confira o JSON, dê um nome e salve. Só o primeiro professor entra como líder (os outros agentes viram executores dele); o prompt só aparece em agentes que subiram com um.';
  $('plist').innerHTML = `<div class="prow"><input id="snapname" placeholder="nome do conjunto" value="${esc(c.nome || '')}"><button id="snapsave">salvar</button><button class="ghost" id="snapback">voltar</button></div>
    ${(j.avisos || []).map((w) => `<div class="small bad">${esc(w)}</div>`).join('')}
    <textarea id="snapjson" rows="18" spellcheck="false">${esc(JSON.stringify(c, null, 2))}</textarea>`;
  $('snapback').onclick = openSetsPicker;
  $('snapsave').onclick = async () => {
    let data;
    try {
      data = JSON.parse($('snapjson').value);
    } catch (e) {
      return toast(`JSON inválido: ${e.message}`);
    }
    const nome = $('snapname').value.trim();
    if (nome) data.nome = nome;
    const r = await api('/api/conjuntos', data);
    if (r.err) return;
    toast(`conjunto ${r.id} salvo`);
    openSetsPicker();
  };
  $('snapname').focus();
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
    links = s.links || [];
    for (const a of agents) {
      const t = tabs[a.label];
      if (!t) continue;
      if (t.pid && a.pid && t.pid !== a.pid && a.state !== 'closed') {
        // o supervisor foi reaberto (troca de conta, reabrir com nome): o log de eventos recomeça do zero
        if (t.es) t.es.close();
        t.el?.remove();
        const was = t.was || t.a?.account;
        delete tabs[a.label];
        if (was && a.account && was !== a.account) toast(`${a.label.split('@')[0]}: conta ${LAB[was] || was} sem cota; continua na ${LAB[a.account] || a.account}`);
        if (cur === a.label || chainOf(curBase() || '').includes(a.label)) setTimeout(() => show(curBase() || a.label, true), 0);
        continue;
      }
      if (t.a?.state === 'closed' && a.state !== 'closed' && (!a.pid || a.pid !== t.pid)) {
        // o stream disse "agente encerrado", mas a lista o tem vivo sem pid (ou com outro): está sendo
        // reaberto (troca de conta, reabrir com nome). A aba fica, 'abrindo…', até o pid novo chegar e
        // o ramo acima recriá-la do zero; sem isto ela sumia e a página pulava para outra aba
        t.was = t.was || t.a.account; // conta de antes, para o aviso de troca
        t.a = a;
        continue;
      }
      if (a.pid) t.pid = a.pid;
      if (t.a?.state === 'closed') a.state = 'closed'; // encerrado pela página: a máquina pode atrasar
      else if (t.es && a.state !== 'closed' && t.a?.state && t.a.state !== 'starting') {
        // a aba sabe o estado pelos eventos; a consulta às máquinas atrasa até 15 s. Exceção: 'abrindo…'
        // é o chute provisório da página, e só a máquina diz quando o agente de fato ficou pronto
        a.state = t.a.state;
        a.msg = t.a.msg ?? a.msg;
      }
      // a lista pode trazer a conta nova (entrada provisória da troca) antes de o pid novo existir;
      // guarda a de antes para o aviso de troca quando o ramo do pid recriar a aba
      if (t.a?.account && a.account && a.account !== t.a.account) t.was = t.was || t.a.account;
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
    setsProgress(s.conjuntos_abrindo);
  } catch (_) {
    /* servidor fora; tenta no próximo ciclo */
  }
}
refresh();
setInterval(refresh, 5000);
