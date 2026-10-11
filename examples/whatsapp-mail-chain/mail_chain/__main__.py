"""Command line: python -m mail_chain {setup,check,serve,poll,status,groups} --config config.json"""

import argparse
import logging
import os
import threading
import time

from .config import load_config, load_dotenv
from .engine import Engine
from .mailer import SmtpImapMailer
from .server import serve
from .setup import run_setup
from .store import Store

COMMANDS = {
    "setup": "créer config.json en répondant à quelques questions",
    "check": "vérifier la config et la connexion SMTP/IMAP",
    "serve": "lancer le webhook WhatsApp et la lecture des mails",
    "poll": "lire les réponses mail une seule fois",
    "status": "lister les demandes en cours",
    "groups": "lister les groupes WhatsApp vus par le service (pour trouver leur identifiant)",
}


def main():
    parser = argparse.ArgumentParser(prog="mail_chain")
    parser.add_argument("command", choices=list(COMMANDS),
                        help="; ".join(f"{k}: {v}" for k, v in COMMANDS.items()))
    parser.add_argument("--config", default="config.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "setup":
        run_setup(args.config)
        return

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(args.config)), ".env"))
    cfg = load_config(args.config)
    store = Store(cfg.db_path)

    if args.command == "status":
        for c in store.open_chains():
            print(f"{c['id']}  {cfg.levels[c['level']].name:<12} {c['grp']}: {c['text'][:60]}")
        return

    if args.command == "groups":
        rows = store.seen_groups()
        if not rows:
            print("Aucun groupe vu pour l'instant : envoyez un message dans le groupe, puis relancez.")
        for g in rows:
            watched = "surveillé" if Engine(cfg, store, None).accepts_group(g["group_id"], g["name"]) else "ignoré"
            print(f"{g['group_id']:<32} {g['name'] or '(sans nom)':<30} {watched}")
        return

    mailer = SmtpImapMailer()
    engine = Engine(cfg, store, mailer)

    if args.command == "check":
        print(f"Config OK : {len(cfg.levels)} rangs, groupes surveillés : {cfg.groups or 'tous'}")
        mailer.fetch_replies(dry_run=True)
        print("IMAP OK")
        mailer.check_smtp()
        print("SMTP OK")
        return

    if args.command == "poll":
        engine.poll()
        return

    httpd = serve(engine)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        while True:
            try:
                engine.poll()
            except Exception:
                logging.exception("mail polling failed, retrying in %ss", cfg.poll_seconds)
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
