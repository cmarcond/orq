"""Imitação do `claude -p --input-format stream-json --output-format stream-json` para os testes.

Fala o subconjunto do protocolo que o supervisor usa. O comportamento depende do texto da mensagem:
  "ferramenta"  -> tool_use Bash + pedido de permissão; allow => tool_result + "feito"; deny => "negado"
  "pergunte"    -> pedido de permissão AskUserQuestion; a resposta vem em updatedInput.answers
  "demore"      -> fica ocupado até receber um control_request interrupt (ou 5 s)
  "injete"      -> emite uma mensagem `user` sem marca de origem (como um cron faria)
  qualquer outro -> stream_event palavra a palavra + texto "eco: ..." + result
Sempre ecoa a mensagem do usuário (--replay-user-messages) e responde interrupts com control_response.
"""

import json
import sys
import threading
import time

SID = "fake-sid-0001"
out_lock = threading.Lock()
pending = {}  # request_id -> Event com a resposta
interrupted = threading.Event()


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


def handle_user(text):
    emit({"type": "user", "message": {"role": "user", "content": text}})
    if "ferramenta" in text:
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


def main():
    emit({"type": "system", "subtype": "init", "session_id": SID, "model": "fake"})
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
