// Formatação pura: escape de HTML, tempos e Markdown sanitizado.

export const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const pad2 = (n) => String(n).padStart(2, '0');

/** Segundos até o reset -> "45min", "4h05", "3d12h". null/undefined -> "". */
export function left(s) {
  if (s == null) return '';
  if (s < 3600) return `${Math.floor(s / 60)}min`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${pad2(Math.floor((s % 3600) / 60))}`;
  return `${Math.floor(s / 86400)}d${pad2(Math.floor((s % 86400) / 3600))}h`;
}

/** Instante (epoch s) -> "agora", "5 min", "3 h", "2 d". */
export function ago(ts, now = Date.now() / 1e3) {
  const s = now - ts;
  if (s < 60) return 'agora';
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

/**
 * Markdown -> HTML seguro. Usa marked + DOMPurify quando carregados (CDN); sem eles, texto
 * escapado com quebras de linha. `libs` permite injetar nos testes.
 */
export function md(text, libs = globalThis) {
  try {
    if (libs.marked && libs.DOMPurify) return libs.DOMPurify.sanitize(libs.marked.parse(text, { breaks: true }));
  } catch (_) {
    /* cai no texto puro */
  }
  return esc(text).replace(/\n/g, '<br>');
}

/** Mensagem final com os caminhos dos arquivos enviados para a _inbox do projeto. */
export function withAttachments(text, paths) {
  if (!paths || !paths.length) return text;
  const list = paths.map((p) => `- ${p}`).join('\n');
  const head = text.trim() || (paths.length === 1 ? 'Veja o arquivo enviado.' : 'Veja os arquivos enviados.');
  return `${head}\n\nArquivo(s) enviado(s) para a pasta _inbox do projeto:\n${list}`;
}

/** Tamanho legível: 812 B, 3,4 KB, 12 MB. */
export function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1).replace('.', ',')} KB`;
  return `${Math.round(n / 1024 / 1024)} MB`;
}

const KINDS = [
  [['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif'], '🖼', 'image'],
  [['pdf'], '📕', 'pdf'],
  [['mp4', 'webm', 'mov', 'm4v', 'ogv'], '🎬', 'video'],
  [['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'], '🎵', 'audio'],
  [['md', 'markdown'], '📝', 'text', true],
  [['txt', 'log', 'csv', 'tsv', 'json', 'yaml', 'yml', 'toml', 'xml', 'html', 'css', 'py', 'js', 'ts', 'sh', 'sql', 'r', 'tex'], '📄', 'text'],
  [['zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', '7z', 'rar'], '📦', null],
  [['xlsx', 'xls', 'ods', 'numbers'], '📊', null],
  [['docx', 'doc', 'odt', 'pages', 'pptx', 'ppt', 'key'], '📑', null],
];
/** Ícone e como mostrar um arquivo pelo nome: view = image|pdf|video|audio|text|null (null = só baixar). */
export function fileKind(name) {
  const ext = (String(name).toLowerCase().match(/\.([a-z0-9]+)$/) || [])[1] || '';
  const row = KINDS.find(([exts]) => exts.includes(ext));
  return row ? { icon: row[1], view: row[2], md: Boolean(row[3]) } : { icon: '📄', view: null, md: false };
}

// ---------------------------------------------------------------- histórico de uso (usage-hist painel --json)
const pts = (x, d = 1) => (x == null ? '—' : x.toFixed(d).replace('.', ','));

/** Duração curta em horas: 45min, 3h20, 2d4h. */
export function hours(h) {
  if (h == null) return '';
  return left(Math.round(h * 3600));
}

/** Barras das últimas 24 h (uma por hora, pontos da semana), em SVG, com o valor de cada hora no tooltip.
 *  `top` é a escala comum entre as contas; `t24` é o início da série (ISO), para rotular as horas. */
export function sparkSvg(values, top, t24) {
  const n = values.length || 1;
  const max = top > 0 ? top : Math.max(0, ...values);
  const w = 4;
  const gap = 1;
  const h = 16;
  const start = t24 ? new Date(t24) : null;
  const bars = values
    .map((v, i) => {
      const bh = max > 0 && v > 0.05 ? Math.max(2, Math.round((v / max) * h)) : 1;
      const hour = start ? new Date(start.getTime() + i * 3600e3).getHours() : i;
      const cls = v > 0.05 ? 'on' : 'off';
      return `<rect class="${cls}" x="${i * (w + gap)}" y="${h - bh}" width="${w}" height="${bh}"><title>${hour}h: ${pts(v)} pts</title></rect>`;
    })
    .join('');
  return `<svg class="spark" viewBox="0 0 ${n * (w + gap) - gap} ${h}" preserveAspectRatio="none" role="img" aria-label="consumo por hora nas últimas 24 h">${bars}</svg>`;
}

/** Linha curta de uma conta: hoje, ritmo e previsão. `warn` quando a semana acaba antes do reset. */
export function histSummary(a) {
  if (!a) return { text: '', warn: false };
  const star = (p) => (p ? '*' : '');
  const parts = [];
  if (a.today_7d != null) parts.push(`hoje +${pts(a.today_7d)}${star(a.today_partial)}`);
  if (a.rate_6h != null) parts.push(`${pts(a.rate_6h, 2)}/h${star(a.rate_partial)}`);
  if (a.hours_to_100 != null) parts.push(a.before_reset ? `⚠ 100% em ${hours(a.hours_to_100)}` : `100% em ${hours(a.hours_to_100)}`);
  return { text: parts.join(' · '), warn: Boolean(a.before_reset) };
}

/** Alertas novos (os com ⚠) que ainda não foram mostrados nas últimas `ttl` horas. `seen` é {texto: ms}. */
export function freshAlerts(alerts, seen, now = Date.now(), ttl = 6) {
  const keep = {};
  for (const [k, t] of Object.entries(seen || {})) if (now - t < ttl * 3600e3) keep[k] = t;
  const fresh = (alerts || []).filter((a) => a.startsWith('⚠') && !(a in keep));
  for (const a of fresh) keep[a] = now;
  return { fresh, seen: keep };
}

/** Selo do cartão de uma conta a partir dos alertas estruturados (`alert_items` do usage-hist): ⚠ em vermelho se
 *  houver alerta, · em cinza se só houver aviso; um alerta mostra o rótulo curto, vários mostram a contagem; o
 *  título (tooltip) traz o texto de todos. null sem nada para essa conta. */
export function badgeFor(items, account) {
  const mine = (items || []).filter((i) => i.account === account);
  if (!mine.length) return null;
  const warn = mine.filter((i) => i.level === 'warn');
  const shown = warn.length ? warn : mine;
  const sign = warn.length ? '⚠' : '·';
  return {
    level: warn.length ? 'warn' : 'info',
    label: shown.length === 1 ? `${sign} ${shown[0].short}` : `${sign} ${shown.length}`,
    title: mine.map((i) => i.text).join('\n'),
  };
}

/** Selo do TOTAL: quantos alertas ⚠ há em toda a frota (contas e coletor/máquinas); null sem nenhum. */
export function fleetBadge(items) {
  const warn = (items || []).filter((i) => i.level === 'warn');
  if (!warn.length) return null;
  return { level: 'warn', label: `⚠ ${warn.length} alerta${warn.length > 1 ? 's' : ''}`, title: warn.map((i) => i.text).join('\n') };
}
