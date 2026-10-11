// Whapi webhook: a message in a watched WhatsApp group starts a chain and
// e-mails the first list (rang 1).
// URL: https://<projet>.supabase.co/functions/v1/whatsapp-webhook?token=<mc_settings.webhook_token>
import { admin, Chain, json, loadConfig, safeEqual, sendLevel } from "./chain.ts";

type Msg = { id: string; group: string; groupName: string; sender: string; senderName: string; text: string };

// Whapi format: {"messages": [{id, from_me, type, chat_id, chat_name?, from, from_name, text: {body}}]}
// Generic format (tests, other bridges): {id, group_id, sender, text}
function parse(body: any): Msg[] {
  if (Array.isArray(body?.messages)) {
    return body.messages
      .filter((m: any) => !m.from_me && String(m.chat_id ?? "").endsWith("@g.us") && m.text?.body)
      .map((m: any) => ({
        id: String(m.id), group: m.chat_id, groupName: String(m.chat_name ?? ""),
        sender: String(m.from ?? ""), senderName: String(m.from_name ?? ""), text: String(m.text.body),
      }));
  }
  if (body?.text && body?.group_id) {
    return [{ id: String(body.id ?? crypto.randomUUID()), group: String(body.group_id), groupName: "",
      sender: String(body.sender ?? ""), senderName: "", text: String(body.text) }];
  }
  return [];
}

// Compare phone numbers whatever their format: 06…, +33 6…, 336…@s.whatsapp.net.
const phoneKey = (v: string) => {
  const digits = v.split("@")[0].replace(/\D/g, "");
  return digits.length >= 9 ? digits.slice(-9) : digits;
};

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ ok: false }, 405);
  const sb = admin();
  const { settings: s, levels } = await loadConfig(sb);

  const token = new URL(req.url).searchParams.get("token") ?? "";
  if (!safeEqual(token, s.webhook_token)) return json({ ok: false, error: "unauthorized" }, 401);

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ ok: false, error: "invalid json" }, 400);
  }

  const started: string[] = [];
  const ignored: string[] = [];
  for (const m of parse(body)) {
    await sb.from("mc_seen_groups").upsert(
      { group_id: m.group, ...(m.groupName ? { name: m.groupName } : {}), last_seen: new Date().toISOString() },
      { onConflict: "group_id" },
    );
    if (!s.groups.includes(m.group)) { ignored.push("group"); continue; }
    if (s.allowed_senders.length) {
      const allowed = new Set(s.allowed_senders.map(phoneKey).filter(Boolean));
      // Numbers only: display names are chosen by the sender and can be faked.
      if (!allowed.has(phoneKey(m.sender))) { ignored.push("sender"); continue; }
    }
    if (!levels.length) { ignored.push("no levels"); continue; }
    if (s.max_chains_per_hour > 0) {
      const since = new Date(Date.now() - 3600_000).toISOString();
      const { count } = await sb.from("mc_chains").select("id", { count: "exact", head: true }).gte("created_at", since);
      if ((count ?? 0) >= s.max_chains_per_hour) {
        console.error(`max_chains_per_hour (${s.max_chains_per_hour}) reached, message ${m.id} ignored`);
        ignored.push("rate limit");
        continue;
      }
    }
    const { data, error } = await sb.from("mc_chains").upsert({
      wa_message_id: m.id, grp: m.groupName || m.group, sender: m.senderName || m.sender.split("@")[0], text: m.text,
    }, { onConflict: "wa_message_id", ignoreDuplicates: true }).select();
    if (error) throw new Error(`insert chain: ${error.message}`);
    if (!data?.length) { ignored.push("duplicate"); continue; }
    const chain = data[0] as Chain;
    await sendLevel(sb, s, levels, chain);
    started.push(chain.id);
  }
  // Always 200 once authenticated, so Whapi does not retry filtered messages.
  return json({ ok: true, started, ignored });
});
