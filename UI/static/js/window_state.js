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

                if (closeBtn) {
                    closeBtn.onclick = () => {
                        try {
                            if (typeof window.handleWindowClose === 'function') {
                                window.handleWindowClose();
                            } else if (window.pywebview && window.pywebview.api && window.pywebview.api.close_window) {
                                window.pywebview.api.close_window();
                            }
                        } catch (error) {
                            console.error('Error closing window:', error);
                        }
                    };
                }

                // Drag-to-restore logic using native drag for Windows snap support
                if (dragRegion) {
                    dragRegion.addEventListener('mousedown', (e) => {
                        try {
                            if (e.button !== 0) return;

                            const api = window.pywebview && window.pywebview.api;
                            if (!api || !api.begin_drag) return;

                            const startNativeDrag = () => {
                                try {
                                    // Fire and forget; the bridge call is async but we don't need to await it
                                    api.begin_drag();
                                } catch (callErr) {
                                    console.error('Failed to start native drag:', callErr);
                                }
                            };

                            const needsRestore = isMaximized && api.toggle_maximize;

                            if (needsRestore) {
                                e.preventDefault();
                                Promise.resolve(api.toggle_maximize())
                                    .then(() => setTimeout(startNativeDrag, 20))
                                    .catch((toggleErr) => console.error('Failed to restore window before drag:', toggleErr));
                            } else {
                                e.preventDefault();
                                startNativeDrag();
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
