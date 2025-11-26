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

                // Custom drag region limited to the titlebar
                if (dragRegion) {
                    dragRegion.addEventListener('mousedown', (e) => {
                        try {
                            if (e.button !== 0) {
                                return;
                            }

                            const api = window.pywebview && window.pywebview.api;
                            if (!api || typeof api.begin_drag !== 'function') {
                                return;
                            }

                            const startNativeDrag = () => {
                                try {
                                    api.begin_drag();
                                } catch (callErr) {
                                    console.error('Failed to start native drag:', callErr);
                                }
                            };

                            const needsRestore = isMaximized && typeof api.toggle_maximize === 'function';

                            e.preventDefault();
                            if (needsRestore) {
                                Promise.resolve(api.toggle_maximize())
                                    .then(() => setTimeout(startNativeDrag, 16))
                                    .catch((err) => {
                                        console.error('Failed to restore window before drag:', err);
                                        startNativeDrag();
                                    });
                            } else {
                                startNativeDrag();
                            }
                        } catch (err) {
                            console.error('Drag handler failed:', err);
                        }
                    });
                }
            } catch (error) {
                console.error('Error initializing window controls:', error);
            }
        }, 50); // Reduced from 100ms for faster initialization
    });
})();
