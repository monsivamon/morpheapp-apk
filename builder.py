import os
import subprocess
import time

import apkmirror
from utils import patch_apk, merge_apk, panic
from patches import extract_patches_metadata, get_supported_versions, get_patches_for_version
from apkmirror import get_target_apk_variants
from github import publish_github_release
from download_bins import download_apkeditor, download_morphe_cli


# Morphe CLIでパッチを適用しAPKをビルドする
def build_target_apk(target_name: str, version: str, patches_to_apply: list, input_apk: str):
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"

    output_apk = f"{target_name}-morphe-v{version}.apk"

    print(f"  -> Building {output_apk} (Force applying {len(patches_to_apply)} patches)...")
    patch_apk(cli, patches, input_apk, includes=patches_to_apply, excludes=[], out=output_apk)

    if not os.path.exists(output_apk): panic(f"Failed to build {output_apk}")
    print(f"  -> [SUCCESS] {output_apk} successfully built.")
    return output_apk


# 古いAPKや一時ファイルを削除する
def clean_workspace():
    for f in ["youtube_base.apk", "youtube_base.apkm", "youtube_base_merged.apk",
              "ytmusic_base.apk", "ytmusic_base.apkm", "ytmusic_base_merged.apk",
              "bins/patches.mpp"]:
        if os.path.exists(f): os.remove(f)
    for f in os.listdir("."):
        if f.endswith(".apk") and "morphe-v" in f: os.remove(f)


# バリアントを優先度順にフォールバックしながらベースAPKをダウンロードする
def download_with_fallback(app_id: str, base_url: str, supported_versions: list):
    # 対応バージョンを新しい順に試す
    for version in reversed(supported_versions):
        print(f"\n  -> [FALLBACK ROUTINE] Trying to fetch v{version} for {app_id}...")
        v, variants = get_target_apk_variants(base_url, version, app_id)
        if not variants:
            print(f"  -> [SKIP] No valid variants found for v{version}. Trying older version...")
            continue

        # 取得した全バリアントを優先度順に試す
        for idx, variant in enumerate(variants, 1):
            print(
                f"  -> [VARIANT {idx}/{len(variants)}] "
                f"bundle={variant.is_bundle} arch={variant.architecture} dpi={variant.dpi}"
            )

            ext = ".apkm" if variant.is_bundle else ".apk"
            filename = f"{app_id.replace('-', '')}_base"
            filepath = f"{filename}{ext}"
            merged_filepath = f"{filename}_merged.apk"

            if os.path.exists(filepath): os.remove(filepath)
            if os.path.exists(merged_filepath): os.remove(merged_filepath)

            try:
                apkmirror.download_apk(variant, path=filepath)
                if os.path.exists(filepath):
                    print(
                        f"  -> [SUCCESS] Base APK downloaded: v{version} "
                        f"({variant.architecture} / {variant.dpi})"
                    )
                    if variant.is_bundle:
                        merge_apk(filepath)
                        return merged_filepath, version
                    else:
                        return filepath, version
            except BaseException as e:
                print(f"  -> [BLOCKED] Variant {idx} failed: {e}")
                if os.path.exists(filepath): os.remove(filepath)
                if os.path.exists(merged_filepath): os.remove(merged_filepath)
                time.sleep(2)
                continue

        print(f"  -> All {len(variants)} variants failed for v{version}. Trying older version...")
        time.sleep(3)

    return None, None


# 1アプリ分（youtube or ytmusic）のビルドを実行する
def _build_one_app(app: str, patches_list: list, urls: dict):
    if app == "youtube":
        pkg = "com.google.android.youtube"
        url = urls["youtube"]
        bin_name = "youtube"
    elif app == "ytmusic":
        pkg = "com.google.android.apps.youtube.music"
        url = urls["ytmusic"]
        bin_name = "ytmusic"
    else:
        return None, None

    versions = get_supported_versions(patches_list, pkg)
    print(f"\n[{app.upper()}] Discovered versions: {versions}")

    if not versions:
        return None, None

    app_id = "youtube-music" if app == "ytmusic" else "youtube"
    input_apk, final_ver = download_with_fallback(app_id, url, versions)
    if not (input_apk and final_ver):
        return None, None

    try:
        patches = get_patches_for_version(patches_list, pkg, final_ver)
        out = build_target_apk(bin_name, final_ver, patches, input_apk)
        return out, final_ver
    except BaseException as e:
        print(f"  -> [WARNING] {app} build failed: {e}")
        return None, None


