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

test('internal reconhece registros do Claude Code que não são mensagens', () => {
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

test('clean troca pasted_content por 📋 e remove system-reminder', () => {
  assert.equal(clean('veja <pasted_content id="a1">texto colado</pasted_content> fim'), 'veja 📋 texto colado fim');
  assert.equal(clean('a <system-reminder>\nsegredo\n</system-reminder> b'), 'a  b');
  assert.equal(clean(null), '');
});

test('isMachine: carimbo do supervisor manda', () => {
  assert.equal(isMachine({ orq_origin: 'injected' }), true);
  assert.equal(isMachine({ orq_origin: 'page', origin: { kind: 'cron' } }), false);
});

test('isMachine: origin.kind diferente de human é máquina', () => {
  assert.equal(isMachine({ origin: { kind: 'cron' } }), true);
  assert.equal(isMachine({ origin: { kind: 'human' } }), false);
});

test('isMachine: no replay, nenhuma marca é máquina; ao vivo, não', () => {
  assert.equal(isMachine({ replay: true }), true);
  assert.equal(isMachine({ replay: true, promptSource: 'sdk' }), false);
  assert.equal(isMachine({}), false);
});

test('originLabel', () => {
  assert.equal(originLabel('cron'), 'agendado');
  assert.equal(originLabel('loop'), 'loop');
  assert.equal(originLabel(undefined), 'sistema');
  assert.equal(originLabel('xyz'), 'xyz');
});

test('toolSummary escolhe o campo mais útil e compacta espaços', () => {
  assert.equal(toolSummary({ command: 'ls   -la\n  /x' }), 'ls -la /x');
  assert.equal(toolSummary({ file_path: '/a/b.py', content: 'x' }), '/a/b.py');
  assert.equal(toolSummary({ pattern: 'TODO' }), 'TODO');
  assert.equal(toolSummary({ foo: 1, bar: 'texto' }), 'texto');
  assert.equal(toolSummary({}), '');
  assert.equal(toolSummary(null), '');
  assert.equal(toolSummary({ command: 'x'.repeat(200) }).length, 140);
});

test('permissionBody: comando, arquivo com conteúdo, edição com diff, ou JSON', () => {
  assert.equal(permissionBody({ command: 'rm x' }), 'rm x');
  assert.equal(permissionBody({ file_path: '/f', content: 'abc' }), '/f\n\nabc');
  assert.equal(permissionBody({ file_path: '/f', old_string: 'a', new_string: 'b' }), '/f\n\n- a\n+ b');
  assert.equal(permissionBody({ url: 'http://x' }), JSON.stringify({ url: 'http://x' }, null, 1));
});

test('firstLine e toolName', () => {
  assert.equal(firstLine('\n\n### Aviso de andamento\ncorpo'), 'Aviso de andamento');
  assert.equal(firstLine('a'.repeat(200)).length, 160);
  assert.equal(toolName('mcp__Claude_Browser__navigate'), 'Claude_Browser__navigate');
  assert.equal(toolName('Bash'), 'Bash');
});
