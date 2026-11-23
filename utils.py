import os
import shutil
import subprocess
import sys
import time
import random
import urllib.parse
import html as html_lib
import threading
import requests
import re

_scraper = None

# Cloudflare は TLS/HTTP2 フィンガープリントと UA の整合性も判定材料にする。
# 全リクエストで現行 Chrome 相当に揃える。
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 意図しないプロセス強制終了(sys.exit)を防ぐための例外
class ProcessExitException(BaseException): pass

def prevent_exit(code=0):
    raise ProcessExitException(f"Process exit prevented! (exit code {code})")

# エラー発生時にメッセージを出力して中断する
def panic(message: str):
    print(f"  -> [FATAL] {message}")
    raise ProcessExitException(message)

# Cloudflare のチャレンジページ（HTTP 200 で返ってくる）かどうかを判定する
def _is_challenge_page(res) -> bool:
    if res is None or getattr(res, "status_code", None) != 200:
        return False
    ctype = (res.headers.get("Content-Type") or "").lower()
    if ctype and "html" not in ctype:
        return False
    try:
        text = (res.text or "")[:5000]
    except Exception:
        return False
    markers = (
        "Just a moment", "cf-chl-", "__cf_chl_", "Checking your browser",
        "Attention Required", "challenge-platform", "cf_chl_opt",
    )
    return any(m in text for m in markers)


