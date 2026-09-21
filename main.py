import sys
import os
import argparse

# 意図しないプロセス強制終了(sys.exit)を防ぐ
from utils import ProcessExitException, panic, prevent_exit

from version import version_greater
from github import get_latest_releases
from builder import process


# エントリポイント: 更新を確認し処理を開始する
def main():
    parser = argparse.ArgumentParser(description="Morphe Auto Builder")
    parser.add_argument("--app", choices=["youtube", "ytmusic", "all"], default="all",
                        help="Which app to build")
    parser.add_argument("--no-release", action="store_true",
                        help="Build APKs but skip GitHub release upload (for local testing)")
    parser.add_argument("--force", action="store_true",
                        help="Force build even if no new updates are available")
    parser.add_argument("--tag", type=str, default=None,
                        help="Build a specific upstream tag (implies --force)")

    # parse_args() の間は sys.exit を握り潰さない（argparse のエラーを素直に出す）
    args = parser.parse_args()

    # ここから先でだけ sys.exit / os._exit を握り潰す
    sys.exit = prevent_exit
    os._exit = prevent_exit

    repo_url = "monsivamon/morpheapp-apk"
    upstream_repo = "MorpheApp/morphe-patches"

    print("\n[STEP 1] Fetching release history for upstream and my repo...")
    upstream = get_latest_releases(upstream_repo, require_mpp=True)
    my_repo = get_latest_releases(repo_url, require_mpp=False)

    print("\n--- VERSION STATUS ---")
    print(f"Upstream Stable: {upstream['stable']}")
    print(f"Upstream Pre   : {upstream['pre']}")
    print(f"My Repo  Stable: {my_repo['stable']}")
    print(f"My Repo  Pre   : {my_repo['pre']}")
    print("----------------------\n")

    print("[STEP 2] Verifying build history for updates...")
    build_targets = []

    # --tag 指定時: そのタグを直接ビルド対象にする
    if args.tag:
        print(f"  -> [FORCE] Using explicit tag: {args.tag}")
        is_pre = "-" in args.tag.lstrip("v")  # 簡易判定
        build_targets.append({"tag": args.tag, "is_pre": is_pre})
    else:
        # 通常: upstream が自分の repo より新しいものだけを対象にする
        if upstream["stable"] and version_greater(upstream["stable"], my_repo["stable"]):
            build_targets.append({"tag": upstream["stable"], "is_pre": False})

        if upstream["pre"] and version_greater(upstream["pre"], my_repo["pre"]):
            build_targets.append({"tag": upstream["pre"], "is_pre": True})

        # --force 指定時: 更新がなくても upstream の最新を強制ビルド
        if args.force and not build_targets:
            print("  -> [FORCE] No updates found, but --force is set. Using upstream latest.")
            if upstream["stable"]:
                build_targets.append({"tag": upstream["stable"], "is_pre": False})
            if upstream["pre"]:
                build_targets.append({"tag": upstream["pre"], "is_pre": True})

    if not build_targets:
        print("  -> [EXIT] No new updates found. Skipping build.")
        return

    print(f"  -> [RESULT] Found {len(build_targets)} pending update(s).")

    for target in build_targets:
        process(target["tag"], target["is_pre"], args.app, no_release=args.no_release)


if __name__ == "__main__":
    main()