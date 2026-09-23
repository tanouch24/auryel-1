"""Regression checks for the legacy WhatsApp webhook logging boundary."""

from pathlib import Path


_SOURCE = Path(__file__).with_name("auryel_bot.py").read_text(encoding="utf-8")
_WEBHOOK = _SOURCE.split('# ============================================================\n# WEBHOOK TELEGRAM', 1)[0]
_WEBHOOK = _WEBHOOK.split('# ============================================================\n# WEBHOOK WHATSAPP', 1)[1]


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print(f"OK  {label}")


check("print(f\"👤 {from_num}: {user_text}\")" not in _WEBHOOK,
      "raw incoming WhatsApp message is not logged")
check("print(f\"🔮 {nom}: {reply}\")" not in _WEBHOOK,
      "raw assistant reply is not logged")
check("traceback.print_exc()" not in _WEBHOOK,
      "raw webhook traceback is not printed")
check("log_event(\"webhook_message_received\", phone_hash=" in _WEBHOOK,
      "incoming webhook diagnostic uses a phone hash")
check("log_event(\"webhook_processing_error\", error=type(e).__name__)" in _WEBHOOK,
      "webhook errors log only the exception type")

print("5 OK / 0 KO")
