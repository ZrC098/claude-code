import json
import os
import sys
import tempfile
import unittest
from email.message import EmailMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mail_chain.config import load_config, load_dotenv  # noqa: E402
from mail_chain.setup import run_setup  # noqa: E402
from mail_chain.engine import Engine, match_rule  # noqa: E402
from mail_chain.mailer import CHAIN_TAG, Reply, parse_reply, strip_quoted  # noqa: E402
from mail_chain.store import Store  # noqa: E402
from mail_chain.whatsapp import WaMessage, parse_payload  # noqa: E402

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "config.example.json")


class FakeMailer:
    def __init__(self):
        self.sent = []
        self.inbox = []

    def send(self, to, subject, body):
        self.sent.append((list(to), subject, body))

    def fetch_replies(self):
        replies, self.inbox = self.inbox, []
        return replies


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with open(EXAMPLE, encoding="utf-8") as f:
            raw = json.load(f)
        raw["db_path"] = os.path.join(self.tmp.name, "db.sqlite3")
        path = os.path.join(self.tmp.name, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        self.cfg = load_config(path)
        self.mailer = FakeMailer()
        self.store = Store(self.cfg.db_path)
        self.engine = Engine(self.cfg, self.store, self.mailer)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, mid="wamid.1", group="Équipe Logistique"):
        return self.engine.on_whatsapp_message(
            WaMessage(mid, "123@g.us", group, "Paul", "Besoin de 3 palettes pour demain"))

    def reply(self, chain_id, sender, body, mid):
        return self.engine.on_reply(Reply(chain_id, sender, body, mid))

    def test_full_chain_to_final_level(self):
        cid = self.start()
        self.assertEqual(self.mailer.sent[-1][0], ["chef.equipe@example.com", "adjoint@example.com"])
        self.assertEqual(CHAIN_TAG.search(self.mailer.sent[-1][1]).group(1), cid)

        self.assertEqual(self.reply(cid, "adjoint@example.com", "OK pour moi", "<a>"), "level:2")
        self.assertEqual(self.mailer.sent[-1][0], ["responsable.site@example.com"])
        self.assertIn("réponse de adjoint@example.com : OK pour moi", self.mailer.sent[-1][2])

        self.assertEqual(self.reply(cid, "responsable.site@example.com", "Validé", "<b>"), "level:3")
        self.assertEqual(self.mailer.sent[-1][0], ["direction@example.com"])

        self.assertEqual(self.reply(cid, "direction@example.com", "oui", "<c>"), "approved")
        self.assertEqual(self.store.get(cid)["status"], "approved")
        self.assertIn("VALIDÉE", self.mailer.sent[-1][1])  # notify_on_close

    def test_refusal_stops_chain(self):
        cid = self.start()
        self.assertEqual(self.reply(cid, "chef.equipe@example.com", "Non, pas de stock", "<a>"), "rejected")
        self.assertEqual(self.reply(cid, "chef.equipe@example.com", "oui", "<b>"), "ignored")

    def test_goto_rule_on_final_level(self):
        cid = self.start()
        self.reply(cid, "chef.equipe@example.com", "ok", "<a>")
        self.reply(cid, "responsable.site@example.com", "ok", "<b>")
        self.assertEqual(self.reply(cid, "direction@example.com", "À revoir svp", "<c>"), "level:1")
        self.assertEqual(self.mailer.sent[-1][0][0], "chef.equipe@example.com")

    def test_reply_from_outsider_or_wrong_level_is_ignored(self):
        cid = self.start()
        self.assertEqual(self.reply(cid, "intrus@example.com", "ok", "<a>"), "ignored")
        self.assertEqual(self.reply(cid, "direction@example.com", "ok", "<b>"), "ignored")
        self.assertEqual(self.store.get(cid)["level"], 0)

    def test_unrecognised_and_duplicate(self):
        cid = self.start()
        self.assertEqual(self.reply(cid, "chef.equipe@example.com", "Je regarde", "<a>"), "unrecognised")
        self.assertEqual(self.reply(cid, "chef.equipe@example.com", "ok", "<b>"), "level:2")
        self.assertEqual(self.reply(cid, "chef.equipe@example.com", "ok", "<b>"), "duplicate")

    def test_group_filter_and_dedupe(self):
        self.assertIsNone(self.start(group="Autre groupe"))
        self.assertIsNotNone(self.start())
        self.assertIsNone(self.start())  # same WhatsApp message id

    def test_groups_are_recorded_even_when_ignored(self):
        self.start(group="Autre groupe")
        self.start(mid="wamid.2")
        names = {g["name"] for g in self.store.seen_groups()}
        self.assertEqual(names, {"Équipe Logistique"})  # same id "123@g.us", name updated

    def test_allowed_senders_by_number_only(self):
        self.cfg.allowed_senders = ["06 12 34 56 78"]
        msg = lambda mid, sender, name="": WaMessage(mid, "123@g.us", "Équipe Logistique",
                                                     sender, "demande", sender_name=name)
        self.assertIsNotNone(self.engine.on_whatsapp_message(msg("a", "33612345678@c.us")))
        self.assertIsNone(self.engine.on_whatsapp_message(msg("b", "33700000000@c.us", "06 12 34 56 78")))

    def test_hourly_cap(self):
        self.cfg.max_chains_per_hour = 2
        self.assertIsNotNone(self.start("m1"))
        self.assertIsNotNone(self.start("m2"))
        self.assertIsNone(self.start("m3"))
        self.assertEqual(len(self.mailer.sent), 2)

    def test_timeout_escalates(self):
        self.cfg.on_timeout = "next"
        cid = self.start()
        sent_at = self.store.get(cid)["level_sent_at"]
        self.assertEqual(self.engine.check_timeouts(sent_at + 23 * 3600), [])
        self.assertEqual(self.engine.check_timeouts(sent_at + 24 * 3600), [cid])
        self.assertEqual(self.store.get(cid)["level"], 1)

    def test_poll_reads_inbox(self):
        cid = self.start()
        self.mailer.inbox.append(Reply(cid, "chef.equipe@example.com", "oui", "<x>"))
        self.engine.poll()
        self.assertEqual(self.store.get(cid)["level"], 1)


