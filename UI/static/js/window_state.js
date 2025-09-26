(function () {
    let isMaximized = false;

    window.addEventListener('windowStateChanged', e => {
        try {
            isMaximized = !!(e.detail && e.detail.maximized);
            // Remove minimizing class when window state changes (restore, maximize, etc.)
            document.body.classList.remove('minimizing');
        } catch (error) {
            console.error('Error handling window state change:', error);
        }
    });

    // Also listen for focus events to ensure animation class is removed when window is restored
    window.addEventListener('focus', () => {
        document.body.classList.remove('minimizing');
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
                            // Add minimizing animation class to body
                            document.body.classList.add('minimizing');

                            // Wait for animation to complete before actually minimizing
                            setTimeout(() => {
                                window.pywebview.api.minimize();
                                // Remove animation class after minimize (in case window is restored)
                                setTimeout(() => {
                                    document.body.classList.remove('minimizing');
                                }, 100);
                            }, 300); // Match animation duration
                        } catch (error) {
                            console.error('Error minimizing window:', error);
                            // Remove animation class on error
                            document.body.classList.remove('minimizing');
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
