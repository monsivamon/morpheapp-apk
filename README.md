# YouTube & YT Music APK (Morphe Patches) - Auto Builder

[![Build Status](https://github.com/monsivamon/morpheapp-apk/actions/workflows/build.yaml/badge.svg)](https://github.com/monsivamon/morpheapp-apk/actions)
[![Latest Release](https://img.shields.io/github/v/release/monsivamon/morpheapp-apk)](https://github.com/monsivamon/morpheapp-apk/releases/latest)

Automated build system for applying [Morphe's](https://github.com/MorpheApp/morphe-patches) patches to YouTube and YouTube Music.
The core mechanism of this builder is based on [monsivamon/twitter-apk](https://github.com/monsivamon/twitter-apk).

## ⚠️ Disclaimer
**App stability is NOT guaranteed.** This build system is configured to automatically force-apply **ALL compatible patches** for the target applications, bypassing the upstream default recommendations. Because it aggressively applies every supported patch, unexpected bugs or crashes may occur. Use at your own risk.

**Note on Missing APKs:** Due to APKMirror's strict download restrictions (Cloudflare), an app's base APK might occasionally fail to download. In that case, the builder automatically falls back to older compatible versions, so **the released version may not always be the latest supported one**. If a release only contains either YouTube or YouTube Music, **this is by design** — the system publishes whichever app successfully builds and skips the other.

## ⚠️ Requirements
To use the patched YouTube and YouTube Music apps and log in with your Google account, you **MUST** install MicroG (GmsCore). 
We highly recommend using **[MicroG-RE](https://github.com/MorpheApp/MicroG-RE)** provided by the Morphe team.

## ✨ Key Features & Improvements

### 1. Dynamic Patch Extraction via Morphe CLI
Instead of relying on fragile upstream JSON files or Kotlin constants, the system dynamically parses patch metadata (including version compatibility) directly from the **Morphe CLI** text output and forcefully applies **all compatible patches** for the successfully downloaded APK version, ensuring you get maximum features.

### 2. Dual-Track Release System (Stable & Pre-release)
The builder independently monitors the upstream repository for both **Stable** and **Pre-release** channels. It automatically triggers builds for both, appropriately tagging them on GitHub Releases, so you always have access to the latest channels.

### 3. Daily Automation
The build pipeline relies entirely on the modern Morphe CLI for fast, reliable patching, automatically running daily via GitHub Actions.

## 📥 Download

Get the latest pre-built APKs from the **[Releases Page](https://github.com/monsivamon/morpheapp-apk/releases)**.

## Credits

* [MorpheApp/morphe-patches](https://github.com/MorpheApp/morphe-patches) - The patch source.
* [MorpheApp/morphe-cli](https://github.com/MorpheApp/morphe-cli) - Morphe CLI patcher.