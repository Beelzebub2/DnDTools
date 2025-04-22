# Dark and Darker Stash Organizer

A modern PyWebView application for organizing and viewing Dark and Darker character stashes. This application provides a clean, dark-themed interface to view and search through your characters' inventories.

## Features

- View all characters with their class, level, and last update time
- Visual stash grid display for each character
- Search items across all characters' stashes
- Dark theme matching Dark and Darker's aesthetic
- Responsive design for different window sizes

## Setup

1. Install Python 3.8 or higher
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Make sure you have run the packet capture tool first to collect character data
4. Launch the application:
   ```bash
   python app.py
   ```

## Usage

- **Characters View**: The main page shows all your characters with their basic information
- **Character Details**: Click on a character to view their stash in a grid layout
- **Search**: Use the search page to find specific items across all character stashes

## Extending the Application

The application is designed to be easily extendable:

- Add new features by creating new routes in `app.py`
- Create new templates in the `templates` folder
- Add new styles in `static/css/style.css`
- Implement new functionality in `static/js/app.js`

## Data Structure

The application expects character data in JSON format from the packet capture tool with the following structure:
- Character base information including nickname, class, and level
- Stash data with item information and slot positions

## Notes

- The application reads data from the capture tool's output directory
- Stash views are automatically updated when new character data is captured
- The search function works across all available character data