# Version sans serveur : Whapi + Supabase + Netlify

```
Groupe WhatsApp ──Whapi──▶ Supabase « whatsapp-webhook » ──Brevo──▶ mail Rang 1 (un lien personnel par destinataire)
                                                                        │ clic Valider / Refuser
                                                                        ▼
                      Page Netlify « reponse-demandes-tps » ──▶ Supabase « decision » ──▶ mail Rang suivant … ou clôture
```

- Pas de serveur à maintenir, pas de boîte mail à lire : la réponse se fait par
  boutons, dans une page Netlify.
- Chaque destinataire reçoit un **lien personnel signé** (HMAC), valable 7 jours.
  Ouvrir le lien ne fait rien (un antivirus qui « clique » sur les liens ne peut
  pas répondre à votre place) : il faut appuyer sur un bouton.
- Une seule réponse par personne et par rang ; la base verrouille la demande
  pendant le traitement, donc deux clics simultanés ne sautent jamais un rang.

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `supabase/migrations/001_mail_chain.sql` | Tables `mc_*` (RLS active, aucune politique : seules les fonctions y accèdent) et fonction `mc_apply_decision`. |
| `supabase/functions/whatsapp-webhook/` | Reçoit Whapi, démarre la demande, envoie le rang 1. |
| `supabase/functions/decision/` | Lit et enregistre les réponses, envoie le rang suivant ou la clôture. |
| `netlify/` | Page des boutons (HTML/JS statique, en-têtes de sécurité dans `_headers`). |

`chain.ts` est copié à l'identique dans les deux fonctions.

## Réglages (Supabase > Table Editor)

**`mc_settings`** (une seule ligne) :

| Colonne | Rôle |
| --- | --- |
| `trigger_keyword` | Mot obligatoire dans le message (`chargement`, sans tenir compte des majuscules ni des accents). Les messages sans ce mot, comme « Solution trouvée », sont ignorés. |
| `mode` | `validation` : Valider → rang suivant, Refuser → clôture. `cascade` : Oui, dispo → clôture (bookée), Pas dispo → rang suivant quand tout le rang a refusé. |
| `groups` | Identifiants des groupes surveillés (`…@g.us`). Vide = aucun : les groupes vus apparaissent dans `mc_seen_groups`. |
| `allowed_senders` | Numéros autorisés à déclencher (vide = tout le groupe). Le nom affiché n'est jamais utilisé. |
| `max_chains_per_hour` | Plafond de demandes par heure (10). |
| `mail_from` | Expéditeur, ex. `Transports BYVARI <adresse@orange.fr>` : adresse vérifiée chez Brevo (ou domaine vérifié chez Resend). |
| `notify_on_close` | Adresses qui reçoivent le récapitulatif final. |
| `decision_page_url` | `https://reponse-demandes-tps.netlify.app/` |
| `webhook_token`, `link_secret` | Générés au hasard par la base. Ne pas diffuser. |

**`mc_levels`** : une ligne par rang (`position` 0 = rang 1, `name`, `recipients` = liste d'adresses,
`carrier` = nom du transporteur écrit dans l'application de pilotage ; vide = l'adresse e-mail).

**Secret Edge Functions** (Supabase > Edge Functions > Secrets) : `BREVO_API_KEY` (aucun nom de domaine requis,
l'adresse d'expéditeur est vérifiée par mail chez Brevo) ou, à défaut, `RESEND_API_KEY` (domaine vérifié requis).

## Lien avec l'application de pilotage

1. Message « Chargement » : la fonction existante `whatsapp-demande` découpe le message (lieu de chargement,
   livraison, date, heure, véhicule, km, prix estimés) et crée le transport **« À valider »** (`records`,
   collection `transports`). Les identifiants sont gardés dans `mc_chains.pilotage_ids`.
2. « Oui, dispo » + prix : le transport passe **« Acceptée »** avec le transporteur et le **prix d'achat**
   (`mc_pilotage_update`). Le statut ne bouge que s'il est encore « À valider ».
3. Personne de dispo : une note « Sans preneur » est ajoutée, le statut ne change pas.
4. Le **prix de vente à XPO** se saisit dans l'application.

## Suivi

- `mc_chains` : les demandes et leur état (`open`, `approved`, `rejected`).
- `mc_events` : tout l'historique (`sent`, `send_failed` avec la raison, `decision`, `closed`).
