# Dark and Darker Tools

## 🎮 Demo

<p align="center">
  <video src="https://github.com/user-attachments/assets/100d069c-3b83-4177-a7ff-7760b1cd092d" controls autoplay loop muted width="100%"></video>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
  <a href="https://github.com/Beelzebub2/DnDTools/releases/"><img src="https://img.shields.io/github/v/release/Beelzebub2/DnDTools?include_prereleases&label=version" alt="Version"></a>
  <a href="https://github.com/Beelzebub2/DnDTools/actions/workflows/build-and-release.yml"><img src="https://github.com/Beelzebub2/DnDTools/actions/workflows/build-and-release.yml/badge.svg" alt="Build Status"></a>
</p>


A tool to capture and generate visual previews of Dark and Darker stash contents and organize them.

🌐 **Website:** [https://dndtools.rrmtools.uk](https://dndtools.rrmtools.uk)  
💬 **Discord:** [Join our community](https://discord.gg/X8FuqR2cq6)  
❓**Wiki** [FAQ](https://github.com/Beelzebub2/DnDTools/wiki)

## ⚠️ Disclaimer

This project is not affiliated with, endorsed by, or connected to IRONMACE Co., Ltd. or Dark and Darker in any way. This is a fan-made tool for educational purposes only.

All game content and materials are trademarks and copyrights of IRONMACE Co., Ltd. or its licensors. All rights reserved.

## 🚀 Features

- Captures Dark and Darker network data for stash and inventory
- Visualizes your characters, stashs and inventory in a clean layout
- Allows sorting of your stashes using inventory for temporary storage
- Includes a search box to quickly find items across all characters

## 📋 Requirements

- Python 3.7+ [Download](https://www.python.org/downloads/)
- For versions **2.1.4 and earlier**: Npcap [Download](https://npcap.com/#download)
- For versions **after 2.1.4**: Wireshark [Download](https://www.wireshark.org/download.html)

## 🔧 Installation

### Windows (recommended)

1. Download the latest `DnDTools-Setup-<version>.exe` from the [releases page](https://github.com/Beelzebub2/DnDTools/releases).
2. Run the installer and follow the prompts. The application will be available from the Start Menu (and optionally on the desktop).

### Development setup

1. Clone the repository:
```bash
git clone https://github.com/Beelzebub2/DnDTools.git
cd DnDTools
```
2. Navigate to the UI folder and install required dependencies:
```bash
pip install -r UI/requirements.txt
```

## 📖 Usage
1. Start Dark and Darker
2. Navigate to the UI folder and run the application::
```bash
cd UI
python app.py
```
3. Make sure packet capture is enabled.
4. From the character selection screen, select the character you want to capture.
5. Open your stash in-game. Your character’s stash and inventory will appear in the Characters tab.

## 📦 Building the Windows Installer

You can produce a signed-ready Windows installer using Inno Setup:

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) and ensure `ISCC.exe` is on your `PATH` or set the `ISCC_PATH` environment variable.
2. From the project root, run:

```cmd
installer\build_installer.bat
```

This command will rebuild the standalone executable with PyInstaller, synchronize the installer metadata with the application version, and emit `DnDTools-Setup-<version>.exe` in the `dist\` directory.

> Need to stamp a one-off version (for example, to match a GitHub release tag)? Set `DNDTOOLS_RELEASE_VERSION` before running the installer script:
>
> ```cmd
> set DNDTOOLS_RELEASE_VERSION=v3.6.1
> installer\build_installer.bat
> ```
>
> If the build should be treated as a dev/Test release, also set `DNDTOOLS_RELEASE_CHANNEL`:
>
> ```cmd
> set DNDTOOLS_RELEASE_VERSION=v3.6.1
> set DNDTOOLS_RELEASE_CHANNEL=dev
> installer\build_installer.bat
> python scripts\generate_update_manifest.py --release-tag v3.6.1 --version v3.6.1 --channel dev
> ```
>
> The GitHub Actions workflow now sets both variables automatically from the `workflow_dispatch` inputs (`tag` and `channel`), so the CI-built installer and manifest always match what you typed on the Actions form.

> The GitHub Actions release pipeline invokes the same script and attaches the generated installer to each tagged pre-release.

## ⚙️ Configuration
### Updating Protobuf Files After a Game Update

After a **Dark and Darker** update, you will need to run:
```
UI\networking\extract.bat
```
to grab the fresh `.proto` files from the game binary.

> **Important:**  
> Before running, update the path inside `extract.bat` if your game is installed somewhere other than the default.  
> The default path is:
> ```
> C:\Program Files\IRONMACE\Dark and Darker\DungeonCrawler\Binaries\Win64\DungeonCrawler.exe
> ```

We will try to keep the `.proto` files in the repository updated, but if they are outdated, you can use this script to generate the latest ones yourself.

### Opt into development/Test builds

If you want to help test unreleased builds tagged as `Test-*` on GitHub:

1. Open the app and go to **Settings → Updates**.
2. Enable **Opt into Test builds** and save.
3. The built-in updater (and auto-update checks) will now look for the newest `Test-` prerelease instead of the latest stable release.

You can switch back at any time by unchecking the option; update checks will immediately return to the stable channel.

## 🛡️ Legal

This project:
- Does not modify any game files
- Does not interact with the game process
- Only captures and analyzes network traffic
- Is provided "AS IS" without warranty of any kind

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 🙏 Acknowledgments
thanks to:
- **Kokkor** on Discord for their help with protobuf and packet capture.
- **Anders** on Discord for their help with this project and for allowing us to use custom models.
- [Darkerdb](https://darkerdb.com/) for the amazing api.

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 💬 Support

For support or questions, please [open an issue](https://github.com/Beelzebub2/darkanddarker-stash-preview/issues) on GitHub.

## 📝 TODO
```diff 
- Run in background popout when game opens
- Show crafting usage for items
```
---
*Remember to always comply with Dark and Darker's Terms of Service while using this tool.*

### Support us

[<img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" height="80" />](https://www.buymeacoffee.com/DnDTools)

