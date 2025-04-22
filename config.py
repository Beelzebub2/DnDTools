import os

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# Asset paths
ITEM_DATA_PATH = os.path.join(ASSETS_DIR, "item_data")
MATCHING_DB_PATH = os.path.join(ASSETS_DIR, "matchingdb")
IMAGES_PATH = os.path.join(ASSETS_DIR, "images")

def ensure_asset_dirs():
    """Ensure all asset directories exist"""
    for path in [ASSETS_DIR, ITEM_DATA_PATH, MATCHING_DB_PATH, IMAGES_PATH]:
        os.makedirs(path, exist_ok=True)
