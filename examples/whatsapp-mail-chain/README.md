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

Copiez `config.example.json` en `config.json` et adaptez :

| Clé | Rôle |
| --- | --- |
| `whatsapp.groups` | Noms ou identifiants (`…@g.us`) des groupes surveillés. Vide = tous. |
| `whatsapp.trigger_prefix` | Préfixe obligatoire du message (vide = tous les messages). |
| `levels` | Les rangs, dans l'ordre : `name`, `recipients`, `timeout_hours`, `rules` (facultatif, remplace les règles globales pour ce rang). |
| `rules` | Règles globales : `keywords` + `action` (`next`, `stop`, `goto` + `target`). |
| `on_timeout` | Sans réponse après `timeout_hours` : `wait` (ne rien faire), `next` (passer au rang suivant) ou `stop`. |
| `notify_on_close` | Adresses qui reçoivent le récapitulatif final. |
| `subject_template`, `body_template` | Modèles des mails (champs : `{chain_id}`, `{group}`, `{sender}`, `{text}`, `{level_name}`, `{level_number}`, `{level_count}`, `{history}`, `{instructions}`). L'objet doit contenir `{chain_id}`. |

Les secrets passent par des variables d'environnement :

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

## Lancer

```bash
cd examples/whatsapp-mail-chain
python3 -m mail_chain serve  --config config.json   # webhook + lecture des mails en continu
python3 -m mail_chain status --config config.json   # demandes en cours et leur rang
python3 -m mail_chain poll   --config config.json   # lire les réponses une seule fois
python3 -m unittest discover -s tests               # tests
```

En production, placez le service derrière un reverse proxy HTTPS (Caddy,
nginx) et lancez-le avec systemd ou Docker pour qu'il redémarre tout seul.

## Limites

- Les réponses sont lues par mots-clés sur la première ligne : gardez des
  consignes simples (OUI / NON).
- Si l'envoi d'un mail échoue au moment de passer au rang suivant, l'erreur
  est journalisée ; vérifiez les logs et l'état avec `status`.
