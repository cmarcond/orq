"""Imitação do `claude -p --input-format stream-json --output-format stream-json` para os testes.

Fala o subconjunto do protocolo que o supervisor usa. O comportamento depende do texto da mensagem:
  "ferramenta"  -> tool_use Bash + pedido de permissão; allow => tool_result + "feito"; deny => "negado"
  "pergunte"    -> pedido de permissão AskUserQuestion; a resposta vem em updatedInput.answers
  "demore"      -> fica ocupado até receber um control_request interrupt (ou 5 s)
  "injete"      -> emite uma mensagem `user` sem marca de origem (como um cron faria)
  "esgote"      -> rate_limit_event rejected + result de erro de limite (a conta ficou sem cota)
  "delegue: X para NOME" -> tool_use SendMessage para NOME com a mensagem X (vai pela caixa de correio)
  qualquer outro -> stream_event palavra a palavra + texto "eco: ..." + result
Sempre ecoa a mensagem do usuário (--replay-user-messages) e responde interrupts com control_response.

Sessões entre si (como o SendMessage real): com `-n NOME` e FAKE_PEER_DIR no ambiente, cada processo
lê FAKE_PEER_DIR/<NOME>.inbox; uma linha lá vira um turno `user` com origin.kind == "peer" (o mesmo
formato do claude). Mensagem recebida que começa com "responda " é respondida a quem mandou via
SendMessage com o resto do texto; qualquer outra vira o texto "o peer disse: ...". `--append-system-prompt`
é só guardado e informado no init (fake_role), para os testes conferirem que o papel chegou.
"""

import json
import os
import sys
import threading
import time
import uuid

SID = "fake-sid-0001"
out_lock = threading.Lock()
pending = {}  # request_id -> Event com a resposta
interrupted = threading.Event()
NAME = None  # -n
ROLE = None  # --append-system-prompt
PEER_DIR = os.environ.get("FAKE_PEER_DIR")


def emit(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def assistant(blocks, stop_reason="end_turn"):
    emit(
        {"type": "assistant", "message": {"role": "assistant", "content": blocks, "stop_reason": stop_reason}}
    )


def result(ok=True, subtype=None):
    emit(
        {
            "type": "result",
            "subtype": subtype or ("success" if ok else "error_during_execution"),
            "is_error": not ok,
            "num_turns": 1,
            "session_id": SID,
        }
    )


def ask(tool, inp, request_id):
    ev = threading.Event()
    pending[request_id] = [ev, None]
    emit(
        {
            "type": "control_request",
            "request_id": request_id,
            "request": {"subtype": "can_use_tool", "tool_name": tool, "display_name": tool, "input": inp},
        }
    )
    ev.wait(30)
    return pending.pop(request_id)[1] or {}


def send_peer(to, message, summary="mensagem"):
    """tool_use SendMessage + entrega na caixa do destinatário + tool_result com o msg_id (como o real)."""
    tid = "tu-sm-" + uuid.uuid4().hex[:6]
    msg_id = uuid.uuid4().hex
    assistant(
        [
            {
                "type": "tool_use",
                "id": tid,
                "name": "SendMessage",
                "input": {"to": to, "message": message, "summary": summary},
            }
        ],
        "tool_use",
    )
    ok = bool(PEER_DIR)
    if ok:
        with open(os.path.join(PEER_DIR, f"{to}.inbox"), "a") as fh:
            fh.write(json.dumps({"from": NAME, "body": message, "msg_id": msg_id}) + "\n")
    res = (
        {"success": ok, "message": f"→ {to}", "msg_id": msg_id}
        if ok
        else {"success": False, "message": "sem FAKE_PEER_DIR"}
    )
    emit(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": [{"type": "text", "text": json.dumps(res)}],
                    }
                ],
            },
            "tool_use_result": res,
        }
    )


def handle_peer(m):
    """Chegou mensagem de outra sessão: o mesmo evento `user` que o claude emite (origin.kind peer)."""
    body = m.get("body", "")
    emit({"type": "system", "subtype": "init", "session_id": SID, "model": "fake"})
    emit(
        {
            "type": "user",
            "isReplay": True,
            "isSynthetic": True,
            "origin": {
                "kind": "peer",
                "from": "uds:/tmp/fake.sock",
                "name": m.get("from"),
                "msg_id": m.get("msg_id"),
                "body": body,
            },
            "message": {
                "role": "user",
                "content": 'Another Claude session sent a message:\n<cross-session-message from-name="'
                + str(m.get("from"))
                + '">\n'
                + body
                + "\n</cross-session-message>\n\nThis came from another Claude session.",
            },
        }
    )
    if body.startswith("responda "):
        send_peer(m.get("from"), body[len("responda ") :], "resposta")
        assistant([{"type": "text", "text": "respondi a " + str(m.get("from"))}])
    else:
        assistant([{"type": "text", "text": "o peer disse: " + body}])
    result()


