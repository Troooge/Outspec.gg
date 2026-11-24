"""Replace all old Augury Discord channel banners & purple embed colors with Outspec gold."""
import json, os, time, urllib.error, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DTOKEN = os.environ["DISCORD_BOT_TOKEN"]

PURPLE = 0x8B5CF6
GOLD = 0xC8AA6E

BANNER_MESSAGES = [
    ("welcome", "1574598873173991516", "1574609245599436843", "welcome.png"),
    ("rules", "1574598874281279739", "1574609250863292528", "rules.png"),
    ("announcements", "1574598875459883068", "1574609254621388853", "announcements.png"),
    ("changelog", "1574598876428636160", "1574609259331715154", "changelog.png"),
    ("private-support", "1574604676681437226", "1574609272245981204", "private-support.png"),
]

COLOR_ONLY_MESSAGES = [
    ("changelog-0.2.84", "1574598876428636160", "1574696771550974065"),
    ("status", "1574598877477343362", "1574609268559315026"),
    ("help", "1574598882309046312", "1574609263148531763"),
    ("feedback", "1574598883605094440", "1574609266340274177"),
]


def dc_json(method, path, body=None):
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


def dc_multipart(method, path, payload_dict, filename, file_bytes):
    boundary = "----OutspecBoundary" + uuid.uuid4().hex
    parts = []
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"{json.dumps(payload_dict)}\r\n".encode("utf-8")
    )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        "https://discord.com/api/v10" + path,
        data=body,
        method=method,
        headers={
            "Authorization": "Bot " + DTOKEN,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "DiscordBot (https://github.com/Troooge/Outspec.gg, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:500]


def clean_embed(e, banner_filename=None):
    out = {}
    for k in ("title", "description", "url", "timestamp", "fields", "footer", "author"):
        if k in e and e[k] is not None:
            out[k] = e[k]
    color = e.get("color")
    out["color"] = GOLD if color == PURPLE or color is None else color
    if banner_filename:
        out["image"] = {"url": f"attachment://{banner_filename}"}
    elif e.get("image") and e["image"].get("url"):
        out["image"] = {"url": e["image"]["url"]}
    return out


def main():
    for slug, cid, mid, png_name in BANNER_MESSAGES:
        st, msg = dc_json("GET", f"/channels/{cid}/messages/{mid}")
        if st != 200 or not isinstance(msg, dict):
            print(f"ERROR GET #{slug} {mid}: {st} {msg}")
            continue
        embeds = msg.get("embeds") or []
        new_embeds = [
            clean_embed(e, banner_filename=(png_name if i == 0 else None))
            for i, e in enumerate(embeds)
        ]
        png_bytes = open(os.path.join(ROOT, "assets", "discord-banners", png_name), "rb").read()
        payload = {"attachments": [], "embeds": new_embeds}
        st_p, res = dc_multipart("PATCH", f"/channels/{cid}/messages/{mid}", payload, png_name, png_bytes)
        if st_p == 200 and isinstance(res, dict):
            atts = [a.get("filename") for a in (res.get("attachments") or [])]
            cols = [hex(e["color"]) for e in (res.get("embeds") or []) if isinstance(e.get("color"), int)]
            imgs = [(e.get("image") or {}).get("url", "")[:60] for e in (res.get("embeds") or []) if e.get("image")]
            print(f"OK BANNER #{slug} ({mid}): atts={atts} colors={cols} imgs={imgs}")
        else:
            print(f"FAIL BANNER #{slug} ({mid}): {st_p} {res}")
        time.sleep(0.6)

    for slug, cid, mid in COLOR_ONLY_MESSAGES:
        st, msg = dc_json("GET", f"/channels/{cid}/messages/{mid}")
        if st != 200 or not isinstance(msg, dict):
            print(f"ERROR GET #{slug} {mid}: {st} {msg}")
            continue
        embeds = [clean_embed(e) for e in (msg.get("embeds") or [])]
        st_p, res = dc_json("PATCH", f"/channels/{cid}/messages/{mid}", {"embeds": embeds})
        if st_p == 200 and isinstance(res, dict):
            cols = [hex(e["color"]) for e in (res.get("embeds") or []) if isinstance(e.get("color"), int)]
            print(f"OK COLOR #{slug} ({mid}): colors={cols}")
        else:
            print(f"FAIL COLOR #{slug} ({mid}): {st_p} {res}")
        time.sleep(0.6)


if __name__ == "__main__":
    main()
