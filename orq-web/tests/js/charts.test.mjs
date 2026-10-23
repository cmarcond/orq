import { test } from 'node:test';
import assert from 'node:assert/strict';
import { weekChart, hourChart, dayChart } from '../../orqweb/server/static/js/charts.js';

const H = 3600e3;
const base = Date.parse('2026-10-18T00:00:00Z');
const iso = (ms) => new Date(ms).toISOString();
const week = (series, endH = 168) => ({ start: iso(base), end: iso(base + endH * H), reset_at: iso(base + endH * H), series });

test('weekChart: curva usada, ritmo ideal, agora e marca do reset', () => {
  const svg = weekChart(week([[iso(base + 24 * H), 10], [iso(base + 48 * H), 20]]), iso(base + 50 * H), null);
  assert.match(svg, /<polyline class="used" points="[\d.]+,[\d.]+ [\d.]+,[\d.]+"\/>/);
  assert.match(svg, /<line class="pace" x1="34" y1="158" x2="632" y2="10">/);
  assert.match(svg, /class="now"/);
  assert.match(svg, /class="xlab now-lab" x="[\d.]+" y="19">agora</); // no alto da linha, longe do rótulo do reset
  assert.match(svg, />reset \d\d\/\d\d \d\d:\d\d</);
  assert.ok(!svg.includes('class="proj'), 'sem ritmo, sem projeção');
});

test('weekChart: projeção até o reset ou até 100%', () => {
  const s = [[iso(base + 48 * H), 40]];
  const calm = weekChart(week(s), iso(base + 48 * H), 0.1); // 40 + 0,1 × 120 h = 52% no reset
  assert.match(calm, /<line class="proj" /);
  assert.match(calm, />52% no reset</);
  const fast = weekChart(week(s), iso(base + 48 * H), 2); // chega a 100% em 30 h, antes do reset
  assert.match(fast, /<line class="proj hit" /);
  assert.match(fast, /class="note hit"[^>]*>100% \d\d\/\d\d \d\d:\d\d</);
});

test('weekChart: série vazia não quebra', () => {
  const svg = weekChart({ start: iso(base), end: iso(base), reset_at: null, series: [] }, iso(base), 1);
  assert.ok(svg.startsWith('<svg') && !svg.includes('polyline') && !svg.includes('proj'));
});

test('hourChart: 24 barras, eixo a cada 6 h, escala pelo máximo', () => {
  const v = Array.from({ length: 24 }, (_, i) => (i === 23 ? 4 : 0));
  const svg = hourChart(v, iso(base));
  assert.equal((svg.match(/<rect /g) || []).length, 24);
  assert.equal((svg.match(/class="xlab"/g) || []).length, 4);
  assert.match(svg, />4,0<\/text>/);
  assert.equal((svg.match(/class="on"/g) || []).length, 1);
});

test('dayChart: um dia por barra, valor em cima, dia embaixo, — vira vazio', () => {
  const days = [{ label: 'qua 21/10', pts: null }, { label: 'qui 22/10', pts: 15 }, { label: 'sex 23/10', pts: 3.5 }];
  const svg = dayChart(days);
  assert.equal((svg.match(/<rect /g) || []).length, 3);
  assert.match(svg, />qua<\/text>/);
  assert.match(svg, /class="vlab"[^>]*>15<\/text>/);
  assert.match(svg, /class="vlab"[^>]*>3,5<\/text>/);
  assert.match(svg, /qui 22\/10: 15,0 pts/);
});
