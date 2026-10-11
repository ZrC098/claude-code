"""Load the chain configuration (JSON file) and secrets (environment variables)."""

import json
import os
from dataclasses import dataclass, field

ACTIONS = ("next", "stop", "goto")


@dataclass
class Rule:
    keywords: list
    action: str  # "next", "stop" or "goto"
    target: str = ""  # level name, only for "goto"


@dataclass
class Level:
    name: str
    recipients: list
    timeout_hours: float = 0  # 0 = no timeout
    rules: list = field(default_factory=list)  # overrides the global rules


@dataclass
class Config:
    groups: list
    trigger_prefix: str
    levels: list
    rules: list
    on_timeout: str  # "next", "stop" or "wait"
    notify_on_close: list
    subject_template: str
    body_template: str
    poll_seconds: int
    db_path: str
    listen_host: str
    listen_port: int
    webhook_path: str

    def level_index(self, name):
        for i, level in enumerate(self.levels):
            if level.name.lower() == name.lower():
                return i
        raise KeyError(f"unknown level: {name}")


DEFAULT_RULES = [
    {"keywords": ["oui", "ok", "valide", "validé", "accord", "d'accord", "go"], "action": "next"},
    {"keywords": ["non", "refus", "refusé", "stop", "annule", "annulé"], "action": "stop"},
]

DEFAULT_SUBJECT = "[CHAIN-{chain_id}] {level_name} - Demande WhatsApp ({group})"

DEFAULT_BODY = """Bonjour,

Une demande est arrivée sur le groupe WhatsApp « {group} ».

De : {sender}
Message :
{text}

{history}
Merci de répondre à ce mail (sans modifier l'objet) :
{instructions}

-- Envoyé automatiquement ({level_name}, étape {level_number}/{level_count})
"""


def _parse_rules(raw, where):
    rules = []
    for r in raw:
        action = r.get("action", "")
        if action not in ACTIONS:
            raise ValueError(f"{where}: invalid action {action!r}, expected one of {ACTIONS}")
        if action == "goto" and not r.get("target"):
            raise ValueError(f"{where}: a 'goto' rule needs a 'target' level name")
        keywords = r.get("keywords") or []
        if not keywords:
            raise ValueError(f"{where}: a rule needs at least one keyword")
        rules.append(Rule(keywords=list(keywords), action=action, target=r.get("target", "")))
    return rules


def load_config(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    wa = raw.get("whatsapp", {})
    levels_raw = raw.get("levels") or []
    if not levels_raw:
        raise ValueError("config: 'levels' must contain at least one level")

    levels = []
    for i, lv in enumerate(levels_raw):
        recipients = lv.get("recipients") or []
        if not recipients:
            raise ValueError(f"level {i + 1}: 'recipients' is empty")
        levels.append(Level(
            name=lv.get("name") or f"Rang {i + 1}",
            recipients=[r.strip().lower() for r in recipients],
            timeout_hours=float(lv.get("timeout_hours", 0)),
            rules=_parse_rules(lv.get("rules", []), f"level {i + 1}"),
        ))

    on_timeout = raw.get("on_timeout", "wait")
    if on_timeout not in ("next", "stop", "wait"):
        raise ValueError("config: 'on_timeout' must be 'next', 'stop' or 'wait'")

    cfg = Config(
        groups=wa.get("groups", []),
        trigger_prefix=wa.get("trigger_prefix", ""),
        levels=levels,
        rules=_parse_rules(raw.get("rules", DEFAULT_RULES), "rules"),
        on_timeout=on_timeout,
        notify_on_close=[a.strip().lower() for a in raw.get("notify_on_close", [])],
        subject_template=raw.get("subject_template", DEFAULT_SUBJECT),
        body_template=raw.get("body_template", DEFAULT_BODY),
        poll_seconds=int(raw.get("poll_seconds", 60)),
        db_path=raw.get("db_path", "mail_chain.sqlite3"),
        listen_host=raw.get("listen_host", "0.0.0.0"),
        listen_port=int(raw.get("listen_port", 8080)),
        webhook_path=wa.get("webhook_path", "/webhook/whatsapp"),
    )
    if "{chain_id}" not in cfg.subject_template:
        raise ValueError("config: 'subject_template' must contain {chain_id} to match replies")

    # Validate goto targets now rather than when a reply arrives.
    for rule in cfg.rules + [r for lv in levels for r in lv.rules]:
        if rule.action == "goto":
            cfg.level_index(rule.target)
    return cfg


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"missing environment variable: {name}")
    return value
