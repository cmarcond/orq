import { test } from 'node:test';
import assert from 'node:assert/strict';
import { esc, left, ago, md, withAttachments, fmtBytes } from '../../orqweb/server/static/js/format.js';

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
