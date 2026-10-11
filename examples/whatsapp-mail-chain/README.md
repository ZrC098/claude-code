# Chaîne de validation WhatsApp → e-mails par rangs

Un message reçu sur un groupe WhatsApp déclenche une demande par e-mail au
**rang 1**. Selon la réponse à ce mail, la demande part au **rang 2** (une
autre liste de destinataires), puis au rang 3, et ainsi de suite jusqu'à la
**liste finale**.

```
WhatsApp (groupe) ──webhook──▶ mail Rang 1 ──réponse "OK"──▶ mail Rang 2 ──▶ … ──▶ Rang final
                                    │                           │
                                    └── réponse "NON" ──▶ demande refusée et close
```

- Python 3.9+ uniquement, sans dépendance à installer.
- Envoi par SMTP, lecture des réponses par IMAP (Gmail, Outlook/Office 365, OVH…).
- Chaque demande porte un identifiant dans l'objet (`[CHAIN-1a2b3c4d]`) qui
  relie la réponse à la bonne demande.
- L'état est gardé dans une base SQLite locale : rien ne se perd si le service redémarre.

## Fonctionnement

1. Un message arrive sur un groupe autorisé (`whatsapp.groups`). Si un
   `trigger_prefix` est défini (par ex. `#demande`), seuls les messages qui
   commencent par ce préfixe sont pris en compte.
2. Un mail part à tous les destinataires du rang 1, avec le message, l'auteur
   et les réponses attendues.
3. Le service lit la boîte mail toutes les `poll_seconds` secondes. La
   **première ligne** de la réponse est comparée aux mots-clés des règles :
   - `next` : envoi au rang suivant (ou, au dernier rang, demande **validée**) ;
   - `stop` : demande **refusée** et close ;
   - `goto` : envoi à un rang précis (par ex. « revoir » renvoie au rang 1).
4. Le mail du rang suivant reprend l'historique des réponses précédentes.
5. À la clôture, un récapitulatif est envoyé à `notify_on_close` (facultatif).

