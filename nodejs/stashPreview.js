const sharp = require('sharp');
const fs = require('fs-extra');
const path = require('path');

class ItemInfo {
    constructor({ name, slotId, itemId, itemCount }) {
        this.name = name;
        this.slotId = slotId;
        this.itemId = itemId;
        this.itemCount = itemCount || 1;
    }
}

class ItemDataManager {
    constructor() {
        // Resolve project root one level above this script
        const scriptDir = path.dirname(__filename);
        this.baseDir = path.resolve(scriptDir, "..");
        this.assetsDir = path.join(this.baseDir, "assets");
        this.ITEM_DATA_FILE = path.join(this.assetsDir, "item-data.json");
        this.MATCHING_DB_FILE = path.join(this.assetsDir, "matchingdb.json");
        fs.ensureDirSync(this.assetsDir);

        this.itemData = this.loadJson(this.ITEM_DATA_FILE);
        this.matchingDb = this.loadMatchingDb();
        this.imageCache = new Map();
        this.unmatchedItems = new Set();
        this.itemStats = {
            matched: new Set(),
            unmatched: new Set(),
            modified: new Set()
        };
    }

    loadJson(filename) {
        return fs.readJsonSync(filename, { encoding: 'utf-8' });
    }

    loadMatchingDb() {
        try {
            return this.loadJson(this.MATCHING_DB_FILE);
        } catch {
            return {};
        }
    }

    saveMatchingDb() {
        fs.writeJsonSync(this.MATCHING_DB_FILE, this.matchingDb, { spaces: 2 });
    }

