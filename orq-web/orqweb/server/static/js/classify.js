// Pure classification of the history messages: what is internal, what came from a machine, summaries.

/** Records that Claude Code writes as a user message but nobody typed. */
const INTERNAL =
  /^\s*<(local-command-caveat|command-name|local-command-stdout|task-notification|system-reminder|artifact-view-context)\b/;

export const internal = (s) => INTERNAL.test(s || '');

/** Strips pasted-text markup and system reminders. */
export function clean(s) {
  return String(s ?? '')
    .replace(/<pasted_content[^>]*>/g, '📋 ')
    .replace(/<\/pasted_content>/g, '')
    .replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '')
    .trim();
}

/**
 * Did the `user` message come from a machine (cron, loop, system)?
 * 1. the supervisor's stamp (orq_origin) decides;
 * 2. on replay: origin.kind ≠ human, or no mark at all (neither origin nor promptSource);
 * 3. live without a stamp: only origin.kind ≠ human.
 */
export function isMachine(e) {
  if (e.orq_origin) return e.orq_origin === 'injected';
  const kind = e.origin?.kind;
  if (kind && kind !== 'human') return true;
  return Boolean(e.replay) && !kind && !e.promptSource;
}

const ORIGEM = { cron: 'scheduled', scheduled: 'scheduled', loop: 'loop', task: 'task', hook: 'hook', sdk: 'automatic' };
export const originLabel = (kind) => ORIGEM[kind] || kind || 'system';

/** One line that summarizes the input of a tool: command, file, pattern, URL… */
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

/** Body of the permission card: command, or file + content/diff, or the JSON. */
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

/** First non-empty line, without the heading marker, for the summary of a machine message. */
export function firstLine(text) {
  const l = String(text).split('\n').find((x) => x.trim()) || String(text);
  return l.replace(/^#+\s*/, '').slice(0, 160);
}

export const toolName = (name) => String(name || '').replace(/^mcp__/, '');
