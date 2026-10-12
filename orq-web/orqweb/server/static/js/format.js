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
