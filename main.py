import os
import re
import time
import json
import urllib.request
import apkmirror
import github

from apkmirror import Version, Variant
from utils import panic, publish_release, patch_apk, merge_apk 
from download_bins import download_apkeditor, download_morphe_cli, download_release_asset

# [STEP 1] Kotlin解析: Morpheのソースコードからターゲットバージョンを抽出
def get_latest_version_from_kt(url: str) -> str | None:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        content = response.read().decode('utf-8')

    # AppTarget( から ) までのブロックをそれぞれ抽出
    blocks = re.findall(r'AppTarget\s*\((.*?)\)', content, re.DOTALL)
    
    stable_versions = []
    for block in blocks:
        # "isExperimental = true" の記述があればスキップ
        if re.search(r'isExperimental\s*=\s*true', block):
            continue
            
        # 安定版ブロックからバージョン番号を抽出
        v_match = re.search(r'version\s*=\s*"(\d+\.\d+\.\d+)"', block)
        if v_match:
            stable_versions.append(v_match.group(1))

    if not stable_versions:
        return None
        
    # バージョンを数値のリストとして比較し、最も新しい安定版を取得
    stable_versions.sort(key=lambda s: [int(u) for u in s.split('.')])
    return stable_versions[-1]

def get_target_versions() -> dict:
    yt_url = "https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/heads/main/patches/src/main/kotlin/app/morphe/patches/youtube/shared/Constants.kt"
    ytm_url = "https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/heads/main/patches/src/main/kotlin/app/morphe/patches/music/shared/Constants.kt"

    print("  -> Fetching target versions from Morphe Kotlin files...")
    yt_version = get_latest_version_from_kt(yt_url)
    ytm_version = get_latest_version_from_kt(ytm_url)

    return {
        "youtube": {"version": yt_version, "patches": []},
        "ytmusic": {"version": ytm_version, "patches": []}
    }

# patches-list.json から「推奨(use: true)」と「必須(パッケージ名変更)」を抽出
def extract_patches_from_json() -> dict:
    url = "https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/heads/main/patches-list.json"
    print(f"  -> Extracting patches from {url}...")
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        panic(f"Failed to load patches-list.json: {e}")

    yt_patches = []
    ytm_patches = []

    # インストールに絶対必要なパッチ（use: false でも強制的にリストにねじ込む）
    mandatory_patches = ["Change package name"]

    for patch in data.get("patches", []):
        patch_name = patch.get("name")
        compat = patch.get("compatiblePackages")
        is_use_true = patch.get("use", False) 
        
        # "use: true" の推奨パッチ、または "必須パッチ" の場合のみ採用してリスト化
        if not is_use_true and patch_name not in mandatory_patches:
            continue

        if isinstance(compat, dict):
            if "com.google.android.youtube" in compat: 
                yt_patches.append(patch_name)
            if "com.google.android.apps.youtube.music" in compat: 
                ytm_patches.append(patch_name)
                
    return {"youtube": yt_patches, "ytmusic": ytm_patches}


# [STEP 2] APK取得: リスト画面を無視し、URLを予測して直接狙い撃つ
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
            print(f"  -> Failed or Not Found: {e}")
            time.sleep(1)
            continue

    if not variants:
        print(f"  -> [WARNING] Could not snipe the URL for {target_version}.")
        return None, None

    for variant in variants:
        if variant.is_bundle:
            arch = variant.architecture.lower()
            if "universal" in arch or "arm64" in arch or "nodpi" in arch:
                print(f"  -> [SUCCESS] Valid BUNDLE APK found: {arch}")
                return target_v, variant
                
    for variant in variants:
        if not variant.is_bundle:
            arch = variant.architecture.lower()
            if "nodpi" in arch or "universal" in arch or "arm64" in arch:
                print(f"  -> [SUCCESS] Valid normal APK found: {arch}")
                return target_v, variant
                
    print(f"  -> [WARNING] No valid APK found for {target_version}.")
    return None, None


# [STEP 3] ビルド実行: 抽出したパッチリストを強制適用モード
def build_target_apk(target_name: str, target_data: dict, input_apk: str):
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"
    version = target_data["version"]
    all_patches = target_data["patches"]
    
    output_apk = f"{target_name}-morphe-v{version}.apk"
    print(f"  -> Building {output_apk} (Force applying {len(all_patches)} patches!)...")

    patch_apk(
        cli, patches, input_apk,
        includes=all_patches,
        excludes=[],
        out=output_apk,
    )
    
    if not os.path.exists(output_apk):
        panic(f"  -> [ERROR] Failed to build {output_apk}")
        
    print(f"  -> [SUCCESS] {output_apk} successfully built!")
    return output_apk


# [STEP 4] 処理統合: ダウンロード〜マージ〜ビルドのパイプライン
def process(patch_version: str, morpheRelease, target_data: dict, yt_variant: Variant, ytm_variant: Variant):
    print("\n[STEP 4] Downloading tools and base APKs...")
    download_apkeditor()

    yt_input = None
    if yt_variant:
        is_yt_bundle = yt_variant.is_bundle
        ext = ".apkm" if is_yt_bundle else ".apk"
        
        print(f"  -> Downloading YouTube base {ext}...")
        apkmirror.download_apk(yt_variant, path=f"youtube_base{ext}")
        
        if os.path.exists(f"youtube_base{ext}"):
            if is_yt_bundle:
                print("  -> Merging YouTube Bundle into a single APK...")
                merge_apk("youtube_base.apkm")
                yt_input = "youtube_base_merged.apk"
            else:
                print("  -> Base is already a single APK. Skipping merge.")
                yt_input = "youtube_base.apk"

    ytm_input = None
    if ytm_variant:
        is_ytm_bundle = ytm_variant.is_bundle
        ext = ".apkm" if is_ytm_bundle else ".apk"
        
        print(f"  -> Downloading YouTube Music base {ext}...")
        apkmirror.download_apk(ytm_variant, path=f"ytmusic_base{ext}")
        
        if os.path.exists(f"ytmusic_base{ext}"):
            if is_ytm_bundle:
                print("  -> Merging YouTube Music Bundle into a single APK...")
                merge_apk("ytmusic_base.apkm")
                ytm_input = "ytmusic_base_merged.apk"
            else:
                print("  -> Base is already a single APK. Skipping merge.")
                ytm_input = "ytmusic_base.apk"

    print("\n[STEP 5] Preparing Morphe CLI & Parsing Patches...")
    download_morphe_cli()
    
    # JSON直リンクから「推奨＋必須パッチ」を抽出して target_data にセット
    patch_lists = extract_patches_from_json()
    target_data["youtube"]["patches"] = patch_lists["youtube"]
    target_data["ytmusic"]["patches"] = patch_lists["ytmusic"]

    print(f"\n[STEP 6] Building patched APKs...")
    outputs = []
    
    if yt_input and os.path.exists(yt_input):
        out = build_target_apk("youtube", target_data["youtube"], yt_input)
        outputs.append(out)
        
    if ytm_input and os.path.exists(ytm_input):
        out = build_target_apk("ytmusic", target_data["ytmusic"], ytm_input)
        outputs.append(out)

    if not outputs:
        panic("  -> [ERROR] No APKs were built.")

    print(f"\n[STEP 7] Publishing release to GitHub (Tag: {patch_version})...")
    message: str = f"Changelogs:\n[Morphe Patches {patch_version}]({morpheRelease['html_url']})"
    
    publish_release(
        patch_version,
        outputs,
        message,
        f"Morphe {patch_version}"
    )
    print("  -> [DONE] Release successfully published!")
