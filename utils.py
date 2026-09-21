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

# Cloudflare のチャレンジページ（HTTP 200 で返ってくる）かどうかを判定する
# チャレンジページには downloadButton 等の要素が無いため、ここで 403 相当に変換して
# 無駄なバリアント試行（39個問題）を防ぐ
def _is_challenge_page(res) -> bool:
    if res is None or getattr(res, "status_code", None) != 200:
        return False
    try:
        text = (res.text or "")[:5000]
    except Exception:
        return False
    markers = (
        "Just a moment",
        "cf-chl-",
        "__cf_chl_",
        "Checking your browser",
        "Attention Required",
        "challenge-platform",
        "cf_chl_opt",
    )
    return any(m in text for m in markers)


# CloudflareのBot検知を回避するためのスクレイパーを取得する
# 実績のある cloudscraper をシングルトンで保持し、
# チャレンジページ検出時は 403 相当のダミーレスポンスを返すラッパーを噛ませる
def get_scraper():
    global _scraper
    if _scraper is None:
        import cloudscraper
        _scraper = cloudscraper.create_scraper()
        _scraper.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        })

        original_get = _scraper.get

        # チャレンジページを検出したら 403 相当のダミーを返すラッパー
        def safe_get(url, **kwargs):
            res = original_get(url, timeout=30, **kwargs)
            if _is_challenge_page(res):
                print("  -> [WARNING] Cloudflare challenge page detected (HTTP 200 -> treating as 403).")
                class Dummy:
                    status_code = 403
                    content = b""
                    text = ""
                    headers = {}
                return Dummy()
            return res

        _scraper.get = safe_get

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
