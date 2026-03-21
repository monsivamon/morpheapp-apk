# YouTube & YT Music APK (Morphe Patches) - Auto Builder

[![Build Status](https://github.com/monsivamon/morpheapp-apk/actions/workflows/build.yaml/badge.svg)](https://github.com/monsivamon/morpheapp-apk/actions)
[![Latest Release](https://img.shields.io/github/v/release/monsivamon/morpheapp-apk)](https://github.com/monsivamon/morpheapp-apk/releases/latest)

Automated build system for applying [Morphe's](https://github.com/MorpheApp/morphe-patches) patches to YouTube and YouTube Music.
The core mechanism of this builder is based on [monsivamon/twitter-apk](https://github.com/monsivamon/twitter-apk).

## ⚠️ Disclaimer
**App stability is NOT guaranteed.** This build system is configured to automatically force-apply **ALL compatible patches** for the target applications, bypassing the upstream default recommendations. Because it aggressively applies every supported patch, unexpected bugs or crashes may occur. Use at your own risk.

**Note on Missing APKs:** Due to APKMirror's strict download restrictions (Cloudflare), an app's base APK might occasionally fail to download even after attempting older compatible versions. If a release only contains either YouTube or YouTube Music, don't worry—this is completely normal. The system automatically publishes whichever app successfully builds.

## ⚠️ Requirements
To use the patched YouTube and YouTube Music apps and log in with your Google account, you **MUST** install MicroG (GmsCore). 
We highly recommend using **[MicroG-RE](https://github.com/MorpheApp/MicroG-RE)** provided by the Morphe team.

## ✨ Key Features & Improvements

### 1. Auto-Fallback Download System
To combat aggressive anti-bot measures on APKMirror, the builder extracts a list of all supported APK versions from the patch JSON. If downloading the latest version is blocked, it automatically falls back to older compatible versions until a successful download is achieved.

### 2. Force-Apply Full Patching
Instead of limiting features to official recommendations, the system dynamically parses Morphe's `patches-list.json` and forcefully applies **all compatible patches** for the successfully downloaded APK version, ensuring you get maximum features.

### 3. Dual-Track Release System (Stable & Pre-release)
The builder independently monitors the upstream repository for both **Stable** and **Pre-release** channels. It automatically triggers builds for both, appropriately tagging them on GitHub Releases, so you always have access to the latest channels.

### 4. Powered by Morphe CLI & Daily Automation
The build pipeline relies entirely on the modern **Morphe CLI** for fast, reliable patching, automatically running daily via GitHub Actions.

## 📥 Download

Get the latest pre-built APKs from the **[Releases Page](https://github.com/monsivamon/morpheapp-apk/releases)**.

## Credits

* [MorpheApp/morphe-patches](https://github.com/MorpheApp/morphe-patches) - The patch source.
* [MorpheApp/morphe-cli](https://github.com/MorpheApp/morphe-cli) - Morphe CLI patcher.