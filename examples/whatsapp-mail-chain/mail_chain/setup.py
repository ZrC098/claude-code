"""Interactive wizard that writes config.json and .env (secrets)."""

import getpass
import json
import os
import re
import secrets

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

MAIL_PRESETS = {
    "gmail": ("smtp.gmail.com", "587", "imap.gmail.com"),
    "outlook": ("smtp.office365.com", "587", "outlook.office365.com"),
    "ovh": ("ssl0.ovh.net", "465", "ssl0.ovh.net"),
}


def _ask(prompt, default="", ask=input):
    suffix = f" [{default}]" if default else ""
    answer = ask(f"{prompt}{suffix} : ").strip()
    return answer or default


def _ask_emails(prompt, ask=input, allow_empty=False):
    while True:
        raw = _ask(prompt, ask=ask)
        emails = [e.strip().lower() for e in re.split(r"[,;\s]+", raw) if e.strip()]
        bad = [e for e in emails if not EMAIL.match(e)]
        if bad:
            print(f"  Adresse(s) invalide(s) : {', '.join(bad)}")
        elif emails or allow_empty:
            return emails
        else:
            print("  Il faut au moins une adresse.")


def _ask_int(prompt, default, ask=input):
    while True:
        raw = _ask(prompt, str(default), ask=ask)
        if raw.isdigit() and int(raw) >= 0:
            return int(raw)
        print("  Entrez un nombre entier.")


def build_config(ask=input):
    print("\n== Groupe WhatsApp ==")
    print("Nom exact du groupe tel qu'il s'affiche dans WhatsApp. Laissez vide si vous ne le")
    print("connaissez pas : lancez ensuite `python3 -m mail_chain groups` pour le retrouver.")
    group = _ask("Nom du groupe", ask=ask)
    prefix = _ask("Préfixe obligatoire des messages (ex. #demande, vide = tous les messages)", ask=ask)

    print("\n== Rangs ==")
    count = max(1, _ask_int("Combien de rangs (listes successives) ?", 3, ask=ask))
    levels = []
    for i in range(count):
        final = " (liste finale)" if i == count - 1 else ""
        name = _ask(f"Nom du rang {i + 1}{final}", f"Rang {i + 1}", ask=ask)
        recipients = _ask_emails(f"Adresses e-mail du {name} (séparées par des virgules)", ask=ask)
        timeout = _ask_int(f"Délai de réponse du {name} en heures (0 = aucun)", 24, ask=ask)
        levels.append({"name": name, "recipients": recipients, "timeout_hours": timeout})

    print("\n== Sans réponse dans le délai ==")
    on_timeout = ""
    while on_timeout not in ("wait", "next", "stop"):
        on_timeout = _ask("wait = attendre, next = passer au rang suivant, stop = refuser", "wait", ask=ask)
    notify = _ask_emails("Adresses qui reçoivent le récapitulatif final (vide = personne)",
                         ask=ask, allow_empty=True)
    return {
        "whatsapp": {"groups": [group] if group else [], "trigger_prefix": prefix,
                     "webhook_path": "/webhook/whatsapp"},
        "levels": levels,
        "on_timeout": on_timeout,
        "notify_on_close": notify,
        "poll_seconds": 60,
        "db_path": "data/mail_chain.sqlite3",
        "listen_host": "0.0.0.0",
        "listen_port": 8080,
    }


def build_env(ask=input, ask_secret=getpass.getpass):
    print("\n== Boîte mail d'envoi (dédiée de préférence) ==")
    provider = ""
    while provider not in (*MAIL_PRESETS, "autre"):
        provider = _ask("Fournisseur : gmail, outlook, ovh ou autre", "gmail", ask=ask).lower()
    smtp_host, smtp_port, imap_host = MAIL_PRESETS.get(provider, ("", "587", ""))
    if provider == "autre":
        smtp_host = _ask("Serveur SMTP", ask=ask)
        smtp_port = _ask("Port SMTP", "587", ask=ask)
        imap_host = _ask("Serveur IMAP", ask=ask)
    user = _ask_emails("Adresse de la boîte mail", ask=ask)[0]
    if provider == "gmail":
        print("  Gmail : utilisez un « mot de passe d'application » (myaccount.google.com/apppasswords).")
    password = ask_secret("Mot de passe (non affiché) : ")
    print("\n== Serveur ==")
    domain = _ask("Nom de domaine du serveur pour le HTTPS (ex. demandes.mon-site.fr, vide si inconnu)", ask=ask)
    return {
        "DOMAIN": domain,
        "SMTP_HOST": smtp_host, "SMTP_PORT": smtp_port, "SMTP_USER": user,
        "SMTP_PASSWORD": password, "MAIL_FROM": user, "IMAP_HOST": imap_host,
        "WEBHOOK_TOKEN": secrets.token_hex(24),
    }


def write_env(path, values):
    lines = [f"{k}={v}" for k, v in values.items()]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_setup(config_path, ask=input, ask_secret=getpass.getpass):
    if os.path.exists(config_path) and _ask(f"{config_path} existe déjà. Le remplacer ? (o/n)",
                                            "n", ask=ask).lower() != "o":
        print("Rien n'a été modifié.")
        return
    cfg = build_config(ask)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"\n{config_path} écrit.")

    env_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), ".env")
    if os.path.exists(env_path) and _ask(".env existe déjà. Le remplacer ? (o/n)", "n", ask=ask).lower() != "o":
        print(".env conservé.")
        return
    env = build_env(ask, ask_secret)
    write_env(env_path, env)
    print(f"{env_path} écrit (lisible par vous seul).")
    print("\nURL à donner au fournisseur WhatsApp :")
    print(f"  https://{env['DOMAIN'] or '<serveur>'}/webhook/whatsapp?token={env['WEBHOOK_TOKEN']}")
    print("Étape suivante : `python3 -m mail_chain check`, puis `docker compose up -d`.")