# ビルド・リリースのメインパイプライン
def process(tag: str, is_pre: bool, target_app: str, no_release: bool = False):
    print(f"\n=======================================================")
    print(f"INITIATING BUILD PIPELINE FOR: {tag} ({target_app.upper()})")
    print(f"=======================================================")

    clean_workspace()

    print("\n[STEP 3] Downloading patches & CLI...")
    subprocess.run(["gh", "release", "download", tag, "-R", "MorpheApp/morphe-patches",
                    "-p", "*.mpp", "-O", "bins/patches.mpp"], check=True)
    download_apkeditor()
    download_morphe_cli()

    print("\n[STEP 4] Extracting metadata directly from Morphe CLI...")
    patches_list = extract_patches_metadata("bins/morphe-cli.jar", "bins/patches.mpp")
    if not patches_list:
        panic("Extracted patch list is empty!")
    print(f"  -> Successfully extracted {len(patches_list)} patches metadata.")

    urls = {
        "youtube": "https://www.apkmirror.com/apk/google-inc/youtube/",
        "ytmusic": "https://www.apkmirror.com/apk/google-inc/youtube-music/",
    }

    # YT Music を先にビルド（対応バージョンが少なく、失敗＝即詰みのため）
    app_order = []
    if target_app in ["ytmusic", "all"]:
        app_order.append("ytmusic")
    if target_app in ["youtube", "all"]:
        app_order.append("youtube")

    outputs = []
    included_apps_text = []

    print("\n[STEP 5] Building target APKs with fallback...")

    first = True
    for app in app_order:
        # 1つ目のアプリの後は、レート制限回避のためクールダウンを挟む
        if not first:
            print("\n[WAIT] Cooling down before next app (avoid rate limit)...")
            time.sleep(90)
        first = False

        out, ver = _build_one_app(app, patches_list, urls)
        if out and ver:
            outputs.append(out)
            if app == "youtube":
                included_apps_text.append(f"- YouTube v{ver}")
            else:
                included_apps_text.append(f"- YouTube Music v{ver}")

    if not outputs:
        panic("No APKs were built. Aborting release.")

    # リリースノートの表示順を固定（YouTube を先頭、YouTube Music を次に）
    display_order = ["- YouTube v", "- YouTube Music v"]
    sorted_apps = sorted(
        included_apps_text,
        key=lambda s: next(
            (i for i, prefix in enumerate(display_order) if s.startswith(prefix)),
            len(display_order),
        ),
    )

    # アップロード順も同じ並びに揃える（YouTube APK を先にアップ）
    file_order = ["youtube-morphe-", "ytmusic-morphe-"]
    outputs = sorted(
        outputs,
        key=lambda f: next(
            (i for i, prefix in enumerate(file_order) if os.path.basename(f).startswith(prefix)),
            len(file_order),
        ),
    )

    apps_str = "\n".join(sorted_apps)
    message = (f"Changelogs:\n[Morphe Patches {tag}]"
               f"(https://github.com/MorpheApp/morphe-patches/releases/tag/{tag})"
               f"\n\n### Included Apps:\n{apps_str}")

    # --no-release 指定時は GitHub へのアップロードをスキップ
    if no_release:
        print(f"\n[STEP 8] Skipping release upload (--no-release).")
        print(f"  -> Upload order would be: {[os.path.basename(f) for f in outputs]}")
        print(f"  -> Built files:")
        for f in outputs:
            print(f"     - {os.path.abspath(f)}")
        print(f"  -> Tag would be: {tag} (prerelease={is_pre})")
        print(f"  -> [DONE] Build completed locally (not published).")
        return

    print(f"\n[STEP 8] Publishing release to GitHub...")
    print(f"  -> Upload order: {[os.path.basename(f) for f in outputs]}")
    publish_github_release(tag, outputs, message, f"Morphe {tag}", is_pre)
    print("  -> [DONE] Release successfully published.")