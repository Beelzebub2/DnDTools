# Dark and Darker Tools

## 🎮 Demo

<p align="center">
  <video src="https://github.com/user-attachments/assets/ce137f5c-c787-480c-bdac-3c7ebd408979" controls autoplay loop muted width="100%"></video>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/status-beta-orange.svg" alt="Status">
  <a href="https://github.com/Beelzebub2/DnDTools/releases/"><img src="https://img.shields.io/github/v/release/Beelzebub2/DnDTools?include_prereleases&label=version" alt="Version"></a>
  <a href="https://github.com/Beelzebub2/DnDTools/actions/workflows/build-and-release.yml"><img src="https://github.com/Beelzebub2/DnDTools/actions/workflows/build-and-release.yml/badge.svg" alt="Build Status"></a>
</p>


A tool to capture and generate visual previews of Dark and Darker stash contents and organize them.

🌐 **Website:** [https://dndtools.me](https://dndtools.me)  
💬 **Discord:** [Join our community](https://discord.gg/X8FuqR2cq6)

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
! Support for other resolutions
- Quest tracking
- More sorting options
- Show crafting usage for items
```
---
*Remember to always comply with Dark and Darker's Terms of Service while using this tool.*

### Support us

<a href="https://www.buymeacoffee.com/DnDTools"><img src="https://img.buymeacoffee.com/button-api/?text=Support DnDTools&emoji=&slug=DnDTools&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" /></a>
