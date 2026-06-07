import sys
import os
import re
import time
import json
import subprocess
import argparse
import apkmirror
from functools import cmp_to_key

# 意図しないプロセス強制終了(sys.exit)を防ぐ
class ProcessExitException(BaseException): pass
def prevent_exit(code=0):
    raise ProcessExitException(f"Process exit prevented! (exit code {code})")
    
sys.exit = prevent_exit
os._exit = prevent_exit

from apkmirror import Version, Variant
from utils import patch_apk, merge_apk 
from download_bins import download_apkeditor, download_morphe_cli

# エラー発生時にメッセージを出力して中断する
def panic(msg):
    print(f"  -> [FATAL] {msg}")
    raise ProcessExitException(msg)

# v1がv2より新しい(大きい)か判定する
def version_greater(v1: str | None, v2: str | None) -> bool:
    if not v1: return False
    if not v2: return True
    def normalize(v: str):
        v = v.lstrip('v')
        parts = v.split('-', 1)
        main_part = parts[0]
        prerelease_part = parts[1] if len(parts) > 1 else ""
        main_nums = [int(n) for n in re.findall(r'\d+', main_part)[:3]]
        while len(main_nums) < 3: main_nums.append(0)
        pre_parts = [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', prerelease_part) if p]
        return main_nums, pre_parts

    nums1, pre1 = normalize(v1)
    nums2, pre2 = normalize(v2)

    for i in range(3):
        if nums1[i] != nums2[i]: return nums1[i] > nums2[i]

    if not pre1 and pre2: return True
    if pre1 and not pre2: return False
    for p1, p2 in zip(pre1, pre2):
        if p1 != p2:
            return p1 > p2 if type(p1) == type(p2) else str(p1) > str(p2)
    return len(pre1) > len(pre2)

# リポジトリから最新のStable/Preリリースを取得する
def get_latest_releases(repo: str, require_mpp: bool = False) -> dict:
    print(f"  -> Fetching release history for {repo}...")
    cmd = ["gh", "api", f"repos/{repo}/releases?per_page=30"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        releases = json.loads(result.stdout)
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch releases for {repo}: {e}")
        return {"stable": None, "pre": None}
        
    valid_stable = []
    valid_pre = []

    for r in releases:
        tag = r.get("tag_name")
        is_pre = r.get("prerelease", False)
        
        if require_mpp:
            has_mpp = any(a.get("name", "").endswith(".mpp") for a in r.get("assets", []))
            if not has_mpp: continue
        
        if is_pre:
            valid_pre.append(tag)
        else:
            valid_stable.append(tag)

    def cmp_versions(v1, v2):
        if v1 == v2: return 0
        return 1 if version_greater(v1, v2) else -1

    if valid_stable:
        valid_stable.sort(key=cmp_to_key(cmp_versions), reverse=True)
    if valid_pre:
        valid_pre.sort(key=cmp_to_key(cmp_versions), reverse=True)

    return {
        "stable": valid_stable[0] if valid_stable else None,
        "pre": valid_pre[0] if valid_pre else None
    }

# GitHubリリースを作成または更新する
def publish_github_release(tag_name: str, files: list, message: str, title: str, is_prerelease: bool):
    release_type = "Pre-release" if is_prerelease else "Stable release"
    print(f"  -> Publishing {release_type}: {tag_name}...")
    check_cmd = ["gh", "release", "view", tag_name]
    res = subprocess.run(check_cmd, capture_output=True)
    
    if res.returncode == 0:
        print("  -> Release already exists. Uploading assets to the existing release...")
        subprocess.run(["gh", "release", "upload", tag_name] + files + ["--clobber"], check=True)
    else:
        print("  -> Creating new release...")
        cmd_create = ["gh", "release", "create", tag_name] + files + ["-t", title, "-n", message]
        if is_prerelease: cmd_create.append("--prerelease")
        try:
            subprocess.run(cmd_create, check=True)
        except subprocess.CalledProcessError:
            print("  -> Release creation failed (race condition). Falling back to upload...")
            subprocess.run(["gh", "release", "upload", tag_name] + files + ["--clobber"], check=True)

# =======================================================================================
# CLIのテキスト出力を解析し、MPPファイルから直接メタデータを生成する
# =======================================================================================
def extract_patches_metadata(cli_path: str, mpp_path: str) -> list:
    print(f"  -> Extracting patch list dynamically from {mpp_path} via CLI...")
    
    cmd = ["java", "-jar", cli_path, "list-patches", f"--patches={mpp_path}", "-p", "-v"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
        out = result.stdout
    except subprocess.CalledProcessError as e:
        panic(f"Failed to extract patches from CLI. Error: {e.stderr}")
    except Exception as e:
        panic(f"Failed to execute CLI command. Error: {e}")

    patches = []
    current_patch = None
    current_package = None
    in_versions = False

    for line in out.splitlines():
        s_line = line.strip()
        
        if not s_line:
            continue

        if s_line.startswith('Index:'):
            current_patch = {"name": "", "compatiblePackages": []}
            patches.append(current_patch)
            current_package = None
            in_versions = False
            
        elif s_line.startswith('Name:') and current_patch is not None:
            current_patch["name"] = s_line[5:].strip()
            
        elif s_line.startswith('Package name:'):
            pkg_name = s_line.split('Package name:', 1)[1].strip()
            current_package = {"name": pkg_name, "versions": []}
            if current_patch is not None:
                current_patch["compatiblePackages"].append(current_package)
            in_versions = False
            
        elif s_line.startswith('Compatible versions:'):
            in_versions = True
            
        elif in_versions and current_package is not None:
            if s_line[0].isdigit():
                current_package["versions"].append(s_line)
            else:
                in_versions = False

    if not patches:
        panic("Could not parse any patch data from CLI text output.")
        
    return patches

# 対象アプリがサポートするAPKバージョンのリストを抽出し、直近5件を古い順にソートして返す
def get_supported_versions(patches_list: list, package_name: str) -> list:
    versions_set = set()
    for patch in patches_list:
        compat = patch.get("compatiblePackages")
        if isinstance(compat, dict) and package_name in compat:
            if compat[package_name]: versions_set.update(compat[package_name])
        elif isinstance(compat, list):
            for pkg in compat:
                if isinstance(pkg, dict) and pkg.get("name") == package_name:
                    if pkg.get("versions"): versions_set.update(pkg.get("versions"))

    def parse_ver(v):
        return [int(x) for x in re.findall(r'\d+', v)]
    
    sorted_versions = sorted(list(versions_set), key=parse_ver)
    return sorted_versions[-5:]

# 指定されたAPKバージョンと互換性のあるすべてのパッチを抽出する
def get_patches_for_version(patches_list: list, package_name: str, target_version: str) -> list:
    patches = []
    for patch in patches_list:
        patch_name = patch.get("name")
        compat = patch.get("compatiblePackages")

        supports_version = False
        if not compat: 
            supports_version = True
        elif isinstance(compat, dict) and package_name in compat:
            versions = compat[package_name]
            if not versions or target_version in versions: supports_version = True
        elif isinstance(compat, list):
            for pkg in compat:
                if isinstance(pkg, dict) and pkg.get("name") == package_name:
                    versions = pkg.get("versions", [])
                    if not versions or target_version in versions: supports_version = True
                    break

        if supports_version:
            patches.append(patch_name)

    return patches


# APKMirrorから最適なAPK情報を取得する
def get_target_apk_variant(base_url: str, target_version: str, app_id: str) -> tuple[Version | None, Variant | None]:
    if not target_version: return None, None
    print(f"  -> Predicting direct URL for {app_id} v{target_version}...")
    slug_version = target_version.replace('.', '-')
    urls_to_try = [f"{base_url}{app_id}-{slug_version}-release/", f"{base_url}{app_id}-{slug_version}/"]
    
    variants = []
    target_v = None
    for url in urls_to_try:
        target_v = Version(version=target_version, link=url)
        try:
            variants = apkmirror.get_variants(target_v)
            if variants: break
        except BaseException:
            time.sleep(1)
            continue

    if not variants: return None, None

    for variant in variants:
        if variant.is_bundle:
            arch = variant.architecture.lower()
            if "universal" in arch or "arm64" in arch or "nodpi" in arch: return target_v, variant
    for variant in variants:
        if not variant.is_bundle:
            arch = variant.architecture.lower()
            if "nodpi" in arch or "universal" in arch or "arm64" in arch: return target_v, variant
    return None, None

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
    for f in ["youtube_base.apk", "youtube_base.apkm", "youtube_base_merged.apk", "ytmusic_base.apk", "ytmusic_base.apkm", "ytmusic_base_merged.apk", "bins/patches.mpp"]:
        if os.path.exists(f): os.remove(f)
    for f in os.listdir("."):
        if f.endswith(".apk") and "morphe-v" in f: os.remove(f)

# フォールバックしながらベースAPKをダウンロードする
def download_with_fallback(app_id: str, base_url: str, supported_versions: list):
    for version in reversed(supported_versions): 
        print(f"\n  -> [FALLBACK ROUTINE] Trying to fetch v{version} for {app_id}...")
        v, variant = get_target_apk_variant(base_url, version, app_id)
        if not variant:
            print(f"  -> [SKIP] No valid variants found for v{version}. Trying older version...")
            continue

        ext = ".apkm" if variant.is_bundle else ".apk"
        filename = f"{app_id.replace('-', '')}_base"
        filepath = f"{filename}{ext}"
        merged_filepath = f"{filename}_merged.apk"

        if os.path.exists(filepath): os.remove(filepath)
        if os.path.exists(merged_filepath): os.remove(merged_filepath)

        try:
            apkmirror.download_apk(variant, path=filepath)
            if os.path.exists(filepath):
                print(f"  -> [SUCCESS] Base APK downloaded: v{version}")
                if variant.is_bundle:
                    merge_apk(filepath)
                    return merged_filepath, version
                else:
                    return filepath, version
        except BaseException as e:
            print(f"  -> [BLOCKED] Download failed for v{version}. Intercepted exit: {e}")
            if os.path.exists(filepath): os.remove(filepath)
            if os.path.exists(merged_filepath): os.remove(merged_filepath)
            print("  -> Retrying with an older supported version...")
            time.sleep(3) 
            continue

    return None, None

# ビルド・リリースのメインパイプライン
def process(tag: str, is_pre: bool, target_app: str):
    print(f"\n=======================================================")
    print(f"INITIATING BUILD PIPELINE FOR: {tag} ({target_app.upper()})")
    print(f"=======================================================")
    
    clean_workspace()

    print("\n[STEP 3] Downloading patches & CLI...")
    subprocess.run(["gh", "release", "download", tag, "-R", "MorpheApp/morphe-patches", "-p", "*.mpp", "-O", "bins/patches.mpp"], check=True)
    download_apkeditor()
    download_morphe_cli()

    print("\n[STEP 4] Extracting metadata directly from Morphe CLI...")
    patches_list = extract_patches_metadata("bins/morphe-cli.jar", "bins/patches.mpp")
    if not patches_list:
        panic("Extracted patch list is empty!")
    print(f"  -> Successfully extracted {len(patches_list)} patches metadata.")
    
    yt_url = "https://www.apkmirror.com/apk/google-inc/youtube/"
    ytm_url = "https://www.apkmirror.com/apk/google-inc/youtube-music/"

    outputs = []
    included_apps_text = []

    print("\n[STEP 5] Building target APKs with fallback...")

    if target_app in ["youtube", "all"]:
        yt_versions = get_supported_versions(patches_list, "com.google.android.youtube")
        print(f"\n[YOUTUBE] Discovered versions: {yt_versions}")
        
        if yt_versions:
            yt_input, final_yt_ver = download_with_fallback("youtube", yt_url, yt_versions)
            if yt_input and final_yt_ver:
                try:
                    yt_patches = get_patches_for_version(patches_list, "com.google.android.youtube", final_yt_ver)
                    outputs.append(build_target_apk("youtube", final_yt_ver, yt_patches, yt_input))
                    included_apps_text.append(f"- YouTube v{final_yt_ver}")
                except BaseException as e:
                    print(f"  -> [WARNING] YouTube build failed: {e}")

    if target_app in ["ytmusic", "all"]:
        ytm_versions = get_supported_versions(patches_list, "com.google.android.apps.youtube.music")
        print(f"\n[YT MUSIC] Discovered versions: {ytm_versions}")
        
        if ytm_versions:
            ytm_input, final_ytm_ver = download_with_fallback("youtube-music", ytm_url, ytm_versions)
            if ytm_input and final_ytm_ver:
                try:
                    ytm_patches = get_patches_for_version(patches_list, "com.google.android.apps.youtube.music", final_ytm_ver)
                    outputs.append(build_target_apk("ytmusic", final_ytm_ver, ytm_patches, ytm_input))
                    included_apps_text.append(f"- YouTube Music v{final_ytm_ver}")
                except BaseException as e:
                    print(f"  -> [WARNING] YT Music build failed: {e}")

    if not outputs:
        panic("No APKs were built. Aborting release.")

    print(f"\n[STEP 8] Publishing release to GitHub...")
    apps_str = "\n".join(included_apps_text)
    message = f"Changelogs:\n[Morphe Patches {tag}](https://github.com/MorpheApp/morphe-patches/releases/tag/{tag})\n\n### Included Apps:\n{apps_str}"
    
    publish_github_release(tag, outputs, message, f"Morphe {tag}", is_pre)
    print("  -> [DONE] Release successfully published.")

# エントリポイント: 更新を確認し処理を開始する
def main():
    parser = argparse.ArgumentParser(description="Morphe Auto Builder")
    parser.add_argument("--app", choices=["youtube", "ytmusic", "all"], default="all", help="Which app to build")
    args = parser.parse_args()

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
    
    if upstream["stable"] and version_greater(upstream["stable"], my_repo["stable"]):
        build_targets.append({"tag": upstream["stable"], "is_pre": False})
        
    if upstream["pre"] and version_greater(upstream["pre"], my_repo["pre"]):
        build_targets.append({"tag": upstream["pre"], "is_pre": True})

    if not build_targets:
        print("  -> [EXIT] No new updates found. Skipping build.")
        return

    print(f"  -> [RESULT] Found {len(build_targets)} pending update(s).")
    
    for target in build_targets:
        process(target["tag"], target["is_pre"], args.app)

if __name__ == "__main__":
    main()