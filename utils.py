import os
import shutil
import subprocess
import sys
import time
import random
import requests
import re

# 意図しないプロセス強制終了(sys.exit)を防ぐための例外
class ProcessExitException(BaseException): pass

def prevent_exit(code=0):
    raise ProcessExitException(f"Process exit prevented! (exit code {code})")


# エラー発生時にメッセージを出力して中断する
def panic(message: str):
    print(f"  -> [FATAL] {message}")
    raise ProcessExitException(message)
import os
import shutil
import subprocess
import sys
import time
import random
import requests
import re

_scraper = None

# ブラウザ偽装ターゲット（curl_cffi の impersonate 用）
_IMPERSONATE_TARGETS = ["chrome124", "chrome123", "chrome120"]

# UA は impersonate と一致させる必要がある（不一致は即検出される）
_UA_MAP = {
    "chrome124": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "chrome123": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "chrome120": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# 実ブラウザに近づけるための共通ヘッダ
_EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# 新しいセッションを生成する。curl_cffi > cloudscraper > requests の優先順
def _build_session():
    # 1) curl_cffi: TLS/HTTP2フィンガープリントを実ブラウザと一致させる（最優先）
    try:
        from curl_cffi import requests as cffi_requests
        target = random.choice(_IMPERSONATE_TARGETS)
        s = cffi_requests.Session(impersonate=target, timeout=30)
        s.headers.update(_EXTRA_HEADERS)
        s.headers["User-Agent"] = _UA_MAP[target]
        print(f"  -> [SESSION] curl_cffi impersonate={target}")
        return s
    except ImportError:
        pass

    # 2) cloudscraper: 古いIUAMチャレンジ用のフォールバック
    try:
        import cloudscraper
        s = cloudscraper.create_scraper()
        s.headers.update(_EXTRA_HEADERS)
        s.headers["User-Agent"] = _UA_MAP["chrome124"]
        print("  -> [SESSION] cloudscraper (fallback)")
        return s
    except ImportError:
        pass

    # 3) 最終手段
    s = requests.Session()
    s.headers.update(_EXTRA_HEADERS)
    s.headers["User-Agent"] = _UA_MAP["chrome124"]
    print("  -> [SESSION] requests (last resort)")
    return s


# セッションの .get() をラップし、403/例外時にセッションを再生成する
def _install_wrapper(session):
    original_get = session.get

    # 403 や接続エラー時に「セッションを新しく作り直して再試行」するラッパー関数
    def safe_get(url, **kwargs):
        global _scraper
        max_attempts = 4
        for attempt in range(max_attempts):
            # 人間によるアクセスを模倣するため、リクエスト前に1.5〜3.5秒のランダム待機を挟む
            time.sleep(random.uniform(1.5, 3.5))

            current = _scraper
            try:
                res = current._original_get(url, timeout=30, **kwargs)
                # HTTPステータス200(成功) または 404(Not Found) の場合は正常応答として処理
                if res.status_code in (200, 404):
                    return res
                print(
                    f"  -> [WARNING] Cloudflare blocked (HTTP {res.status_code}). "
                    f"Rotating session... (attempt {attempt + 1}/{max_attempts})"
                )
            except Exception as e:
                print(
                    f"  -> [WARNING] Connection error: {e}. "
                    f"Rotating session... (attempt {attempt + 1}/{max_attempts})"
                )

            # 新しいフィンガープリントのセッションへ差し替え（UA・impersonate も再抽選）
            _scraper = _install_wrapper(_build_session())
            # 指数バックオフ（5秒 → 10秒 → 15秒 → 20秒）
            time.sleep(5 * (attempt + 1))

        # 再試行上限に達した場合は、安全なエラーハンドリングのためダミーのレスポンスを返す
        class Dummy:
            status_code = 403
            content = b""
            text = ""
        return Dummy()

    session._original_get = original_get
    session.get = safe_get
    return session


# CloudflareのBot検知を回避するためのスクレイパーを取得する
# 403を検知した場合はUA・TLSフィンガープリントを変えてセッションを自動再生成する
def get_scraper():
    global _scraper
    if _scraper is None:
        _scraper = _install_wrapper(_build_session())
    return _scraper


# 明示的にセッションを破棄したい場合に呼ぶ
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
