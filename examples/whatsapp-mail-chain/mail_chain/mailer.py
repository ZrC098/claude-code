"""Send e-mails over SMTP and read replies over IMAP."""

import email
import email.policy
import imaplib
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from .config import env

CHAIN_TAG = re.compile(r"\[CHAIN-([0-9a-f]{8})\]", re.IGNORECASE)

# Lines that start the quoted original in common mail clients.
QUOTE_HEADERS = [
    re.compile(r"^le .+ a écrit\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^on .+ wrote\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*(original message|message d'origine)", re.IGNORECASE),
    re.compile(r"^(de|from)\s*:\s", re.IGNORECASE),
]


@dataclass
class Reply:
    chain_id: str
    sender: str
    body: str
    message_id: str


def strip_quoted(text):
    """Keep only what the person wrote, not the quoted original mail."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or any(p.match(stripped) for p in QUOTE_HEADERS):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _text_body(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = re.sub(r"<(br|/p|/div)[^>]*>", "\n", content, flags=re.IGNORECASE)
        content = re.sub(r"<[^>]+>", "", content)
    return content


def parse_reply(raw_bytes):
    """Turn a raw e-mail into a Reply, or None if it is not about a chain."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    match = CHAIN_TAG.search(str(msg.get("Subject", "")))
    if not match:
        return None
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    message_id = str(msg.get("Message-ID", "")).strip() or f"<no-id-{hash(raw_bytes)}>"
    return Reply(match.group(1).lower(), sender, strip_quoted(_text_body(msg)), message_id)


class SmtpImapMailer:
    """Real mailer. Settings come from environment variables (see README)."""

    def __init__(self):
        self.smtp_host = env("SMTP_HOST", required=True)
        self.smtp_port = int(env("SMTP_PORT", "587"))
        self.smtp_user = env("SMTP_USER", required=True)
        self.smtp_password = env("SMTP_PASSWORD", required=True)
        self.mail_from = env("MAIL_FROM", self.smtp_user)
        self.imap_host = env("IMAP_HOST", required=True)
        self.imap_port = int(env("IMAP_PORT", "993"))
        self.imap_user = env("IMAP_USER", self.smtp_user)
        self.imap_password = env("IMAP_PASSWORD", self.smtp_password)
        self.imap_folder = env("IMAP_FOLDER", "INBOX")

    def send(self, to, subject, body):
        msg = EmailMessage()
        msg["From"] = self.mail_from
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid()
        msg.set_content(body)
        with self._smtp() as server:
            server.send_message(msg)

    def _smtp(self):
        if self.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            server.starttls()
        server.login(self.smtp_user, self.smtp_password)
        return server

    def check_smtp(self):
        """Log in to the SMTP server without sending anything."""
        with self._smtp():
            pass

    def fetch_replies(self, dry_run=False):
        """Return unread chain replies and mark them as read (dry_run: only log in)."""
        replies = []
        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
            imap.login(self.imap_user, self.imap_password)
            status, _ = imap.select(self.imap_folder)
            if status != "OK":
                raise RuntimeError(f"IMAP folder not found: {self.imap_folder}")
            if dry_run:
                return replies
            status, data = imap.search(None, '(UNSEEN SUBJECT "[CHAIN-")')
            if status != "OK":
                return replies
            for num in data[0].split():
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                reply = parse_reply(msg_data[0][1])
                if reply:
                    replies.append(reply)
                imap.store(num, "+FLAGS", "\\Seen")
        return replies
