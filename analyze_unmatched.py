import os
import glob
from collections import Counter
from datetime import datetime

def analyze_unmatched_items():
    # Find all unmatched items files
    files = glob.glob("assets/unmatched_items_*.txt")
    if not files:
        print("No unmatched items files found")
        return

    # Get most recent file
    latest_file = max(files, key=os.path.getctime)
    
    # Read and analyze items
    with open(latest_file, "r", encoding="utf-8") as f:
        items = [line.strip() for line in f if line.strip()]

    if not items:
        print("No unmatched items found")
        return

    # Count occurrences
    counts = Counter(items)
    
    # Generate report
    print(f"\nUnmatched Items Analysis")
    print(f"Total unique items: {len(counts)}")
    print("\nMost common unmatched items:")
    for item, count in counts.most_common(10):
        print(f"{count:3d}x {item}")

if __name__ == "__main__":
    analyze_unmatched_items()