# APKMIRROR_PROXY (http://user:pass@host:port) が設定されていれば経由する。
# APK本体は CDN から直接取得するため、プロキシを通るのは HTML のみ。
def _get_proxy():
    proxy = (os.environ.get("APKMIRROR_PROXY") or "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


# GitHub のランナー IP は Cloudflare にブロックされることがある。
# その場合でも HTML だけは Google 翻訳のプロキシ経由で取得できる
# （Google のサーバーが APKMirror を取得するため、こちらの IP は問われない）。
# APK 本体は署名付き CDN から直接落とすので、ここを通るのは HTML のみ。
_TRANSLATE_HOST = "www-apkmirror-com.translate.goog"
_TRANSLATE_PARAMS = {"_x_tr_sl": "auto", "_x_tr_tl": "en", "_x_tr_hl": "en"}

# https://www.apkmirror.com/<path> -> https://www-apkmirror-com.translate.goog/<path>
def _to_translate(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query += list(_TRANSLATE_PARAMS.items())
    return urllib.parse.urlunsplit(
        ("https", _TRANSLATE_HOST, parsed.path, urllib.parse.urlencode(query), "")
    )


# 翻訳プロキシが書き換えた絶対 URL を、元の apkmirror.com の相対パスへ戻す。
# これにより以降のパーサーは通常の HTML と同じように扱える。
def _from_translate(html: str) -> str:
    pattern = re.compile(r"https?://www-apkmirror-com\.translate\.goog(?P<rest>/[^\"'\s<>]*)")

    def repl(match: "re.Match[str]") -> str:
        # 翻訳プロキシは & を &amp; で出力するので、まず実体参照を戻す。
        url = html_lib.unescape(match.group("rest"))
        parts = urllib.parse.urlsplit(url)
        query = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if not k.startswith("_x_tr_")
        ]
        return urllib.parse.urlunsplit(("", "", parts.path, urllib.parse.urlencode(query), ""))

    return pattern.sub(repl, html)


# Google 翻訳プロキシへの連続アクセスで 429 にならないよう、最低間隔を空ける
_relay_lock = threading.Lock()
_relay_last = 0.0
_RELAY_MIN_INTERVAL = 2.5


def _relay_get(url: str, **kwargs):
    global _relay_last
    with _relay_lock:
        wait = _RELAY_MIN_INTERVAL - (time.time() - _relay_last)
        if wait > 0:
            time.sleep(wait)
        try:
            return requests.get(url, headers={"User-Agent": CHROME_UA}, **kwargs)
        finally:
            _relay_last = time.time()


def _fetch_via_translate(url: str) -> str | None:
    relay_url = _to_translate(url)
    last_status = None
    for attempt in range(3):
        if attempt:
            time.sleep(5 * attempt)
        try:
            res = _relay_get(relay_url, timeout=90)
        except Exception as e:
            print(f"[scraper] translate relay error: {type(e).__name__}: {e}", flush=True)
            continue
        last_status = res.status_code
        if res.status_code == 429:
            print("[scraper] translate relay rate-limited (429)", flush=True)
            time.sleep(10 * (attempt + 1))
            continue
        if res.status_code != 200:
            print(f"[scraper] translate relay returned {res.status_code}", flush=True)
            continue
        html = _from_translate(res.text)
        if "apkmirror" not in html.lower():
            print("[scraper] translate relay did not return an APKMirror page", flush=True)
            continue
        print("[scraper] fetched via Google Translate relay", flush=True)
        return html
    if last_status is not None:
        print(f"[scraper] translate relay gave up (last status {last_status})", flush=True)
    return None


# 翻訳プロキシが付ける _x_tr_* を落として、翻訳先ホストを元のドメインへ戻す。
# クエリは署名 (X-Amz-Signature) を壊さないよう再エンコードしない。
def _untranslate_url(location: str) -> str | None:
    parts = urllib.parse.urlsplit(location)
    host = parts.netloc
    if not host.endswith(".translate.goog"):
        return None
    host = host[: -len(".translate.goog")]
    host = host.replace("-r2-cloudflarestorage-com", ".r2.cloudflarestorage.com")
    if host.startswith("www-apkmirror-com"):
        host = host.replace("www-apkmirror-com", "www.apkmirror.com", 1)
    # 鍵が無効なときに APKMirror へ戻されることがあるので、CDN 以外は受け付けない。
    if host == "apkmirror.com" or host.endswith(".apkmirror.com"):
        return None
    query = "&".join(part for part in parts.query.split("&") if not part.startswith("_x_tr_"))
    return urllib.parse.urlunsplit((parts.scheme or "https", host, parts.path, query, ""))


# download.php は 302 で署名付き CDN へ飛ぶ。直接叩くと 403 になる環境でも、
# 翻訳プロキシ経由なら 302 がそのまま返るので、Location から CDN の URL を取り出す。
def resolve_apk_via_translate(download_php_url: str) -> str | None:
    relay_url = _to_translate(download_php_url)
    for attempt in range(3):
        if attempt:
            time.sleep(5 * attempt)
        try:
            res = _relay_get(relay_url, timeout=90, allow_redirects=False)
        except Exception as e:
            print(f"[scraper] translate redirect error: {type(e).__name__}: {e}", flush=True)
            continue
        if res.status_code == 429:
            print("[scraper] translate redirect rate-limited (429)", flush=True)
            time.sleep(10 * (attempt + 1))
            continue
        location = res.headers.get("Location") or res.headers.get("location")
        if res.status_code in (301, 302, 303, 307, 308) and location:
            target = _untranslate_url(location)
            if target:
                print("[scraper] resolved CDN URL via Google Translate relay", flush=True)
                return target
            print(f"[scraper] unexpected translate redirect target: {location[:80]}", flush=True)
        elif res.status_code == 200:
            # プロキシが 302 を HTML に包んで返す場合に備えて本文も走査する。
            match = re.search(
                r"https?://[^\"'<>\s]*(?:r2\.cloudflarestorage\.com|"
                r"-r2-cloudflarestorage-com\.translate\.goog)[^\"'<>\s]*",
                html_lib.unescape(res.text or ""),
            )
            if match:
                target = _untranslate_url(match.group(0))
                if target:
                    print("[scraper] resolved CDN URL via Google Translate relay", flush=True)
                    return target
            print("[scraper] translate redirect returned 200 without a CDN URL", flush=True)
        else:
            print(f"[scraper] translate redirect returned {res.status_code}", flush=True)
    return None


# 検証用: 直接アクセスを飛ばして中継経路だけを試す
def _force_relay() -> bool:
    return (os.environ.get("APKMIRROR_FORCE_RELAY") or "").strip().lower() in ("1", "true", "yes")


# ヘッドレスブラウザ経由で取得した HTML を、通常のレスポンスと同じ形で返すためのラッパー
class _SimpleResponse:
    def __init__(self, html: str) -> None:
        self.status_code = 200
        self.content = html.encode("utf-8")
        self.text = html
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}

    def close(self) -> None:
        pass


