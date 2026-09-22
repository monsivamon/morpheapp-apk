import re
import subprocess

from utils import panic


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