def poll_inbox():
    path = os.path.join(PEER_DIR, f"{NAME}.inbox")
    seen = 0
    while True:
        time.sleep(0.1)
        try:
            with open(path) as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in lines[seen:]:
            seen += 1
            try:
                handle_peer(json.loads(line))
            except ValueError:
                pass


def handle_user(text):
    emit({"type": "user", "message": {"role": "user", "content": text}})
    if text.startswith("delegue: ") and " para " in text:
        msg, to = text[len("delegue: ") :].rsplit(" para ", 1)
        send_peer(to.strip(), msg.strip(), "delegando")
        assistant([{"type": "text", "text": "deleguei para " + to.strip()}])
        result()
    elif "ferramenta" in text:
        assistant(
            [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "echo oi"}}], "tool_use"
        )
        resp = ask("Bash", {"command": "echo oi"}, "req-1")
        if resp.get("behavior") == "allow":
            emit(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "oi"}],
                    },
                }
            )
            assistant([{"type": "text", "text": "feito"}])
        else:
            assistant([{"type": "text", "text": "negado: " + str(resp.get("message", ""))}])
        result()
    elif "pergunte" in text:
        q = {
            "questions": [
                {
                    "question": "Qual fruta?",
                    "header": "fruta",
                    "options": [{"label": "manga"}, {"label": "uva"}],
                    "multiSelect": False,
                }
            ]
        }
        assistant([{"type": "tool_use", "id": "tu2", "name": "AskUserQuestion", "input": q}], "tool_use")
        resp = ask("AskUserQuestion", q, "req-2")
        ans = (resp.get("updatedInput") or {}).get("answers") or {}
        assistant([{"type": "text", "text": "você escolheu " + ", ".join(ans.values())}])
        result()
    elif "demore" in text:
        assistant(
            [{"type": "tool_use", "id": "tu3", "name": "Bash", "input": {"command": "sleep 30"}}], "tool_use"
        )
        interrupted.clear()
        if interrupted.wait(5):
            emit(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tu3",
                                "content": "interrompido",
                                "is_error": True,
                            }
                        ],
                    },
                }
            )
            result(ok=False)
        else:
            result()
    elif "esgote" in text:
        emit(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"},
            }
        )
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "num_turns": 1,
                "session_id": SID,
                "result": "You've hit your usage limit · resets 3pm",
            }
        )
    elif "injete" in text:
        emit({"type": "user", "message": {"role": "user", "content": "Aviso automático: cron rodou"}})
        assistant([{"type": "text", "text": "ok"}])
        result()
    else:
        words = ("eco: " + text).split(" ")
        emit(
            {
                "type": "stream_event",
                "event": {"type": "content_block_start", "content_block": {"type": "text"}},
            }
        )
        for w in words:
            emit(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": w + " "},
                    },
                }
            )
        assistant([{"type": "text", "text": "eco: " + text}])
        result()


def parse_args(argv):
    global NAME, ROLE
    i = 0
    while i < len(argv):
        if argv[i] in ("-n", "--name") and i + 1 < len(argv):
            NAME = argv[i + 1]
            i += 2
        elif argv[i] == "--append-system-prompt" and i + 1 < len(argv):
            ROLE = argv[i + 1]
            i += 2
        else:
            i += 1


def main():
    parse_args(sys.argv[1:])
    init = {"type": "system", "subtype": "init", "session_id": SID, "model": "fake"}
    if NAME:
        init["fake_name"] = NAME
    if ROLE:
        init["fake_role"] = ROLE[:200]
    emit(init)
    if NAME and PEER_DIR:
        threading.Thread(target=poll_inbox, daemon=True).start()
    for line in sys.stdin:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        t = o.get("type")
        if t == "user":
            c = o["message"]["content"]
            text = (
                c if isinstance(c, str) else " ".join(b.get("text", "") for b in c if b.get("type") == "text")
            )
            threading.Thread(target=handle_user, args=(text,), daemon=True).start()
        elif t == "control_response":
            resp = o.get("response") or {}
            slot = pending.get(resp.get("request_id"))
            if slot:
                slot[1] = resp.get("response") or {}
                slot[0].set()
        elif t == "control_request" and (o.get("request") or {}).get("subtype") == "interrupt":
            interrupted.set()
            emit(
                {
                    "type": "control_response",
                    "response": {"subtype": "success", "request_id": o.get("request_id"), "response": {}},
                }
            )
    time.sleep(0.1)  # deixa um turno em andamento terminar de escrever


if __name__ == "__main__":
    main()
