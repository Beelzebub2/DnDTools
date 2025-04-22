// Node.js version of Python macros.py using robotjs
const robot = require('robotjs');

// all positions are for 1920x1080 resolution
// distance between stash cells
const jump = 40;

/**
 * Move cursor from a stash slot to another and simulate drag-and-drop
 * @param {number} startPos - starting slot index
 * @param {number} endPos - ending slot index
 * @param {{ width: number, baseScreenPos: { x: number, y: number } }} stash
 */
function moveFromTo(startPos, endPos, stash) {
  const startRow = Math.floor(startPos / stash.width);
  const startCol = startPos % stash.width;
  const endRow = Math.floor(endPos / stash.width);
  const endCol = endPos % stash.width;

  const startX = stash.baseScreenPos.x + jump * startCol;
  const startY = stash.baseScreenPos.y + jump * startRow;
  const endX = stash.baseScreenPos.x + jump * endCol;
  const endY = stash.baseScreenPos.y + jump * endRow;

  // Move and click-drag
  robot.setMouseDelay(100);
  robot.moveMouse(startX, startY);
  robot.mouseToggle('down');

  robot.setMouseDelay(200);
  robot.moveMouse(endX, endY);
  robot.mouseToggle('up');
}

module.exports = { jump, moveFromTo };