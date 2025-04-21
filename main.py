import os
import logging
from capture import PacketCapture
from stash_preview import ItemDataManager, StashPreviewGenerator, parse_stashes, ItemInfo

class StashProcessor:
    def __init__(self, previews_dir: str = "previews"):
        self.previews_dir = previews_dir
        os.makedirs(self.previews_dir, exist_ok=True)
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def capture_packets(self):
        self.logger.info("Starting packet capture...")
        capture = PacketCapture()
        capture.capture()
        self.logger.info("Packet capture completed")

    def generate_previews(self):
        self.logger.info("Generating stash previews...")
        try:
            item_manager = ItemDataManager()
            packet_data = ItemDataManager.load_json("packet_data.json")
            
            stashes = parse_stashes(packet_data, item_manager.item_data)
            if not stashes:
                self.logger.warning("No stashes found in packet data")
                return

            generator = StashPreviewGenerator()
            
            for stash_id, items in stashes.items():
                self.logger.info(f"Processing stash {stash_id}")
                preview = generator.generate_preview(
                    stash_id, 
                    [ItemInfo(**item) for item in items]
                )
                outname = os.path.join(self.previews_dir, f"stash_preview_{stash_id}.png")
                preview.save(outname)
                self.logger.info(f"Saved preview to {outname}")

            generator.item_manager.save_matching_db()
            self.logger.info("Completed preview generation")
            
        except Exception as e:
            self.logger.error(f"Failed to generate previews: {e}")

def main():
    processor = StashProcessor()
    processor.capture_packets()
    processor.generate_previews()

if __name__ == "__main__":
    main()
