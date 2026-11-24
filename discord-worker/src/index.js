// Augury Discord interactions: private support tickets.
// Deployed on Cloudflare Workers; Discord calls this URL when someone clicks a button.

const API = "https://discord.com/api/v10";
const PURPLE = 0x8b5cf6;

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("Augury interactions endpoint", { status: 200 });

    const body = await request.text();
    if (!(await verify(request, body, env.DISCORD_PUBLIC_KEY))) {
      return new Response("bad signature", { status: 401 });
    }
    const i = JSON.parse(body);

    if (i.type === 1) return json({ type: 1 }); // PING

    if (i.type === 3) { // button click
      const id = i.data.custom_id;
      if (id === "ticket:open" || id === "ticket:close") {
        // Acknowledge now (Discord wants an answer within 3 s), finish in the background.
        ctx.waitUntil(handle(id, i, env).catch((e) => followup(i, env, "⚠️ Something went wrong. Please ping a moderator.", e)));
        return json({ type: 5, data: { flags: 64 } }); // deferred, ephemeral
      }
    }
    return json({ type: 4, data: { content: "Unknown action.", flags: 64 } });
  },
};

async function handle(id, i, env) {
  if (id === "ticket:open") return openTicket(i, env);
  return closeTicket(i, env);
}

async function openTicket(i, env) {
  const user = i.member.user;
  const name = `🎫・${(user.global_name || user.username).slice(0, 80)}`;
  const channel = i.channel_id;

  // One open ticket per person: reuse it if it exists.
  const active = await dc(env, "GET", `/guilds/${i.guild_id}/threads/active`);
  for (const t of active.threads || []) {
    if (t.parent_id !== channel || t.type !== 12) continue;
    const m = await dc(env, "GET", `/channels/${t.id}/thread-members/${user.id}`, null, true);
    if (m && t.owner_id === env.APP_ID && t.name.endsWith(`#${user.id.slice(-4)}`)) {
      return followup(i, env, `You already have an open ticket: <#${t.id}>`);
    }
  }

  const thread = await dc(env, "POST", `/channels/${channel}/threads`, {
    name: `${name} #${user.id.slice(-4)}`,
    type: 12, // private thread
    invitable: false,
    auto_archive_duration: 10080,
  });
  await dc(env, "PUT", `/channels/${thread.id}/thread-members/${user.id}`);

  const staff = (env.STAFF_ROLE_IDS || "").split(",").filter(Boolean);
  await dc(env, "POST", `/channels/${thread.id}/messages`, {
    content: `<@${user.id}> ${staff.map((r) => `<@&${r}>`).join(" ")}`,
    allowed_mentions: { users: [user.id], roles: staff },
    embeds: [{
      color: PURPLE,
      title: "🎫 Private support ticket",
      description:
        "Only you and the Augury team can see this thread.\n\n" +
        "Tell us what's going on. If it's about the app, include **Settings → Copy diagnostics**.\n" +
        "**Never share your password.** We will never ask for it.",
    }],
    components: [{ type: 1, components: [{ type: 2, style: 4, label: "Close ticket", emoji: { name: "🔒" }, custom_id: "ticket:close" }] }],
  });

  return followup(i, env, `✅ Your private ticket is open: <#${thread.id}>`);
}

async function closeTicket(i, env) {
  const thread = await dc(env, "GET", `/channels/${i.channel_id}`);
  if (thread.type !== 12) return followup(i, env, "This button only works inside a ticket.");
  const perms = BigInt(i.member.permissions || "0");
  const isStaff = (perms & (1n << 34n)) !== 0n || (perms & 8n) !== 0n; // MANAGE_THREADS or ADMINISTRATOR
  const isOwner = thread.name.endsWith(`#${i.member.user.id.slice(-4)}`);
  if (!isStaff && !isOwner) return followup(i, env, "Only the ticket owner or staff can close this ticket.");

  await dc(env, "POST", `/channels/${thread.id}/messages`, {
    embeds: [{ color: PURPLE, description: `🔒 Ticket closed by <@${i.member.user.id}>.` }],
    allowed_mentions: { parse: [] },
  });
  await followup(i, env, "Ticket closed.");
  await dc(env, "PATCH", `/channels/${thread.id}`, { archived: true, locked: true });
}

// ---------- helpers ----------

async function dc(env, method, path, body, allow404 = false) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const res = await fetch(API + path, {
      method,
      headers: { Authorization: `Bot ${env.DISCORD_BOT_TOKEN}`, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 429) {
      const { retry_after = 1 } = await res.json().catch(() => ({}));
      await new Promise((r) => setTimeout(r, retry_after * 1000 + 200));
      continue;
    }
    if (allow404 && res.status === 404) return null;
    if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${await res.text()}`);
    return res.status === 204 ? null : res.json();
  }
  throw new Error(`${method} ${path}: rate limited`);
}

async function followup(i, env, content, err) {
  if (err) console.error(err);
  await fetch(`${API}/webhooks/${env.APP_ID}/${i.token}/messages/@original`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, allowed_mentions: { parse: [] } }),
  });
}

function json(obj) {
  return new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });
}

async function verify(request, body, publicKey) {
  const sig = request.headers.get("X-Signature-Ed25519");
  const ts = request.headers.get("X-Signature-Timestamp");
  if (!sig || !ts || !publicKey) return false;
  const key = await crypto.subtle.importKey("raw", hex(publicKey), { name: "Ed25519" }, false, ["verify"]);
  return crypto.subtle.verify("Ed25519", key, hex(sig), new TextEncoder().encode(ts + body));
}

function hex(s) {
  const out = new Uint8Array(s.length / 2);
  for (let k = 0; k < out.length; k++) out[k] = parseInt(s.substr(k * 2, 2), 16);
  return out;
}
