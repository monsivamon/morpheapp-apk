# YouTube & YT Music APK (Morphe Patches) - Auto Builder

[![Build Status](https://github.com/monsivamon/morpheapp-apk/actions/workflows/build.yaml/badge.svg)](https://github.com/monsivamon/morpheapp-apk/actions)
[![Latest Release](https://img.shields.io/github/v/release/monsivamon/morpheapp-apk)](https://github.com/monsivamon/morpheapp-apk/releases/latest)

Automated build system for applying [Morphe's](https://github.com/MorpheApp/morphe-patches) patches to YouTube and YouTube Music.
The core mechanism of this builder is based on [monsivamon/twitter-apk](https://github.com/monsivamon/twitter-apk).

## ⚠️ Disclaimer
**App stability is NOT guaranteed.** This build system is configured to automatically parse the upstream patch list and apply only the **official recommended patches**. While this intelligent selection ensures a highly stable configuration, unexpected bugs or crashes may still occur depending on upstream updates. Use at your own risk.

## ⚠️ Requirements
To use the patched YouTube and YouTube Music apps and log in with your Google account, you **MUST** install MicroG (GmsCore). 
We highly recommend using **[MicroG-RE](https://github.com/MorpheApp/MicroG-RE)** provided by the Morphe team.

## ✨ Key Features & Improvements

### 1. Dual-Track Release System (Stable & Pre-release)
The builder independently monitors the upstream repository for both **Stable** and **Pre-release** channels. It automatically triggers builds for both, appropriately tagging them on GitHub Releases, so you can choose between maximum stability or bleeding-edge features.

### 2. Intelligent Patch Selection
Instead of blindly applying every patch, the system dynamically parses Morphe's `patches-list.json`. It strictly enforces only the officially recommended patches (`"use": true`) alongside mandatory core patches. This prevents experimental or conflicting patches from breaking the app.

### 3. Smart Base APK Versioning
Directly parses Morphe's Kotlin source code (`Constants.kt`) to extract the exact supported base APK versions.

### 4. Powered by Morphe CLI & Daily Automation
The build pipeline relies entirely on the modern **Morphe CLI** for fast, reliable patching, automatically running daily via GitHub Actions.

## 📥 Download

Get the latest pre-built APKs from the **[Releases Page](https://github.com/monsivamon/morpheapp-apk/releases)**.

## Credits

* [MorpheApp/morphe-patches](https://github.com/MorpheApp/morphe-patches) - The patch source.
* [MorpheApp/morphe-cli](https://github.com/MorpheApp/morphe-cli) - Morphe CLI patcher.