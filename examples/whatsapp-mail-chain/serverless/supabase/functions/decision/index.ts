// Called by the Netlify decision page.
//   GET  ?t=<signed link>                    -> what the request is about (no side effect,
//                                               so mail scanners opening links change nothing)
//   POST {t, action: approve|reject, price, comment} -> records the answer and moves the chain on
//   (in cascade mode « Oui, dispo » needs the offer price, written to the pilotage app)
import {
  admin, Chain, closeInPilotage, cors, json, labels, loadConfig, notifyClose, sendLevel, verifyLink,
} from "./chain.ts";

// « 850 », « 850,50 », « 1 200 € » -> number; anything else -> null.
function parsePrice(v: unknown): number | null {
  const n = Number(String(v ?? "").replace(/[\s\u00a0€]/g, "").replace(",", "."));
  return Number.isFinite(n) && n > 0 && n < 100000 ? Math.round(n * 100) / 100 : null;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "GET" && req.method !== "POST") return json({ ok: false }, 405);

  const sb = admin();
  const { settings: s, levels } = await loadConfig(sb);
  let input: { t?: string; action?: string; comment?: string; price?: unknown } = {};
  if (req.method === "POST") {
    try {
      input = await req.json();
    } catch {
      return json({ ok: false, error: "Requête invalide." }, 400);
    }
  } else {
    input.t = new URL(req.url).searchParams.get("t") ?? "";
  }

  const link = await verifyLink(s.link_secret, input.t ?? "");
  if (!link) return json({ ok: false, error: "Lien invalide ou expiré." }, 403);

  const { data: chain } = await sb.from("mc_chains").select("*").eq("id", link.c).single();
  if (!chain) return json({ ok: false, error: "Demande introuvable." }, 404);
  const level = levels[link.l];
  if (!level || !level.recipients.includes(link.r)) {
    return json({ ok: false, error: "Ce lien ne correspond plus à la liste de destinataires." }, 403);
  }

  const { data: mine } = await sb.from("mc_events").select("detail").eq("chain_id", chain.id)
    .eq("level", link.l).eq("kind", "decision").eq("actor", link.r).maybeSingle();
  const l = labels(s.mode);
  const state = {
    group: chain.grp, sender: chain.sender, text: chain.text,
    level_name: level.name, level_number: link.l + 1, level_count: levels.length,
    recipient: link.r, labels: { approve: l.approve, reject: l.reject },
    need_price: s.mode === "cascade",
    open: chain.status === "open" && chain.level === link.l,
    status: chain.status === "open" ? "open" : chain.status === "approved" ? l.approved : l.rejected,
    already_answered: mine ? (String(mine.detail).startsWith("approve") ? l.approve : l.reject) : null,
  };
  if (req.method === "GET") return json({ ok: true, ...state });

  if (input.action !== "approve" && input.action !== "reject") {
    return json({ ok: false, error: "Action inconnue." }, 400);
  }
  const comment = String(input.comment ?? "").slice(0, 500).trim();
  const price = parsePrice(input.price);
  if (input.action === "approve" && state.need_price && price == null) {
    return json({ ok: false, error: "Indiquez le prix de votre offre." }, 400);
  }
  const { data: rows, error } = await sb.rpc("mc_apply_decision", {
    p_chain: chain.id, p_level: link.l, p_actor: link.r, p_action: input.action, p_comment: comment,
    p_price: input.action === "approve" ? price : null,
  });
  if (error) throw new Error(`mc_apply_decision: ${error.message}`);
  const r = rows[0] as { status: string; level: number; moved: boolean };

  if (r.moved) {
    const { data: fresh } = await sb.from("mc_chains").select("*").eq("id", chain.id).single();
    const updated = (fresh ?? { ...chain, status: r.status, level: r.level }) as Chain;
    if (r.status === "open") await sendLevel(sb, s, levels, updated);
    else {
      await closeInPilotage(sb, levels, updated);
      await notifyClose(sb, s, levels, updated);
    }
  }
  const recorded = !state.already_answered && state.open;
  return json({
    ok: true,
    recorded,
    message: !recorded
      ? (state.already_answered ? `Vous avez déjà répondu : ${state.already_answered}.`
        : "Cette demande n'attend plus de réponse de votre part.")
      : r.status === "open" && r.level > link.l
      ? `Réponse enregistrée. La demande passe à « ${levels[r.level].name} ».`
      : r.status === "open"
      ? "Réponse enregistrée. Merci."
      : `Réponse enregistrée. La demande est ${r.status === "approved" ? l.approved : l.rejected}.`,
  });
});
