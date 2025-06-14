(function () {
    let isMaximized = false;

    window.addEventListener('windowStateChanged', e => {
        isMaximized = !!(e.detail && e.detail.maximized);
    });

    document.addEventListener('DOMContentLoaded', () => {
        // Add a small delay to ensure all elements are properly initialized
        setTimeout(() => {
            const minimizeBtn = document.querySelector('.titlebar-button.minimize');
            const maximizeBtn = document.querySelector('.titlebar-button.maximize');
            const closeBtn = document.querySelector('.titlebar-button.close');
            const dragRegion = document.querySelector('.titlebar-title');

            if (minimizeBtn && window.pywebview && window.pywebview.api && window.pywebview.api.minimize) {
                minimizeBtn.onclick = () => {
                    window.pywebview.api.minimize();
                };
            }
            if (maximizeBtn && window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize) {
                maximizeBtn.onclick = () => window.pywebview.api.toggle_maximize();
            }
            if (closeBtn && window.pywebview && window.pywebview.api && window.pywebview.api.close_window) {
                closeBtn.onclick = () => window.pywebview.api.close_window();
            }

            // Drag-to-restore logic with synthetic drag
            if (dragRegion) {
                dragRegion.addEventListener('mousedown', async (e) => {
                    if (e.button !== 0) return;
                    if (
                        isMaximized &&
                        window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize
                    ) {
                        e.preventDefault();
                        await window.pywebview.api.toggle_maximize();
                        setTimeout(() => {
                            const evt = new MouseEvent('mousedown', {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                                button: 0,
                                clientX: e.clientX,
                                clientY: e.clientY
                            });
                            dragRegion.dispatchEvent(evt);
                        }, 50);
                    }
                });
            }
        }, 100);
    });
})();
