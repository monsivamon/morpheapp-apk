import os
import re
import time
import urllib.request
import apkmirror
import github

from apkmirror import Version, Variant
from utils import panic, publish_release, patch_apk
from download_bins import download_morphe_cli, download_release_asset

# [STEP 1] Kotlin解析: Morpheのソースコードからターゲットバージョンを正規表現で抽出
def get_latest_version_from_kt(url: str) -> str | None:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        content = response.read().decode('utf-8')

    # "20.45.36" のようなバージョン番号の文字列をすべて抽出
    versions = re.findall(r'"(\d+\.\d+\.\d+)"', content)
    if not versions:
        return None
        
    # バージョンを数値のリストとして比較し、最も新しいもの（最大値）を取得
    versions.sort(key=lambda s: [int(u) for u in s.split('.')])
    return versions[-1]

def get_target_versions() -> dict:
    yt_url = "https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/heads/main/patches/src/main/kotlin/app/morphe/patches/youtube/shared/Constants.kt"
    ytm_url = "https://raw.githubusercontent.com/MorpheApp/morphe-patches/refs/heads/main/patches/src/main/kotlin/app/morphe/patches/music/shared/Constants.kt"

    print("  -> Fetching target versions from Morphe Kotlin files...")
    
    yt_version = get_latest_version_from_kt(yt_url)
    ytm_version = get_latest_version_from_kt(ytm_url)

    return {
        "youtube": {"version": yt_version},
        "ytmusic": {"version": ytm_version}
    }

# [STEP 2] APK取得: リスト画面を無視し、URLを予測して直接狙い撃つ（スナイパーモード）
def get_target_apk_variant(base_url: str, target_version: str, app_id: str) -> tuple[Version | None, Variant | None]:
    if not target_version:
        return None, None
        
    print(f"  -> [SNIPER MODE] Predicting direct URL for {app_id} v{target_version}...")
    
    # バージョンのドットをハイフンに変換 (例: 20.05.46 -> 20-05-46)
    slug_version = target_version.replace('.', '-')
    
    # 考えられるURLパターンのリスト（APKMirrorは -release が付く場合と付かない場合がある）
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
            # リスト画面を通さず、いきなりバリアント取得関数に突撃する
            variants = apkmirror.get_variants(target_v)
            if variants:
                print("  -> [SUCCESS] Direct link hit! Found variants.")
                break # 見つかったらURL試行ループを抜ける
        except Exception as e:
            print(f"  -> Failed or Not Found: {e}")
            time.sleep(1) # 次のURLパターンを試す前に1秒待機
            continue

    if not variants:
        print(f"  -> [WARNING] Could not snipe the URL for {target_version}. Cloudflare block or wrong version.")
        return None, None

    # Bundleを避け、nodpi, universal, または arm64 の通常APKを探す
    for variant in variants:
        if not variant.is_bundle:
            arch = variant.architecture.lower()
            if "nodpi" in arch or "universal" in arch or "arm64" in arch:
                print(f"  -> [SUCCESS] Valid normal APK found: {arch}")
                return target_v, variant
                
    print(f"  -> [WARNING] No valid normal APK (non-bundle) found for {target_version}.")
    return None, None

# [STEP 3] ビルド実行: デフォルト推奨パッチ全適用モード
def build_target_apk(target_name: str, target_data: dict, input_apk: str):
    patches = "bins/patches.mpp"
    cli = "bins/morphe-cli.jar"
    
    version = target_data["version"]
    
    output_apk = f"{target_name}-morphe-v{version}.apk"
    print(f"  -> Building {output_apk} (Applying all default patches)...")

    exclude_list = []

    # includesを指定しないことで、Morpheのデフォルト推奨パッチがすべて自動で適用されます
    patch_apk(
        cli, patches, input_apk,
        includes=[], 
        excludes=exclude_list,
        out=output_apk,
    )
    
    if not os.path.exists(output_apk):
        panic(f"  -> [ERROR] Failed to build {output_apk}")
        
    print(f"  -> [SUCCESS] {output_apk} successfully built!")
    return output_apk

# [STEP 4] 処理統合: ダウンロード〜ビルド〜リリースのパイプライン
def process(patch_version: str, morpheRelease, target_data: dict, yt_variant: Variant, ytm_variant: Variant):
    print("\n[STEP 4] Downloading base APKs (Directly to .apk, no merge needed)...")
    
    if yt_variant:
        print("  -> Downloading YouTube base APK...")
        apkmirror.download_apk(yt_variant, path="youtube_base.apk")
    if ytm_variant:
        print("  -> Downloading YouTube Music base APK...")
        apkmirror.download_apk(ytm_variant, path="ytmusic_base.apk")

    print("\n[STEP 5] Preparing Morphe CLI...")
    download_morphe_cli()
    
    print(f"\n[STEP 6] Building patched APKs...")
    outputs = []
    
    if os.path.exists("youtube_base.apk"):
        out = build_target_apk("youtube", target_data["youtube"], "youtube_base.apk")
        outputs.append(out)
        
    if os.path.exists("ytmusic_base.apk"):
        out = build_target_apk("ytmusic", target_data["ytmusic"], "ytmusic_base.apk")
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

