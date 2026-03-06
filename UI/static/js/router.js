// AJAX Content-Swapping Router
// Intercepts sidebar navigation clicks and swaps only the <main class="content">
// area instead of doing a full page reload. The sidebar persists across navigations.
(function () {
    'use strict';

    // Known base CSS that should never be removed during page swaps
    var BASE_CSS_MARKERS = [
        '/css/style.css',
        '/css/sidebar-indicator.css',
        '/css/utils.css',
        'fonts.googleapis.com'
    ];

    // Known global scripts that should not be swapped
    var GLOBAL_SCRIPT_MARKERS = [
        '/js/app.js',
        '/js/utils.js',
        '/js/global.js',
        '/js/router.js'
    ];

    // Route-to-script mapping (more reliable than parsing fetched HTML)
    var PAGE_SCRIPTS = {
        '/': '/static/js/index.js',
        '/record': '/static/js/record.js',
        '/search': '/static/js/search.js',
        '/quests': '/static/js/quest.js',
        '/settings': '/static/js/settings.js',
        '/faq': null,
        '/feedback': null,
        '/packet_viewer': '/static/js/packet_viewer.js'
    };

    // Routes eligible for AJAX navigation
    var AJAX_ROUTES = Object.keys(PAGE_SCRIPTS);

    var isNavigating = false;

    // Ensure cleanup array exists
    window.__pageCleanup = window.__pageCleanup || [];

    // ── Helpers ──

    function isBaseCSS(href) {
        if (!href) return true;
        for (var i = 0; i < BASE_CSS_MARKERS.length; i++) {
            if (href.indexOf(BASE_CSS_MARKERS[i]) !== -1) return true;
        }
        return false;
    }

    function isGlobalScript(src) {
        if (!src) return false;
        for (var i = 0; i < GLOBAL_SCRIPT_MARKERS.length; i++) {
            if (src.indexOf(GLOBAL_SCRIPT_MARKERS[i]) !== -1) return true;
        }
        return false;
    }

    function getPathname(href) {
        try {
            return new URL(href, window.location.origin).pathname;
        } catch (e) {
            return href;
        }
    }

    function isAjaxEligible(pathname) {
        return AJAX_ROUTES.indexOf(pathname) !== -1;
    }

    // ── Page Cleanup ──

    function runPageCleanup() {
        var cleanups = window.__pageCleanup || [];
        window.__pageCleanup = [];

        for (var i = 0; i < cleanups.length; i++) {
            try {
                cleanups[i]();
            } catch (err) {
                console.warn('[Router] Cleanup error:', err);
            }
        }

        // Reset cross-page state
        window.unsavedChangesGuard = null;
        window.hasUnsavedChanges = false;
    }

    // ── CSS Swap ──

    function findPageCSSElements() {
        var links = document.head.querySelectorAll('link[rel="stylesheet"], style[data-page-css]');
        var pageCSS = [];
        for (var i = 0; i < links.length; i++) {
            var el = links[i];
            if (el.hasAttribute('data-page-css')) {
                pageCSS.push(el);
            } else if (el.tagName === 'LINK' && !isBaseCSS(el.getAttribute('href'))) {
                pageCSS.push(el);
            }
        }
        return pageCSS;
    }

    function extractNewPageCSS(doc) {
        var headEls = doc.head.querySelectorAll('link[rel="stylesheet"], style');
        var newCSS = [];
        for (var i = 0; i < headEls.length; i++) {
            var el = headEls[i];
            if (el.tagName === 'STYLE') {
                newCSS.push({ type: 'style', text: el.textContent });
            } else if (el.tagName === 'LINK' && !isBaseCSS(el.getAttribute('href'))) {
                newCSS.push({ type: 'link', href: el.getAttribute('href') });
            }
        }
        return newCSS;
    }

    function loadNewCSS(newCSSDescriptors) {
        var promises = [];
        var elements = [];

        for (var i = 0; i < newCSSDescriptors.length; i++) {
            var desc = newCSSDescriptors[i];
            if (desc.type === 'style') {
                var style = document.createElement('style');
                style.setAttribute('data-page-css', '');
                style.textContent = desc.text;
                document.head.appendChild(style);
                elements.push(style);
                // Inline styles load instantly
            } else if (desc.type === 'link') {
                var link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = desc.href;
                link.setAttribute('data-page-css', '');
                promises.push(new Promise(function (resolve) {
                    link.onload = resolve;
                    link.onerror = resolve; // Don't block on CSS errors
                }));
                document.head.appendChild(link);
                elements.push(link);
            }
        }

        // Wait for all CSS to load, with a 3s timeout as safety net
        if (promises.length === 0) return Promise.resolve(elements);

        return Promise.race([
            Promise.all(promises),
            new Promise(function (resolve) { setTimeout(resolve, 3000); })
        ]).then(function () {
            return elements;
        });
    }

    function removeOldCSS(oldElements) {
        for (var i = 0; i < oldElements.length; i++) {
            if (oldElements[i].parentNode) {
                oldElements[i].parentNode.removeChild(oldElements[i]);
            }
        }
    }

    // ── Script Swap ──

    function removeOldPageScript() {
        var old = document.querySelector('script[data-page-script]');
        if (old && old.parentNode) {
            old.parentNode.removeChild(old);
        }
    }

    function loadPageScript(pathname) {
        var src = PAGE_SCRIPTS[pathname];
        if (!src) return Promise.resolve();

        return new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = src + '?_t=' + Date.now(); // Cache-bust to re-execute
            script.setAttribute('data-page-script', '');
            script.onload = resolve;
            script.onerror = function () {
                console.error('[Router] Failed to load page script:', src);
                reject(new Error('Script load failed: ' + src));
            };
            document.body.appendChild(script);
        });
    }

    // Activate <script> tags inside content innerHTML (for faq/feedback etc.)
    function activateInlineScripts(container) {
        var scripts = container.querySelectorAll('script');
        for (var i = 0; i < scripts.length; i++) {
            var original = scripts[i];
            var replacement = document.createElement('script');
            if (original.src) {
                replacement.src = original.src;
            } else {
                replacement.textContent = original.textContent;
            }
            // Copy attributes except src (already handled)
            for (var j = 0; j < original.attributes.length; j++) {
                var attr = original.attributes[j];
                if (attr.name !== 'src') {
                    replacement.setAttribute(attr.name, attr.value);
                }
            }
            original.parentNode.replaceChild(replacement, original);
        }
    }

    // ── Active Nav Link ──

    function updateActiveNavLink(pathname) {
        var links = document.querySelectorAll('.nav-link[data-page]');
        for (var i = 0; i < links.length; i++) {
            var link = links[i];
            var page = link.getAttribute('data-page');
            var linkHref = link.getAttribute('href');
            var isActive = false;

            if (pathname === '/' && page === 'index') {
                isActive = true;
            } else if (pathname !== '/' && page !== 'index' && pathname.indexOf('/' + page) !== -1) {
                isActive = true;
            } else if (linkHref === pathname) {
                isActive = true;
            }

            if (isActive) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        }
    }

    // ── Scroll Reset ──

    function resetContentScroll() {
        var content = document.querySelector('.content');
        if (content) content.scrollTop = 0;
    }

    // ── Core Navigation ──

    async function navigateAjax(href, options) {
        options = options || {};
        var pushState = options.pushState !== false;

        if (isNavigating) return;

        var pathname = getPathname(href);

        // Skip if already on this page (same path, ignoring query params for base check)
        if (pathname === window.location.pathname && !href.includes('?')) return;

        // Check unsaved changes guard
        var guard = window.unsavedChangesGuard;
        if (guard && typeof guard.shouldPrompt === 'function' && guard.shouldPrompt()) {
            if (typeof guard.requestNavigation === 'function') {
                guard.requestNavigation(href);
                return;
            }
        }

        // If route isn't eligible for AJAX, fall back to full reload
        if (!isAjaxEligible(pathname)) {
            window.location.href = href;
            return;
        }

        isNavigating = true;

        var contentEl = document.querySelector('.content');
        if (!contentEl) {
            window.location.href = href;
            isNavigating = false;
            return;
        }

        try {
            // 1. Run cleanup for current page
            runPageCleanup();

            // 2. Mark content as router-managed (disables CSS animation, uses transition)
            contentEl.classList.add('router-managed');

            // 3. Fade out
            contentEl.style.opacity = '0';
            contentEl.style.transform = 'translateY(5px)';

            await new Promise(function (resolve) { setTimeout(resolve, 150); });

            // 4. Fetch new page
            var response = await fetch(href, { cache: 'no-store' });
            if (!response.ok) throw new Error('Fetch failed: ' + response.status);

            // 5. Check for redirects (e.g., packet_viewer when dev mode is off)
            var responseURL = response.url ? getPathname(response.url) : pathname;
            if (responseURL !== pathname && !isAjaxEligible(responseURL)) {
                window.location.href = href;
                isNavigating = false;
                return;
            }

            var html = await response.text();

            // 6. Parse the response
            var parser = new DOMParser();
            var doc = parser.parseFromString(html, 'text/html');

            var newContent = doc.querySelector('main.content');
            if (!newContent) throw new Error('No <main class="content"> found in response');

            // 7. Identify old page CSS
            var oldPageCSS = findPageCSSElements();

            // 8. Load new page CSS (wait for it before swapping)
            var newCSSDescriptors = extractNewPageCSS(doc);
            await loadNewCSS(newCSSDescriptors);

            // 9. Swap content
            contentEl.innerHTML = newContent.innerHTML;

            // 10. Remove old CSS (after new CSS is loaded and content is swapped)
            removeOldCSS(oldPageCSS);

            // 11. Activate inline scripts inside content (faq, feedback)
            activateInlineScripts(contentEl);

            // 12. Remove old page script and load new one
            removeOldPageScript();
            await loadPageScript(pathname);

            // 13. Reset scroll position
            resetContentScroll();

            // 14. Update active nav link
            updateActiveNavLink(pathname);

            // 15. Update browser history
            if (pushState) {
                history.pushState({ path: href }, '', href);
            }

            // 16. Fade in (use rAF to ensure paint before transition starts)
            requestAnimationFrame(function () {
                contentEl.style.opacity = '1';
                contentEl.style.transform = 'translateY(0)';
            });

        } catch (err) {
            console.error('[Router] AJAX navigation failed, falling back:', err);
            window.location.href = href;
        } finally {
            isNavigating = false;
        }
    }

    // ── Public API ──

    window.navigateWithTransition = function (href) {
        navigateAjax(href);
    };

    // ── Nav Click Handlers ──

    function setupNavClickHandlers() {
        var links = document.querySelectorAll('.nav-link');
        links.forEach(function (link) {
            link.addEventListener('click', function (e) {
                if (link.classList.contains('disabled')) {
                    e.preventDefault();
                    return;
                }
                e.preventDefault();
                navigateAjax(link.href);
            });
        });
    }

    // ── History Handlers ──

    window.addEventListener('popstate', function (e) {
        if (e.state && e.state.path) {
            navigateAjax(e.state.path, { pushState: false });
        }
    });

    // ── Init ──

    function initRouter() {
        // Mark existing page-specific CSS with data-page-css
        var allLinks = document.head.querySelectorAll('link[rel="stylesheet"]');
        for (var i = 0; i < allLinks.length; i++) {
            if (!isBaseCSS(allLinks[i].getAttribute('href'))) {
                allLinks[i].setAttribute('data-page-css', '');
            }
        }
        // Also mark any inline <style> that's page-specific (faq)
        var allStyles = document.head.querySelectorAll('style');
        // Don't mark the base.html inline styles, only ones that look page-specific
        // For now, base.html styles don't have data-page-css, so no action needed

        // Mark existing page script
        var allScripts = document.body.querySelectorAll('script[src]');
        for (var j = 0; j < allScripts.length; j++) {
            var src = allScripts[j].getAttribute('src');
            if (src && !isGlobalScript(src)) {
                allScripts[j].setAttribute('data-page-script', '');
            }
        }

        // Set up nav click handlers
        setupNavClickHandlers();

        // Set active nav link for current page
        updateActiveNavLink(window.location.pathname);

        // Seed initial history state
        history.replaceState({ path: window.location.href }, '', window.location.href);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initRouter, { once: true });
    } else {
        initRouter();
    }
})();
