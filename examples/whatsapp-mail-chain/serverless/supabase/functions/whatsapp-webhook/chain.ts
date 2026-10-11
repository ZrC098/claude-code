// Shared by the whatsapp-webhook and decision functions (a copy lives in each).
import { createClient, SupabaseClient } from "npm:@supabase/supabase-js@2";

export const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
  });

export type Settings = {
  mode: "validation" | "cascade";
  groups: string[];
  allowed_senders: string[];
  max_chains_per_hour: number;
  notify_on_close: string[];
  mail_from: string;
  decision_page_url: string;
  link_ttl_hours: number;
  webhook_token: string;
  link_secret: string;
};
export type Level = { position: number; name: string; recipients: string[] };
export type Chain = {
  id: string; grp: string; sender: string; text: string; level: number; status: string;
};

export function admin(): SupabaseClient {
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
    JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") ?? "{}").default;
  return createClient(Deno.env.get("SUPABASE_URL")!, key, { auth: { persistSession: false } });
}

export async function loadConfig(sb: SupabaseClient) {
  const [{ data: settings, error: e1 }, { data: levels, error: e2 }] = await Promise.all([
    sb.from("mc_settings").select("*").eq("id", 1).single(),
    sb.from("mc_levels").select("*").order("position"),
  ]);
  if (e1 || e2) throw new Error(`config: ${(e1 ?? e2)!.message}`);
  return { settings: settings as Settings, levels: (levels ?? []) as Level[] };
}

export function safeEqual(a: string, b: string) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export const esc = (s: string) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

// ---- signed decision links ------------------------------------------------

const enc = new TextEncoder();
const b64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (s: string) =>
  Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

async function hmac(secret: string, data: string) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  return b64url(new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(data))));
}

export type LinkPayload = { c: string; l: number; r: string; e: number };

export async function signLink(secret: string, p: LinkPayload) {
  const body = b64url(enc.encode(JSON.stringify(p)));
  return `${body}.${await hmac(secret, body)}`;
}

export async function verifyLink(secret: string, token: string): Promise<LinkPayload | null> {
  const [body, sig] = (token ?? "").split(".");
  if (!body || !sig || !safeEqual(sig, await hmac(secret, body))) return null;
  try {
    const p = JSON.parse(new TextDecoder().decode(fromB64url(body))) as LinkPayload;
    return p.e * 1000 > Date.now() ? p : null;
  } catch {
    return null;
  }
}

// ---- e-mail -----------------------------------------------------------------

export function labels(mode: Settings["mode"]) {
  return mode === "cascade"
    ? { approve: "Oui, dispo", reject: "Pas dispo", approved: "bookée", rejected: "sans preneur",
        intro: "Un transport est à booker. Êtes-vous dispo ?" }
    : { approve: "Valider", reject: "Refuser", approved: "validée", rejected: "refusée",
        intro: "Une demande est arrivée sur WhatsApp. Merci d'y répondre :" };
}

// The Brevo key comes from the BREVO_API_KEY Edge Function secret, or else
// from Supabase Vault (secret "mc_brevo_api_key", read by mc_mail_key()).
async function brevoKey(sb: SupabaseClient) {
  const env = Deno.env.get("BREVO_API_KEY");
  if (env) return env;
  const { data } = await sb.rpc("mc_mail_key");
  return (data as string | null) || null;
}

// Brevo when a key is available (works with a single verified address, no
// domain needed), otherwise Resend (needs a verified domain).
async function sendMail(sb: SupabaseClient, from: string, to: string[], subject: string, html: string, text: string) {
  if (!from) throw new Error("mc_settings.mail_from est vide");
  const brevo = await brevoKey(sb);
  if (brevo) {
    const m = from.match(/^\s*(.*?)\s*<([^>]+)>\s*$/);
    const sender = m ? { name: m[1] || m[2], email: m[2] } : { email: from.trim() };
    const res = await fetch("https://api.brevo.com/v3/smtp/email", {
      method: "POST",
      headers: { "api-key": brevo, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        sender, replyTo: { email: sender.email }, to: to.map((email) => ({ email })),
        subject, htmlContent: html, textContent: text,
      }),
    });
    if (!res.ok) throw new Error(`Brevo ${res.status}: ${(await res.text()).slice(0, 300)}`);
    return;
  }
  const key = Deno.env.get("RESEND_API_KEY");
  if (!key) throw new Error("BREVO_API_KEY ou RESEND_API_KEY manquant (secret Edge Functions)");
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from, to, subject, html, text }),
  });
  if (!res.ok) throw new Error(`Resend ${res.status}: ${(await res.text()).slice(0, 300)}`);
}

