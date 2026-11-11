import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  clean,
  firstLine,
  internal,
  isMachine,
  originLabel,
  permissionBody,
  toolName,
  toolSummary,
} from '../../orqweb/server/static/js/classify.js';

test('internal recognizes Claude Code records that are not messages', () => {
  for (const s of [
    '<local-command-caveat>The command below…',
    '  <command-name>/exit</command-name>',
    '<local-command-stdout>Bye!</local-command-stdout>',
    '<task-notification> <task-id>x</task-id>',
    '<system-reminder>…',
    '<artifact-view-context artifact="x">',
  ])
    assert.equal(internal(s), true, s);
  assert.equal(internal('oi <b>x</b>'), false);
  assert.equal(internal(''), false);
  assert.equal(internal(undefined), false);
});

test('clean replaces pasted_content with 📋 and removes system-reminder', () => {
  assert.equal(clean('see <pasted_content id="a1">pasted text</pasted_content> end'), 'see 📋 pasted text end');
  assert.equal(clean('a <system-reminder>\nsecret\n</system-reminder> b'), 'a  b');
  assert.equal(clean(null), '');
});

test('isMachine: the supervisor stamp decides', () => {
  assert.equal(isMachine({ orq_origin: 'injected' }), true);
  assert.equal(isMachine({ orq_origin: 'page', origin: { kind: 'cron' } }), false);
});

test('isMachine: origin.kind other than human is a machine', () => {
  assert.equal(isMachine({ origin: { kind: 'cron' } }), true);
  assert.equal(isMachine({ origin: { kind: 'human' } }), false);
});

test('isMachine: on replay, no mark means machine; live, it does not', () => {
  assert.equal(isMachine({ replay: true }), true);
  assert.equal(isMachine({ replay: true, promptSource: 'sdk' }), false);
  assert.equal(isMachine({}), false);
});

test('originLabel', () => {
  assert.equal(originLabel('cron'), 'scheduled');
  assert.equal(originLabel('loop'), 'loop');
  assert.equal(originLabel(undefined), 'system');
  assert.equal(originLabel('xyz'), 'xyz');
});

test('toolSummary picks the most useful field and collapses whitespace', () => {
  assert.equal(toolSummary({ command: 'ls   -la\n  /x' }), 'ls -la /x');
  assert.equal(toolSummary({ file_path: '/a/b.py', content: 'x' }), '/a/b.py');
  assert.equal(toolSummary({ pattern: 'TODO' }), 'TODO');
  assert.equal(toolSummary({ foo: 1, bar: 'text' }), 'text');
  assert.equal(toolSummary({}), '');
  assert.equal(toolSummary(null), '');
  assert.equal(toolSummary({ command: 'x'.repeat(200) }).length, 140);
});

test('permissionBody: command, file with content, edit with diff, or JSON', () => {
  assert.equal(permissionBody({ command: 'rm x' }), 'rm x');
  assert.equal(permissionBody({ file_path: '/f', content: 'abc' }), '/f\n\nabc');
  assert.equal(permissionBody({ file_path: '/f', old_string: 'a', new_string: 'b' }), '/f\n\n- a\n+ b');
  assert.equal(permissionBody({ url: 'http://x' }), JSON.stringify({ url: 'http://x' }, null, 1));
});

test('firstLine and toolName', () => {
  assert.equal(firstLine('\n\n### Progress notice\nbody'), 'Progress notice');
  assert.equal(firstLine('a'.repeat(200)).length, 160);
  assert.equal(toolName('mcp__Claude_Browser__navigate'), 'Claude_Browser__navigate');
  assert.equal(toolName('Bash'), 'Bash');
});