# 403/チャレンジの内容をログに残す（ハードブロックかチャレンジかの切り分け用）
def _describe_block(res) -> str:
    try:
        text = res.text or ""
    except Exception:
        return f"status={getattr(res, 'status_code', '?')}"
    match = re.search(r"<title>(.*?)</title>", text[:20000], re.S)
    title = match.group(1).strip() if match else text[:100].replace("\n", " ")
    return f"status={res.status_code} title={title!r}"


# CloudflareのBot検知を回避するためのスクレイパーを取得する
class _HybridSession:
    def __init__(self) -> None:
        self._proxy = _get_proxy()
        self._primary = None
        self._fallback = None
        self.headers = {"User-Agent": CHROME_UA}
        via = "via proxy" if self._proxy else "direct"
        print(f"[scraper] primary: curl_cffi (current Chrome fingerprint, {via})", flush=True)

    # 実ブラウザの TLS/HTTP2 フィンガープリントを持つ curl_cffi を主系にする。
    # バージョン固定 (chrome124 等) は Cloudflare 側で既知のボット署名になり
    # チャレンジされるため、"chrome"（その時点の最新）を使う。
    def _get_primary(self):
        if self._primary is None:
            from curl_cffi import requests as curl_requests
            last_error = None
            for impersonate in ("chrome", "chrome136", "chrome131", "safari17_0"):
                try:
                    self._primary = curl_requests.Session(
                        impersonate=impersonate, proxies=self._proxy
                    )
                    print(f"[scraper] curl_cffi impersonate={impersonate}", flush=True)
                    break
                except Exception as e:
                    last_error = e
            if self._primary is None:
                raise last_error
        return self._primary

    def _get_fallback(self):
        if self._fallback is None:
            import cloudscraper
            try:
                session = cloudscraper.create_scraper(enable_stealth=True)
            except TypeError:
                session = cloudscraper.create_scraper()
            # cloudscraper の既定 UA は古い端末文字列でブロックされるため上書きする
            session.headers.update({"User-Agent": CHROME_UA})
            if self._proxy:
                session.proxies.update(self._proxy)
            self._fallback = session
            print("[scraper] fallback: cloudscraper (stealth + Chrome UA)", flush=True)
        return self._fallback

    def _send(self, client, url, *args, **kwargs):
        headers = kwargs.pop("headers", None)
        if headers:
            kwargs["headers"] = headers
        return client.get(url, *args, **kwargs)

    def get(self, url, *args, **kwargs):
        stream = bool(kwargs.get("stream"))

        # 検証・障害時の強制中継モード（HTML のみ）
        if not stream and _force_relay():
            html = _fetch_via_translate(url)
            if html is not None:
                return _SimpleResponse(html)

        # チャレンジ判定でストリーム（APK本体）を読み切らないよう、
        # stream=True のときは本文を検査しない。
        try:
            r = self._send(self._get_primary(), url, *args, **kwargs)
        except Exception as e:
            print(f"[scraper] primary error: {type(e).__name__}: {e}", flush=True)
            r = None

        blocked = (
            r is None
            or r.status_code == 403
            or (not stream and _is_challenge_page(r))
        )
        if not blocked:
            return r

        reason = "error" if r is None else ("403" if r.status_code == 403 else "challenge page")
        if r is not None:
            try:
                r.close()
            except Exception:
                pass
        print(f"[scraper] primary returned {reason}, retrying with cloudscraper...", flush=True)
        fallback = self._send(self._get_fallback(), url, *args, **kwargs)

        if fallback.status_code != 403 and (stream or not _is_challenge_page(fallback)):
            return fallback

        print(f"[scraper] cloudscraper also blocked ({_describe_block(fallback)})", flush=True)

        # 両方の HTTP クライアントがブロックされた場合の最終手段。
        # APK本体は CDN から直接落とすため、ここでは HTML のみを扱う。
        if not stream:
            html = _fetch_via_translate(url)
            if html is not None:
                return _SimpleResponse(html)

            try:
                from browser import get_browser
                html = get_browser().get_html(url)
                print(f"[scraper] fetched via headless browser: {url}", flush=True)
                return _SimpleResponse(html)
            except Exception as e:
                print(f"[scraper] browser fallback failed: {type(e).__name__}: {e}", flush=True)

        return fallback

def get_scraper():
    global _scraper
    if _scraper is None:
        _scraper = _HybridSession()
    return _scraper

