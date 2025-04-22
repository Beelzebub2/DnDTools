// Node.js version of Python item.py
const { ItemDataManager } = require('./stashPreview');

class Item {
  constructor(name, rarity, position, width, height) {
    this.name = name;
    this.rarity = rarity;
    this.position = position;
    this.width = width;
    this.height = height;
  }

  static compare(a, b) {
    if (a.height !== b.height) return b.height - a.height;
    if (a.width !== b.width) return b.width - a.width;
    if (a.rarity !== b.rarity) return b.rarity - a.rarity;
    return a.name.localeCompare(b.name);
  }

  equals(other) {
    return (
      other &&
      this.name === other.name &&
      this.rarity === other.rarity &&
      this.position === other.position
    );
  }

  toString() {
    return `${this.rarity} ${this.name} ${this.position} ${this.width}X${this.height}`;
  }

  toDict() {
    return {
      name: this.name,
      rarity: this.rarity,
      position: this.position,
      width: this.width,
      height: this.height
    };
  }

  /**
   * Construct an Item from raw packet data using ItemDataManager for dimensions
   * @param {{ itemId: string, slotId?: number }} data
   * @returns {Promise<Item>}
   */
  static async fromDict(data) {
    const itemId = data.itemId || '';
    const designStr = 'DesignDataItem:Id_Item_';
    const parts = itemId.replace(designStr, '').split('_');
    const name = parts[0] || '';
    let rarity = 0;
    if (parts.length === 2) {
      rarity = parseInt(parts[1][0], 10);
    }
    const position = data.slotId ?? -1;

    const manager = new ItemDataManager();
    const imgInfo = await manager.getItemImagePath(name) || {};
    const width = imgInfo.width || 1;
    const height = imgInfo.height || 1;

    return new Item(name, rarity, position, width, height);
  }
}

module.exports = Item;