"""Command line: python -m mail_chain {serve,poll,status} --config config.json"""

import argparse
import logging
import threading
import time

from .config import load_config
from .engine import Engine
from .mailer import SmtpImapMailer
from .server import serve
from .store import Store


def main():
    parser = argparse.ArgumentParser(prog="mail_chain")
    parser.add_argument("command", choices=["serve", "poll", "status"],
                        help="serve: webhook + mail loop; poll: read replies once; status: list open chains")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    store = Store(cfg.db_path)

    if args.command == "status":
        for c in store.open_chains():
            print(f"{c['id']}  {cfg.levels[c['level']].name:<12} {c['grp']}: {c['text'][:60]}")
        return

    engine = Engine(cfg, store, SmtpImapMailer())
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