class SetupTest(unittest.TestCase):
    def test_wizard_writes_valid_config_and_private_env(self):
        answers = iter([
            "Mon groupe", "",                                   # group, prefix
            "06 12 34 56 78", "5",                              # allowed senders, max/hour
            "2",                                                # ranks
            "", "a@x.fr, B@x.fr", "12",                         # rank 1
            "Direction", "pas-une-adresse", "d@x.fr", "0",      # rank 2 (one retry)
            "next", "",                                         # timeout, notify
            "outlook", "bot@x.fr", "demandes.example.fr",       # mail + domain
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            run_setup(path, ask=lambda _: next(answers), ask_secret=lambda _: "pw")
            cfg = load_config(path)
            self.assertEqual(cfg.groups, ["Mon groupe"])
            self.assertEqual([lv.recipients for lv in cfg.levels], [["a@x.fr", "b@x.fr"], ["d@x.fr"]])
            self.assertEqual((cfg.levels[1].name, cfg.on_timeout), ("Direction", "next"))
            env_path = os.path.join(tmp, ".env")
            self.assertEqual(os.stat(env_path).st_mode & 0o777, 0o600)
            self.assertEqual((cfg.allowed_senders, cfg.max_chains_per_hour), (["06 12 34 56 78"], 5))
            for key in ("SMTP_PASSWORD", "WEBHOOK_TOKEN", "DOMAIN", "WAHA_API_KEY"):
                os.environ.pop(key, None)
            load_dotenv(env_path)
            self.assertEqual(os.environ["SMTP_PASSWORD"], "pw")
            self.assertEqual(os.environ["DOMAIN"], "demandes.example.fr")
            self.assertEqual(len(os.environ["WEBHOOK_TOKEN"]), 48)
            self.assertNotEqual(os.environ["WAHA_API_KEY"], os.environ["WEBHOOK_TOKEN"])


class ParsingTest(unittest.TestCase):
    def test_match_rule_whole_words_and_accents(self):
        cfg_rules = load_config(EXAMPLE).rules
        self.assertEqual(match_rule("VALIDE !", cfg_rules).action, "next")
        self.assertEqual(match_rule("Refusé.", cfg_rules).action, "stop")
        self.assertIsNone(match_rule("nonobstant\nok", cfg_rules))  # only first line, whole words

    def test_strip_quoted(self):
        body = "Oui go\n\nLe lun. 6 oct. 2026, Bot a écrit :\n> Merci de répondre OK"
        self.assertEqual(strip_quoted(body), "Oui go")

    def test_parse_reply(self):
        msg = EmailMessage()
        msg["From"] = "Chef <Chef.Equipe@example.com>"
        msg["Subject"] = "RE: [CHAIN-abcd1234] Rang 1 - Demande"
        msg["Message-ID"] = "<m1@example.com>"
        msg.set_content("ok\n> quoted")
        r = parse_reply(msg.as_bytes())
        self.assertEqual((r.chain_id, r.sender, r.body), ("abcd1234", "chef.equipe@example.com", "ok"))

    def test_webhook_formats(self):
        green = {"typeWebhook": "incomingMessageReceived", "idMessage": "g1",
                 "senderData": {"chatId": "1@g.us", "chatName": "G", "senderName": "Ana"},
                 "messageData": {"typeMessage": "textMessage", "textMessageData": {"textMessage": "hi"}}}
        whapi = {"messages": [{"id": "w1", "chat_id": "2@g.us", "from": "33", "from_me": False,
                               "text": {"body": "yo"}}]}
        private = {"messages": [{"id": "w2", "chat_id": "33@s.whatsapp.net", "text": {"body": "x"}}]}
        waha = {"event": "message", "session": "default",
                "payload": {"id": "false_9@g.us_A", "from": "9@g.us", "fromMe": False,
                            "participant": "33612345678@c.us", "body": "palettes",
                            "_data": {"notifyName": "Paul"}}}
        own = {"event": "message", "payload": {"from": "9@g.us", "fromMe": True, "body": "x"}}
        msg = parse_payload(waha)[0]
        self.assertEqual((msg.group_id, msg.sender, msg.sender_name, msg.text),
                         ("9@g.us", "33612345678@c.us", "Paul", "palettes"))
        self.assertEqual(parse_payload(own), [])
        generic = {"id": "x1", "group_name": "G", "sender": "Bob", "text": "hello"}
        self.assertEqual(parse_payload(green)[0].text, "hi")
        self.assertEqual(parse_payload(whapi)[0].group_id, "2@g.us")
        self.assertEqual(parse_payload(private), [])
        self.assertTrue(parse_payload(generic)[0].is_group)


if __name__ == "__main__":
    unittest.main()
