"""One-shot Discord server & bot rebrand helper for Outspec."""
import base64, json, os, re, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
DTOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD = CFG["guild"]


def dc(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        "https://discord.com/api/v10" + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bot " + DTOKEN,
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/Troooge/Outspec.gg, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, raw[:500]


def data_uri(rel_path):
    p = os.path.join(ROOT, rel_path)
    b64 = base64.b64encode(open(p, "rb").read()).decode("ascii")
    return "data:image/png;base64," + b64


def replace_brand(text):
    if not text:
        return text
    out = text
    out = re.sub(r"https?://github\.com/Troooge/augury", "https://github.com/Troooge/Outspec.gg", out, flags=re.I)
    out = re.sub(r"Troooge/augury", "Troooge/Outspec.gg", out, flags=re.I)
    out = out.replace("feanZupp46", "ugwaArqYuA")
    out = out.replace("AUGURY", "OUTSPEC")
    out = out.replace("Augury", "Outspec")
    out = out.replace("augury", "outspec")
    return out


def main():
    # 1. Bot user
    st, me = dc("GET", "/users/@me")
    print(f"BOT BEFORE ({st}): username={me.get('username') if isinstance(me, dict) else me} avatar={me.get('avatar') if isinstance(me, dict) else None}")
    bot_patch = {"avatar": data_uri("assets/discord/bot-avatar-512.png")}
    if isinstance(me, dict) and me.get("username") != "Outspec":
        bot_patch["username"] = "Outspec"
    st, res = dc("PATCH", "/users/@me", bot_patch)
    if st == 200 and isinstance(res, dict):
        print(f"BOT AFTER (200): username={res.get('username')} avatar={res.get('avatar')}")
    else:
        print(f"BOT PATCH FAILED ({st}): {res}")
        # Retry avatar-only if username change rate-limited or taken
        if "username" in bot_patch:
            st2, res2 = dc("PATCH", "/users/@me", {"avatar": bot_patch["avatar"]})
            print(f"BOT AVATAR-ONLY ({st2}): {res2.get('avatar') if isinstance(res2, dict) else res2}")

    # 2. Guild info & rebrand
    st, guild = dc("GET", f"/guilds/{GUILD}")
    if st != 200 or not isinstance(guild, dict):
        print(f"GUILD GET FAILED ({st}): {guild}")
        return
    print(f"GUILD BEFORE: name={guild.get('name')!r} desc={guild.get('description')!r} tier={guild.get('premium_tier')} icon={guild.get('icon')} banner={guild.get('banner')}")

    guild_patch = {
        "name": "Outspec",
        "icon": data_uri("assets/discord/server-icon-512.png"),
    }
    if guild.get("description") is not None:
        new_desc = replace_brand(guild.get("description"))
        if not new_desc or "Augury" in (guild.get("description") or ""):
            new_desc = "Outspec: augment and item calls for League of Legends Arena. Get help, send feedback, follow updates."
        guild_patch["description"] = new_desc
    if (guild.get("premium_tier") or 0) >= 2:
        guild_patch["banner"] = data_uri("assets/discord/banner-clean-1920x1080.png")

    st, res = dc("PATCH", f"/guilds/{GUILD}", guild_patch)
    if st == 200 and isinstance(res, dict):
        print(f"GUILD AFTER (200): name={res.get('name')!r} desc={res.get('description')!r} icon={res.get('icon')} banner={res.get('banner')}")
    else:
        print(f"GUILD PATCH FAILED ({st}): {res}")

    # 3. Channels & forum topics
    st, channels = dc("GET", f"/guilds/{GUILD}/channels")
    if st != 200 or not isinstance(channels, list):
        print(f"CHANNELS GET FAILED ({st}): {channels}")
        return
    print(f"CHANNELS ({len(channels)}):")
    for ch in channels:
        cid = ch["id"]
        cname = ch.get("name", "")
        topic = ch.get("topic")
        new_name = replace_brand(cname)
        new_topic = replace_brand(topic)
        print(f"  - #{cname} (id={cid}, type={ch.get('type')}) topic={topic!r}")
        patch = {}
        if new_name != cname:
            patch["name"] = new_name
        if new_topic != topic:
            patch["topic"] = new_topic
        if patch:
            st_c, res_c = dc("PATCH", f"/channels/{cid}", patch)
            print(f"    -> PATCH channel ({st_c}): {patch}")
            time.sleep(0.5)

        # Check bot-authored pinned/recent messages in text channels for Augury wording
        if ch.get("type") in (0, 5):
            st_m, msgs = dc("GET", f"/channels/{cid}/messages?limit=20")
            if st_m == 200 and isinstance(msgs, list):
                for m in msgs:
                    if m.get("author", {}).get("id") == me.get("id"):
                        content = m.get("content") or ""
                        embeds = m.get("embeds") or []
                        new_content = replace_brand(content)
                        new_embeds = json.loads(replace_brand(json.dumps(embeds)))
                        if new_content != content or new_embeds != embeds:
                            st_e, _ = dc("PATCH", f"/channels/{cid}/messages/{m['id']}", {
                                "content": new_content,
                                "embeds": new_embeds,
                            })
                            print(f"    -> PATCH bot message {m['id']} in #{cname} ({st_e})")
                            time.sleep(0.5)


if __name__ == "__main__":
    main()
