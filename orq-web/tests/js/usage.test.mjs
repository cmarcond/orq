import { test } from 'node:test';
import assert from 'node:assert/strict';
import { usageTableHtml } from '../../orqweb/server/static/js/usage.js';

const data = {
  hours: 5,
  accounts: [
    {
      account: 'gmail',
      label: 'Gmail',
      quota: { '5h': 40, '7d': 52.4 },
      groups: [
        { host: 'node0', folder: 'acervo', cwd: '/home/c/acervo', kind: 'interativo', n: 1, first: '2026-10-23T18:31:00Z', last: '2026-10-23T22:28:00Z', share: 0.56, pts: 6.1, now: true, live: [{ pid: 7, kind: 'interativo', tmux: 'claude-gmail-acervo' }] },
        { host: 'laptop', folder: 'claude-diario', cwd: '/Users/c/claude-diario', kind: 'script', n: 9, first: '2026-10-23T17:06:00Z', last: '2026-10-23T17:53:00Z', share: 0.44, pts: null, now: false, live: [] },
      ],
      rest: { n: 2, share: 0.01, w: 10, pts: 0.1 },
      stale: [{ host: 'node4', folder: 'mycluster', cwd: '/home/c/mycluster', pid: 9, kind: 'interativo', tmux: '', idle_s: 2 * 86400 + 3 * 3600 }],
    },
    { account: 'work', label: 'Work', quota: { '5h': null, '7d': 40 }, groups: [], rest: null, stale: [] },
  ],
};

test('usageTableHtml: um bloco por conta com a cota, linhas de consumo, resto e parados', () => {
  const html = usageTableHtml(data, { color: () => '#c00' });
  assert.equal((html.match(/<tbody>/g) || []).length, 2);
  assert.match(html, /<i class="sw"[^>]*><\/i>Gmail<\/span><span class="q">5h 40% · 7d 52%<\/span>/);
  assert.match(html, /<i class="sw"[^>]*><\/i>Work<\/span><span class="q">5h \? · 7d 40%<\/span>/);
  assert.match(html, /<td>acervo<\/td>|>acervo<\/td>/);
  assert.match(html, />9×</);
  assert.match(html, /width:56%/);
  assert.match(html, />≈6,1</);
  assert.match(html, />\?</); // ≈pts sem calibração
  assert.match(html, />tmux claude-gmail-acervo</);
  assert.match(html, /… mais 2/);
  assert.match(html, />parado<\/td>/);
  assert.match(html, />há 2 d 3 h</);
  assert.match(html, /sem consumo na janela/);
  assert.equal((html.match(/<td class="now">●<\/td>/g) || []).length, 1);
});

test('usageTableHtml: linhas de agentes abertos viram atalho para a aba', () => {
  const html = usageTableHtml(data, { agentFor: (host, cwd) => (host === 'node0' && cwd === '/home/c/acervo' ? 'acervo@node0' : null) });
  assert.equal((html.match(/class="go"/g) || []).length, 1);
  assert.match(html, /data-l="acervo@node0" title="abrir a aba de acervo@node0"/);
});

test('usageTableHtml: sem contas, sem dados, e escapa texto', () => {
  assert.match(usageTableHtml({ accounts: [] }), /nenhuma conta/);
  assert.match(usageTableHtml(null), /nenhuma conta/);
  const evil = { accounts: [{ account: 'x', label: '<b>x</b>', quota: {}, groups: [], rest: null, stale: [] }] };
  assert.ok(!usageTableHtml(evil).includes('<b>x</b>'));
});
