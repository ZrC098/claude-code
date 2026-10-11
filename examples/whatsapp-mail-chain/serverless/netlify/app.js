// Decision page: shows the request behind a signed link and sends the answer.
// Nothing here is secret: the signed link in the URL is what authorises the answer.
const API = "https://zfumfywhjwgjejtdkoiu.supabase.co/functions/v1/decision";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const token = params.get("t") || "";
const preselected = params.get("a");

// Keep the link out of the address bar history once read.
history.replaceState(null, "", location.pathname);

function show(el, on = true) { el.hidden = !on; }
function done(message, ok = true) {
  show($("form"), false);
  $("result").textContent = message;
  $("result").className = ok ? "ok" : "error";
  show($("result"));
}

async function call(method, body) {
  const res = await fetch(method === "GET" ? `${API}?t=${encodeURIComponent(token)}` : API, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ ok: false, error: "Réponse illisible du serveur." }));
  if (!res.ok || !data.ok) throw new Error(data.error || `Erreur ${res.status}`);
  return data;
}

let needPrice = false;

async function answer(action) {
  const price = $("price").value.trim();
  if (action === "approve" && needPrice && !/^\d[\d\s]*([.,]\d{1,2})?\s*€?$/.test(price)) {
    $("result").textContent = "Indiquez le prix de votre offre (ex. : 850).";
    $("result").className = "error";
    show($("result"));
    $("price").focus();
    return;
  }
  $("approve").disabled = $("reject").disabled = true;
  try {
    const r = await call("POST", { t: token, action, price, comment: $("comment").value });
    done(r.message, r.recorded);
  } catch (e) {
    done(e.message, false);
  }
}

(async () => {
  if (!token) return done("Lien incomplet : ouvrez le bouton reçu par e-mail.", false);
  try {
    const d = await call("GET");
    $("level").textContent = `${d.level_name} (étape ${d.level_number}/${d.level_count})`;
    $("group").textContent = d.group;
    $("sender").textContent = d.sender;
    $("text").textContent = d.text;
    $("recipient").textContent = d.recipient;
    show($("request"));
    show($("status"), false);

    if (d.already_answered) return done(`Vous avez déjà répondu : ${d.already_answered}.`);
    if (!d.open) {
      return done(d.status === "open" ? "Cette demande n'attend plus de réponse de votre part."
        : `Cette demande est close (${d.status}).`);
    }
    needPrice = !!d.need_price;
    $("approve-label").textContent = d.labels.approve;
    show($("price-row"), needPrice);
    $("approve").textContent = d.labels.approve;
    $("reject").textContent = d.labels.reject;
    if (preselected === "approve" || preselected === "reject") $(preselected).classList.add("preselected");
    $("approve").onclick = () => answer("approve");
    $("reject").onclick = () => answer("reject");
    show($("form"));
  } catch (e) {
    show($("status"), false);
    done(e.message, false);
  }
})();
