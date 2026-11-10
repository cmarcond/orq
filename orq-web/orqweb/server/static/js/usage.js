// Aba "uso": quem está gastando cada conta (usage-hist uso --json), em HTML. Puro: recebe os dados e uma função
// que diz se uma máquina + pasta é um agente aberto na página (a linha vira atalho para a aba dele).
import { esc } from './format.js';

const pct = (x) => (x == null ? '?' : `${Math.round(x)}%`);
const pts = (x) => (x == null ? '?' : `≈${x.toFixed(1).replace('.', ',')}`);
const pad2 = (n) => String(n).padStart(2, '0');
const hhmm = (iso) => {
  const d = new Date(iso);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};
const ago = (s) => (s < 3600 ? `${Math.floor(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)} h ${pad2(Math.floor((s % 3600) / 60))}` : `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`);
const barCell = (share) => `<span class="ubar"><i style="width:${Math.round(Math.min(1, share) * 100)}%"></i></span> ${Math.round(share * 100)}%`;

/** Descrição curta do processo vivo de uma linha: "tmux X" ou "pid N script". */
function liveText(live) {
  if (!live || !live.length) return '';
  const p = live[0];
  return p.tmux ? `tmux ${p.tmux}` : `pid ${p.pid} ${p.kind}`;
}

/** A tabela: um bloco por conta (nome e cota na primeira linha), as linhas de consumo e as dos processos parados.
 *  `opts.color(conta)` dá a cor; `opts.agentFor(host, cwd)` devolve o rótulo de um agente aberto ou null. */
export function usageTableHtml(data, opts = {}) {
  const color = opts.color || (() => 'var(--mute)');
  const agentFor = opts.agentFor || (() => null);
  const accounts = (data && data.accounts) || [];
  if (!accounts.length) return '<p class="small mute">nenhuma conta no histórico</p>';
  const body = accounts
    .map((a) => {
      const rows = [];
      for (const g of a.groups || []) {
        const label = agentFor(g.host, g.cwd);
        rows.push(`<tr class="${label ? 'go' : ''}"${label ? ` data-l="${esc(label)}" title="abrir a aba de ${esc(label)}"` : ''}>
          <td>${esc(g.host)}</td><td class="fold" title="${esc(g.cwd || g.folder)}">${esc(g.folder)}</td><td>${esc(g.kind)}</td>
          <td class="r">${g.n > 1 ? `${g.n}×` : ''}</td><td class="t">${hhmm(g.first)}–${hhmm(g.last)}</td>
          <td class="share">${barCell(g.share)}</td><td class="r">${pts(g.pts)}</td>
          <td class="now">${g.now ? '●' : ''}</td><td class="live small mute">${esc(liveText(g.live))}</td></tr>`);
      }
      if (a.rest) rows.push(`<tr class="rest"><td></td><td>… mais ${a.rest.n}</td><td></td><td></td><td></td><td class="share">${barCell(a.rest.share)}</td><td class="r">${pts(a.rest.pts)}</td><td></td><td></td></tr>`);
      for (const s of a.stale || []) {
        const label = agentFor(s.host, s.cwd);
        rows.push(`<tr class="stale ${label ? 'go' : ''}"${label ? ` data-l="${esc(label)}" title="abrir a aba de ${esc(label)}"` : ''}>
          <td>${esc(s.host)}</td><td class="fold" title="${esc(s.cwd || s.folder)}">${esc(s.folder)}</td><td>parado</td><td></td>
          <td class="t">há ${ago(s.idle_s)}</td><td></td><td></td><td></td><td class="live small mute">${esc(s.tmux ? `tmux ${s.tmux}` : `pid ${s.pid}`)}</td></tr>`);
      }
      if (!rows.length) rows.push('<tr class="empty"><td colspan="9" class="mute">sem consumo na janela</td></tr>');
      const head = `<tr class="acc" style="--acc:${color(a.account)}"><th colspan="9"><div class="ah"><span><i class="sw" style="background:${color(a.account)}"></i>${esc(a.label)}</span><span class="q">5h ${pct(a.quota && a.quota['5h'])} · 7d ${pct(a.quota && a.quota['7d'])}</span></div></th></tr>`;
      return `<tbody>${head}${rows.join('')}</tbody>`;
    })
    .join('');
  return `<table class="usage-table"><thead><tr><th>máquina</th><th>pasta</th><th>tipo</th><th class="r">n</th><th>horário</th><th>parte</th><th class="r">≈pts</th><th>●</th><th>processo</th></tr></thead>${body}</table>`;
}