Garde-fous :
- seule une personne **du rang en cours** peut faire avancer la demande (une
  réponse d'un autre expéditeur est ignorée et journalisée) ;
- une réponse non reconnue (« je regarde ») ne fait rien : la personne peut
  répondre à nouveau ;
- un même message WhatsApp ou un même mail n'est jamais traité deux fois.

## Configuration

Lancez `python3 -m mail_chain setup`, ou copiez `config.example.json` en `config.json` et adaptez :

| Clé | Rôle |
| --- | --- |
| `whatsapp.groups` | Noms ou identifiants (`…@g.us`) des groupes surveillés. Vide = tous. |
| `whatsapp.trigger_prefix` | Préfixe obligatoire du message (vide = tous les messages). |
| `levels` | Les rangs, dans l'ordre : `name`, `recipients`, `timeout_hours`, `rules` (facultatif, remplace les règles globales pour ce rang). |
| `rules` | Règles globales : `keywords` + `action` (`next`, `stop`, `goto` + `target`). |
| `on_timeout` | Sans réponse après `timeout_hours` : `wait` (ne rien faire), `next` (passer au rang suivant) ou `stop`. |
| `notify_on_close` | Adresses qui reçoivent le récapitulatif final. |
| `subject_template`, `body_template` | Modèles des mails (champs : `{chain_id}`, `{group}`, `{sender}`, `{text}`, `{level_name}`, `{level_number}`, `{level_count}`, `{history}`, `{instructions}`). L'objet doit contenir `{chain_id}`. |

Les secrets passent par des variables d'environnement (fichier `.env`, créé par `setup`) :

```bash
export SMTP_HOST=smtp.office365.com SMTP_PORT=587
export SMTP_USER=demandes@monentreprise.fr SMTP_PASSWORD='…'
export IMAP_HOST=outlook.office365.com          # IMAP_USER/IMAP_PASSWORD = SMTP par défaut
export WEBHOOK_TOKEN="$(openssl rand -hex 24)"  # protège le webhook
# Uniquement pour l'API officielle Meta :
export META_VERIFY_TOKEN='…' META_APP_SECRET='…'
```

Utilisez une boîte mail dédiée : le service marque comme lus les mails dont
l'objet contient `[CHAIN-`. Pour Gmail, créez un « mot de passe d'application ».

## Brancher WhatsApp

WhatsApp ne pousse pas les messages de groupe tout seul : il faut un
fournisseur qui appelle le webhook `POST http://<serveur>:8080/webhook/whatsapp`.
Formats reconnus automatiquement :

| Fournisseur | Remarque |
| --- | --- |
| [Green-API](https://green-api.com) | Webhook « incomingMessageReceived ». URL : `…/webhook/whatsapp?token=$WEBHOOK_TOKEN`. |
| [Whapi.Cloud](https://whapi.cloud) | Webhook « messages ». Même URL avec `?token=`. |
| API WhatsApp Cloud (Meta) | Messages de groupe seulement si votre compte a accès à l'API Groups. Vérification par `META_VERIFY_TOKEN`, signature par `META_APP_SECRET`. |
| Votre propre passerelle | JSON `{"id", "group_name", "group_id", "sender", "text"}` avec l'en-tête `Authorization: Bearer $WEBHOOK_TOKEN`. |

Green-API et Whapi relient un numéro WhatsApp normal (scan d'un QR code) :
ajoutez ce numéro dans le groupe. Vérifiez les conditions d'utilisation de
WhatsApp pour votre usage.

Test sans WhatsApp :

```bash
curl -X POST http://localhost:8080/webhook/whatsapp \
  -H "Authorization: Bearer $WEBHOOK_TOKEN" -H 'Content-Type: application/json' \
  -d '{"id":"test-1","group_name":"Équipe Logistique","sender":"Paul","text":"Besoin de 3 palettes"}'
```

## Mise en service pas à pas

1. **Un serveur** joignable depuis Internet (un petit VPS suffit) avec Docker,
   et un nom de domaine (ou sous-domaine) qui pointe vers lui.
2. **Configurer** (5 minutes) :
   ```bash
   git clone <ce dépôt> && cd claude-code/examples/whatsapp-mail-chain
   python3 -m mail_chain setup     # écrit config.json et .env (secrets, lisible par vous seul)
   python3 -m mail_chain check     # teste la connexion à la boîte mail
   ```
3. **Relier WhatsApp** : créez un compte chez Green-API ou Whapi.Cloud, reliez
   un numéro WhatsApp (scan du QR code). Ce numéro doit **rejoindre le groupe** :
   ouvrez le lien d'invitation du groupe sur le téléphone de ce numéro (ni
   Green-API ni Whapi ne savent rejoindre un groupe par lien via leur API).
   Dans le tableau de bord du fournisseur, collez l'URL de webhook affichée
   à la fin de `setup` et activez les messages entrants.
4. **Démarrer** :
   ```bash
   mkdir -p data && sudo chown 1000:1000 data   # le conteneur n'écrit pas en root
   docker compose up -d
   docker compose logs -f app
   ```
   Le HTTPS est obtenu automatiquement par Caddy pour le domaine `DOMAIN` de `.env`.
5. **Vérifier le groupe** : envoyez un message dans le groupe, puis
   `docker compose exec app python3 -m mail_chain groups --config /app/config.json`.
   Si le groupe apparaît « ignoré » (nom différent), copiez son identifiant
   `…@g.us` dans `whatsapp.groups` de `config.json`, puis `docker compose restart app`.

Sans Docker : `deploy/mail-chain.service` (systemd), derrière votre propre reverse proxy HTTPS.

Ne mettez jamais le lien d'invitation du groupe, `config.json` ni `.env`
dans le dépôt : ils sont exclus par `.gitignore`.

## Commandes

```bash
python3 -m mail_chain setup    # questions -> config.json + .env
python3 -m mail_chain check    # config + connexion SMTP/IMAP
python3 -m mail_chain serve    # webhook + lecture des mails en continu
python3 -m mail_chain status   # demandes en cours et leur rang
python3 -m mail_chain groups   # groupes WhatsApp vus et s'ils sont surveillés
python3 -m mail_chain poll     # lire les réponses une seule fois
python3 -m unittest discover -s tests
```

## Limites

- Les réponses sont lues par mots-clés sur la première ligne : gardez des
  consignes simples (OUI / NON).
- Si l'envoi d'un mail échoue au moment de passer au rang suivant, l'erreur
  est journalisée ; vérifiez les logs et l'état avec `status`.
