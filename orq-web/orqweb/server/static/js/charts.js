// Charts of the account panel (usage-hist history), in plain SVG. No DOM: each function returns the
// SVG text, for app.js to insert and for the Node tests to check. The colors come from the CSS (classes).

const fmt = (x, d = 1) => (x == null ? '—' : x.toFixed(d));
const t = (iso) => new Date(iso).getTime();
const pad2 = (n) => String(n).padStart(2, '0');
const hhmm = (ms) => {
  const d = new Date(ms);
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};
const r1 = (x) => Math.round(x * 10) / 10;

/** Curve of the weekly % in the current window (from the start to the reset), with:
 *  - the ideal pace line (0% at the start of the window, 100% at the reset), dashed;
 *  - the mark for now;
 *  - the projection at the current pace (`rate`, points/h) from the last sample, up to the reset or to 100%.
 *  `week` = {start, end, reset_at, series: [[iso, pct], …]}. */
export function weekChart(week, now, rate, { w = 640, h = 180 } = {}) {
  const L = 34;
  const R = 8;
  const T = 10;
  const B = 22;
  const x0 = t(week.start);
  const x1 = Math.max(t(week.end), x0 + 1);
  const X = (ms) => r1(L + ((Math.min(Math.max(ms, x0), x1) - x0) / (x1 - x0)) * (w - L - R));
  const Y = (p) => r1(T + (1 - Math.min(Math.max(p, 0), 100) / 100) * (h - T - B));
  const grid = [0, 25, 50, 75, 100]
    .map((p) => `<line class="grid${p === 100 ? ' top' : ''}" x1="${L}" x2="${w - R}" y1="${Y(p)}" y2="${Y(p)}"/><text class="ylab" x="${L - 4}" y="${Y(p) + 3}">${p}%</text>`)
    .join('');
  const pace = `<line class="pace" x1="${X(x0)}" y1="${Y(0)}" x2="${X(x1)}" y2="${Y(100)}"><title>window pace: 0% at the start, 100% at the reset</title></line>`;
  const pts = (week.series || []).map(([ts, p]) => [t(ts), p]);
  const line = pts.length ? `<polyline class="used" points="${pts.map(([ms, p]) => `${X(ms)},${Y(p)}`).join(' ')}"/>` : '';
  const dots = pts.length ? `<circle class="last" cx="${X(pts[pts.length - 1][0])}" cy="${Y(pts[pts.length - 1][1])}" r="3"><title>${fmt(pts[pts.length - 1][1], 0)}% at ${hhmm(pts[pts.length - 1][0])}</title></circle>` : '';
  let proj = '';
  if (pts.length && rate != null && rate > 0.01) {
    const [tl, pl] = pts[pts.length - 1];
    const hoursLeft = (x1 - tl) / 3600e3;
    const atEnd = pl + rate * hoursLeft;
    const hitMs = atEnd > 100 ? tl + ((100 - pl) / rate) * 3600e3 : null;
    const [ex, ep] = hitMs ? [hitMs, 100] : [x1, atEnd];
    proj = `<line class="proj${hitMs ? ' hit' : ''}" x1="${X(tl)}" y1="${Y(pl)}" x2="${X(ex)}" y2="${Y(ep)}"/>`;
    proj += hitMs
      ? `<text class="note hit" x="${X(hitMs)}" y="${Y(100) - 3}">100% ${hhmm(hitMs)}</text>`
      : `<text class="note" x="${X(x1) - 2}" y="${Y(ep) - 4}" text-anchor="end">${fmt(ep, 0)}% at reset</text>`;
  }
  const nowMs = t(now);
  const nowMark = nowMs > x0 && nowMs < x1 ? `<line class="now" x1="${X(nowMs)}" x2="${X(nowMs)}" y1="${T}" y2="${h - B}"/><text class="xlab now-lab" x="${X(nowMs) - 3}" y="${T + 9}">now</text>` : '';
  const ends = `<text class="xlab start" x="${L}" y="${h - 6}">${hhmm(x0)}</text><text class="xlab end" x="${w - R}" y="${h - 6}">${week.reset_at ? 'reset ' : ''}${hhmm(x1)}</text>`;
  return `<svg class="chart week" viewBox="0 0 ${w} ${h}" role="img" aria-label="weekly quota up to the reset">${grid}${pace}${line}${proj}${dots}${nowMark}${ends}</svg>`;
}

/** Bars of the last 24 h (weekly points per hour), with the hour of the day every 6 h on the axis. */
export function hourChart(values, t24, { w = 300, h = 120 } = {}) {
  const L = 26;
  const B = 18;
  const T = 8;
  const n = values.length || 1;
  const max = Math.max(0.5, ...values);
  const bw = (w - L) / n;
  const start = t24 ? t(t24) : 0;
  const bars = values
    .map((v, i) => {
      const bh = v > 0.05 ? Math.max(2, r1((v / max) * (h - T - B))) : 1;
      const hour = new Date(start + i * 3600e3).getHours();
      const lab = i % 6 === 0 ? `<text class="xlab" x="${r1(L + i * bw + bw / 2)}" y="${h - 5}">${hour}h</text>` : '';
      return `<rect class="${v > 0.05 ? 'on' : 'off'}" x="${r1(L + i * bw + 1)}" y="${r1(h - B - bh)}" width="${r1(bw - 2)}" height="${bh}"><title>${hour}h: ${fmt(v)} pts</title></rect>${lab}`;
    })
    .join('');
  return `<svg class="chart hours" viewBox="0 0 ${w} ${h}" role="img" aria-label="points per hour in the last 24 h"><text class="ylab" x="${L - 4}" y="${T + 6}">${fmt(max)}</text><line class="grid" x1="${L}" x2="${w}" y1="${h - B}" y2="${h - B}"/>${bars}</svg>`;
}

/** Bars of the last 7 days (weekly points per day), with the day below and the value above. */
export function dayChart(days, { w = 300, h = 120 } = {}) {
  const B = 18;
  const T = 14;
  const n = days.length || 1;
  const max = Math.max(1, ...days.map((d) => d.pts || 0));
  const bw = w / n;
  const bars = days
    .map((d, i) => {
      const v = d.pts;
      const bh = v ? Math.max(2, r1((v / max) * (h - T - B))) : 1;
      const x = r1(i * bw + bw * 0.15);
      const cx = r1(i * bw + bw / 2);
      return `<rect class="${v ? 'on' : 'off'}" x="${x}" y="${r1(h - B - bh)}" width="${r1(bw * 0.7)}" height="${bh}"><title>${d.label}: ${fmt(v)} pts</title></rect>
        <text class="vlab" x="${cx}" y="${r1(h - B - bh - 3)}">${v == null ? '' : fmt(v, v >= 10 ? 0 : 1)}</text><text class="xlab" x="${cx}" y="${h - 5}">${d.label.slice(0, 3)}</text>`;
    })
    .join('');
  return `<svg class="chart days" viewBox="0 0 ${w} ${h}" role="img" aria-label="points per day in the last 7 days">${bars}</svg>`;
}
