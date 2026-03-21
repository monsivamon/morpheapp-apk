# YouTube & YT Music APK (Morphe Patches) - Auto Builder

[![Build Status](https://github.com/monsivamon/morpheapp-apk/actions/workflows/build.yaml/badge.svg)](https://github.com/monsivamon/morpheapp-apk/actions)
[![Latest Release](https://img.shields.io/github/v/release/monsivamon/morpheapp-apk)](https://github.com/monsivamon/morpheapp-apk/releases/latest)

Automated build system for applying [Morphe's](https://github.com/MorpheApp/morphe-patches) patches to YouTube and YouTube Music.
The core mechanism of this builder is based on [monsivamon/twitter-apk](https://github.com/monsivamon/twitter-apk).

## ⚠️ Disclaimer
**App stability is NOT guaranteed.** This build system is configured to automatically apply the **default recommended patches** determined by the Morphe CLI. While this is generally stable, unexpected bugs or crashes may still occur depending on upstream patch updates. Use at your own risk.

## ⚠️ Requirements
To use the patched YouTube and YouTube Music apps and log in with your Google account, you **MUST** install MicroG (GmsCore). 
We highly recommend using **[MicroG-RE](https://github.com/MorpheApp/MicroG-RE)** provided by the Morphe team.

## ✨ Key Features & Improvements

### 1. Always Latest Patches (Smart Parsing)
The system automatically detects updates to **Morphe's patches**. It directly parses Morphe's Kotlin source code (`Constants.kt`) to accurately extract the exactly supported base APK versions, downloads them from APKMirror, and rebuilds the apps.

### 2. Powered by Morphe CLI
The build pipeline relies entirely on **Morphe CLI** for fast, reliable, and modern patch application.

### 3. Daily Automation
Checks for patch updates every day automatically via GitHub Actions.

## 📥 Download

Get the latest pre-built APKs from the **[Releases Page](https://github.com/monsivamon/morpheapp-apk/releases)**.

## Credits

* [MorpheApp/morphe-patches](https://github.com/MorpheApp/morphe-patches) - The patch source.
* [MorpheApp/morphe-cli](https://github.com/MorpheApp/morphe-cli) - Morphe CLI patcher.