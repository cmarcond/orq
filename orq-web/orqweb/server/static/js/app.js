// Page state, API and DOM. The pure logic lives in format.js, classify.js and events.js.
import { esc, left, ago, md, withAttachments, fmtBytes, fileKind, sparkSvg, histSummary, freshAlerts, badgeFor, fleetBadge } from './format.js';
import { firstLine, originLabel, permissionBody, toolName, toolSummary } from './classify.js';
import { reduce, collectAnswers, sentMsgId } from './events.js';
import { weekChart, hourChart, dayChart } from './charts.js';
import { usageTableHtml } from './usage.js';

const T = new URLSearchParams(location.search).get('t') || '';
const H = { 'X-Orq-Token': T, 'Content-Type': 'application/json' };
const $ = (id) => document.getElementById(id);
const ST = {
  working: 'working…',
  waiting: 'waiting for you',
  permission: 'asks permission',
  error: 'error',
  starting: 'opening…',
  closed: 'closed',
  quota: 'out of quota: switching account…',
};

// ---------------------------------------------------------------- notifications
// Tab title with how many agents are waiting for you (permission, question, answer ready), and a browser
// notification when the page is not visible. The bell at the top asks for the permission once.
const attn = new Set(); // labels that have needed you since the last time you looked
// Short beep from the page itself (the system notification sound does not always play). The AudioContext is
// created on the bell click: the browser only allows audio after a gesture on the page.
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
    /* no audio */
  }
}
function updateTitle() {
  document.title = attn.size ? `(${attn.size}) orq` : 'orq';
}
function needsYou(label, why, body) {
  if (label === cur && !document.hidden) return; // you are looking at it
  attn.add(label);
  updateTitle();
  if (!document.hidden) return;
  beep(); // even without notification permission (on http the browser never grants it): the beep is enough
  if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    try {
      const n = new Notification(`${label.split('@')[0]}: ${why}`, { body: (body || '').slice(0, 120), tag: label });
      n.onclick = () => {
        window.focus();
        show(label, true);
        n.close();
      };
    } catch (_) {
      /* browser without support */
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
    new Notification('orq: test', { body: 'if you can see this, browser notifications work', tag: 'orq-teste' });
    toast('test notification sent; if nothing showed up, check System Settings → Notifications → your browser');
  } catch (e) {
    toast('the browser refused the notification: ' + e.message);
  }
}
function askNotify() {
  // Outside https/localhost the browser refuses without asking; the click at least unlocks the beep audio.
  if (!window.isSecureContext) {
    beep();
    return toast('browser notifications only work on https or localhost; on this page (http) you get the beep and the count in the title — beep on');
  }
  if (typeof Notification === 'undefined') return toast('this browser has no notifications');
  if (Notification.permission === 'granted') return testNotify();
  Notification.requestPermission().then((p) => {
    if (p === 'granted') return testNotify();
    beep();
    toast('notifications refused by the browser (allow them at the padlock in the address bar); the beep stays on');
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

// ---------------------------------------------------------------- quota
let LAB = {};
let COL = {}; // color per account, from accounts.json (or from the default palette)
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
// ---------------------------------------------------------------- usage history (usage-hist database)
// The server sends `history` in /api/state (usage-hist painel --json): per account, today, pace, forecast and
// the 24 h by hour; alerts; when the collection ran. Without a database, {} comes and the cards show only the quota.
function histBlock(h, name, top) {
  const a = h && h.accounts && h.accounts[name];
  if (!a) return '';
  const s = histSummary(a);
  return `<div class="hrow" title="weekly points spent per hour in the last 24 h (hover over a bar)">${sparkSvg(a.last_24h || [], top, h.t24)}</div>
    <div class="hsum ${s.warn ? 'bad' : ''}" title="today: weekly points spent since midnight · pace: points per hour in the last 6 h · forecast: when it reaches 100% at this pace (⚠ = before the reset) · * = since collection started">${esc(s.text)}</div>`;
}
// alert badge: stays on the card while the alert exists (the toast just goes by); the title carries the full text
const seloHtml = (b) => (b ? `<span class="selo ${b.level}" title="${esc(b.title)}">${esc(b.label)}</span>` : '');
function histTotal(h) {
  if (!h || !h.accounts) return '';
  const age = h.collected ? (Date.now() - new Date(h.collected).getTime()) / 60e3 : null;
  const stale = age != null && age > 15 ? ` · <span class="bad">collection stalled for ${left(Math.round(age * 60))}</span>` : '';
  const used = h.used_24h != null ? `24 h: ${h.used_24h.toFixed(1)} pts spent` : '';
  return `<div class="small mute hsum" title="usage-hist history (collector every 5 min)">${used}${stale}</div>`;
}
let ALERTS_SEEN = null; // text -> when it was shown (ms), so the same alert is not repeated every 5 s
function showAlerts(h) {
  if (!h || !Array.isArray(h.alerts)) return;
  if (ALERTS_SEEN === null) {
    try {
      ALERTS_SEEN = JSON.parse(localStorage.getItem('orq.alertas') || '{}');
    } catch (_) {
      ALERTS_SEEN = {};
    }
  }
  const r = freshAlerts(h.alerts, ALERTS_SEEN);
  ALERTS_SEEN = r.seen;
  try {
    localStorage.setItem('orq.alertas', JSON.stringify(r.seen));
  } catch (_) {
    /* no storage: the alert shows up again on reload */
  }
  if (r.fresh.length) toast(r.fresh.slice(0, 2).join('  ·  ') + (r.fresh.length > 2 ? `  (+${r.fresh.length - 2})` : ''));
}
function totalCard(t, h) {
  // fleet-wide summary of the week (`_total` of usage-all), in the same format as the accounts: bar of the average
  // used with the average pace; on the right, when it runs out (∞ = given the resets, it does not run out this
  // week); burn 1.0× = on pace
  if (!t) return '';
  const d = t['7d'] - t['7d_pace'];
  const burn = t['7d_burn'] ?? 0;
  const bcls = burn < 0.9 ? 'ok' : burn <= 1.15 ? 'warn' : 'bad';
  const runs = t['7d_runs_out'] != null ? left(t['7d_runs_out']) : '∞';
  return `<div class="ucard total" title="fleet week: bar = average used, ┃ = average pace; on the right, when it runs out (∞ = does not run out given the resets); burn 1.0× = at the pace of the windows"><div class="name"><span>TOTAL · week ${seloHtml(h && fleetBadge(h.alert_items))}</span><span class="burn ${bcls}">burn ${burn.toFixed(1)}×</span></div>
    <div class="urow"><span>7d</span><div class="meter ${d >= 15 ? 'far' : d > 0 ? 'over' : ''}"><i style="width:${Math.min(100, t['7d'])}%"></i><b style="left:${Math.min(99, t['7d_pace'])}%"></b></div>
    <span class="pct">${Math.round(t['7d'])}%</span><span>${runs}</span></div>
    <div class="small mute">${Math.round(t['7d_free'])}% free on average across the ${t.accounts} accounts</div>${histTotal(h)}</div>`;
}
function renderUsage(us, hist) {
  const h = hist && hist.accounts ? hist : null;
  const top = h ? Math.max(0, ...Object.values(h.accounts).flatMap((a) => a.last_24h || [])) : 0;
  $('usage').innerHTML =
    `<span class="tools"><button id="uso" class="ghost" title="usage: who is spending each account right now, on all machines (from the usage-hist history)">📊 usage</button><button id="sets" class="ghost" title="sets: open several agents at once (each with its own prompt, with or without a teacher), save the open tabs as a set or upload a ready-made JSON">⊞ sets</button><button id="bell" class="ghost" title="browser notifications when an agent asks permission or finishes; click to test">🔔</button></span>` +
    totalCard(us._total, h) +
    Object.entries(us)
    .filter(([n]) => !n.startsWith('_'))
    .map(
      ([n, u]) => `<div class="ucard${h && h.accounts && h.accounts[n] ? ' clickable' : ''}" data-acc="${esc(n)}" style="--acc:${colorOf(n)}" title="${h ? 'click: account panel (24 h, 7 days, week up to the reset, what spent the most)' : ''}"><div class="name"><span><i class="sw" style="background:${colorOf(n)}"></i>${esc(LAB[n] || n)} ${seloHtml(h && badgeFor(h.alert_items, n))}</span>${
        u.err ? '<span class="small bad">error</span>' : ''
      }</div>${u.err ? `<div class="small mute">${esc(u.err).slice(0, 80)}</div>` : meter(u, '5h') + meter(u, '7d')}${histBlock(h, n, top)}</div>`,
    )
    .join('');
  $('bell').onclick = askNotify;
  $('sets').onclick = openSetsPicker;
  $('uso').onclick = () => openUsageView(5);
  $('usage').querySelectorAll('.ucard.clickable').forEach((el) => (el.onclick = () => openAccountPanel(el.dataset.acc)));
  const tot = $('usage').querySelector('.ucard.total');
  if (tot) tot.onclick = () => $('usage').classList.toggle('open');
}

// ---------------------------------------------------------------- tabs and levels
// A level (professor, arquiteto…) is an agent with `parent` = label of the agent right below it. The tab is
// always that of the base agent; the chat shown (`cur`) is a label in the chain, by default the top one.
const tabs = {}; // label -> {a, el, seq, es, pending, chips, perms, live, buf, follow, wires}
let cur = null;
let agents = [];
let links = []; // links between agents (orq-agent links), from all machines
const samePair = (lk, a, b) => (lk.local === a && lk.remote === b) || (lk.local === b && lk.remote === a);
const dropLink = (a, b) => (links = links.filter((lk) => !samePair(lk, a, b)));
/** Live links of `label`: [{other, host, kind, local, remote}]. */
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
const chainOf = (base) => [base, ...levelsAbove(base)]; // bottom to top
const levelName = (l) => agentOf(l)?.level || (isLevel(agentOf(l)) ? l.split('@')[0].split('~').pop() : 'executor');
const levelColor = (l) => LEVEL_COLORS[levelName(l)] || 'var(--mute)';
const curBase = () => (cur ? baseOf(cur) : null);
/** Recipient of a SendMessage to display: the name, or, if it is a `uds:` address, the level right above
 *  the sender (that is who a reply goes to), otherwise "reply". */
function wireTo(from, to) {
  if (!to.startsWith('uds:')) return to;
  const chain = chainOf(baseOf(from));
  const i = chain.indexOf(from);
  return i >= 0 && i + 1 < chain.length ? `↑ ${levelName(chain[i + 1])}` : 'reply';
}

function tabOrder() {
  // sdk and terminal (the latter only to convert); a closed or vanished agent, or one that is another's level, takes no tab
  return agents.filter((a) => live(a) && !isLevel(a)).map((a) => a.label);
}
function renderTabs() {
  const nav = $('tabs');
  const order = tabOrder();
  const onTab = curBase();
  // one row per machine: the machine label on the left, its tabs on the row, and a + that opens right there
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
            ? `${lv.length ? `<span class="lv" title="${esc(lv.map(levelName).reverse().join(' → '))} → executor">⇅${lv.length}</span>` : ''}${nl.length ? `<span class="lv lk" title="linked to ${esc(nl.map((k) => k.other).join(', '))}">🔗${nl.length}</span>` : ''}${n < 9 ? `<span class="kbd">⌘${n + 1}</span>` : ''}<span class="x" data-x="${esc(l)}" title="close agent">✕</span>`
            : a.tmux
              ? `<span class="cv" data-cv="${esc(l)}" title="convert: closes the claude in the terminal and reopens the conversation here">⇄</span>`
              : `<span class="small mute" title="pane of the old orq grid, without its own tmux">grid</span>`;
          const col = colorOf(a.account);
          const tip = `${a.dir || ''}\naccount ${LAB[a.account] || a.account || '?'}`;
          // the tab dot follows the state of the level shown in the chat (the top), not only that of the base
          const shown = lv.length && l === onTab ? agentOf(cur) || a : lv.length ? agentOf(lv[lv.length - 1]) || a : a;
          return `<div class="tab ${shown.state || ''} ${sdk ? '' : 'term'} ${l === onTab ? 'on' : ''}" data-l="${esc(l)}" title="${esc(tip)}" style="--acc:${col}">
          <span class="dot"></span><span class="meta"><b>${esc(l.split('@')[0])}</b>${sdk ? '' : ' · <i>terminal</i>'}</span><i class="sw" style="background:${col}" title="${esc(LAB[a.account] || a.account || '')}"></i>${tail}</div>`;
        })
        .join('');
      return `<div class="trow"><span class="host" title="machine">${esc(h)}</span><div class="tline">${row}<div class="tab plus" data-host="${esc(h)}" title="open project on ${esc(h)}">+</div></div></div>`;
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
    (lv.length ? agentApi(l, 'downgrade') : Promise.resolve()).then(() => agentApi(l, 'stop')).then(() => toast('closing ' + l));
    t._confirm = false;
    return;
  }
  if (t) t._confirm = true;
  toast('click ✕ again to close ' + l.split('@')[0] + (levelsAbove(l).length ? ' and its levels' : ''));
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
/** Shows the tab of `l`. `exact` shows that label even if it is a level or a base with levels. */
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
  for (const k of chain) if (!tabs[k].es) stream(k); // all the levels stay connected: the wires come from all of them
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
  t.el.innerHTML = `<div class="inner"><div class="perm term"><div class="t">Claude session in the terminal</div>
    <div class="small mute">tmux <code>${esc(a.tmux || '?')}</code> · <i class="sw" style="background:${colorOf(a.account)}"></i>${esc(LAB[a.account] || a.account || '?')} · <code>${esc(a.dir || '')}</code>${
      a.msg ? ` · ${esc(a.msg)}` : ''
    }</div>
    <p>Its conversation does not go through the page. <b>Convert</b> sends <code>/exit</code> to it (or Ctrl-C, if it does not respond) and reopens the same conversation here, as an agent of the page. Nothing is lost: Claude Code writes every message to disk.</p>
    <div class="btns">${
      a.tmux
        ? '<button class="ok">⇄ Convert to the page</button>'
        : '<span class="small mute">pane of the old orq grid: close it in the terminal (q in the menu)</span>'
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
  toast(`converting ${l.split('@')[0]}… (closes the claude in the terminal and reopens it here)`);
  const j = await api('/api/agent/convert', { label: l });
  if (!j.label) return;
  toast(`${j.label} converted${j.how ? ` (closed by ${j.how})` : ''}`);
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
  // tabs whose agent closed (✕, /exit, claude exited, downgrade) or vanished from the list: they close the stream and go away
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
  const st8 = a && a.set ? ` · <span title="opened by the set ${esc(a.set)}">⊞ ${esc(a.set)}</span>` : '';
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
  $('lnk').innerHTML = nl ? `🔗 ${nl}` : '🔗<span class="lbl"> link</span>';
  $('lnk').title = nl ? 'links of this agent (click to see them; link to one more from the panel)' : 'link to another agent on the page (any machine): each can ask the other for things through SendMessage';
  outBadge(base);
  $('lvl').innerHTML = `⇅<span class="lbl"> ${has ? 'levels' : 'add level'}</span>`;
  $('lvl').title = has ? 'show/hide the levels panel' : 'add a teacher (or more levels) above this agent';
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

// ---------------------------------------------------------------- levels and wires panel
// It lives on the base tab: cards (one per level, from the top down, clickable to see the raw chat of that
// level) and the list of wires: each SendMessage leaving a level, marked "delivered" when it reaches the other.
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
    panel.innerHTML = `<div class="lhead"><b>levels</b><button id="down" class="ghost" title="closes the levels above; the tab goes back to talking to the executor">↓ undo</button></div>
    <div class="lcards"></div><div class="lhead"><b>wires</b><span class="small mute">messages between the levels</span></div>`;
    panel.appendChild(wiresOf(base).el);
    panel.querySelector('#down').onclick = () => askDowngrade(base);
    renderLevelCards(base);
  } else panel.innerHTML = '';
  renderSide(base);
}

// ---------------------------------------------------------------- side panel: levels or files
// The panel to the right of the base tab shows the levels (whenever they exist) or the list of files the agent
// shared (📂 opens it). Only one view at a time; the small tabs at the top switch between them.
const SIDE = { view: 'levels', files: false }; // requested view and whether the file list is open
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
    (hasLv ? `<button data-v="levels" class="${view === 'levels' ? 'on' : ''}">⇅ levels</button>` : '') +
    (nLinks ? `<button data-v="links" class="${view === 'links' ? 'on' : ''}">🔗 links <span class="n">${nLinks}</span></button>` : '') +
    `<button data-v="files" class="${view === 'files' ? 'on' : ''}">📂 files${n ? ` <span class="n">${n}</span>` : ''}</button>` +
    (view === 'files' ? '<button class="ghost sx" title="close the file list">✕</button>' : '');
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
/** Links view: one card per agent linked to `base` (click opens its tab; ✕ removes the link) and the wires. */
function renderLinks(base) {
  const box = $('links');
  const items = linksOf(base);
  box.innerHTML = `<div class="lhead"><b>links</b><button class="ghost more" title="link to one more agent">+ link</button></div>
    <div class="lcards">${items
      .map((lk) => {
        const a = agentOf(lk.other) || { label: lk.other };
        const st = a.state || '';
        return `<div class="lcard link ${st}" data-l="${esc(lk.other)}" title="${esc(lk.other)}: open its tab"><div class="lrow"><span class="dot"></span><b>${esc(lk.other.split('@')[0])}</b><span class="small mute">${esc(hostOf(lk.other))}${lk.kind === 'ssh' ? ' · ssh' : ''} · ${esc(ST[st] || st)}</span><button class="ghost ul" data-u="${esc(lk.other)}" title="remove the link">✕</button></div></div>`;
      })
      .join('')}</div><div class="lhead"><b>wires</b><span class="small mute">requests and replies through SendMessage</span></div>`;
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
      toast(`link with ${other.split('@')[0]} removed`);
      dropLink(base, other);
      renderSide(base);
      renderTabs();
      status();
    });
    return;
  }
  t._unlink = other;
  toast(`click ✕ again to remove the link with ${other.split('@')[0]}`);
  setTimeout(() => (t._unlink = null), 4000);
}
/** Picks another agent on the page (any machine) to link to `base`. */
function openLinkPicker(base) {
  const linked = new Set(linksOf(base).map((lk) => lk.other));
  const chain = new Set(chainOf(base));
  const rows = agents.filter((a) => live(a) && a.kind === 'sdk' && a.label !== base && !linked.has(a.label) && !chain.has(a.label) && !isLevel(a));
  picker(
    `Link ${base.split('@')[0]} to…`,
    'The two start seeing each other in ListAgents and asking each other for things through SendMessage (on different machines, orq stretches the socket over ssh). Each one receives a note saying how to talk to the other.',
    rows.length
      ? rows
          .map(
            (a) => `<div class="p" data-l="${esc(a.label)}"><span class="mute">${esc(hostOf(a.label))}</span><span class="path"><b>${esc(a.label.split('@')[0])}</b> <span class="mute">${esc(a.dir || '')}</span></span><span><i class="sw" style="background:${colorOf(a.account)}"></i>${esc(LAB[a.account] || a.account || '')}</span><span class="small mute">${esc(ST[a.state] || a.state || '')}</span></div>`,
          )
          .join('')
      : '<div class="mute">No other agent on the page to link to. Open another project with <b>+</b> first.</div>',
  );
  $('plist').querySelectorAll('.p').forEach((el) => {
    el.onclick = async () => {
      const to = el.dataset.l;
      $('picker').hidden = true;
      toast(`linking ${base.split('@')[0]} to ${to.split('@')[0]}…${hostOf(base) !== hostOf(to) ? ' (ssh tunnel)' : ''}`);
      const j = await agentApi(base, 'link', { to });
      if (!j.ok) return;
      if (j.link) {
        dropLink(base, to);
        links.push(j.link);
      }
      toast(`${base.split('@')[0]} ⇄ ${to.split('@')[0]} linked${(j.note_errors || []).length ? ' (the note did not reach one of them)' : ''}`);
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
/** Reads the _outbox/ of the base agent. A file that was not in the previous read becomes a notice and a highlight. */
async function loadFiles(base) {
  if (!base || agentOf(base)?.kind !== 'sdk') return;
  const fl = filesOf(base);
  if (fl.busy) return;
  fl.busy = true;
  try {
    const r = await fetch(`/api/agent/${encodeURIComponent(base)}/files`, { headers: H });
    if (r.status === 404) {
      fl.busy = false; // the server does not know the agent yet (it has just opened): left for the next time
      return;
    }
    const j = await r.json().catch(() => ({ err: 'invalid response' }));
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
          toast(`📂 ${base.split('@')[0]}: ${novo.length === 1 ? `new file ${novo[0].name}` : `${novo.length} new files`}`);
        }
      }
      fl.known = now;
    }
    fl.loaded = true;
  } catch (_) {
    /* server down; the previous list stays */
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
  const head = `<div class="lhead"><b>files</b><span class="small mute"><code title="${esc(dir)}">_outbox/</code> <button class="ghost rf" title="refresh the list">↻</button></span></div>`;
  let body;
  if (!fl.loaded) body = '<div class="small mute">loading…</div>';
  else if (fl.err && !fl.items.length) body = `<div class="small bad">${esc(fl.err)}</div>`;
  else if (!fl.items.length)
    body = `<div class="fempty"><div>📂</div><p>Nothing shared yet.</p><p class="small">Ask the agent to copy the file to <code>_outbox/</code> in the project folder; it shows up here to view or download.</p></div>`;
  else
    body = `<div class="flist">${fl.items
      .map((f) => {
        const k = fileKind(f.name);
        return `<div class="frow ${fl.fresh.has(f.name) ? 'fresh' : ''}" data-n="${esc(f.name)}" title="${k.view ? 'view' : 'download'} ${esc(f.name)}"><span class="fico">${k.icon}</span><div class="fmeta"><b>${esc(f.name)}</b><span class="small mute">${fmtBytes(f.size)} · ${ago(f.mtime)}</span></div><a class="fdl" href="${fileUrl(base, f.name, true)}" download="${esc(f.name)}" title="download">⬇</a></div>`;
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
  fl.fresh.clear(); // seen: the highlight stays until the next read
  outBadge(base);
}
/** Preview of a file: image, PDF (the browser's viewer), video, audio, text/markdown; the rest is downloaded. */
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
  v.querySelector('.vdl').hidden = false;
  const body = v.querySelector('.vbody');
  if (k.view === 'image') body.innerHTML = `<img src="${url}" alt="${esc(f.name)}">`;
  else if (k.view === 'pdf') body.innerHTML = `<iframe src="${url}" title="${esc(f.name)}"></iframe>`;
  else if (k.view === 'video') body.innerHTML = `<video src="${url}" controls autoplay></video>`;
  else if (k.view === 'audio') body.innerHTML = `<audio src="${url}" controls autoplay></audio>`;
  else if (f.size > 2 * 1024 * 1024) body.innerHTML = '<p class="small mute">text too large to show here; download it.</p>';
  else {
    body.innerHTML = '<div class="small mute">loading…</div>';
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
      .catch(() => (body.innerHTML = '<p class="small bad">could not read the file</p>'));
  }
}
// ---------------------------------------------------------------- panel of one account (usage history)
// Clicking the card of an account opens, in the same window as the file preview, the usage-hist of the account: the
// week up to the reset (with the ideal pace and the projection), the last 24 h by hour, the 7 days and what spent the most.
const ptsTxt = (x, d = 1) => (x == null ? '—' : x.toFixed(d));
async function openAccountPanel(name) {
  const v = $('view');
  v.hidden = false;
  v.querySelector('.vname').textContent = `Panel · ${LAB[name] || name}`;
  v.querySelector('.vsize').textContent = '';
  v.querySelector('.vdl').hidden = true;
  const body = v.querySelector('.vbody');
  body.innerHTML = '<div class="apanel"><div class="small mute">loading…</div></div>';
  const d = await api(`/api/conta/${encodeURIComponent(name)}`);
  if (v.hidden || !v.querySelector('.vname').textContent.endsWith(LAB[name] || name)) return;
  if (d.err) {
    body.innerHTML = `<div class="apanel"><p class="small bad">${esc(d.err)}</p></div>`;
    return;
  }
  body.innerHTML = `<div class="apanel" style="--acc:${colorOf(name)}">${accountPanelHtml(d)}</div>`;
  body.querySelectorAll('.ptabs button').forEach(
    (b) =>
      (b.onclick = () => {
        body.querySelectorAll('.ptabs button').forEach((x) => x.classList.toggle('on', x === b));
        body.querySelectorAll('.ptable').forEach((x) => (x.hidden = x.dataset.p !== b.dataset.p));
      }),
  );
}
// ---------------------------------------------------------------- "usage" tab: who is spending each account
// The usage-sessoes table, from the usage-hist database (GET /api/uso?horas=5|24|168), in the same window as the
// panel. A row of an agent that is open here leads to its tab.
const normDir = (d) => (d || '').replace(/\/+$/, '');
const agentAt = (host, cwd) => {
  const a = agents.find((x) => live(x) && !isLevel(x) && x.host === host && x.dir && normDir(x.dir) === normDir(cwd));
  return a ? a.label : null;
};
async function openUsageView(hours) {
  const v = $('view');
  v.hidden = false;
  v.querySelector('.vname').textContent = 'Usage · who is spending each account';
  v.querySelector('.vsize').textContent = '';
  v.querySelector('.vdl').hidden = true;
  const body = v.querySelector('.vbody');
  const tabs = [5, 24, 168].map((h) => `<button class="ghost ${h === hours ? 'on' : ''}" data-h="${h}">${h === 168 ? '7 days' : `${h} h`}</button>`).join('');
  body.innerHTML = `<div class="apanel"><div class="ptabs">${tabs}</div><div class="uwrap small mute">loading…</div></div>`;
  body.querySelectorAll('.ptabs button').forEach((b) => (b.onclick = () => openUsageView(Number(b.dataset.h))));
  const d = await api(`/api/uso?horas=${hours}`);
  if (v.hidden || !v.querySelector('.vname').textContent.startsWith('Usage')) return;
  const wrap = body.querySelector('.uwrap');
  if (!wrap) return;
  if (d.err) {
    wrap.innerHTML = `<p class="small bad">${esc(d.err)}</p>`;
    return;
  }
  wrap.classList.remove('small', 'mute');
  const down = Object.keys(d.errors || {});
  const when = d.collected ? new Date(d.collected).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '?';
  wrap.innerHTML =
    usageTableHtml(d, { color: colorOf, agentFor: agentAt }) +
    `<p class="small mute">share: each row's slice of the account's usage in the window · ≈pts: weekly points estimated from the tokens · ●: responded in the last 10 min · highlighted rows: agents open here (click to go to the tab)</p>
     <p class="small mute">collected at ${esc(when)}${down.length ? ` · no response: ${esc(down.join(', '))}` : ''}</p>`;
  wrap.querySelectorAll('tr.go').forEach(
    (tr) =>
      (tr.onclick = () => {
        closeView();
        show(tr.dataset.l);
      }),
  );
}
function accountPanelHtml(d) {
  const w7 = d['7d'] || {};
  const w5 = d['5h'] || {};
  const star = (p) => (p ? '*' : '');
  const resetIn = w7.resets_at ? left(Math.max(0, (new Date(w7.resets_at) - Date.now()) / 1e3)) : '';
  const kpis = [
    ['week', `${ptsTxt(w7.pct, 0)}%`, resetIn ? `reset in ${resetIn}` : ''],
    ['5 h', `${ptsTxt(w5.pct, 0)}%`, w5.resets_at ? `reset in ${left(Math.max(0, (new Date(w5.resets_at) - Date.now()) / 1e3))}` : 'window not started'],
    ['today', `+${ptsTxt(d.today_7d)}${star(d.today_partial)}`, 'weekly points'],
    ['pace', `${ptsTxt(d.rate_6h, 2)}${star(d.rate_partial)}`, 'points/h, last 6 h'],
    ['forecast', d.hours_to_100 == null ? '—' : left(Math.round(d.hours_to_100 * 3600)), d.hours_to_100 == null ? 'pace too low to forecast' : d.before_reset ? '⚠ until 100%, before the reset' : 'until 100%, after the reset'],
  ]
    .map(([k, val, sub]) => `<div class="kpi${k === 'forecast' && d.before_reset ? ' bad' : ''}"><span class="k">${k}</span>${val ? `<b>${esc(val)}</b>` : ''}<span class="s">${esc(sub)}</span></div>`)
    .join('');
  const rows = (list) =>
    (list || []).length
      ? `<table><tr><th>machine</th><th>folder</th><th>type</th><th class="r">sessions</th><th class="r">≈pts</th></tr>${list
          .map(
            (r) =>
              `<tr><td>${esc(r.host)}</td><td title="${esc(r.folder)}">${esc(r.folder)}</td><td>${esc(r.kind)}</td><td class="r">${r.sessions}</td><td class="r">${r.pts == null ? '?' : ptsTxt(r.pts)}</td></tr>`,
          )
          .join('')}</table>`
      : '<p class="small mute">nothing in the period</p>';
  const cal = d.calibration && d.calibration.tokens_per_point;
  const notes = [
    cal ? `1 point ≈ ${(cal / 1e6).toFixed(2)}M weighted tokens (calibration with ${ptsTxt(d.calibration.points)} points over 7 days)` : '≈pts shows up once the calibration has 3 sample points',
    d.first_sample ? `quota collected since ${new Date(d.first_sample).toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' })}; * = since collection started` : '',
  ].filter(Boolean);
  return `<div class="kpis">${kpis}</div>
    ${(d.alerts || []).map((a) => `<div class="palert">${esc(a)}</div>`).join('')}
    <h4>week up to the reset <span class="mute small">— solid line: % used · gray dashes: window pace · colored dashes: projection at the current pace</span></h4>
    ${weekChart(d.week, d.now, d.rate_6h, window.innerWidth < 600 ? { w: 360, h: 220 } : undefined)}
    <div class="pcols"><div><h4>last 24 h <span class="mute small">points per hour</span></h4>${hourChart(d.hourly_24h || [], d.t24)}</div>
      <div><h4>last 7 days <span class="mute small">points per day</span></h4>${dayChart(d.days_7 || [])}</div></div>
    <h4>what spent the most</h4>
    <div class="ptabs"><button class="ghost on" data-p="24">24 h</button><button class="ghost" data-p="7">7 days</button></div>
    <div class="ptable" data-p="24">${rows(d.projects_24h)}</div><div class="ptable" data-p="7" hidden>${rows(d.projects_7d)}</div>
    ${notes.map((n) => `<p class="small mute">${esc(n)}</p>`).join('')}`;
}
function closeView() {
  const v = $('view');
  v.hidden = true;
  v.querySelector('.vbody').innerHTML = ''; // stops the video and releases the connection
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
      // permission pending on a level that is not in the chat: answer right here (a question opens its chat)
      const ask = pend
        ? pend.ask
          ? `<div class="lask"><span class="small">question: ${esc((pend.request.input?.questions || [])[0]?.question || '').slice(0, 80)}</span><button class="go">answer</button></div>`
          : `<div class="lask"><code>${esc(toolName(pend.request.tool_name))} ${esc(toolSummary(pend.request.input)).slice(0, 80)}</code><span class="lbtns"><button data-ok="1">Allow</button><button data-ok="0">Deny</button></span></div>`
        : '';
      return `<div class="lcard ${st} ${l === cur ? 'on' : ''}" data-l="${esc(l)}" style="--lc:${levelColor(l)}" title="${esc(l)}: see the raw chat of this level">
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
      toast(`levels closed: ${j.stopped.map(levelName).join(', ')}`);
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
  toast('click ↓ undo again to close the levels (the executor keeps going)');
  setTimeout(() => (t._down = false), 4000);
}
/** A wire: SendMessage leaving `from` (wireOut) or arriving (wireIn); the same message becomes "delivered". */
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
    el.innerHTML = `<div class="wh"><b>${esc(levelName(from))}</b><span>→ ${esc(to)}</span><span class="st" title="sent">…</span></div>${
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
      sent.querySelector('.st').title = 'delivered to ' + levelName(from);
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
  if (a.kind === 'permission' && !a.replayed) needsYou(lab, 'asks permission', toolName(a.request.tool_name) + ' ' + toolSummary(a.request.input));
  else if (a.kind === 'ask' && !a.replayed) needsYou(lab, 'question', (a.request.input?.questions || [])[0]?.question);
  else if (a.kind === 'result' && !t.a?.replaying) needsYou(lab, a.isError ? 'error' : 'finished', a.text || t.lastText || '');
  if (a.kind === 'text') t.lastText = a.text;
  if (a.kind === 'result' && !t.a?.replaying) setTimeout(() => loadFiles(baseOf(lab)), 800); // the round may have left a file in _outbox
  switch (a.kind) {
    case 'sys':
      add(t, `<div class="sys ${esc(a.level)}">${esc(a.text)}</div>`);
      if (/agent closed|claude exited|agente encerrado|claude saiu/.test(a.text)) {
        setState(t, 'closed', '');
        setTimeout(refresh, 1500);
      }
      return;
    case 'interrupted':
      return add(t, `<div class="sys">interrupted by you</div>`);
    case 'permissionAnswered': {
      if (t.pend?.requestId === a.requestId) t.pend = null;
      renderLevelCards(baseOf(t.a?.label || cur));
      const p = t.perms[a.requestId];
      if (!p) return;
      p.classList.add('done');
      const ask = p.classList.contains('ask');
      const txt = a.behavior === 'allow' ? (ask ? 'answered' : 'allowed') : ask ? 'canceled' : 'denied';
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
      // in the chat of this level: a "from <who>" chip; in the panel: delivery of the wire
      const ch = add(
        t,
        `<div class="chip peer"><div class="head"><b>from ${esc(a.from)}</b><span class="sum">${esc(firstLine(a.text))}</span><span class="st">▸</span></div><pre></pre></div>`,
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
        if (!a.isError && msgId) return; // the JSON of the send is of no interest; the chip keeps the message
      }
      ch.querySelector('pre').textContent = a.text || '(no output)';
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

// ---------------------------------------------------------------- permissions
function permCard(t, a) {
  const r = a.request;
  const p = add(
    t,
    `<div class="perm"><div class="t">Permission: ${esc(r.display_name || r.tool_name)}</div>${
      r.description ? `<div class="small mute">${esc(r.description)}</div>` : ''
    }<pre>${esc(permissionBody(r.input))}</pre><div class="btns"><button data-ok="1">Allow</button><button data-ok="0">Deny</button></div></div>`,
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
    `<div class="perm ask"><div class="t">The agent asks</div>${qs
      .map(
        (q, i) => `<div class="q"><div class="qh">${esc(q.header || '')}</div><div class="qt">${esc(q.question)}</div><div class="opts">${(
          q.options || []
        )
          .map((o) => opt(q, i, o.label, `<b>${esc(o.label)}</b>${o.description ? `<small>${esc(o.description)}</small>` : ''}`))
          .join('')}${opt(q, i, '__other__', '<input type="text" class="otxt" placeholder="Other…">')}</div></div>`,
      )
      .join('')}<div class="btns"><button class="ok">Answer</button><button class="no">Cancel</button></div></div>`,
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
    if (!answers) return toast('answer all the questions');
    agentApi(t.a.label, 'permission', { request_id: a.requestId, allow: true, input: { ...inp, answers } });
  };
  p.querySelector('.no').onclick = () => agentApi(t.a.label, 'permission', { request_id: a.requestId, allow: false });
}

// ---------------------------------------------------------------- attachments
let queue = []; // files chosen, not uploaded yet (File[])
function renderFiles() {
  const box = $('files');
  box.hidden = !queue.length;
  box.innerHTML = queue
    .map((f, i) => `<span class="fchip" title="${esc(f.name)}">📄 ${esc(f.name)} <small>${fmtBytes(f.size)}</small><b data-i="${i}" title="remove">✕</b></span>`)
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
    toast(`uploading ${f.name}…`);
    const r = await fetch(`/api/agent/${encodeURIComponent(label)}/upload`, {
      method: 'POST',
      headers: { 'X-Orq-Token': T, 'X-Filename': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' },
      body: f,
    });
    const j = await r.json().catch(() => ({}));
    if (j.err) {
      toast(`${f.name} failed: ${j.err}`);
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
document.addEventListener('drop', (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); if (cur) addFiles(e.dataTransfer.files); else toast('open a tab before dropping files'); } });

// ---------------------------------------------------------------- composer
async function send() {
  const typed = $('input').value.trim();
  if ((!typed && !queue.length) || !cur) return;
  const t = tabs[cur];
  let paths = [];
  if (queue.length) {
    paths = await uploadAll(cur);
    if (!paths) return; // failed: keeps the text and the queue to try again
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

// ---------------------------------------------------------------- open project / add level
function picker(title, hint, rowsHtml) {
  $('picker').hidden = false;
  $('ptitle').textContent = title;
  $('phint').innerHTML = hint;
  $('pnewrow').hidden = !rowsHtml ? false : true;
  $('plist').innerHTML = rowsHtml || 'loading…';
}
async function openPicker(host) {
  picker(host ? `Open project on ${host}` : 'Open project', 'Recent projects on all machines. Or a new folder: <code>machine:/path</code>');
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
      .join('') || '<div class="mute">no recent projects</div>';
  $('plist').querySelectorAll('.p').forEach((el) => (el.onclick = () => openAgent(JSON.parse(el.dataset.p), 'resume')));
}
async function openLevelPicker(base) {
  picker(
    `Levels above ${base.split('@')[0]}`,
    'Adds a level above (teacher etc.) on the same machine, folder and account. The tab starts talking to it; the current agent becomes the executor and receives the work through SendMessage.',
    '…',
  );
  if (agentOf(base) && !agentOf(base).name) {
    // opened before agents were started with -n: without a name SendMessage cannot find it. Reopens in the same conversation.
    $('plist').innerHTML = `<div class="mute">This agent was opened without a name and the levels would not be able to talk to it.
      <b>Reopen</b> closes and reopens the same conversation, now with a name (takes a few seconds).</div>
      <div class="prow"><button class="ok">↻ Reopen with a name</button></div>`;
    $('plist').querySelector('.ok').onclick = async () => {
      $('picker').hidden = true;
      toast(`reopening ${base.split('@')[0]}…`);
      const j = await agentApi(base, 'reopen');
      if (!j.label) return;
      if (tabs[base]) {
        tabs[base].el?.remove();
        delete tabs[base];
      }
      cur = null;
      await refresh();
      show(base);
      toast(`${base.split('@')[0]} reopened with a name; now ⇅ add level`);
    };
    return;
  }
  const s = await api('/api/pipelines');
  $('plist').innerHTML =
    (s.items || [])
      .map(
        (p) => {
          const nl = p.multi ? linksOf(base).length : 0;
          const tail = p.multi ? (nl ? ` → ${nl + 1} executors (this one + ${nl} linked)` : ' → executor (none linked yet: 🔗 first)') : ' → executor';
          return `<div class="p lvp" data-id="${esc(p.id)}"><span class="path"><b>${esc(p.titulo)}</b></span><span class="mute small">${esc(
            p.niveis.join(' → '),
          )}${esc(tail)}</span><span class="ttl">${esc(p.descricao)}</span></div>`;
        },
      )
      .join('') || '<div class="mute">no recipe in orqweb/pipelines</div>';
  $('plist').querySelectorAll('.p').forEach((el) => (el.onclick = () => upgrade(base, el.dataset.id)));
}
async function upgrade(base, pipeline) {
  $('picker').hidden = true;
  toast(`adding levels above ${base.split('@')[0]}…`);
  const j = await agentApi(base, 'upgrade', { pipeline });
  if (!j.top) return;
  SIDE.view = 'levels'; // the panel shows the newly added levels, even if it was on the links
  const nl = (j.links || []).length;
  const le = Object.keys(j.link_errors || {}).length;
  toast(`${j.levels.map(levelName).reverse().join(' → ')} → ${nl ? `${nl + 1} executors` : 'executor'}; the tab talks to ${levelName(j.top)}${le ? ` (${le} link(s) failed)` : ''}`);
  await refresh();
  show(base);
}
async function openAgent(p, mode) {
  $('picker').hidden = true;
  toast(`opening ${p.cwd.split('/').pop() || p.cwd} on ${p.host}…`);
  const j = await api('/api/agent/open', { ...p, mode });
  if (j.label) {
    toast(`${j.label} · account ${j.acc} (${j.why})`);
    await refresh();
    show(j.label);
  }
}
// ---------------------------------------------------------------- sets
// ⊞ at the top: a set is a JSON (in ~/.config/orq-web/conjuntos, on the server) with several agents to
// open at once, each with its own prompt and task, with or without a lead teacher. The server opens them in a
// thread; the progress arrives through /api/state (conjuntos_abrindo) and becomes a toast here.
const SETS = { seen: {} }; // id -> last step seen, to announce each step once
const setApi = (id, op, body) => api(`/api/conjuntos/${encodeURIComponent(id)}${op ? '/' + op : ''}`, body);
async function openSetsPicker() {
  picker(
    'Sets',
    'Several agents at once, each with its own prompt and task, with or without a lead teacher. Click to open; ⬇ downloads the JSON; ✕ deletes. If any agent of the set is already open, the page refuses and says which ones.',
    '…',
  );
  const s = await api('/api/conjuntos');
  if (s.err) return;
  const rows = (s.items || [])
    .map((c) => {
      const open = c.abertos.length;
      const tag = [`${c.n} agent${c.n === 1 ? '' : 's'}`, c.maquinas.join(', '), c.lider ? '👨‍🏫 teacher' : 'independent', c.permissao]
        .filter(Boolean)
        .join(' · ');
      return `<div class="p setp" data-id="${esc(c.id)}" title="open the set ${esc(c.nome)}">
        <span class="path"><b>${esc(c.nome)}</b> <span class="mute small">${esc(tag)}</span></span>
        <span class="small ${open ? 'bad' : 'mute'}">${open ? `${open} already open` : ''}</span>
        <span class="sbtns"><button class="ghost" data-dl="${esc(c.id)}" title="download the JSON">⬇</button><button class="ghost" data-del="${esc(c.id)}" title="delete the set">✕</button></span>
        ${c.descricao ? `<span class="ttl">${esc(c.descricao)}</span>` : ''}</div>`;
    })
    .join('');
  $('plist').innerHTML =
    `<div class="prow stools"><button id="setup">📄 upload JSON</button><button id="setsnap">📷 save open tabs</button><span class="mute small">folder: <code>${esc(s.dir)}</code></span></div>` +
    (rows || '<div class="mute">no saved sets. Upload a ready-made JSON or take a snapshot of the open tabs.</div>');
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
  if (j.err) return; // api() has already shown the reason (including "already open: …")
  $('picker').hidden = true;
  SETS.seen[id] = 'abrindo';
  toast(`⊞ ${j.nome}: opening ${j.agentes.map((a) => `${a.label.split('@')[0]} (${LAB[a.acc] || a.acc})`).join(', ')}…`);
}
const SET_STEPS = { abrindo: 'opening the agents…', vínculos: 'linking the executors…', professor: 'adding the teacher…', tarefas: 'sending the tasks…' };
function setsProgress(jobs) {
  for (const j of jobs || []) {
    const prev = SETS.seen[j.id];
    if (prev === j.etapa) continue;
    SETS.seen[j.id] = j.etapa;
    if (prev === undefined && j.etapa === 'pronto') continue; // finished before this page existed
    if (j.etapa !== 'pronto') {
      toast(`⊞ ${j.nome}: ${SET_STEPS[j.etapa] || j.etapa}`);
      continue;
    }
    const errs = Object.entries(j.erros || {});
    const n = j.labels.length;
    toast(
      `⊞ ${j.nome}: ${j.feitos.length} of ${n} agent${n === 1 ? '' : 's'} up${j.top ? ', with a teacher' : ''}${
        errs.length ? `; ${errs.length} problem${errs.length > 1 ? 's' : ''}: ${errs.map(([k, v]) => `${k}: ${v}`).join('; ').slice(0, 240)}` : ''
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
      toast(`set ${id} deleted`);
      openSetsPicker();
    });
    return;
  }
  el._confirm = true;
  toast(`click ✕ again to delete the set ${id}`);
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
      return toast(`${f.name}: invalid JSON (${e.message})`);
    }
    if (data && typeof data === 'object' && !data.nome) data.nome = f.name.replace(/\.json$/i, '');
    const j = await api('/api/conjuntos', data);
    if (j.err) return;
    toast(`set ${j.id} saved`);
    openSetsPicker();
  };
  inp.click();
}
async function snapshotSet() {
  const j = await api('/api/conjuntos/snapshot', {});
  if (j.err) return;
  const c = j.conjunto;
  $('phint').innerHTML =
    'The open tabs as a set: check the JSON, give it a name and save. Only the first teacher goes in as the lead (the other agents become its executors); the prompt only shows up for agents that were started with one.';
  $('plist').innerHTML = `<div class="prow"><input id="snapname" placeholder="set name" value="${esc(c.nome || '')}"><button id="snapsave">save</button><button class="ghost" id="snapback">back</button></div>
    ${(j.avisos || []).map((w) => `<div class="small bad">${esc(w)}</div>`).join('')}
    <textarea id="snapjson" rows="18" spellcheck="false">${esc(JSON.stringify(c, null, 2))}</textarea>`;
  $('snapback').onclick = openSetsPicker;
  $('snapsave').onclick = async () => {
    let data;
    try {
      data = JSON.parse($('snapjson').value);
    } catch (e) {
      return toast(`invalid JSON: ${e.message}`);
    }
    const nome = $('snapname').value.trim();
    if (nome) data.nome = nome;
    const r = await api('/api/conjuntos', data);
    if (r.err) return;
    toast(`set ${r.id} saved`);
    openSetsPicker();
  };
  $('snapname').focus();
}
$('pclose').onclick = () => ($('picker').hidden = true);
$('picker').onclick = (e) => e.target === $('picker') && ($('picker').hidden = true);
$('popen').onclick = () => {
  const m = $('pnew').value.trim().match(/^([\w.-]+):(.+)$/);
  if (!m) return toast('use machine:/path');
  openAgent({ host: m[1], cwd: m[2], account: null }, 'new');
};

// ---------------------------------------------------------------- overall state
async function refresh() {
  try {
    const s = await api('/api/state');
    if (s.err) return;
    LAB = s.labels;
    COL = s.colors || {};
    renderUsage(s.usage, s.history);
    showAlerts(s.history);
    agents = s.agents;
    links = s.links || [];
    for (const a of agents) {
      const t = tabs[a.label];
      if (!t) continue;
      if (t.pid && a.pid && t.pid !== a.pid && a.state !== 'closed') {
        // the supervisor was reopened (account switch, reopen with a name): the event log starts again from scratch
        if (t.es) t.es.close();
        t.el?.remove();
        const was = t.was || t.a?.account;
        delete tabs[a.label];
        if (was && a.account && was !== a.account) toast(`${a.label.split('@')[0]}: account ${LAB[was] || was} out of quota; continuing on ${LAB[a.account] || a.account}`);
        if (cur === a.label || chainOf(curBase() || '').includes(a.label)) setTimeout(() => show(curBase() || a.label, true), 0);
        continue;
      }
      if (t.a?.state === 'closed' && a.state !== 'closed' && (!a.pid || a.pid !== t.pid)) {
        // the stream said the agent was closed, but the list has it alive without a pid (or with another one): it
        // is being reopened (account switch, reopen with a name). The tab stays, 'opening…', until the new pid
        // arrives and the branch above recreates it from scratch; without this it vanished and the page jumped
        // to another tab
        t.was = t.was || t.a.account; // the previous account, for the switch notice
        t.a = a;
        continue;
      }
      if (a.pid) t.pid = a.pid;
      if (t.a?.state === 'closed') a.state = 'closed'; // closed by the page: the machine may lag behind
      else if (t.es && a.state !== 'closed' && t.a?.state && t.a.state !== 'starting') {
        // the tab knows the state from the events; polling the machines lags by up to 15 s. Exception:
        // 'opening…' is the page's provisional guess, and only the machine says when the agent is really ready
        a.state = t.a.state;
        a.msg = t.a.msg ?? a.msg;
      }
      // the list may bring the new account (provisional entry of the switch) before the new pid exists;
      // keep the previous one for the switch notice when the pid branch recreates the tab
      if (t.a?.account && a.account && a.account !== t.a.account) t.was = t.was || t.a.account;
      t.a = a;
    }
    dropClosed();
    const base = curBase();
    if (base && agentOf(base)?.kind === 'sdk') {
      // levels that showed up (upgrade done in another window, or newly added) join the connected chain
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
    /* server down; tries again on the next cycle */
  }
}
refresh();
setInterval(refresh, 5000);