def reset_scraper():
    global _scraper
    _scraper = None

# 指定URLからファイルをチャンク単位でダウンロードし、保存する
def download(link: str, out: str, headers=None, use_scraper=True):
    if os.path.exists(out):
        print(f"{out} already exists skipping download")
        return

    if use_scraper:
        r = get_scraper().get(link, stream=True, headers=headers)
    else:
        # requestsを使用する場合も、連続アクセス防止のために待機時間を設ける
        time.sleep(random.uniform(1.0, 2.0))
        r = requests.get(link, stream=True, headers=headers)

    if r.status_code != 200:
        raise RuntimeError(f"HTTP Error {r.status_code} for URL: {link}")

    with open(out, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    try:
        r.close()
    except Exception:
        pass


# シェルコマンドを実行し、失敗した場合は標準エラー出力にログを記録してプロセスを終了する
def run_command(command: list):
    cmd = subprocess.run(command, capture_output=True, shell=True)

    try:
        cmd.check_returncode()
    except subprocess.CalledProcessError:
        print(cmd.stdout)
        print(cmd.stderr)
        sys.exit(1)


# APKEditorを使用して分割APKバンドル（.apkm等）を単一のAPKにマージする
def merge_apk(path: str):
    subprocess.run(
        ["java", "-jar", "./bins/apkeditor.jar", "m", "-extractNativeLibs", "true", "-i", path]
    ).check_returncode()


# Morphe CLIを使用してベースAPKにパッチを適用し、署名を行う
def patch_apk(
    cli: str,
    patches: str,
    apk: str,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    out: str | None = None,
):
    includes = includes or []
    excludes = excludes or []

    command = [
        "java",
        "-jar",
        cli,
        "patch",
        "-p",
        patches,
        "--continue-on-error",
        "--keystore", "ks.keystore",
        "--keystore-entry-password", "123456789",
        "--keystore-password", "123456789",
        "--signer", "jhc",
        "--keystore-entry-alias", "jhc",
    ]

    for i in includes:
        command += ["-e", i]

    for e in excludes:
        command += ["-d", e]

    command.append(apk)

    print(f"Executing: {' '.join(command)}")

    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')

    if result.stdout:
        print(result.stdout)

    if result.returncode != 0:
        print("--- CLI Error Output ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        print("------------------------", file=sys.stderr)
        result.check_returncode()

    # CLIの実行ログから「Saved to ...」を探し出し、動的に出力先ファイルパスを特定する
    if out is not None:
        output_text = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"Saved to\s+([^\r\n]+)", output_text)

        if not match:
            print("[FATAL] Failed to parse output path from CLI log.", file=sys.stderr)
            sys.exit(1)

        cli_output = match.group(1).strip()

        if os.path.exists(cli_output):
            if os.path.exists(out):
                os.unlink(out)
            shutil.move(cli_output, out)
        else:
            print(f"[FATAL] Generated file could not be found: {cli_output}", file=sys.stderr)
            sys.exit(1)


# 指定したタグとファイル群を用いて、GitHubに新規リリースを作成（既存の場合は上書き）する
def publish_release(tag: str, files: list[str], message: str, title = ""):
    key = os.environ.get("GITHUB_TOKEN")
    if key is None:
        raise Exception("GITHUB_TOKEN is not set")

    if len(files) == 0:
        raise Exception("Files should have at least one item")

    def release_exists(t: str) -> bool:
        result = subprocess.run(
            ["gh", "release", "view", t],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return result.returncode == 0

    if release_exists(tag):
        print(f"Release '{tag}' already exists - deleting old release...")

        subprocess.run(
            ["gh", "release", "delete", tag, "-y"],
            env=os.environ.copy()
        ).check_returncode()

        print(f"Deleting tag '{tag}' via GitHub API...")
        api_cmd = [
            "gh", "api",
            "--method", "DELETE",
            f"/repos/{os.environ['GITHUB_REPOSITORY']}/git/refs/tags/{tag}"
        ]

        subprocess.run(api_cmd, env=os.environ.copy()).check_returncode()
        print("Old release & tag removed. Recreating fresh release...")

    command = ["gh", "release", "create", "--latest", tag, "--notes", message, "--title", title]
    command.extend(files)

    subprocess.run(command, env=os.environ.copy()).check_returncode()
