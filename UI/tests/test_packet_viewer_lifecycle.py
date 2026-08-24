from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
PACKET_VIEWER_SCRIPT = (
    Path(__file__).resolve().parents[1] / "static" / "js" / "packet_viewer.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_corrupt_expanded_packet_storage_cannot_brick_route_initialization():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

for (const storedValue of ['{broken-json', '{"not":"an-array"}']) {
    let removed = 0;
    let domReadyHandler = null;
    const storage = {
        getItem(key) {
            if (key !== 'packetViewerExpanded') throw new Error('unexpected key');
            return storedValue;
        },
        removeItem(key) {
            if (key !== 'packetViewerExpanded') throw new Error('unexpected key');
            removed += 1;
        },
        setItem() {}
    };
    const windowObject = {};
    const documentObject = {
        readyState: 'loading',
        addEventListener(type, handler) {
            if (type === 'DOMContentLoaded') domReadyHandler = handler;
        }
    };
    const context = vm.createContext({
        window: windowObject,
        document: documentObject,
        localStorage: storage,
        console,
        Set,
        Map,
        JSON,
        Number,
        Array
    });

    new vm.Script(source, { filename: 'packet_viewer.js' }).runInContext(context);
    if (removed !== 1) {
        throw new Error(`invalid storage was not cleared: ${storedValue}`);
    }
    if (typeof domReadyHandler !== 'function') {
        throw new Error('packet viewer initialization was not registered');
    }
}
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(PACKET_VIEWER_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
