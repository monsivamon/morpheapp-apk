"""End-to-end APKMirror reachability probe (HTML -> download.php -> CDN range)."""

import platform
import re
import sys

sys.path.insert(0, __import__("os").getcwd())

from bs4 import BeautifulSoup

import utils
from utils import get_scraper

VERSION_URL = "https://www.apkmirror.com/apk/google-inc/youtube/youtube-21-47-483-secondary-release/"


def title_of(html):
    match = re.search(r"<title>(.*?)</title>", html, re.S)
    return match.group(1).strip() if match else "-"


print("os:", platform.platform(), flush=True)
try:
    import requests

    print("egress ip:", requests.get("https://api.ipify.org", timeout=30).text, flush=True)
except Exception as e:
    print("egress ip error:", e, flush=True)

print("\n=== direct curl_cffi ===", flush=True)
try:
    from curl_cffi import requests as cr

    direct = cr.Session(impersonate="chrome").get(VERSION_URL, timeout=60)
    print("status:", direct.status_code, "len:", len(direct.content),
          "title:", title_of(direct.text)[:60], flush=True)
except Exception as e:
    print("error:", type(e).__name__, e, flush=True)

print("\n=== translate relay (standalone) ===", flush=True)
relay_html = utils._fetch_via_translate(VERSION_URL)
if relay_html:
    print("len:", len(relay_html), "variants-table:", "variants-table" in relay_html,
          "title:", title_of(relay_html)[:60], flush=True)

print("\n=== production scraper chain ===", flush=True)
scraper = get_scraper()
res = scraper.get(VERSION_URL)
html = res.text
print("status:", res.status_code, "len:", len(html), "title:", title_of(html)[:60], flush=True)

print("\n=== parse variants ===", flush=True)
soup = BeautifulSoup(html, "html.parser")
table = soup.select_one("div.variants-table")
rows = table.find_all("div", recursive=False)[1:] if table else []
link = None
for row in rows:
    a = row.find("a", {"class": "accent_color"})
    if a:
        link = "https://www.apkmirror.com" + a["href"]
        break
print("variant rows:", len(rows), "first link:", link, flush=True)
if not link:
    raise SystemExit(0)

print("\n=== variant page ===", flush=True)
vres = scraper.get(link)
print("status:", vres.status_code, "downloadButton:", "downloadButton" in vres.text, flush=True)
btn = BeautifulSoup(vres.text, "html.parser").find("a", {"class": "downloadButton"})
if btn is None:
    raise SystemExit(0)

print("\n=== download page ===", flush=True)
dl_page = "https://www.apkmirror.com" + btn["href"]
dres = scraper.get(dl_page)
print("status:", dres.status_code, "nofollow:", 'rel="nofollow"' in dres.text, flush=True)
nf = BeautifulSoup(dres.text, "html.parser").find("a", {"rel": "nofollow"})
if nf is None:
    raise SystemExit(0)

download_php = "https://www.apkmirror.com/" + nf["href"].lstrip("/")
print("\n=== download.php (no redirect) ===", flush=True)
location = None
if utils._force_relay():
    print("  -> force relay: skipping the direct download.php attempt", flush=True)
else:
    pres = scraper.get(download_php, headers={"Referer": dl_page},
                       allow_redirects=False, stream=True)
    location = pres.headers.get("Location") or pres.headers.get("location")
    print("status:", pres.status_code, "location:", (location or "")[:90], flush=True)
    try:
        pres.close()
    except Exception:
        pass

if not location:
    print("  -> resolving via translate relay", flush=True)
    location = utils.resolve_apk_via_translate(download_php)
    print("  -> relay resolved:", (location or "")[:90], flush=True)

if not location:
    print("no CDN location obtained", flush=True)
    raise SystemExit(0)

print("\n=== CDN range request ===", flush=True)
rng = requests.get(location, headers={"Range": "bytes=0-3", "Referer": download_php},
                   timeout=60)
print("status:", rng.status_code, "bytes:", rng.content[:4], flush=True)
