document.addEventListener('DOMContentLoaded', () => {
    const captureSwitch = document.getElementById('captureSwitch');
    const captureToggle = document.getElementById('captureToggle');
    const switchThumb = document.getElementById('switchThumb');
    const switchOn = document.getElementById('switchOn');
    const switchOff = document.getElementById('switchOff');
    const statusIndicator = document.getElementById('statusIndicator');
    const captureStatus = document.getElementById('captureStatus');
    const characterSection = document.getElementById('characterSection');
    const characterGrid = document.getElementById('characterGrid');

    const trafficVisualization = document.getElementById('trafficVisualization');
    const nodeGame = document.getElementById('nodeGame');
    const nodeTool = document.getElementById('nodeTool');
    const nodeServer = document.getElementById('nodeServer');
    const pathGameServer = document.getElementById('pathGameServer');
    const pathGameTool = document.getElementById('pathGameTool');

    let pollingInterval = null;
    let captureRunningState = null;

    initTrafficParticles();
    activateDirectPath();

    function initTrafficParticles() {
        if (!pathGameServer) return;

        for (let i = 0; i < 3; i += 1) {
            const particle = document.createElement('div');
            particle.className = 'traffic-particle';
            particle.id = `particleGameServer_${i}`;
            particle.style.backgroundColor = 'var(--game-color)';
            particle.style.opacity = '0';
            particle.style.display = 'none';
            pathGameServer.appendChild(particle);
        }
    }

    function activateDirectPath() {
        if (!nodeGame || !nodeServer || !nodeTool) return;

        nodeGame.classList.add('pulse');
        nodeServer.classList.add('pulse');
        nodeTool.classList.remove('pulse');

        if (pathGameServer) pathGameServer.style.opacity = '1';
        if (pathGameTool) pathGameTool.style.opacity = '0.4';
        nodeTool.style.opacity = '0.4';

        if (pathGameServer) {
            pathGameServer.querySelectorAll('.traffic-particle').forEach((particle) => {
                particle.style.animation = '';
                particle.style.opacity = '0';
                particle.style.display = 'none';
            });
        }

        if (pathGameTool) {
            pathGameTool.querySelectorAll('.traffic-particle').forEach((particle) => {
                particle.style.animation = '';
                particle.style.opacity = '0';
                particle.style.display = 'none';
            });
        }
    }

    function activateToolPath() {
        if (!nodeGame || !nodeServer || !nodeTool) return;

        nodeGame.classList.add('pulse');
        nodeTool.classList.add('pulse');
        nodeServer.classList.add('pulse');

        if (pathGameServer) pathGameServer.style.opacity = '0.4';
        if (pathGameTool) pathGameTool.style.opacity = '1';
        nodeTool.style.opacity = '1';

        if (pathGameServer) {
            pathGameServer.querySelectorAll('.traffic-particle').forEach((particle) => {
                particle.style.animation = '';
                particle.style.opacity = '0';
                particle.style.display = 'none';
            });
        }

        if (pathGameTool && !document.getElementById('particleGameTool_0')) {
            for (let i = 0; i < 3; i += 1) {
                const particle1 = document.createElement('div');
                particle1.className = 'traffic-particle';
                particle1.id = `particleGameTool_${i}`;
                particle1.style.backgroundColor = 'var(--game-color)';
                particle1.style.opacity = '0';
                particle1.style.display = 'none';
                pathGameTool.appendChild(particle1);

                const particle2 = document.createElement('div');
                particle2.className = 'traffic-particle';
                particle2.id = `particleToolServer_${i}`;
                particle2.style.backgroundColor = 'var(--tool-color)';
                particle2.style.opacity = '0';
                particle2.style.display = 'none';
                pathGameTool.appendChild(particle2);
            }
        }

        if (pathGameTool) {
            pathGameTool.querySelectorAll('.traffic-particle').forEach((particle) => {
                particle.style.animation = '';
                particle.style.opacity = '0';
                particle.style.display = 'none';
            });
        }
    }

    async function loadCharacters() {
        try {
            const response = await fetch('/api/characters');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const characters = await response.json();
            if (!characters || (characters.error && characters.error.length > 0)) {
                console.warn('No characters found or error in response:', characters);
                characterGrid.innerHTML = '';
                characterSection.style.display = 'none';
                return;
            }

            characterGrid.innerHTML = '';

            if (!Array.isArray(characters) || characters.length === 0) {
                characterSection.style.display = 'none';
                return;
            }

            characters.forEach((char) => {
                const card = document.createElement('div');
                card.className = 'character-card';

                const classImageSrc = getClassImage(char.class);

                card.innerHTML = `
          <div class="card-header">
            <img src="${classImageSrc}" alt="${char.class}" class="class-image"
              onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
            <span class="material-icons class-icon-fallback" style="display: none;">person</span>
            <div class="character-title">
              <div class="character-name">${char.nickname}</div>
              <div class="character-subtitle">${char.class} • Level ${char.level}</div>
            </div>
          </div>
        `;
                card.onclick = () => {
                    window.location.href = `/character/${char.id}`;
                };
                characterGrid.appendChild(card);
            });

            if (characters.length > 0) {
                characterSection.style.display = 'block';
            }
        } catch (error) {
            console.error('Failed to load characters:', error);
            showNotification('Failed to load characters', 'error');
        }
    }

    function getClassImage(className) {
        if (!className) return '/assets/classes/fighter.png';

        const classMap = {
            fighter: 'fighter.png',
            ranger: 'ranger.png',
            rogue: 'rogue.png',
            wizard: 'wizard.png',
            cleric: 'cleric.png',
            warlock: 'warlock.png',
            barbarian: 'barbarian.png',
            bard: 'bard.png',
            druid: 'druid.png',
            sorcerer: 'sorcerer.png',
        };

        const classKey = className.toLowerCase();
        const imageName = classMap[classKey] || 'fighter.png';
        return `/assets/classes/${imageName}`;
    }

    function startPolling() {
        if (pollingInterval) clearInterval(pollingInterval);
        pollingInterval = setInterval(loadCharacters, 2000);
    }

    function stopPolling() {
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }
    }

    function applyCaptureState(state = {}, options = {}) {
        const { suppressErrorToast = false } = options || {};
        const running = Boolean(state.running);
        const wasRunning = captureRunningState;

        if (captureSwitch) {
            captureSwitch.checked = running;
        }
        updateToggleUI(running);

        if (running) {
            statusIndicator.className = 'status-indicator capturing';
            captureStatus.textContent = 'Capture is running';
            activateToolPath();
            startPolling();
            if (!wasRunning) {
                loadCharacters().catch((err) => console.warn('Character refresh failed:', err));
            }
        } else {
            statusIndicator.className = 'status-indicator';
            captureStatus.textContent = 'Capture is currently off';
            activateDirectPath();
            stopPolling();
            if (wasRunning) {
                loadCharacters().catch((err) => console.warn('Character refresh failed:', err));
            }
        }

        const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
        if (sidebarCaptureIndicator) {
            if (running) {
                sidebarCaptureIndicator.classList.add('active');
                sidebarCaptureIndicator.classList.remove('stopping');
            } else {
                sidebarCaptureIndicator.classList.remove('active', 'stopping');
            }
        }

        if (state.lastError && !running && !suppressErrorToast) {
            showNotification(state.lastError, 'error');
        }

        captureRunningState = running;
        return running;
    }

    window.applyCaptureState = (state, options) => applyCaptureState(state, options);

    function updateToggleUI(isOn) {
        if (!switchThumb || !switchOn || !switchOff) return;

        if (isOn) {
            switchThumb.classList.add('active');
            switchOn.classList.add('active');
            switchOff.classList.remove('active');
            activateToolPath();
        } else {
            switchThumb.classList.remove('active');
            switchOn.classList.remove('active');
            switchOff.classList.add('active');
            activateDirectPath();
        }
    }

    async function updateCaptureState(targetRunning) {
        try {
            captureToggle.style.pointerEvents = 'none';
            statusIndicator.className = targetRunning
                ? 'status-indicator starting'
                : 'status-indicator stopping';
            captureStatus.textContent = targetRunning
                ? 'Starting capture...'
                : 'Stopping capture...';

            const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
            if (sidebarCaptureIndicator) {
                if (!targetRunning) {
                    sidebarCaptureIndicator.classList.remove('active');
                    sidebarCaptureIndicator.classList.add('stopping');
                } else {
                    sidebarCaptureIndicator.classList.remove('stopping');
                }
            }

            const endpoint = targetRunning
                ? '/api/capture/switch/start'
                : '/api/capture/switch/stop';
            const response = await fetch(endpoint, { method: 'POST' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const result = await response.json();
            if (!result.success) {
                throw new Error(result.error || 'Operation failed');
            }

            const state = result.state || {};
            const expectedState = state.running ?? targetRunning;
            await verifyState(expectedState);

            captureToggle.style.pointerEvents = 'auto';
            applyCaptureState(state, { suppressErrorToast: true });

            const message = expectedState ? 'Capture started' : 'Capture stopped';
            const variant = expectedState ? 'success' : 'info';
            showNotification(message, variant);
        } catch (error) {
            console.error('Failed to update capture state:', error);
            captureToggle.style.pointerEvents = 'auto';
            statusIndicator.className = 'status-indicator';
            captureStatus.textContent = 'Capture error';
            showNotification(
                error?.message || `Failed to ${targetRunning ? 'start' : 'stop'} capture`,
                'error',
            );

            const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
            if (sidebarCaptureIndicator) {
                sidebarCaptureIndicator.classList.remove('active', 'stopping');
            }

            try {
                const stateResp = await fetch('/api/capture/state');
                if (stateResp.ok) {
                    const fallbackState = await stateResp.json();
                    applyCaptureState(fallbackState, { suppressErrorToast: true });
                    return;
                }
            } catch (syncError) {
                console.warn('Unable to resync capture state after failure:', syncError);
            }

            applyCaptureState({ running: !targetRunning }, { suppressErrorToast: true });
        }
    }

    async function verifyState(expectedState) {
        const maxAttempts = 3;
        const delayMs = 300;

        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
            try {
                const resp = await fetch('/api/capture/state');
                if (!resp.ok) {
                    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
                }
                const state = await resp.json();
                if (Boolean(state.running) === Boolean(expectedState)) {
                    return true;
                }
            } catch (error) {
                console.warn('Error verifying state, retrying...', error);
            }

            await new Promise((resolve) => setTimeout(resolve, delayMs));
        }

        throw new Error('Failed to verify capture state change');
    }

    function waitForPywebview() {
        return new Promise((resolve) => {
            if (window.pywebview && window.pywebview.api) {
                resolve();
                return;
            }

            const checkInterval = setInterval(() => {
                if (window.pywebview && window.pywebview.api) {
                    clearInterval(checkInterval);
                    resolve();
                }
            }, 100);
        });
    }

    async function restartCapture() {
        try {
            captureToggle.style.pointerEvents = 'none';
            statusIndicator.className = 'status-indicator starting';
            captureStatus.textContent = 'Restarting capture...';

            await waitForPywebview();
            const response = await fetch('/api/capture/switch/restart', { method: 'POST' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const result = await response.json();
            if (!result.success) {
                throw new Error(result.error || 'Restart failed');
            }

            const state = result.state || {};
            await verifyState(state.running ?? true);

            captureToggle.style.pointerEvents = 'auto';
            applyCaptureState(state, { suppressErrorToast: true });
            showNotification('Capture restarted', 'success');
        } catch (error) {
            console.error('Failed to restart capture:', error);
            captureToggle.style.pointerEvents = 'auto';
            showNotification(error?.message || 'Failed to restart capture', 'error');

            try {
                const stateResp = await fetch('/api/capture/state');
                if (stateResp.ok) {
                    const fallbackState = await stateResp.json();
                    applyCaptureState(fallbackState, { suppressErrorToast: true });
                    return;
                }
            } catch (syncError) {
                console.warn('Unable to resync capture state after restart failure:', syncError);
            }

            applyCaptureState({ running: false }, { suppressErrorToast: true });
        }
    }

    window.restartCapture = restartCapture;

    captureToggle.addEventListener('click', () => {
        const newState = !captureSwitch.checked;
        updateCaptureState(newState);
    });

    async function init() {
        try {
            await waitForPywebview();
            const resp = await fetch('/api/capture/state');
            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
            }

            const state = await resp.json();
            applyCaptureState(state, { suppressErrorToast: true });
            await loadCharacters();
        } catch (error) {
            console.error('Failed to get initial capture state:', error);
            applyCaptureState({ running: false }, { suppressErrorToast: true });

            const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
            if (sidebarCaptureIndicator) {
                sidebarCaptureIndicator.classList.remove('active', 'stopping');
            }

            showNotification('Failed to get capture state', 'error');
        }
    }

    window.triggerTrafficParticle = function triggerTrafficParticle() {
        if (!pathGameServer || !pathGameTool) return;

        const isCaptureOn = captureSwitch.checked;
        const targetPath = isCaptureOn ? pathGameTool : pathGameServer;
        const particles = targetPath.querySelectorAll('.traffic-particle');
        if (particles.length === 0) return;

        let availableParticle = null;
        for (const particle of particles) {
            if (particle.style.animation === '' || particle.style.animation === 'none') {
                availableParticle = particle;
                break;
            }
        }

        if (!availableParticle) {
            [availableParticle] = particles;
        }

        availableParticle.style.display = 'block';
        availableParticle.style.opacity = '1';
        availableParticle.style.animation = 'moveParticle 2s ease-in-out';

        setTimeout(() => {
            availableParticle.style.animation = '';
            availableParticle.style.opacity = '0';
            availableParticle.style.display = 'none';
        }, 2000);
    };

    window.showCharacterCaptureAnimation = function showCharacterCaptureAnimation(characterClass, characterNickname) {
        console.log(`Showing character capture animation for ${characterClass} (${characterNickname})`);

        function resolveClassImage(className) {
            if (!className) return '/assets/classes/fighter.png';

            const classMap = {
                Fighter: 'fighter.png',
                Ranger: 'ranger.png',
                Rogue: 'rogue.png',
                Wizard: 'wizard.png',
                Cleric: 'cleric.png',
                Warlock: 'warlock.png',
                Barbarian: 'barbarian.png',
                Bard: 'bard.png',
                Druid: 'druid.png',
                Sorcerer: 'sorcerer.png',
            };

            const imageName = classMap[className] || 'fighter.png';
            return `/assets/classes/${imageName}`;
        }

        if (!trafficVisualization) {
            console.log('Traffic visualization not found, skipping animation');
            return;
        }

        const charIcon = document.createElement('div');
        charIcon.className = 'character-capture-icon';

        const charImg = document.createElement('img');
        charImg.src = resolveClassImage(characterClass);
        charImg.alt = characterClass;
        charImg.onerror = function onError() {
            this.style.display = 'none';
            const fallbackIcon = document.createElement('span');
            fallbackIcon.className = 'material-icons';
            fallbackIcon.textContent = 'person';
            fallbackIcon.style.color = 'var(--accent-gold)';
            fallbackIcon.style.fontSize = '24px';
            charIcon.appendChild(fallbackIcon);
        };

        charIcon.appendChild(charImg);
        trafficVisualization.appendChild(charIcon);

        setTimeout(() => {
            charIcon.classList.add('flying');
        }, 100);

        setTimeout(() => {
            if (charIcon.parentNode) {
                trafficVisualization.removeChild(charIcon);
            }
        }, 2500);

        if (nodeTool) {
            nodeTool.classList.add('pulse');
            setTimeout(() => {
                nodeTool.classList.remove('pulse');
            }, 2500);
        }
    };

    init();
});
