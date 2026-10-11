"""Turn incoming WhatsApp webhook payloads into a common message shape.

Supported formats:
  - generic: {"id", "group_id", "group_name", "sender", "text"} (for your own bridge)
  - Green-API: typeWebhook == "incomingMessageReceived"
  - Whapi.Cloud: {"messages": [{"chat_id": "...@g.us", ...}]}
  - Meta WhatsApp Cloud API: {"object": "whatsapp_business_account", ...}
  - WAHA (self-hosted): {"event": "message", "session": ..., "payload": {...}}
"""

from dataclasses import dataclass


@dataclass
class WaMessage:
    message_id: str
    group_id: str
    group_name: str
    sender: str
    text: str
    sender_name: str = ""

    @property
    def is_group(self):
        return bool(self.group_id)


def _generic(p):
    return [WaMessage(str(p.get("id", "")), p.get("group_id", "") or p.get("group_name", ""),
                      p.get("group_name", ""), p.get("sender", ""), p.get("text", ""))]


def _green_api(p):
    if p.get("typeWebhook") != "incomingMessageReceived":
        return []
    sd = p.get("senderData", {})
    md = p.get("messageData", {})
    text = (md.get("textMessageData", {}).get("textMessage")
            or md.get("extendedTextMessageData", {}).get("text", ""))
    chat = sd.get("chatId", "")
    if not chat.endswith("@g.us"):
        return []
    return [WaMessage(p.get("idMessage", ""), chat, sd.get("chatName", ""),
                      sd.get("senderName") or sd.get("sender", ""), text)]


def _whapi(p):
    out = []
    for m in p.get("messages", []):
        chat = m.get("chat_id", "")
        if m.get("from_me") or not chat.endswith("@g.us"):
            continue
        text = (m.get("text") or {}).get("body", "")
        out.append(WaMessage(m.get("id", ""), chat, m.get("chat_name", ""),
                             m.get("from_name") or m.get("from", ""), text))
    return out


def _meta(p):
    out = []
    for entry in p.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "")
                     for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                if m.get("type") != "text":
                    continue
                group = m.get("group_id", "")  # only set for Groups API messages
                if not group:
                    continue
                out.append(WaMessage(m.get("id", ""), group, "",
                                     names.get(m.get("from")) or m.get("from", ""),
                                     m.get("text", {}).get("body", "")))
    return out


def _waha(p):
    # "message" fires for incoming messages only ("message.any" would include our own).
    m = p.get("payload") or {}
    if p.get("event") != "message" or m.get("fromMe"):
        return []
    chat = m.get("from", "")
    if not chat.endswith("@g.us"):
        return []
    data = m.get("_data") or {}
    sender = m.get("participant") or data.get("author", "")
    return [WaMessage(m.get("id", ""), chat, "", sender, m.get("body") or "",
                      sender_name=data.get("notifyName", ""))]


def parse_payload(payload):
    if "event" in payload and "payload" in payload:
        return _waha(payload)
    if payload.get("object") == "whatsapp_business_account":
        return _meta(payload)
    if "typeWebhook" in payload:
        return _green_api(payload)
    if "messages" in payload:
        return _whapi(payload)
    if "text" in payload:
        return _generic(payload)
    return []
