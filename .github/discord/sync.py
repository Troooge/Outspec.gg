"""Discord <-> GitHub sync for Augury.

  python sync.py poll      Discord -> GitHub: new forum posts become issues, replies become comments.
  python sync.py event     GitHub -> Discord: label/close/comment/release events update the Discord post.

Env: DISCORD_BOT_TOKEN, GITHUB_TOKEN, GITHUB_REPOSITORY, (event mode) GITHUB_EVENT_NAME, GITHUB_EVENT_PATH.
"""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
REPO = os.environ.get("ISSUES_REPO") or os.environ["GITHUB_REPOSITORY"]
DTOKEN = os.environ["DISCORD_BOT_TOKEN"]
GTOKEN = os.environ["GITHUB_TOKEN"]
GUILD = CFG["guild"]
FORUMS = CFG["forums"]
STATUS_PRIORITY = ["in-progress", "confirmed", "planned", "under-review", "needs-info"]
MARK_RE = re.compile(r"<!-- discord-sync (\{.*?\}) -->")
NO_PINGS = {"parse": []}


def http(method, url, headers, body=None):
    for attempt in range(6):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            if e.code == 429:
                try:
                    wait = float(json.loads(raw).get("retry_after", 2))
                except Exception:
                    wait = 2
                time.sleep(wait + 0.5)
                continue
            if e.code >= 500 and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url} -> {e.code}: {raw[:500]}")
    raise RuntimeError(f"{method} {url}: retries exhausted")


def dc(method, path, body=None):
    return http(method, "https://discord.com/api/v10" + path, {
        "Authorization": "Bot " + DTOKEN, "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://github.com/" + REPO + ", 1.0)"}, body)


def gh(method, path, body=None):
    url = path if path.startswith("https://") else "https://api.github.com" + path
    return http(method, url, {
        "Authorization": "Bearer " + GTOKEN, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        "User-Agent": "augury-discord-sync"}, body)


# ---------- helpers ----------

def safe_md(text):
    """Untrusted Discord text -> GitHub markdown without pings or fake sync markers."""
    text = (text or "").replace("<!--", "&lt;!--")
    return re.sub(r"@(?=[A-Za-z0-9])", "@​", text)


def safe_discord(text, limit=1900):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def read_mark(body):
    m = MARK_RE.search(body or "")
    return json.loads(m.group(1)) if m else None


def write_mark(body, mark):
    tag = "<!-- discord-sync " + json.dumps(mark, separators=(",", ":")) + " -->"
    return MARK_RE.sub(lambda _: tag, body) if MARK_RE.search(body) else body.rstrip() + "\n\n" + tag


def thread_url(tid):
    return f"https://discord.com/channels/{GUILD}/{tid}"


def name_of(user):
    return user.get("global_name") or user.get("username") or "someone"


def attachments_md(msg):
    out = []
    for a in msg.get("attachments", []):
        if (a.get("content_type") or "").startswith("image/"):
            out.append(f"![{safe_md(a['filename'])}]({a['url']})")
        else:
            out.append(f"- [{safe_md(a['filename'])}]({a['url']})")
    return "\n".join(out)


def linked_issues(state="all"):
    issues, page = [], 1
    while True:
        batch = gh("GET", f"/repos/{REPO}/issues?labels=discord&state={state}&per_page=100&page={page}")
        issues += [i for i in batch if "pull_request" not in i and read_mark(i.get("body"))]
        if len(batch) < 100:
            return issues
        page += 1


def ensure_labels():
    have = {l["name"] for l in gh("GET", f"/repos/{REPO}/labels?per_page=100")}
    want = {"discord": "5865F2", "bug": "d73a4a", "enhancement": "a2eeef", "crash": "b60205",
            "ui": "c5def5", "account": "fbca04", "performance": "f9d0c4", "needs-info": "d876e3",
            "confirmed": "0e8a16", "in-progress": "1d76db", "under-review": "bfdadc",
            "planned": "0052cc", "stale": "ededed"}
    for name, color in want.items():
        if name not in have:
            gh("POST", f"/repos/{REPO}/labels", {"name": name, "color": color})


# ---------- Discord -> GitHub ----------

def forum_threads(forum_id):
    threads = [t for t in dc("GET", f"/guilds/{GUILD}/threads/active").get("threads", [])
               if t.get("parent_id") == forum_id]
    before = None
    for _ in range(3):  # recent archived posts too, so nothing is missed if a run is delayed
        q = "?limit=50" + (f"&before={urllib.parse.quote(before)}" if before else "")
        res = dc("GET", f"/channels/{forum_id}/threads/archived/public{q}")
        threads += res.get("threads", [])
        if not res.get("has_more") or not res.get("threads"):
            break
        before = res["threads"][-1]["thread_metadata"]["archive_timestamp"]
    return threads


