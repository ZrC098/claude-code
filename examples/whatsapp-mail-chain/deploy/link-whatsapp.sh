#!/usr/bin/env bash
# Link a WhatsApp number to WAHA with a pairing code (no QR code needed).
#   ./deploy/link-whatsapp.sh 0612345678
# Then on the phone: WhatsApp > Paramètres > Appareils connectés >
# Connecter un appareil > « Connecter avec un numéro de téléphone » > type the code.
# The number is only sent to your own WAHA; it is not written to any file.
set -euo pipefail
cd "$(dirname "$0")/.."

number="${1:?usage: $0 <numéro, ex. 0612345678 ou +33612345678>}"
digits="$(printf '%s' "$number" | tr -cd '0-9')"
# French national format 0X XX XX XX XX -> 33X XX XX XX XX
if [[ ${#digits} -eq 10 && $digits == 0* ]]; then digits="33${digits:1}"; fi

key="$(grep -E '^WAHA_API_KEY=' .env | cut -d= -f2-)"
[[ -n $key ]] || { echo "WAHA_API_KEY absent de .env : lancez python3 -m mail_chain setup" >&2; exit 1; }
api="http://127.0.0.1:3000/api"
call() { curl -sS -H "X-Api-Key: $key" -H 'Content-Type: application/json' "$@"; }

call -X POST "$api/sessions" -d '{"name":"default"}' >/dev/null || true
call -X POST "$api/sessions/default/start" >/dev/null || true

for _ in $(seq 1 30); do
  status="$(call "$api/sessions/default" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null || true)"
  case "$status" in
    WORKING) echo "Déjà connecté."; exit 0 ;;
    SCAN_QR_CODE) break ;;
  esac
  sleep 2
done
[[ $status == SCAN_QR_CODE ]] || { echo "WAHA n'est pas prêt (statut : ${status:-inconnu}). docker compose logs waha" >&2; exit 1; }

code="$(call -X POST "$api/default/auth/request-code" -d "{\"phoneNumber\":\"$digits\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("code",""))')"
[[ -n $code ]] || { echo "Pas de code reçu : utilisez le QR code du tableau de bord WAHA (http://127.0.0.1:3000/dashboard)." >&2; exit 1; }
echo "Code à saisir dans WhatsApp sur le téléphone : $code"
echo "Puis vérifiez : curl -s -H 'X-Api-Key: …' $api/sessions/default  (statut WORKING)"
