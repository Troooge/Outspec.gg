"""Fix bot guild nickname / app name and inspect all bot messages in the 8 info channels."""
import json, os, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
DTOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD = CFG["guild"]

INFO_CHANNELS = {
    "welcome": "1574598873173991516",
    "rules": "1574598874281279739",
    "announcements": "1574598875459883068",
    "changelog": "1574598876428636160",
    "status": "1574598877477343362",
    "help": "1574598882309046312",
    "feedback": "1574598883605094440",
    "private-support": "1574604676681437226",
}


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
        return e.code, e.read().decode("utf-8", errors="replace")[:500]


def main():
    st, me = dc("GET", "/users/@me")
    print(f"USER ({st}): id={me.get('id')} username={me.get('username')!r} global_name={me.get('global_name')!r}")

    st, app = dc("GET", "/applications/@me")
    print(f"APP BEFORE ({st}): name={app.get('name') if isinstance(app, dict) else app!r}")
    if st == 200 and isinstance(app, dict) and app.get("name") != "Outspec":
        st_a, res_a = dc("PATCH", "/applications/@me", {"name": "Outspec"})
        print(f"APP PATCH ({st_a}): name={res_a.get('name') if isinstance(res_a, dict) else res_a!r}")

    st, mem = dc("GET", f"/guilds/{GUILD}/members/{me['id']}")
    print(f"MEMBER BEFORE ({st}): nick={mem.get('nick') if isinstance(mem, dict) else mem!r}")
    st_n, res_n = dc("PATCH", f"/guilds/{GUILD}/members/@me", {"nick": "Outspec"})
    print(f"MEMBER NICK PATCH ({st_n}): nick={res_n.get('nick') if isinstance(res_n, dict) else res_n!r}")

    for slug, cid in INFO_CHANNELS.items():
        st_m, msgs = dc("GET", f"/channels/{cid}/messages?limit=20")
        print(f"\n=== #{slug} ({cid}) status={st_m} count={len(msgs) if isinstance(msgs, list) else 0} ===")
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            is_bot = m.get("author", {}).get("id") == me["id"]
            atts = [a.get("filename") for a in (m.get("attachments") or [])]
            embs = [
                {
                    "title": e.get("title"),
                    "color": hex(e["color"]) if isinstance(e.get("color"), int) else e.get("color"),
                    "image": (e.get("image") or {}).get("url"),
                    "thumb": (e.get("thumbnail") or {}).get("url"),
                }
                for e in (m.get("embeds") or [])
            ]
            print(f"  msg={m['id']} bot={is_bot} atts={atts} content={repr((m.get('content') or '')[:80])} embeds={json.dumps(embs)}")


if __name__ == "__main__":
    main()