def poll():
    ensure_labels()
    me = dc("GET", "/users/@me")["id"]
    issues = linked_issues()
    by_thread = {read_mark(i["body"])["thread"]: i for i in issues}

    for forum_id, f in FORUMS.items():
        tags = {t["id"]: t for t in dc("GET", f"/channels/{forum_id}").get("available_tags", [])}
        for t in forum_threads(forum_id):
            tid = t["id"]
            if tid in by_thread or (t.get("flags", 0) & 2):  # already linked, or the pinned guide post
                continue
            try:
                starter = dc("GET", f"/channels/{tid}/messages/{tid}")
            except RuntimeError as e:
                print("skip", tid, e); continue
            if starter["author"]["id"] == me:
                continue
            content = starter.get("content", "")
            if not content and not starter.get("attachments"):
                print("WARNING: empty message content. Enable Message Content Intent for the bot."); continue
            labels = ["discord", f["base_label"]]
            for tag_id in t.get("applied_tags", []):
                lbl = f["type_tags"].get(tags.get(tag_id, {}).get("name"))
                if lbl and lbl not in labels:
                    labels.append(lbl)
            body = (f"**Reported on Discord by {safe_md(name_of(starter['author']))}** · "
                    f"[Open the Discord post]({thread_url(tid)})\n\n---\n\n{safe_md(content)}")
            att = attachments_md(starter)
            if att:
                body += "\n\n**Attachments**\n" + att
            body = write_mark(body, {"thread": tid, "forum": forum_id, "author": starter["author"]["id"], "last": tid})
            issue = gh("POST", f"/repos/{REPO}/issues", {"title": t["name"][:250], "body": body, "labels": labels})
            by_thread[tid] = issue
            print("created issue", issue["number"], "for thread", tid)
            dc("POST", f"/channels/{tid}/messages", {"allowed_mentions": NO_PINGS, "flags": 4,
                "content": f"Thanks. This is logged as [#{issue['number']}](<{issue['html_url']}>). "
                           "We'll update the tag here as it moves along."})

    # mirror new Discord replies onto open issues (and stale-closed ones, which reopen on a reply)
    stale_closed = [i for i in linked_issues("closed") if "stale" in {l["name"] for l in i["labels"]}]
    for issue in linked_issues("open") + stale_closed:
        mark = read_mark(issue["body"])
        tid, last = mark["thread"], mark.get("last", mark["thread"])
        try:
            msgs = dc("GET", f"/channels/{tid}/messages?after={last}&limit=100")
        except RuntimeError as e:
            print("thread gone?", tid, e); continue
        msgs = sorted(msgs, key=lambda m: int(m["id"]))
        human = False
        for m in msgs:
            if m["author"]["id"] == me or m["author"].get("bot") or m.get("type") not in (0, 19):
                continue
            human = human or m["author"]["id"] == mark.get("author")
            text = safe_md(m.get("content", ""))
            att = attachments_md(m)
            gh("POST", f"/repos/{REPO}/issues/{issue['number']}/comments", {"body":
               f"**{safe_md(name_of(m['author']))}** on [Discord]({thread_url(tid)}):\n\n{text}" + (f"\n\n{att}" if att else "")})
        if msgs:
            mark["last"] = msgs[-1]["id"]
            gh("PATCH", f"/repos/{REPO}/issues/{issue['number']}", {"body": write_mark(issue["body"], mark)})
        names = {l["name"] for l in issue["labels"]}
        if human and issue["state"] == "closed":  # reporter came back: reopen
            gh("DELETE", f"/repos/{REPO}/issues/{issue['number']}/labels/stale")
            issue = gh("PATCH", f"/repos/{REPO}/issues/{issue['number']}", {"state": "open"})
            print("reopened", issue["number"])
        if human and "needs-info" in names:  # reporter answered: hand it back to the team
            gh("DELETE", f"/repos/{REPO}/issues/{issue['number']}/labels/needs-info")
            gh("POST", f"/repos/{REPO}/issues/{issue['number']}/comments",
               {"body": "The reporter replied on Discord, so `needs-info` was removed."})
            issue = gh("GET", f"/repos/{REPO}/issues/{issue['number']}")
            apply_status(issue, note=False)

    close_stale()


STALE_DAYS = 14


def close_stale():
    """Close needs-info issues nobody has touched for STALE_DAYS. A reply on Discord reopens them."""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - STALE_DAYS * 86400))
    for issue in gh("GET", f"/repos/{REPO}/issues?labels=discord,needs-info&state=open&per_page=100"):
        if issue["updated_at"] > cutoff:
            continue
        mark = read_mark(issue["body"])
        if not mark:
            continue
        gh("POST", f"/repos/{REPO}/issues/{issue['number']}/labels", {"labels": ["stale"]})
        gh("POST", f"/repos/{REPO}/issues/{issue['number']}/comments", {"body":
           f"Closing because there's been no reply for {STALE_DAYS} days. A reply in the Discord post reopens it automatically."})
        gh("PATCH", f"/repos/{REPO}/issues/{issue['number']}", {"state": "closed", "state_reason": "not_planned"})
        try:
            dc("POST", f"/channels/{mark['thread']}/messages", {
                "content": f"<@{mark['author']}> Closing this for now since we haven't heard back in {STALE_DAYS} days. "
                           "If it's still happening, reply here and it will reopen.",
                "allowed_mentions": {"users": [mark["author"]]}})
            dc("PATCH", f"/channels/{mark['thread']}", {"archived": True})
        except RuntimeError as e:
            print("stale notify failed", issue["number"], e)
        print("closed stale", issue["number"])