    normalizeName(name) {
        if (!name) return "";
        name = name.replace("DesignDataItem:Id_Item_", "");
        name = name.replace(/[\s'-]/g, "");
        return name.toLowerCase();
    }

    async getItemImagePath(itemName) {
        // Check matching DB first
        if (itemName in this.matchingDb) {
            const matchedName = this.matchingDb[itemName];
            const data = Object.values(this.itemData).find(d => d.name === matchedName);
            if (data) {
                this.itemStats.matched.add(itemName);
                const rel = data.path.replace(/\\/g, path.sep);
                const full = path.join(this.baseDir, rel);
                return { path: full, width: data.inventory_width, height: data.inventory_height, name: data.name };
            }
        }

        // Try direct match
        const normName = this.normalizeName(itemName);
        const data = Object.values(this.itemData).find(d => this.normalizeName(d.name) === normName);

        if (data) {
            if (!(itemName in this.matchingDb)) {
                this.itemStats.modified.add(itemName);
            }
            const rel = data.path.replace(/\\/g, path.sep);
            const full = path.join(this.baseDir, rel);
            return { path: full, width: data.inventory_width, height: data.inventory_height, name: data.name };
        }

        // Track unmatched items
        this.itemStats.unmatched.add(itemName);
        if (!(itemName in this.matchingDb)) {
            this.unmatchedItems.add(itemName);
        }
        return null;
    }

    saveUnmatchedItems() {
        if (this.unmatchedItems.size > 0) {
            const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
            const filename = path.join(path.dirname(this.MATCHING_DB_FILE), 
                `unmatched_items_${timestamp}.txt`);
            
            const content = Array.from(this.unmatchedItems).sort().join('\n');
            fs.writeFileSync(filename, content, 'utf-8');
            
            console.log(`\nItem Processing Statistics:`);
            console.log(`- Matched Items: ${this.itemStats.matched.size}`);
            console.log(`- Modified Items: ${this.itemStats.modified.size}`);
            console.log(`- Unmatched Items: ${this.itemStats.unmatched.size}`);
            console.log(`\nSaved ${this.unmatchedItems.size} unmatched items to ${filename}`);
        }
    }
}

class StashPreviewGenerator {
    constructor(gridWidth = 12, gridHeight = 20, cellSize = 45) {
        this.GRID_WIDTH = gridWidth;
        this.GRID_HEIGHT = gridHeight;
        this.CELL_SIZE = cellSize;
        this.itemManager = new ItemDataManager();
    }

    async generatePreview(stashId, items) {
        const width = this.GRID_WIDTH * this.CELL_SIZE;
        const height = this.GRID_HEIGHT * this.CELL_SIZE;

        console.log(`\nProcessing ${items.length} items in stash ${stashId}...`);

        // Initialize Sharp image with background
        const image = sharp({ create: { width, height, channels: 4, background: { r:24, g:20, b:16, alpha:255 } } });

        // Collect overlays: grid and items
        const composites = [];
        // Grid overlay
        const gridSVG = this._createGridSVG(width, height, { r:60, g:50, b:30, alpha:180 }, { r:212, g:175, b:55, alpha:255 });
        composites.push({ input: Buffer.from(gridSVG), top: 0, left: 0 });

        for (const item of items) {
            const itemData = await this.itemManager.getItemImagePath(item.name);
            if (!itemData) {
                console.warn(`  ❌ No data entry for item: ${item.name}`);
                continue;
            }
            const imgPath = itemData.path;
            if (!fs.existsSync(imgPath)) {
                console.warn(`  ❌ File not found for item '${item.name}': ${imgPath}`);
                continue;
            }
            try {
                const w = (itemData.width || 1) * this.CELL_SIZE;
                const h = (itemData.height || 1) * this.CELL_SIZE;
                const buf = await sharp(imgPath).resize(w, h).toBuffer();
                const x = item.slotId % this.GRID_WIDTH;
                const y = Math.floor(item.slotId / this.GRID_WIDTH);
                console.log(`  ✓ Queue '${itemData.name}' at (${x},${y})`);
                this.itemManager.matchingDb[item.name] = itemData.name;
                composites.push({ input: buf, top: y * this.CELL_SIZE, left: x * this.CELL_SIZE });
            } catch (e) {
                console.error(`  ❌ Failed to process ${imgPath}:`, e);
            }
        }

        // Composite grid and items on background
        return image.composite(composites);
    }

    _createGridSVG(width, height, gridColor, borderColor) {
        const lines = [];
        
        // Vertical lines
        for (let x = 1; x < this.GRID_WIDTH; x++) {
            const xPos = x * this.CELL_SIZE;
            lines.push(`<line x1="${xPos}" y1="0" x2="${xPos}" y2="${height}" 
                stroke="rgba(${gridColor.r},${gridColor.g},${gridColor.b},${gridColor.alpha/255})" 
                stroke-width="1" shape-rendering="crispEdges" />`);
        }
        
        // Horizontal lines
        for (let y = 1; y < this.GRID_HEIGHT; y++) {
            const yPos = y * this.CELL_SIZE;
            lines.push(`<line x1="0" y1="${yPos}" x2="${width}" y2="${yPos}" 
                stroke="rgba(${gridColor.r},${gridColor.g},${gridColor.b},${gridColor.alpha/255})" 
                stroke-width="1" shape-rendering="crispEdges" />`);
        }

        // Border
        const border = `<rect x="0" y="0" width="${width}" height="${height}" fill="none" 
            stroke="rgba(${borderColor.r},${borderColor.g},${borderColor.b},${borderColor.alpha/255})" 
            stroke-width="4" shape-rendering="crispEdges" />`;

        return `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg" 
                shape-rendering="crispEdges">
            ${lines.join('\n')}
            ${border}
        </svg>`;
    }
}

function getItemNameFromId(itemId) {
    if (!itemId.startsWith("DesignDataItem:Id_Item_")) {
        return null;
    }
    let base = itemId.substring("DesignDataItem:Id_Item_".length);
    base = base.replace(/_\d+$/, '');
    base = base.replace(/_/g, ' ');
    return base;
}

function parseStashes(packetData, itemData) {
    const stashes = {};
    const storageInfos = packetData?.characterDataBase?.CharacterStorageInfos || [];

    for (const storage of storageInfos) {
        const inventoryId = storage.inventoryId;
        const items = storage.CharacterStorageItemList || [];
        const stashItems = [];
        const usedSlots = new Set();

        // Process items with defined slots
        for (const item of items) {
            if ('slotId' in item) {
                const itemId = item.itemId || '';
                const slotId = item.slotId;
                const name = getItemNameFromId(itemId);
                stashItems.push({
                    name,
                    slotId,
                    itemId,
                    itemCount: item.itemCount || 1
                });
                usedSlots.add(slotId);
            }
        }

        // Process items without slots
        for (const item of items) {
            if (!('slotId' in item)) {
                const itemId = item.itemId || '';
                let slotId = 0;
                while (usedSlots.has(slotId)) {
                    slotId++;
                }
                const name = getItemNameFromId(itemId);
                stashItems.push({
                    name,
                    slotId,
                    itemId,
                    itemCount: item.itemCount || 1
                });
                usedSlots.add(slotId);
            }
        }

        if (stashItems.length > 0) {
            stashes[inventoryId] = stashItems;
        }
    }
    return stashes;
}

async function main() {
    // Use project root as base directory
    const scriptDir = path.dirname(__filename);
    const baseDir = path.resolve(scriptDir, "..");
    const packetDataPath = path.join(baseDir, "packet_data.json");
    const outputDir = path.normalize(path.join(baseDir, "output"));

    try {
        // Ensure output directory exists
        await fs.ensureDir(outputDir);
        
        // Test write permissions by creating a test file
        const testFile = path.join(outputDir, '.test');
        await fs.writeFile(testFile, '');
        await fs.remove(testFile);

        const itemDataManager = new ItemDataManager();
        
        if (!fs.existsSync(packetDataPath)) {
            console.error(`Error: ${packetDataPath} not found. Please run packet capture first.`);
            return;
        }

        const packetData = await fs.readJson(packetDataPath);
        const stashes = parseStashes(packetData, itemDataManager.itemData);

        if (Object.keys(stashes).length === 0) {
            console.log("No stashes found in packet data.");
            return;
        }

        console.log("\nStarting stash preview generation...");
        console.log(`Output directory: ${outputDir}`);
        
        const generator = new StashPreviewGenerator();

        for (const [stashId, items] of Object.entries(stashes)) {
            const preview = await generator.generatePreview(
                stashId, 
                items.map(item => new ItemInfo(item))
            );
            
            const outname = path.join(outputDir, `stash_preview_${stashId}.png`);
            await preview.toFile(outname);
            console.log(`✓ Preview saved as ${outname}`);
        }

        generator.itemManager.saveUnmatchedItems();
        generator.itemManager.saveMatchingDb();
        console.log("\n✓ Matching DB saved as matchingdb.json");

    } catch (e) {
        console.error("\n❌ Error generating previews:", e);
        if (e.code === 'EACCES') {
            console.error(`Permission denied accessing output directory: ${outputDir}`);
        }
    }
}

if (require.main === module) {
    main().catch(console.error);
}

module.exports = {
    StashPreviewGenerator,
    ItemInfo,
    ItemDataManager,
    parseStashes,
    getItemNameFromId
};