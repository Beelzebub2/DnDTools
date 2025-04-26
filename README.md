# Dark and Darker Tools
<p align="center">
  <img src="UI/assets/banner.jpg" alt="DnDTools Banner" width="640" height="320">
</p>

## 🎮 Demo

<p align="center">
  <video src="https://github.com/user-attachments/assets/84e1ba79-fa67-420b-bb62-493fd7ed21fd" controls autoplay loop muted width="100%"></video>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/status-beta-orange.svg" alt="Status">
</p>


A tool to capture and generate visual previews of Dark and Darker stash contents and organize them.

## ⚠️ Disclaimer

This project is not affiliated with, endorsed by, or connected to IRONMACE Co., Ltd. or Dark and Darker in any way. This is a fan-made tool for educational purposes only.

All game content and materials are trademarks and copyrights of IRONMACE Co., Ltd. or its licensors. All rights reserved.

## 🚀 Features

- Real-time packet capture of stash data
- Visual stash preview generation
- Item name matching and caching
- Grid-based inventory visualization

## 📋 Requirements

- Python 3.7+
- PIL/Pillow
- pyshark
- protobuf
- **[Wireshark](https://www.wireshark.org/download.html) must be installed on your system** (required for packet capture)

## 🔧 Installation

1. Install required dependencies:
```bash
pip install -r requirements.txt
```
2. Configure your network interface in config.json (optional)

## 📖 Usage

1. Start Dark and Darker
2. Run the capture script:
```bash
python main.py
```
3. Open your stash in-game
4. Preview images will be generated in the `previews` folder

## ⚙️ Configuration

- `GRID_WIDTH`: Stash width (default: 12)
- `GRID_HEIGHT`: Stash height (default: 20)
- `CELL_SIZE`: Size of each grid cell in pixels (default: 45)

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
- [Darkerdb](https://darkerdb.com/) for the amazing api

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 💬 Support

For support or questions, please [open an issue](https://github.com/Beelzebub2/darkanddarker-stash-preview/issues) on GitHub.

---
*Remember to always comply with Dark and Darker's Terms of Service while using this tool.*
