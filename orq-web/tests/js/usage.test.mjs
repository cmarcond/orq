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

test('usageTableHtml: one block per account with the quota, usage rows, the rest and the idle ones', () => {
  const html = usageTableHtml(data, { color: () => '#c00' });
  assert.equal((html.match(/<tbody>/g) || []).length, 2);
  assert.match(html, /<i class="sw"[^>]*><\/i>Gmail<\/span><span class="q">5h 40% · 7d 52%<\/span>/);
  assert.match(html, /<i class="sw"[^>]*><\/i>Work<\/span><span class="q">5h \? · 7d 40%<\/span>/);
  assert.match(html, /<td>acervo<\/td>|>acervo<\/td>/);
  assert.match(html, />9×</);
  assert.match(html, /width:56%/);
  assert.match(html, />≈6\.1</);
  assert.match(html, />\?</); // ≈pts without calibration
  assert.match(html, />tmux claude-gmail-acervo</);
  assert.match(html, /… 2 more/);
  assert.match(html, />idle<\/td>/);
  assert.match(html, />2 d 3 h ago</);
  assert.match(html, /no usage in the window/);
  assert.equal((html.match(/<td class="now">●<\/td>/g) || []).length, 1);
});

test('usageTableHtml: rows of open agents become a shortcut to the tab', () => {
  const html = usageTableHtml(data, { agentFor: (host, cwd) => (host === 'node0' && cwd === '/home/c/acervo' ? 'acervo@node0' : null) });
  assert.equal((html.match(/class="go"/g) || []).length, 1);
  assert.match(html, /data-l="acervo@node0" title="open the tab of acervo@node0"/);
});

test('usageTableHtml: no accounts, no data, and escapes text', () => {
  assert.match(usageTableHtml({ accounts: [] }), /no accounts/);
  assert.match(usageTableHtml(null), /no accounts/);
  const evil = { accounts: [{ account: 'x', label: '<b>x</b>', quota: {}, groups: [], rest: null, stale: [] }] };
  assert.ok(!usageTableHtml(evil).includes('<b>x</b>'));
});
