import { test } from 'node:test';
import assert from 'node:assert/strict';
import { esc, left, ago, md, withAttachments, fmtBytes, fileKind } from '../../orqweb/server/static/js/format.js';

test('esc escapa tudo que vira HTML', () => {
  assert.equal(esc(`<a href="x">'&'</a>`), '&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;');
  assert.equal(esc(null), '');
  assert.equal(esc(12), '12');
});

test('left formata segundos até o reset', () => {
  assert.equal(left(null), '');
  assert.equal(left(45 * 60), '45min');
  assert.equal(left(4 * 3600 + 5 * 60), '4h05');
  assert.equal(left(3 * 86400 + 12 * 3600), '3d12h');
  assert.equal(left(0), '0min');
});

test('ago em faixas', () => {
  const now = 10_000;
  assert.equal(ago(now - 10, now), 'agora');
  assert.equal(ago(now - 300, now), '5 min');
  assert.equal(ago(now - 3 * 3600, now), '3 h');
  assert.equal(ago(now - 2 * 86400, now), '2 d');
});

test('md usa marked+DOMPurify quando existem e cai no texto escapado sem eles', () => {
  const libs = { marked: { parse: (t) => `<p>${t}</p><script>x</script>` }, DOMPurify: { sanitize: (h) => h.replace(/<script>.*<\/script>/, '') } };
  assert.equal(md('oi', libs), '<p>oi</p>');
  assert.equal(md('a <b>\nc', {}), 'a &lt;b&gt;<br>c');
});

test('md não propaga erro das bibliotecas', () => {
  const libs = { marked: { parse: () => { throw new Error('x'); } }, DOMPurify: { sanitize: (h) => h } };
  assert.equal(md('ok', libs), 'ok');
});

test('withAttachments acrescenta os caminhos e dá um texto padrão sem mensagem', () => {
  assert.equal(withAttachments('oi', []), 'oi');
  assert.equal(withAttachments('  ', ['/p/_inbox/a.pdf']), 'Veja o arquivo enviado.\n\nArquivo(s) enviado(s) para a pasta _inbox do projeto:\n- /p/_inbox/a.pdf');
  assert.match(withAttachments('resuma', ['/a', '/b']), /^resuma\n\n.*\n- \/a\n- \/b$/s);
});

test('fmtBytes', () => {
  assert.equal(fmtBytes(812), '812 B');
  assert.equal(fmtBytes(3500), '3,4 KB');
  assert.equal(fmtBytes(12 * 1024 * 1024), '12 MB');
});

test('fileKind decide ícone e prévia pela extensão', () => {
  assert.deepEqual(fileKind('Relatório Final.PDF'), { icon: '📕', view: 'pdf', md: false });
  assert.deepEqual(fileKind('foto.jpeg'), { icon: '🖼', view: 'image', md: false });
  assert.deepEqual(fileKind('notas.md'), { icon: '📝', view: 'text', md: true });
  assert.deepEqual(fileKind('dados.csv'), { icon: '📄', view: 'text', md: false });
  assert.deepEqual(fileKind('aula.mp4'), { icon: '🎬', view: 'video', md: false });
  assert.deepEqual(fileKind('tudo.tar.gz'), { icon: '📦', view: null, md: false });
  assert.deepEqual(fileKind('sem-extensao'), { icon: '📄', view: null, md: false });
});

import { hours, sparkSvg, histSummary, freshAlerts } from '../../orqweb/server/static/js/format.js';

test('hours formata horas em duração curta', () => {
  assert.equal(hours(null), '');
  assert.equal(hours(0.75), '45min');
  assert.equal(hours(3 + 20 / 60), '3h20');
  assert.equal(hours(52), '2d04h'); // o mesmo formato do left() da página
});

test('sparkSvg: uma barra por hora, escala comum, tooltip com a hora e os pontos', () => {
  const svg = sparkSvg([0, 1, 2, 0.5], 2, '2026-10-22T10:00:00Z');
  assert.equal((svg.match(/<rect /g) || []).length, 4);
  assert.match(svg, /viewBox="0 0 19 16"/);
  assert.match(svg, /class="on" x="10" y="0" width="4" height="16"><title>\d+h: 2,0 pts<\/title>/);
  assert.match(svg, /class="off" x="0" y="15" width="4" height="1"/);
  assert.match(svg, /height="8"><title>\d+h: 1,0 pts/);
  assert.ok(sparkSvg([0, 0], 0, null).includes('class="off"'));
});

test('histSummary: hoje, ritmo e previsão, com * nos parciais e ⚠ antes do reset', () => {
  assert.deepEqual(histSummary(null), { text: '', warn: false });
  const s = histSummary({ today_7d: 2.5, today_partial: true, rate_6h: 0.4, rate_partial: false, hours_to_100: 30, before_reset: true });
  assert.equal(s.text, 'hoje +2,5* · 0,40/h · ⚠ 100% em 1d06h');
  assert.equal(s.warn, true);
  assert.equal(histSummary({ today_7d: 0, rate_6h: null, hours_to_100: null }).text, 'hoje +0,0');
});

test('freshAlerts: só os ⚠, uma vez a cada ttl horas', () => {
  const now = 1_000_000_000;
  const a = ['⚠ Gmail: acaba', '· processo parado'];
  let r = freshAlerts(a, {}, now);
  assert.deepEqual(r.fresh, ['⚠ Gmail: acaba']);
  r = freshAlerts(a, r.seen, now + 3600e3);
  assert.deepEqual(r.fresh, []);
  r = freshAlerts(a, r.seen, now + 7 * 3600e3);
  assert.deepEqual(r.fresh, ['⚠ Gmail: acaba']);
});
