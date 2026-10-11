// Classificação pura das mensagens do histórico: o que é interno, o que veio de máquina, resumos.

/** Registros que o Claude Code grava como mensagem do usuário mas ninguém digitou. */
const INTERNAL =
  /^\s*<(local-command-caveat|command-name|local-command-stdout|task-notification|system-reminder|artifact-view-context)\b/;

export const internal = (s) => INTERNAL.test(s || '');

/** Tira marcações de texto colado e lembretes do sistema. */
export function clean(s) {
  return String(s ?? '')
    .replace(/<pasted_content[^>]*>/g, '📋 ')
    .replace(/<\/pasted_content>/g, '')
    .replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '')
    .trim();
}

/**
 * Mensagem `user` veio de máquina (cron, loop, sistema)?
 * 1. carimbo do supervisor (orq_origin) manda;
 * 2. no replay: origin.kind ≠ human, ou nenhuma marca (nem origin nem promptSource);
 * 3. ao vivo sem carimbo: só origin.kind ≠ human.
 */
export function isMachine(e) {
  if (e.orq_origin) return e.orq_origin === 'injected';
  const kind = e.origin?.kind;
  if (kind && kind !== 'human') return true;
  return Boolean(e.replay) && !kind && !e.promptSource;
}

const ORIGEM = { cron: 'agendado', scheduled: 'agendado', loop: 'loop', task: 'tarefa', hook: 'hook', sdk: 'automático' };
export const originLabel = (kind) => ORIGEM[kind] || kind || 'sistema';

/** Uma linha que resume a entrada de uma ferramenta: comando, arquivo, padrão, URL… */
export function toolSummary(input) {
  const inp = input || {};
  const v =
    inp.command ||
    inp.file_path ||
    inp.pattern ||
    inp.url ||
    inp.query ||
    inp.description ||
    inp.prompt ||
    Object.values(inp).find((x) => typeof x === 'string') ||
    '';
  return String(v).replace(/\s+/g, ' ').slice(0, 140);
}

/** Corpo do cartão de permissão: comando, ou arquivo + conteúdo/diff, ou o JSON. */
export function permissionBody(input) {
  const inp = input || {};
  if (inp.command) return inp.command;
  if (inp.file_path) {
    let s = inp.file_path;
    if (inp.content) s += '\n\n' + String(inp.content).slice(0, 1500);
    if (inp.new_string) s += `\n\n- ${String(inp.old_string).slice(0, 600)}\n+ ${String(inp.new_string).slice(0, 600)}`;
    return s;
  }
  return JSON.stringify(inp, null, 1).slice(0, 1500);
}

/** Primeira linha não vazia, sem marcador de título, para o resumo de uma mensagem de máquina. */
export function firstLine(text) {
  const l = String(text).split('\n').find((x) => x.trim()) || String(text);
  return l.replace(/^#+\s*/, '').slice(0, 160);
}

export const toolName = (name) => String(name || '').replace(/^mcp__/, '');
