#!/usr/bin/env python3
"""
US Trucking News: web page + email digest
-----------------------------------------
- Collects trucking news from Google News searches and trucking feeds
- Writes a mobile-friendly web page to docs/index.html (last 7 days)
- Emails a digest of NEW items when SEND_EMAIL=true

Environment variables (email only):
    SMTP_USER      sender address (Gmail: use an App Password)
    SMTP_PASSWORD  the app password
    EMAIL_TO       one or more recipients, comma-separated
    SMTP_HOST      optional, default smtp.gmail.com
    SMTP_PORT      optional, default 465
"""

import html
import json
import os
import smtplib
import time
import urllib.parse
from email.message import EmailMessage
from pathlib import Path

import feedparser

ROOT = Path(__file__).parent
HISTORY_FILE = ROOT / "history.json"
PAGE_FILE = ROOT / "docs" / "index.html"

PAGE_TITLE = "US Trucking News"
KEEP_DAYS = 7              # how long items stay on the page
FETCH_HOURS = 48           # ignore feed items older than this
MAX_EMAIL_ITEMS = 40

SEARCH_QUERIES = [
    "FMCSA",
    "trucking regulations",
    "truck drivers new rule",
    "ELD mandate",
    "hours of service",
    "CDL English proficiency",
    "non-domiciled CDL",
    "DOT inspection enforcement",
    "CVSA inspection",
    "truck parking",
    "diesel prices",
    "freight rates trucking",
    "Amazon Relay",
    "trucking fraud scam alert",
    "highway closure trucks",
    "truck weigh station",
]

# Verify each URL still works; remove any that fail.
DIRECT_FEEDS = [
    "https://www.freightwaves.com/news/feed",
]

KEYWORDS = [
    "fmcsa", "dot", "cdl", "eld", "hours of service", "truck", "trucking",
    "driver", "freight", "diesel", "inspection", "cvsa", "safety", "rule",
]


def google_news_url(query):
    q = urllib.parse.quote(query + " when:2d")
    return "https://news.google.com/rss/search?q=" + q + "&hl=en-US&gl=US&ceid=US:en"


def entry_time(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return time.mktime(parsed) if parsed else time.time()


def matches_keywords(entry):
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    return any(k in text for k in KEYWORDS)


def collect_items():
    items = {}
    cutoff = time.time() - FETCH_HOURS * 3600
    sources = [(google_news_url(q), False) for q in SEARCH_QUERIES]
    sources += [(u, True) for u in DIRECT_FEEDS]

    for url, needs_filter in sources:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print("Feed error:", url, e)
            continue
        for entry in feed.entries:
            link = entry.get("link")
            title = entry.get("title", "").strip()
            if not link or not title:
                continue
            t = entry_time(entry)
            if t < cutoff:
                continue
            if needs_filter and not matches_keywords(entry):
                continue
            source = ""
            src = entry.get("source")
            if src:
                source = src.get("title", "")
            items[link] = {
                "title": title,
                "link": link,
                "time": t,
                "source": source,
                "emailed": False,
            }
    return list(items.values())


def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return []
    return []


def merge(history, fresh):
    by_link = {i["link"]: i for i in history}
    added = 0
    for item in fresh:
        if item["link"] not in by_link:
            by_link[item["link"]] = item
            added += 1
    cutoff = time.time() - KEEP_DAYS * 86400
    merged = [i for i in by_link.values() if i["time"] >= cutoff]
    merged.sort(key=lambda x: x["time"], reverse=True)
    return merged, added


def fmt_time(t):
    return time.strftime("%b %d, %H:%M UTC", time.gmtime(t))


def build_page(items):
    rows = []
    for i in items:
        meta = fmt_time(i["time"])
        if i.get("source"):
            meta = html.escape(i["source"]) + " · " + meta
        rows.append(
            '<li><a href="{link}" target="_blank" rel="noopener">{title}</a>'
            '<div class="meta">{meta}</div></li>'.format(
                link=html.escape(i["link"], quote=True),
                title=html.escape(i["title"]),
                meta=meta,
            )
        )
    updated = fmt_time(time.time())
    body = "\n".join(rows) if rows else "<li>No news yet. Check back soon.</li>"
    return PAGE_TEMPLATE.replace("{{TITLE}}", PAGE_TITLE).replace(
        "{{UPDATED}}", updated
    ).replace("{{ITEMS}}", body)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
:root { --bg:#ffffff; --fg:#1a1a1a; --muted:#666; --link:#0b5cad; --line:#e3e3e3; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181b; --fg:#ececec; --muted:#9aa0a6; --link:#7db7ff; --line:#2c2f33; }
}
body { margin:0; background:var(--bg); color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }
main { max-width:720px; margin:0 auto; padding:20px 16px 48px; }
h1 { font-size:1.4rem; margin:0 0 4px; }
.updated { color:var(--muted); font-size:.85rem; margin-bottom:16px; }
ul { list-style:none; padding:0; margin:0; }
li { padding:12px 0; border-bottom:1px solid var(--line); }
a { color:var(--link); text-decoration:none; font-weight:600; line-height:1.35; }
a:hover { text-decoration:underline; }
.meta { color:var(--muted); font-size:.8rem; margin-top:4px; }
</style>
</head>
<body>
<main>
<h1>🚛 {{TITLE}}</h1>
<div class="updated">Last updated: {{UPDATED}}</div>
<ul>
{{ITEMS}}
</ul>
</main>
</body>
</html>
"""


def send_email(items):
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    recipients = [r.strip() for r in os.environ.get("EMAIL_TO", "").split(",") if r.strip()]
    if not (user and password and recipients):
        print("Email not configured; skipping.")
        return False

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    text_lines = []
    html_rows = []
    for i in items:
        text_lines.append("- " + i["title"] + "\n  " + i["link"])
        html_rows.append(
            '<li style="margin-bottom:12px"><a href="{l}">{t}</a>'
            '<br><span style="color:#777;font-size:12px">{m}</span></li>'.format(
                l=html.escape(i["link"], quote=True),
                t=html.escape(i["title"]),
                m=html.escape((i.get("source") or "") + " " + fmt_time(i["time"])),
            )
        )

    msg = EmailMessage()
    msg["Subject"] = "🚛 US Trucking News: " + str(len(items)) + " new items"
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.set_content("US Trucking News\n\n" + "\n\n".join(text_lines))
    msg.add_alternative(
        "<html><body><h2>US Trucking News</h2><ul>" + "".join(html_rows) + "</ul></body></html>",
        subtype="html",
    )

    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.send_message(msg)
        print("Email sent to", len(recipients), "recipient(s)")
        return True
    except Exception as e:
        print("Email error:", e)
        return False


def main():
    history = load_history()
    fresh = collect_items()
    merged, added = merge(history, fresh)
    print(added, "new items;", len(merged), "total on page")

    if os.environ.get("SEND_EMAIL", "").lower() == "true":
        recent = time.time() - 36 * 3600
        pending = [i for i in merged if not i.get("emailed")]
        to_send = [i for i in pending if i["time"] >= recent][:MAX_EMAIL_ITEMS]
        if to_send and send_email(to_send):
            sent_links = {i["link"] for i in to_send}
            for i in merged:
                if i["link"] in sent_links or i["time"] < recent:
                    i["emailed"] = True
        elif not to_send:
            print("Nothing new to email.")

    PAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAGE_FILE.write_text(build_page(merged), encoding="utf-8")
    HISTORY_FILE.write_text(json.dumps(merged), encoding="utf-8")


if __name__ == "__main__":
    main()