# バージョンの新旧比較ロジック
# プレリリース部分（例: dev.10 と dev.9）も正しく数値として比較する完全版。
def version_greater(v1: str, v2: str) -> bool:
    print(f"\n[DEBUG] Comparing: '{v1}' > '{v2}' ?")

    def normalize(v: str):
        v = v.lstrip('v')
        parts = v.split('-', 1)
        main_part = parts[0]
        prerelease_part = parts[1] if len(parts) > 1 else ""

        # メインバージョンの抽出 (例: 11.65.0 -> [11, 65, 0])
        main_nums = re.findall(r'\d+', main_part)
        main_nums = [int(n) for n in main_nums[:3]]
        while len(main_nums) < 3:
            main_nums.append(0)

        # プレリリース部分を文字列と数値に分解 (例: dev.10 -> ['dev.', 10])
        pre_parts = []
        if prerelease_part:
            for part in re.split(r'(\d+)', prerelease_part):
                if part == '':
                    continue
                if part.isdigit():
                    pre_parts.append(int(part))
                else:
                    pre_parts.append(part)

        return main_nums, pre_parts

    nums1, pre1 = normalize(v1)
    nums2, pre2 = normalize(v2)

    # 1. メインバージョンの比較
    for i in range(3):
        if nums1[i] != nums2[i]:
            result = nums1[i] > nums2[i]
            print(f"  -> Numeric check pos {i+1}: {nums1[i]} vs {nums2[i]} -> {result}")
            return result

    # 2. プレリリース部分の有無を比較 (正式版はプレリリース版より新しい)
    if not pre1 and pre2:
        return True
    if pre1 and not pre2:
        return False

    # 3. プレリリース部分の各要素を比較
    for p1, p2 in zip(pre1, pre2):
        if p1 != p2:
            if type(p1) == type(p2):
                result = p1 > p2
            else:
                result = str(p1) > str(p2)
            print(f"  -> Prerelease check: {p1} vs {p2} -> {result}")
            return result

    # 全て一致した場合は、要素数が多い方が新しい (例: dev.1.1 > dev.1)
    return len(pre1) > len(pre2)

# メインシーケンス
def main():
    # 🚀 変更完了！指定されたリポジトリ名にセットしました
    repo_url: str = "monsivamon/morpheapp-apk" 
    yt_url: str = "https://www.apkmirror.com/apk/google-inc/youtube/"
    ytm_url: str = "https://www.apkmirror.com/apk/google-inc/youtube-music/"

    print("\n[STEP 1] Fetching the latest Morphe patches from GitHub...")
    # 1. 最新のMorpheパッチ(.mpp)を取得
    morpheRelease = download_release_asset(
        "MorpheApp/morphe-patches",
        r".*\.mpp$",
        "bins",
        "patches.mpp",
        include_prereleases=True
    )
    final_patch_ver = morpheRelease["tag_name"]
    print(f"  -> Latest Morphe patch: {final_patch_ver}")

    print("\n[STEP 2] Verifying build history for updates...")
    last_build_version = github.get_last_build_version(repo_url)
    last_ver_patch = last_build_version.tag_name if last_build_version else None

    print(f"  -> Target Patch: {final_patch_ver}")
    print(f"  -> Previous Build Patch: {last_ver_patch}")

    is_new_patch = False
    if last_ver_patch is None:
        print("  -> No previous release found. Treating as initial build.")
        is_new_patch = True
    else:
        is_new_patch = version_greater(final_patch_ver, last_ver_patch)

    if not is_new_patch:
        print("\n  -> [EXIT] No updates for Morphe patches. Skipping build.")
        return

    print("\n  -> [RESULT] Patch update detected! Initiating build sequence.")

    print("\n[STEP 3] Fetching target APK versions from Constants.kt...")
    # 3. Kotlinファイルから最新対応バージョンを取得
    target_data = get_target_versions()
    yt_target_ver = target_data["youtube"]["version"]
    ytm_target_ver = target_data["ytmusic"]["version"]
    print(f"  -> Target YouTube version: {yt_target_ver}")
    print(f"  -> Target YT Music version: {ytm_target_ver}")

    # 4. APKMirrorから指定バージョンの通常APKをピンポイントで探す (スナイパーモード)
    yt_v, yt_variant = get_target_apk_variant(yt_url, yt_target_ver, "youtube")
    ytm_v, ytm_variant = get_target_apk_variant(ytm_url, ytm_target_ver, "youtube-music")

    if not yt_variant and not ytm_variant:
        print("  -> [EXIT] Could not find any valid APK variants on APKMirror.")
        return

    # 5. すべての準備が整ったらビルドパイプラインへ！
    process(final_patch_ver, morpheRelease, target_data, yt_variant, ytm_variant)

if __name__ == "__main__":
    main()