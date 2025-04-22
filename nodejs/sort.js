// Node.js version of Python sort.py: arrange items in stash using similar algorithm
const fs = require('fs-extra');
const path = require('path');
const { parseStashes, ItemDataManager } = require('./stashPreview');

class Stash {
  constructor(stashType, items) {
    this.stashType = Number(stashType);
    if (this.stashType >= 4 && this.stashType <= 30) {
      this.width = 12; this.height = 20;
    } else {
      this.width = 10; this.height = 5;
    }
    this.size = this.width * this.height;
    this.grid = Array(this.size).fill(null);
    this.items = items; // list of item objects
    this.pq = [...items]; // will sort in place
    this._loadGrid();
  }

  _loadGrid() {
    for (const item of this.items) {
      const pos = item.slotId;
      this.grid[pos] = item;
    }
  }

  findEmptySlot(item) {
    for (let y = this.height - item.height; y >= 0; y--) {
      for (let x = this.width - item.width; x >= 0; x--) {
        let ok = true;
        for (let dy = 0; dy < item.height; dy++) {
          for (let dx = 0; dx < item.width; dx++) {
            const idx = (y + dy) * this.width + (x + dx);
            if (this.grid[idx] && this.grid[idx] !== item) { ok = false; break; }
          }
          if (!ok) break;
        }
        if (ok) return y * this.width + x;
      }
    }
    return null;
  }

  move(item, newPos) {
    console.log(`Moving ${item.name} from ${item.slotId} to ${newPos}`);
    // clear old
    for (let dy = 0; dy < item.height; dy++) {
      for (let dx = 0; dx < item.width; dx++) {
        this.grid[item.slotId + dy * this.width + dx] = null;
      }
    }
    // place new
    for (let dy = 0; dy < item.height; dy++) {
      for (let dx = 0; dx < item.width; dx++) {
        this.grid[newPos + dy * this.width + dx] = item;
      }
    }
    item.slotId = newPos;
  }
}

function sortStash(stash) {
  // sort by height desc, width desc, rarity desc, name asc
  stash.pq.sort((a, b) => {
    if (a.height !== b.height) return b.height - a.height;
    if (a.width !== b.width) return b.width - a.width;
    if (a.rarity !== b.rarity) return b.rarity - a.rarity;
    return a.name.localeCompare(b.name);
  });

  let curRow = 0, curCol = 0, curHeight = 0;

  while (stash.pq.length) {
    const item = stash.pq.shift();
    if (curHeight === 0) curHeight = item.height;

    if (curCol + item.width > stash.width) {
      curRow += curHeight;
      curHeight = item.height;
      curCol = 0;
    }
    if (curRow + item.height > stash.height) {
      console.log('Out of space');
      return;
    }

    // clear occupiers
    for (let dx = 0; dx < item.width; dx++) {
      for (let dy = 0; dy < item.height; dy++) {
        const idx = (curRow + dy) * stash.width + curCol + dx;
        const occ = stash.grid[idx];
        if (occ && occ !== item) {
          const newPos = stash.findEmptySlot(occ);
          if (newPos != null) stash.move(occ, newPos);
          else console.log('Cannot find temp location for', occ.name);
        }
      }
    }

    stash.move(item, curRow * stash.width + curCol);
    curCol += item.width;
  }
}

async function main() {
  const baseDir = path.resolve(__dirname, '..');
  const packetPath = path.join(baseDir, 'packet_data.json');

  if (!fs.existsSync(packetPath)) {
    console.error(`${packetPath} not found. Run capture first.`);
    return;
  }

  const packetData = await fs.readJson(packetPath);
  const itemData = new ItemDataManager();
  const rawStashes = parseStashes(packetData, itemData.itemData);

  for (const [stashId, itemsRaw] of Object.entries(rawStashes)) {
    // augment items with width, height, rarity
    const items = [];
    for (const raw of itemsRaw) {
      const imgInfo = await itemData.getItemImagePath(raw.name);
      items.push({
        name: raw.name,
        slotId: raw.slotId,
        width: imgInfo?.width || 1,
        height: imgInfo?.height || 1,
        rarity: 0
      });
    }

    const stash = new Stash(stashId, items);
    console.log(`Stash ${stashId} initial state:`);
    console.log(stash.grid.map(i => i?.name || '.'));
    sortStash(stash);
    console.log(`Stash ${stashId} final grid:`);
    console.log(stash.grid.map(i => i?.name || '.'));
    process.exit(0);
  }
}

main().catch(console.error);