# ---------- GitHub -> Discord ----------

def status_for(issue, f):
    if "stale" in {l["name"] for l in issue.get("labels", [])}:
        return f["status"].get("needs-info")
    if issue["state"] == "closed":
        return f["status"].get("closed:" + (issue.get("state_reason") or "completed"))
    names = {l["name"] for l in issue.get("labels", [])}
    for key in STATUS_PRIORITY:
        if key in names and key in f["status"]:
            return f["status"][key]
    return None


def apply_status(issue, note=True):
    mark = read_mark(issue.get("body"))
    if not mark or mark.get("forum") not in FORUMS:
        return
    f, tid = FORUMS[mark["forum"]], mark["thread"]
    tags = {t["name"]: t["id"] for t in dc("GET", f"/channels/{mark['forum']}").get("available_tags", [])}
    status_ids = {tags[n] for n in f["status"].values() if n in tags}
    thread = dc("GET", f"/channels/{tid}")
    current = thread.get("applied_tags", [])
    want_name = status_for(issue, f)
    want = [t for t in current if t not in status_ids]
    if want_name and want_name in tags:
        want.append(tags[want_name])
    if sorted(want) == sorted(current):
        return
    dc("PATCH", f"/channels/{tid}", {"archived": False, "applied_tags": want[:5]})
    if note and want_name:
        ping = want_name in ("Fixed", "Shipped", "Needs Info")
        msg = {"Needs Info": "We need a bit more information to look into this. Could you reply here with more details?",
               "Fixed": "This is fixed and will be in the next update.",
               "Shipped": "This has been added and will be in the next update.",
               }.get(want_name, f"Status: {want_name}")
        dc("POST", f"/channels/{tid}/messages", {
            "content": (f"<@{mark['author']}> " if ping else "") + msg,
            "allowed_mentions": {"users": [mark["author"]]} if ping else NO_PINGS})


def on_comment(ev):
    c, issue = ev["comment"], ev["issue"]
    mark = read_mark(issue.get("body"))
    if not mark or c["user"].get("type") == "Bot" or "on [Discord](" in c["body"][:300]:
        return
    if c.get("author_association") not in ("OWNER", "MEMBER", "COLLABORATOR"):
        return
    dc("POST", f"/channels/{mark['thread']}/messages", {"allowed_mentions": NO_PINGS, "embeds": [{
        "color": 0x8B5CF6, "author": {"name": c['user']['login'], "icon_url": c["user"]["avatar_url"]},
        "description": safe_discord(c["body"], 3900), "url": c["html_url"]}]})


def on_release(ev):
    rel = ev["release"]
    if rel.get("draft") or rel.get("prerelease"):
        return
    notes = (rel.get("body") or "").strip() or "Bug fixes and improvements."
    dc("POST", f"/channels/{CFG['changelog_channel']}/messages", {"allowed_mentions": NO_PINGS, "embeds": [{
        "color": 0x8B5CF6, "title": rel.get('name') or rel['tag_name'],
        "description": safe_discord(notes, 3900),
        "timestamp": rel.get("published_at")}]})
    # tell reporters whose fixes shipped: issues closed as completed since the previous release
    rels = [r for r in gh("GET", f"/repos/{os.environ['GITHUB_REPOSITORY']}/releases?per_page=20")
            if not r.get("draft") and not r.get("prerelease") and r.get("published_at")]
    rels.sort(key=lambda r: r["published_at"])
    now = rel.get("published_at") or rels[-1]["published_at"]
    older = [r["published_at"] for r in rels if r["published_at"] < now]
    since = older[-1] if older else "0000"
    for issue in linked_issues("closed"):
        if issue.get("state_reason") != "completed" or not (since < (issue.get("closed_at") or "") <= now):
            continue
        mark = read_mark(issue["body"])
        try:
            dc("POST", f"/channels/{mark['thread']}/messages", {
                "content": f"<@{mark['author']}> This is out in {rel.get('name') or rel['tag_name']}. Thanks for posting it.",
                "allowed_mentions": {"users": [mark["author"]]}})
        except RuntimeError as e:
            print("notify failed", issue["number"], e)


def event():
    name = os.environ["GITHUB_EVENT_NAME"]
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    if name == "issues":
        apply_status(ev["issue"])
    elif name == "issue_comment" and "pull_request" not in ev["issue"]:
        on_comment(ev)
    elif name == "release":
        on_release(ev)


if __name__ == "__main__":
    {"poll": poll, "event": event}[sys.argv[1]]()
