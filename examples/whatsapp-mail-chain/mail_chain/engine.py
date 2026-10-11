"""The escalation chain: WhatsApp message -> level 1 mail -> reply -> level 2 mail -> ..."""

import logging
import re
import time
import unicodedata

log = logging.getLogger("mail_chain")


def _normalize(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def phone_key(value):
    """Compare phone numbers whatever their format: 06..., +33 6..., 336...@c.us."""
    digits = re.sub(r"\D", "", value.split("@")[0])
    return digits[-9:] if len(digits) >= 9 else digits


def match_rule(body, rules):
    """Return the first rule whose keyword appears as a whole word in the reply.

    Only the first non-empty line is checked, so that "OK" in a signature or a
    long explanation further down does not trigger anything by accident.
    """
    first_line = next((l for l in body.splitlines() if l.strip()), "")
    haystack = _normalize(first_line)
    for rule in rules:
        for kw in rule.keywords:
            if re.search(rf"(?<!\w){re.escape(_normalize(kw))}(?!\w)", haystack):
                return rule
    return None


class Engine:
    def __init__(self, cfg, store, mailer):
        self.cfg = cfg
        self.store = store
        self.mailer = mailer

    # ---- WhatsApp side -------------------------------------------------

    def accepts_group(self, group_id, group_name):
        if not self.cfg.groups:
            return True
        allowed = {g.lower() for g in self.cfg.groups}
        return (group_id or "").lower() in allowed or (group_name or "").lower() in allowed

    def accepts_sender(self, msg):
        if not self.cfg.allowed_senders:
            return True
        # Numbers only: display names are chosen by the sender and can be faked.
        keys = {phone_key(n) for n in self.cfg.allowed_senders if phone_key(n)}
        return phone_key(msg.sender) in keys

    def on_whatsapp_message(self, msg):
        """Start a chain for a group message. Return the chain id or None."""
        if not msg.is_group:
            return None
        self.store.see_group(msg.group_id, msg.group_name)
        if not self.accepts_group(msg.group_id, msg.group_name):
            log.info("message from group %s (%s) ignored: not in whatsapp.groups",
                     msg.group_id, msg.group_name or "sans nom")
            return None
        if not self.accepts_sender(msg):
            log.warning("message from %s ignored: not in whatsapp.allowed_senders", msg.sender)
            return None
        limit = self.cfg.max_chains_per_hour
        if limit and self.store.chains_since(time.time() - 3600) >= limit:
            log.error("max_chains_per_hour (%s) reached: message %s ignored", limit, msg.message_id)
            return None
        text = msg.text.strip()
        prefix = self.cfg.trigger_prefix
        if prefix:
            if not text.lower().startswith(prefix.lower()):
                return None
            text = text[len(prefix):].strip()
        if not text:
            return None
        chain_id = self.store.create_chain(
            msg.message_id, msg.group_name or msg.group_id,
            msg.sender_name or msg.sender.split("@")[0], text)
        if chain_id is None:
            log.info("WhatsApp message %s already handled", msg.message_id)
            return None
        log.info("chain %s started from group %s", chain_id, msg.group_name or msg.group_id)
        self._send_level(chain_id, 0)
        return chain_id

    # ---- e-mail side ---------------------------------------------------

    def on_reply(self, reply, now=None):
        """Apply a reply to its chain. Return the action taken (for logs and tests)."""
        if not self.store.mark_mail_processed(reply.message_id):
            return "duplicate"
        chain = self.store.get(reply.chain_id)
        if chain is None or chain["status"] != "open":
            return "ignored"
        level = self.cfg.levels[chain["level"]]
        if reply.sender not in level.recipients:
            # Only the people of the current level can move the chain forward.
            self.store.add_event(chain["id"], chain["level"], "ignored", reply.sender,
                                 "expéditeur hors du rang en cours", now)
            log.warning("chain %s: reply from %s ignored (not in %s)",
                        chain["id"], reply.sender, level.name)
            return "ignored"

        rule = match_rule(reply.body, level.rules or self.cfg.rules)
        if rule is None:
            self.store.add_event(chain["id"], chain["level"], "ignored", reply.sender,
                                 f"réponse non reconnue : {reply.body[:200]}", now)
            log.warning("chain %s: unrecognised reply from %s", chain["id"], reply.sender)
            return "unrecognised"

        self.store.add_event(chain["id"], chain["level"], "reply", reply.sender, reply.body, now)
        return self._apply(chain, rule.action, rule.target, now)

    def check_timeouts(self, now=None):
        """Apply on_timeout to chains whose current level has not answered in time."""
        now = now or time.time()
        if self.cfg.on_timeout == "wait":
            return []
        moved = []
        for chain in self.store.open_chains():
            level = self.cfg.levels[chain["level"]]
            if level.timeout_hours and now - chain["level_sent_at"] >= level.timeout_hours * 3600:
                self.store.add_event(chain["id"], chain["level"], "timeout", "",
                                     f"pas de réponse après {level.timeout_hours} h", now)
                self._apply(chain, self.cfg.on_timeout, "", now)
                moved.append(chain["id"])
        return moved

    def poll(self):
        for reply in self.mailer.fetch_replies():
            action = self.on_reply(reply)
            log.info("chain %s: reply from %s -> %s", reply.chain_id, reply.sender, action)
        self.check_timeouts()

    # ---- internals -----------------------------------------------------

    def _apply(self, chain, action, target, now):
        current = chain["level"]
        if action == "stop":
            return self._close(chain["id"], "rejected", now)
        if action == "goto":
            nxt = self.cfg.level_index(target)
        else:
            nxt = current + 1
        if nxt >= len(self.cfg.levels):
            return self._close(chain["id"], "approved", now)
        self.store.set_level(chain["id"], nxt, now)
        self._send_level(chain["id"], nxt, now)
        return f"level:{nxt + 1}"

    def _close(self, chain_id, status, now):
        self.store.close(chain_id, status)
        self.store.add_event(chain_id, self.store.get(chain_id)["level"], "closed", "", status, now)
        if self.cfg.notify_on_close:
            chain = self.store.get(chain_id)
            label = "VALIDÉE" if status == "approved" else "REFUSÉE"
            subject = f"[CHAIN-{chain_id}] Demande {label} ({chain['grp']})"
            body = (f"La demande WhatsApp de {chain['sender']} est {label.lower()}.\n\n"
                    f"Message :\n{chain['text']}\n\n{self._history(chain_id)}")
            self.mailer.send(self.cfg.notify_on_close, subject, body)
        log.info("chain %s closed: %s", chain_id, status)
        return status

    def _history(self, chain_id):
        lines = []
        for ev in self.store.events(chain_id):
            name = self.cfg.levels[ev["level"]].name
            if ev["kind"] == "reply":
                lines.append(f"- {name} : réponse de {ev['actor']} : {ev['detail'].splitlines()[0]}")
            elif ev["kind"] == "timeout":
                lines.append(f"- {name} : {ev['detail']}")
        return ("Historique :\n" + "\n".join(lines) + "\n") if lines else ""

    def _instructions(self, level):
        lines = []
        for rule in level.rules or self.cfg.rules:
            if rule.action == "next":
                what = "passer au rang suivant" if level is not self.cfg.levels[-1] else "valider définitivement"
            elif rule.action == "stop":
                what = "refuser et clore la demande"
            else:
                what = f"envoyer à « {rule.target} »"
            lines.append(f"  - « {rule.keywords[0].upper()} » pour {what}")
        return "\n".join(lines)

    def _send_level(self, chain_id, index, now=None):
        chain = self.store.get(chain_id)
        level = self.cfg.levels[index]
        fields = {
            "chain_id": chain_id,
            "group": chain["grp"],
            "sender": chain["sender"],
            "text": chain["text"],
            "level_name": level.name,
            "level_number": index + 1,
            "level_count": len(self.cfg.levels),
            "history": self._history(chain_id),
            "instructions": self._instructions(level),
        }
        self.mailer.send(level.recipients,
                         self.cfg.subject_template.format(**fields),
                         self.cfg.body_template.format(**fields))
        self.store.add_event(chain_id, index, "sent", "", ", ".join(level.recipients), now)
