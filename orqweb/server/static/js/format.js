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
