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
| [WAHA](https://waha.devlike.pro) (auto-hébergé, gratuit) | Inclus dans `compose.yaml`, rien à exposer. Voir ci-dessous. |
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

## Mise en service pas à pas (WAHA, recommandé)

[WAHA](https://waha.devlike.pro) est une passerelle WhatsApp gratuite que vous
hébergez vous-même : vos messages ne passent par aucun service tiers, et rien
n'est ouvert sur Internet (WAHA se connecte lui-même à WhatsApp).

1. **Un serveur allumé en permanence** avec Docker (petit VPS, vieux PC, Raspberry Pi).
2. **Configurer** :
   ```bash
   git clone <ce dépôt> && cd claude-code/examples/whatsapp-mail-chain
   python3 -m mail_chain setup     # écrit config.json et .env (secrets, lisible par vous seul)
   python3 -m mail_chain check     # teste la connexion à la boîte mail
   ```
3. **Démarrer** :
   ```bash
   mkdir -p data && sudo chown 1000:1000 data   # le conteneur n'écrit pas en root
   docker compose up -d
   ```
4. **Relier le numéro WhatsApp** (sur le serveur) :
   ```bash
   ./deploy/link-whatsapp.sh 06XXXXXXXX
   ```
   Le script affiche un code de 8 caractères. Sur le téléphone : WhatsApp >
   Paramètres > Appareils connectés > Connecter un appareil > « Connecter avec
   un numéro de téléphone », puis saisissez le code. À défaut, scannez le QR
   code du tableau de bord WAHA : `ssh -L 3000:127.0.0.1:3000 <serveur>`, puis
   http://localhost:3000/dashboard (clé : `WAHA_API_KEY` de `.env`).
5. **Le groupe** : le numéro doit en faire partie (sinon ouvrez le lien
   d'invitation sur ce téléphone). Envoyez un message dans le groupe, puis
   `docker compose exec app python3 -m mail_chain groups --config /app/config.json`.
   WAHA ne transmet pas le nom du groupe : copiez son identifiant `…@g.us`
   dans `whatsapp.groups` de `config.json`, puis `docker compose restart app`.

Avec Green-API / Whapi à la place de WAHA : renseignez `DOMAIN` dans `.env`,
lancez `docker compose --profile public up -d` (HTTPS automatique par Caddy) et
donnez au fournisseur l'URL `https://<DOMAIN>/webhook/whatsapp?token=<WEBHOOK_TOKEN>`.

Sans Docker : `deploy/mail-chain.service` (systemd), derrière votre propre reverse proxy HTTPS.

## Sécurité (les mails partent vers des clients)

- **Qui déclenche** : `whatsapp.allowed_senders` liste les numéros autorisés
  (`06…`, `+33…` ou l'identifiant affiché dans les logs). Seul le numéro compte,
  jamais le nom affiché, qui peut être imité. Vide = tout membre du groupe.
- **Groupe** : seuls les groupes de `whatsapp.groups` sont pris en compte.
- **Débit** : `max_chains_per_hour` (10 par défaut) bloque un emballement ou un abus.
- **Destinataires figés** : les adresses viennent uniquement de `config.json`,
  jamais du message WhatsApp ; seule une personne du rang en cours fait avancer la demande.
- **Réseau** : WAHA et le webhook ne sont pas exposés à Internet ; l'API WAHA
  est protégée par `WAHA_API_KEY`, le webhook par `WEBHOOK_TOKEN` (aléatoires, dans `.env`).
- **Mail** : boîte dédiée, mot de passe d'application, TLS obligatoire. Pour
  ne pas finir en spam chez vos clients, vérifiez que SPF, DKIM et DMARC sont
  configurés sur le domaine d'envoi.
- **Numéro** : WAHA n'est pas une API officielle de WhatsApp ; un compte relié
  ainsi peut être suspendu. Un numéro dédié limite le risque pour votre ligne principale.
- **Secrets** : `config.json`, `.env`, `data/` (session WhatsApp comprise) et
  le lien d'invitation ne doivent jamais être versionnés.

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
