import os
import re
import time
import json
import urllib.request
import subprocess
import apkmirror

from apkmirror import Version, Variant
from utils import panic, patch_apk, merge_apk 
from download_bins import download_apkeditor, download_morphe_cli

# GitHub CLI を使ってリポジトリの「最新Stable」と「最新Pre-release」を両方取得する
def get_latest_releases(repo: str) -> dict:
    print(f"  -> Fetching release history for {repo}...")
    cmd = ["gh", "release", "list", "-R", repo, "--limit", "30", "--json", "tagName,isPrerelease"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        releases = json.loads(result.stdout)
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch releases for {repo}. Might be empty.")
        return {"stable": None, "pre": None}
        
    stable = None
    pre = None
    for r in releases:
        tag = r["tagName"]
        if r["isPrerelease"]:
            if not pre: pre = tag
        else:
            if not stable: stable = tag
        if stable and pre: break
        
    return {"stable": stable, "pre": pre}

# GitHub CLI を使ってリリースを作成（Pre-releaseフラグを自動制御）
def publish_github_release(tag_name: str, files: list, message: str, title: str, is_prerelease: bool):
    release_type = "Pre-release" if is_prerelease else "Stable release"
    print(f"  -> Publishing {release_type}: {tag_name}...")
    
    cmd = ["gh", "release", "create", tag_name] + files + ["-t", title, "-n", message]
    if is_prerelease:
        cmd.append("--prerelease")
        
    subprocess.run(cmd, check=True)


# Kotlin解析: 対象タグのソースコードからターゲットバージョンを抽出
def get_latest_version_from_kt(url: str) -> str | None:
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            content = response.read().decode('utf-8')
    except Exception as e:
        print(f"  -> [WARNING] Could not fetch {url}: {e}")
        return None

    blocks = re.findall(r'AppTarget\s*\((.*?)\)', content, re.DOTALL)
    stable_versions = []
    for block in blocks:
        if re.search(r'isExperimental\s*=\s*true', block): continue
        v_match = re.search(r'version\s*=\s*"(\d+\.\d+\.\d+)"', block)
        if v_match: stable_versions.append(v_match.group(1))

    if not stable_versions: return None
    stable_versions.sort(key=lambda s: [int(u) for u in s.split('.')])
    return stable_versions[-1]

def get_target_versions(tag: str) -> dict:
    # 常に最新ではなく「今からビルドするタグのバージョン」のソースコードを取得する
    yt_url = f"https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/tags/{tag}/patches/src/main/kotlin/app/morphe/patches/youtube/shared/Constants.kt"
    ytm_url = f"https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/tags/{tag}/patches/src/main/kotlin/app/morphe/patches/music/shared/Constants.kt"

    print(f"  -> Fetching target versions from Kotlin files (Tag: {tag})...")
    yt_version = get_latest_version_from_kt(yt_url)
    ytm_version = get_latest_version_from_kt(ytm_url)

    return {
        "youtube": {"version": yt_version, "patches": []},
        "ytmusic": {"version": ytm_version, "patches": []}
    }

# JSON直リンク（対象タグ指定）から推奨パッチを抽出
def extract_patches_from_json(tag: str) -> dict:
    url = f"https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/tags/{tag}/patches-list.json"
    print(f"  -> Extracting patches from {url}...")
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        panic(f"Failed to load patches-list.json: {e}")

    yt_patches = []
    ytm_patches = []
    mandatory_patches = ["Change package name"]

    for patch in data.get("patches", []):
        patch_name = patch.get("name")
        compat = patch.get("compatiblePackages")
        is_use_true = patch.get("use", False) 
        
        if not is_use_true and patch_name not in mandatory_patches: continue

        if isinstance(compat, dict):
            if "com.google.android.youtube" in compat: yt_patches.append(patch_name)
            if "com.google.android.apps.youtube.music" in compat: ytm_patches.append(patch_name)
                
    return {"youtube": yt_patches, "ytmusic": ytm_patches}


# APK取得: リスト画面を無視し、URLを予測して直接狙い撃つ
def get_target_apk_variant(base_url: str, target_version: str, app_id: str) -> tuple[Version | None, Variant | None]:
    if not target_version: return None, None
        
    print(f"  -> [SNIPER MODE] Predicting direct URL for {app_id} v{target_version}...")
    slug_version = target_version.replace('.', '-')
    
    urls_to_try = [
        f"{base_url}{app_id}-{slug_version}-release/",
        f"{base_url}{app_id}-{slug_version}/"
    ]
    
    variants = []
    target_v = None
    
    for url in urls_to_try:
        print(f"  -> Trying direct link: {url}")
        target_v = Version(version=target_version, link=url)
        try:
            variants = apkmirror.get_variants(target_v)
            if variants:
                print("  -> [SUCCESS] Direct link hit! Found variants.")
                break
        except Exception as e:
            time.sleep(1)
            continue

    if not variants:
        print(f"  -> [WARNING] Could not snipe the URL for {target_version}.")
        return None, None

    for variant in variants:
        if variant.is_bundle:
            arch = variant.architecture.lower()
            if "universal" in arch or "arm64" in arch or "nodpi" in arch:
                return target_v, variant
                
    for variant in variants:
        if not variant.is_bundle:
            arch = variant.architecture.lower()
            if "nodpi" in arch or "universal" in arch or "arm64" in arch:
                return target_v, variant
                
    return None, None


# ビルド実行
def build_target_apk(target_name: str, target_data: dict, input_apk: str):
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"
    version = target_data["version"]
    all_patches = target_data["patches"]
    
    output_apk = f"{target_name}-morphe-v{version}.apk"
    print(f"  -> Building {output_apk} (Force applying {len(all_patches)} patches!)...")

    patch_apk(cli, patches, input_apk, includes=all_patches, excludes=[], out=output_apk)
    
    if not os.path.exists(output_apk):
        panic(f"  -> [ERROR] Failed to build {output_apk}")
        
    print(f"  -> [SUCCESS] {output_apk} successfully built!")
    return output_apk


# 古い作業ファイルを削除（連続ビルド時の干渉を防ぐ）
def clean_workspace():
    files = ["youtube_base.apk", "youtube_base.apkm", "youtube_base_merged.apk",
             "ytmusic_base.apk", "ytmusic_base.apkm", "ytmusic_base_merged.apk", "bins/patches.mpp"]
    for f in files:
        if os.path.exists(f): os.remove(f)
    for f in os.listdir("."):
        if f.endswith(".apk") and "morphe-v" in f:
            os.remove(f)


# 処理統合: 1つのタグに対するビルドパイプライン
def process(tag: str, is_pre: bool):
    print(f"\n=======================================================")
    print(f"INITIATING BUILD PIPELINE FOR: {tag} ({'Pre-release' if is_pre else 'Stable'})")
    print(f"=======================================================")
    
    clean_workspace()

    print("\n[STEP 3] Downloading patches.mpp for the target version...")
    subprocess.run(["gh", "release", "download", tag, "-R", "MorpheApp/morphe-patches", "-p", "*.mpp", "-O", "bins/patches.mpp"], check=True)

    print("\n[STEP 4] Fetching target APK versions...")
    target_data = get_target_versions(tag)
    yt_target_ver = target_data["youtube"]["version"]
    ytm_target_ver = target_data["ytmusic"]["version"]

    yt_url = "https://www.apkmirror.com/apk/google-inc/youtube/"
    ytm_url = "https://www.apkmirror.com/apk/google-inc/youtube-music/"

    yt_v, yt_variant = get_target_apk_variant(yt_url, yt_target_ver, "youtube")
    ytm_v, ytm_variant = get_target_apk_variant(ytm_url, ytm_target_ver, "youtube-music")

    if not yt_variant and not ytm_variant:
        print("  -> [EXIT] Could not find any valid APK variants on APKMirror.")
        return

    print("\n[STEP 5] Downloading tools and base APKs...")
    download_apkeditor()

    yt_input = None
    if yt_variant:
        ext = ".apkm" if yt_variant.is_bundle else ".apk"
        apkmirror.download_apk(yt_variant, path=f"youtube_base{ext}")
        if os.path.exists(f"youtube_base{ext}"):
            if yt_variant.is_bundle:
                merge_apk("youtube_base.apkm")
                yt_input = "youtube_base_merged.apk"
            else:
                yt_input = "youtube_base.apk"

    ytm_input = None
    if ytm_variant:
        ext = ".apkm" if ytm_variant.is_bundle else ".apk"
        apkmirror.download_apk(ytm_variant, path=f"ytmusic_base{ext}")
        if os.path.exists(f"ytmusic_base{ext}"):
            if ytm_variant.is_bundle:
                merge_apk("ytmusic_base.apkm")
                ytm_input = "ytmusic_base_merged.apk"
            else:
                ytm_input = "ytmusic_base.apk"

    print("\n[STEP 6] Preparing Morphe CLI & Parsing Patches...")
    download_morphe_cli()
    patch_lists = extract_patches_from_json(tag)
    target_data["youtube"]["patches"] = patch_lists["youtube"]
    target_data["ytmusic"]["patches"] = patch_lists["ytmusic"]

    print(f"\n[STEP 7] Building patched APKs...")
    outputs = []
    if yt_input and os.path.exists(yt_input):
        outputs.append(build_target_apk("youtube", target_data["youtube"], yt_input))
    if ytm_input and os.path.exists(ytm_input):
        outputs.append(build_target_apk("ytmusic", target_data["ytmusic"], ytm_input))

    if not outputs:
        panic("  -> [ERROR] No APKs were built.")

    print(f"\n[STEP 8] Publishing release to GitHub...")
    message = f"Changelogs:\n[Morphe Patches {tag}](https://github.com/MorpheApp/morphe-patches/releases/tag/{tag})\n\n### Included Apps:\n- YouTube v{yt_target_ver}\n- YouTube Music v{ytm_target_ver}"
    publish_github_release(tag, outputs, message, f"Morphe {tag}", is_pre)
    print("  -> [DONE] Release successfully published!")


# バージョンの新旧比較ロジック
def version_greater(v1: str | None, v2: str | None) -> bool:
    if not v1: return False
    if not v2: return True
    
    print(f"  -> [DEBUG] Comparing: '{v1}' > '{v2}' ?")
    def normalize(v: str):
        v = v.lstrip('v')
        parts = v.split('-', 1)
        main_part = parts[0]
        prerelease_part = parts[1] if len(parts) > 1 else ""

        main_nums = re.findall(r'\d+', main_part)
        main_nums = [int(n) for n in main_nums[:3]]
        while len(main_nums) < 3: main_nums.append(0)

        pre_parts = []
        if prerelease_part:
            for part in re.split(r'(\d+)', prerelease_part):
                if part == '': continue
                if part.isdigit(): pre_parts.append(int(part))
                else: pre_parts.append(part)

        return main_nums, pre_parts

    nums1, pre1 = normalize(v1)
    nums2, pre2 = normalize(v2)

    for i in range(3):
        if nums1[i] != nums2[i]:
            result = nums1[i] > nums2[i]
            print(f"  -> Numeric check: {nums1[i]} vs {nums2[i]} -> {result}")
            return result

    if not pre1 and pre2: return True
    if pre1 and not pre2: return False

    for p1, p2 in zip(pre1, pre2):
        if p1 != p2:
            if type(p1) == type(p2): result = p1 > p2
            else: result = str(p1) > str(p2)
            print(f"  -> Prerelease check: {p1} vs {p2} -> {result}")
            return result
    return len(pre1) > len(pre2)


# メイン処理
def main():
    repo_url = "monsivamon/morpheapp-apk" 
    upstream_repo = "MorpheApp/morphe-patches"

    print("\n[STEP 1] Fetching release history for upstream and my repo...")
    upstream = get_latest_releases(upstream_repo)
    my_repo = get_latest_releases(repo_url)
    
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

    print(f"  -> [RESULT] Found {len(build_targets)} pending update(s)!")
    
    for target in build_targets:
        process(target["tag"], target["is_pre"])


if __name__ == "__main__":
    main()

    