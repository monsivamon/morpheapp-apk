import os
import shutil
import subprocess
import sys
import time
import random
import requests

_scraper = None

# CloudflareのBot検知を回避するためのスクレイパーを取得する
# GitHub Actions環境で最も安定してアクセス可能な設定(cloudscraper + User-Agent固定)を使用
def get_scraper():
    global _scraper
    if _scraper is None:
        import cloudscraper
        _scraper = cloudscraper.create_scraper()
        _scraper.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        })

        original_get = _scraper.get
        
        # 連続アクセス（Rate Limit）によるブロックを回避するためのラッパー関数
        def safe_get(url, **kwargs):
            max_attempts = 4
            for attempt in range(max_attempts):
                # 人間によるアクセスを模倣するため、リクエスト前に2.0〜4.5秒のランダムな待機時間を設ける
                sleep_time = random.uniform(2.0, 4.5)
                time.sleep(sleep_time)
                
                try:
                    res = original_get(url, timeout=30, **kwargs)
                    # HTTPステータス200(成功) または 404(Not Found) の場合は正常な応答として処理
                    if res.status_code in (200, 404):
                        return res
                    
                    print(f"  -> [WARNING] Cloudflare blocked (HTTP {res.status_code}). Cooling down...")
                except Exception as e:
                    print(f"  -> [WARNING] Connection error: {e}. Cooling down...")
                
                # アクセスが拒否された場合は5秒間待機し、再試行する
                time.sleep(5)
            
            # 再試行上限に達した場合は、安全なエラーハンドリングのためにダミーのレスポンスオブジェクトを返す
            class Dummy:
                status_code = 403
                content = b""
                text = ""
            return Dummy()

        _scraper.get = safe_get
        
    return _scraper


# 致命的なエラー発生時にメッセージを出力し、プロセスを終了する
def panic(message: str):
    print(message, file=sys.stderr)
    sys.exit(1)


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
def run_command(command: list[str]):
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

    result = subprocess.run(command, capture_output=True, text=True)
    
    if result.stdout:
        print(result.stdout)
    
    if result.returncode != 0:
        print("--- CLI Error Output ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr) 
        print("------------------------", file=sys.stderr)
        result.check_returncode() 

    # CLIの--outパラメータを使用せず、パッチ適用後のファイルをPython側でリネームし、指定パスへ移動する
    if out is not None:
        cli_output = f"{str(apk).removesuffix('.apk')}-patched.apk"
        if os.path.exists(out):
            os.unlink(out)
        shutil.move(cli_output, out)


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
        print(f"Release '{tag}' already exists — deleting old release...")

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