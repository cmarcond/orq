import { test } from 'node:test';
import assert from 'node:assert/strict';
import { esc, left, ago, md, withAttachments, fmtBytes, fileKind } from '../../orqweb/server/static/js/format.js';

test('esc escapes everything that becomes HTML', () => {
  assert.equal(esc(`<a href="x">'&'</a>`), '&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;');
  assert.equal(esc(null), '');
  assert.equal(esc(12), '12');
});

test('left formats seconds until the reset', () => {
  assert.equal(left(null), '');
  assert.equal(left(45 * 60), '45min');
  assert.equal(left(4 * 3600 + 5 * 60), '4h05');
  assert.equal(left(3 * 86400 + 12 * 3600), '3d12h');
  assert.equal(left(0), '0min');
});

test('ago in ranges', () => {
  const now = 10_000;
  assert.equal(ago(now - 10, now), 'now');
  assert.equal(ago(now - 300, now), '5 min');
  assert.equal(ago(now - 3 * 3600, now), '3 h');
  assert.equal(ago(now - 2 * 86400, now), '2 d');
});

test('md uses marked+DOMPurify when they exist and falls back to escaped text without them', () => {
  const libs = { marked: { parse: (t) => `<p>${t}</p><script>x</script>` }, DOMPurify: { sanitize: (h) => h.replace(/<script>.*<\/script>/, '') } };
  assert.equal(md('oi', libs), '<p>oi</p>');
  assert.equal(md('a <b>\nc', {}), 'a &lt;b&gt;<br>c');
});

test('md does not propagate errors from the libraries', () => {
  const libs = { marked: { parse: () => { throw new Error('x'); } }, DOMPurify: { sanitize: (h) => h } };
  assert.equal(md('ok', libs), 'ok');
});

test('withAttachments appends the paths and gives a default text when there is no message', () => {
  assert.equal(withAttachments('oi', []), 'oi');
  assert.equal(withAttachments('  ', ['/p/_inbox/a.pdf']), "See the uploaded file.\n\nFile(s) uploaded to the project's _inbox folder:\n- /p/_inbox/a.pdf");
  assert.match(withAttachments('summarize', ['/a', '/b']), /^summarize\n\n.*\n- \/a\n- \/b$/s);
});

test('fmtBytes', () => {
  assert.equal(fmtBytes(812), '812 B');
  assert.equal(fmtBytes(3500), '3.4 KB');
  assert.equal(fmtBytes(12 * 1024 * 1024), '12 MB');
});

test('fileKind decides icon and preview from the extension', () => {
  assert.deepEqual(fileKind('Final Report.PDF'), { icon: '📕', view: 'pdf', md: false });
  assert.deepEqual(fileKind('photo.jpeg'), { icon: '🖼', view: 'image', md: false });
  assert.deepEqual(fileKind('notes.md'), { icon: '📝', view: 'text', md: true });
  assert.deepEqual(fileKind('data.csv'), { icon: '📄', view: 'text', md: false });
  assert.deepEqual(fileKind('lecture.mp4'), { icon: '🎬', view: 'video', md: false });
  assert.deepEqual(fileKind('everything.tar.gz'), { icon: '📦', view: null, md: false });
  assert.deepEqual(fileKind('no-extension'), { icon: '📄', view: null, md: false });
});

import { hours, sparkSvg, histSummary, freshAlerts } from '../../orqweb/server/static/js/format.js';

test('hours formats hours as a short duration', () => {
  assert.equal(hours(null), '');
  assert.equal(hours(0.75), '45min');
  assert.equal(hours(3 + 20 / 60), '3h20');
  assert.equal(hours(52), '2d04h'); // the same format as the page's left()
});

test('sparkSvg: one bar per hour, shared scale, tooltip with the hour and the points', () => {
  const svg = sparkSvg([0, 1, 2, 0.5], 2, '2026-10-22T10:00:00Z');
  assert.equal((svg.match(/<rect /g) || []).length, 4);
  assert.match(svg, /viewBox="0 0 19 16"/);
  assert.match(svg, /class="on" x="10" y="0" width="4" height="16"><title>\d+h: 2\.0 pts<\/title>/);
  assert.match(svg, /class="off" x="0" y="15" width="4" height="1"/);
  assert.match(svg, /height="8"><title>\d+h: 1\.0 pts/);
  assert.ok(sparkSvg([0, 0], 0, null).includes('class="off"'));
});

test('histSummary: today, pace and forecast, with * on the partial ones and ⚠ before the reset', () => {
  assert.deepEqual(histSummary(null), { text: '', warn: false });
  const s = histSummary({ today_7d: 2.5, today_partial: true, rate_6h: 0.4, rate_partial: false, hours_to_100: 30, before_reset: true });
  assert.equal(s.text, 'today +2.5* · 0.40/h · ⚠ 100% in 1d06h');
  assert.equal(s.warn, true);
  assert.equal(histSummary({ today_7d: 0, rate_6h: null, hours_to_100: null }).text, 'today +0.0');
});

test('freshAlerts: only the ⚠ ones, once every ttl hours', () => {
  const now = 1_000_000_000;
  const a = ['⚠ Gmail: runs out', '· idle process'];
  let r = freshAlerts(a, {}, now);
  assert.deepEqual(r.fresh, ['⚠ Gmail: runs out']);
  r = freshAlerts(a, r.seen, now + 3600e3);
  assert.deepEqual(r.fresh, []);
  r = freshAlerts(a, r.seen, now + 7 * 3600e3);
  assert.deepEqual(r.fresh, ['⚠ Gmail: runs out']);
});

import { badgeFor, fleetBadge } from '../../orqweb/server/static/js/format.js';

test('badgeFor: ⚠ with the short label, a count when there are several, · for a notice, null with nothing', () => {
  const items = [
    { level: 'warn', account: 'gmail', kind: 'semana', short: 'week', text: '⚠ Gmail: runs out before the reset' },
    { level: 'info', account: 'gmail', kind: 'processo', short: 'idle process', text: '· Gmail: idle process' },
    { level: 'info', account: 'work2', kind: 'processo', short: 'idle process', text: '· Work 2: idle process' },
    { level: 'warn', account: null, kind: 'coletor', short: 'collector', text: '⚠ collector stopped' },
  ];
  assert.deepEqual(badgeFor(items, 'gmail'), { level: 'warn', label: '⚠ week', title: '⚠ Gmail: runs out before the reset\n· Gmail: idle process' });
  assert.deepEqual(badgeFor(items, 'work2'), { level: 'info', label: '· idle process', title: '· Work 2: idle process' });
  assert.equal(badgeFor(items, 'work3'), null);
  assert.equal(badgeFor(undefined, 'gmail'), null);
  const two = [items[0], { ...items[0], kind: '5h', short: '5h' }];
  assert.equal(badgeFor(two, 'gmail').label, '⚠ 2');
});

test('fleetBadge counts only the ⚠ ones, from accounts and from the fleet', () => {
  assert.equal(fleetBadge([{ level: 'info', text: 'x' }]), null);
  const b = fleetBadge([{ level: 'warn', text: 'a' }, { level: 'warn', text: 'b' }, { level: 'info', text: 'c' }]);
  assert.deepEqual(b, { level: 'warn', label: '⚠ 2 alerts', title: 'a\nb' });
  assert.equal(fleetBadge([{ level: 'warn', text: 'a' }]).label, '⚠ 1 alert');
});