export async function history(sb: SupabaseClient, chainId: string, levels: Level[], mode: Settings["mode"]) {
  const { data } = await sb.from("mc_events").select("level, actor, detail, at")
    .eq("chain_id", chainId).eq("kind", "decision").order("at");
  const l = labels(mode);
  return (data ?? []).map((e) => {
    const [action, ...rest] = String(e.detail).split(": ");
    const verb = action === "approve" ? l.approve : l.reject;
    const comment = rest.join(": ");
    const name = levels[e.level]?.name ?? `Rang ${e.level + 1}`;
    return `${name} : ${verb} par ${e.actor}${comment ? ` (« ${comment} »)` : ""}`;
  });
}

const block = (chain: Chain) =>
  `<p style="margin:0 0 4px"><b>Groupe :</b> ${esc(chain.grp)}<br><b>De :</b> ${esc(chain.sender)}</p>
   <blockquote style="margin:12px 0;padding:12px 16px;background:#f4f4f5;border-left:4px solid #a1a1aa;white-space:pre-wrap">${esc(chain.text)}</blockquote>`;

export async function sendLevel(sb: SupabaseClient, s: Settings, levels: Level[], chain: Chain) {
  const level = levels[chain.level];
  const l = labels(s.mode);
  const hist = await history(sb, chain.id, levels, s.mode);
  const exp = Math.floor(Date.now() / 1000) + s.link_ttl_hours * 3600;
  const subject = `${level.name} - Demande WhatsApp (${chain.grp})`;
  const histHtml = hist.length
    ? `<p><b>Historique :</b></p><ul>${hist.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : "";

  for (const to of level.recipients) {
    try {
      if (!s.decision_page_url) throw new Error("mc_settings.decision_page_url est vide");
      const t = await signLink(s.link_secret, { c: chain.id, l: chain.level, r: to, e: exp });
      const url = (a: string) => `${s.decision_page_url}?t=${encodeURIComponent(t)}&a=${a}`;
      const btn = (a: string, label: string, color: string) =>
        `<a href="${esc(url(a))}" style="display:inline-block;padding:12px 22px;margin:4px 8px 4px 0;border-radius:8px;background:${color};color:#fff;text-decoration:none;font-weight:600">${label}</a>`;
      const html = `<div style="font-family:system-ui,Arial,sans-serif;font-size:15px;color:#18181b;max-width:560px">
        <p>Bonjour,</p><p>${esc(l.intro)}</p>${block(chain)}${histHtml}
        <p>${btn("approve", l.approve, "#15803d")}${btn("reject", l.reject, "#b91c1c")}</p>
        <p style="color:#71717a;font-size:13px">${esc(level.name)}, étape ${chain.level + 1}/${levels.length}. Lien personnel, valable ${Math.round(s.link_ttl_hours / 24)} jours : ne le transférez pas.</p></div>`;
      const text = `Demande WhatsApp (${chain.grp}) de ${chain.sender} :\n\n${chain.text}\n\n` +
        (hist.length ? `Historique :\n${hist.map((h) => `- ${h}`).join("\n")}\n\n` : "") +
        `${l.approve} : ${url("approve")}\n${l.reject} : ${url("reject")}\n`;
      await sendMail(sb, s.mail_from, [to], subject, html, text);
      await sb.from("mc_events").insert({ chain_id: chain.id, level: chain.level, kind: "sent", actor: to });
    } catch (err) {
      console.error(`chain ${chain.id}: mail to ${to} failed`, err);
      await sb.from("mc_events").insert({
        chain_id: chain.id, level: chain.level, kind: "send_failed", actor: to, detail: String(err).slice(0, 500),
      });
    }
  }
}

export async function notifyClose(sb: SupabaseClient, s: Settings, levels: Level[], chain: Chain) {
  if (!s.notify_on_close.length) return;
  const l = labels(s.mode);
  const word = chain.status === "approved" ? l.approved : l.rejected;
  const hist = await history(sb, chain.id, levels, s.mode);
  const html = `<div style="font-family:system-ui,Arial,sans-serif;font-size:15px;color:#18181b;max-width:560px">
    <p>La demande WhatsApp est <b>${esc(word)}</b>.</p>${block(chain)}
    <ul>${hist.map((h) => `<li>${esc(h)}</li>`).join("")}</ul></div>`;
  const text = `Demande ${word} (${chain.grp}) :\n\n${chain.text}\n\n${hist.map((h) => `- ${h}`).join("\n")}\n`;
  try {
    await sendMail(sb, s.mail_from, s.notify_on_close, `Demande ${word} - ${chain.grp}`, html, text);
  } catch (err) {
    console.error(`chain ${chain.id}: close notification failed`, err);
    await sb.from("mc_events").insert({
      chain_id: chain.id, level: chain.level, kind: "send_failed", actor: "notify_on_close", detail: String(err).slice(0, 500),
    });
  }
}
