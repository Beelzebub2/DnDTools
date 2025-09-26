(function () {
    let isMaximized = false;

    window.addEventListener('windowStateChanged', e => {
        try {
            isMaximized = !!(e.detail && e.detail.maximized);
        } catch (error) {
            console.error('Error handling window state change:', error);
        }
    });

    document.addEventListener('DOMContentLoaded', () => {
        // Add a small delay to ensure all elements are properly initialized
        setTimeout(() => {
            try {
                const minimizeBtn = document.querySelector('.titlebar-button.minimize');
                const maximizeBtn = document.querySelector('.titlebar-button.maximize');
                const closeBtn = document.querySelector('.titlebar-button.close');
                const dragRegion = document.querySelector('.titlebar-title');

                if (minimizeBtn && window.pywebview && window.pywebview.api && window.pywebview.api.minimize) {
                    minimizeBtn.onclick = () => {
                        try {
                            window.pywebview.api.minimize();
                        } catch (error) {
                            console.error('Error minimizing window:', error);
                        }
                    };
                }

                if (maximizeBtn && window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize) {
                    maximizeBtn.onclick = () => {
                        try {
                            window.pywebview.api.toggle_maximize();
                        } catch (error) {
                            console.error('Error toggling maximize:', error);
                        }
                    };
                }

                if (closeBtn && window.pywebview && window.pywebview.api && window.pywebview.api.close_window) {
                    closeBtn.onclick = () => {
                        try {
                            window.pywebview.api.close_window();
                        } catch (error) {
                            console.error('Error closing window:', error);
                        }
                    };
                }

                // Drag-to-restore logic with synthetic drag
                if (dragRegion) {
                    dragRegion.addEventListener('mousedown', async (e) => {
                        try {
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
                        } catch (error) {
                            console.error('Error handling drag region:', error);
                        }
                    });
                }
            } catch (error) {
                console.error('Error initializing window controls:', error);
            }
        }, 50); // Reduced from 100ms for faster initialization
    });
})();
