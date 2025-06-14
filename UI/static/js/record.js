document.addEventListener('DOMContentLoaded', () => {
    // Elements
    const captureSwitch = document.getElementById('captureSwitch');
    const captureToggle = document.getElementById('captureToggle');
    const switchThumb = document.getElementById('switchThumb');
    const switchOn = document.getElementById('switchOn');
    const switchOff = document.getElementById('switchOff');
    const statusIndicator = document.getElementById('statusIndicator');
    const captureStatus = document.getElementById('captureStatus');
    const characterSection = document.getElementById('characterSection');
    const characterGrid = document.getElementById('characterGrid');

    // Traffic visualization elements
    const trafficVisualization = document.getElementById('trafficVisualization');
    const nodeGame = document.getElementById('nodeGame');
    const nodeTool = document.getElementById('nodeTool');
    const nodeServer = document.getElementById('nodeServer');
    const pathGameServer = document.getElementById('pathGameServer');
    const pathGameTool = document.getElementById('pathGameTool');
    const particleGameServer = document.getElementById('particleGameServer');
    const particleGameTool = document.getElementById('particleGameTool');
    const particleToolServer = document.getElementById('particleToolServer');

    let pollingInterval = null;

    // Initialize particles with staggered animation delays
    function initTrafficParticles() {
        // For Game->Server direct path (capture off)
        const particles = [];
        for (let i = 0; i < 3; i++) {
            const particle = document.createElement('div');
            particle.className = 'traffic-particle';
            particle.id = `particleGameServer_${i}`;
            particle.style.backgroundColor = 'var(--game-color)';
            particle.style.animationDelay = `${i * 0.7}s`;
            particles.push(particle);
            pathGameServer.appendChild(particle);
        }
    }

    // Initialize traffic visualization
    initTrafficParticles();

    // Animation functions
    function activateDirectPath() {
        // Direct path animation (Game -> Server)
        nodeGame.classList.add('pulse');
        nodeServer.classList.add('pulse');
        nodeTool.classList.remove('pulse');

        // Show the direct path, hide the tool path
        pathGameServer.style.opacity = '1';
        pathGameTool.style.opacity = '0.4';
        nodeTool.style.opacity = '0.4';

        // Activate direct path animation
        pathGameServer.querySelectorAll('.traffic-particle').forEach(particle => {
            particle.style.animation = 'moveParticle 2s infinite linear';
            particle.style.opacity = '1';
            particle.style.display = 'block';
        });

        // Deactivate tool path animation
        particleGameTool.style.animation = '';
        particleToolServer.style.animation = '';
        particleGameTool.style.opacity = '0';
        particleToolServer.style.opacity = '0';
    }

    function activateToolPath() {
        // Tool intercept path animation (Game -> Tool -> Server)
        nodeGame.classList.add('pulse');
        nodeTool.classList.add('pulse');
        nodeServer.classList.add('pulse');

        // Show the tool path, hide direct path
        pathGameServer.style.opacity = '0.4';
        pathGameTool.style.opacity = '1';
        nodeTool.style.opacity = '1';

        // Deactivate direct path animation
        pathGameServer.querySelectorAll('.traffic-particle').forEach(particle => {
            particle.style.animation = '';
            particle.style.opacity = '0';
        });

        // Create particles for Game->Tool and Tool->Server if they don't exist
        if (!document.getElementById('particleGameTool_0')) {
            for (let i = 0; i < 3; i++) {
                const particle1 = document.createElement('div');
                particle1.className = 'traffic-particle';
                particle1.id = `particleGameTool_${i}`;
                particle1.style.backgroundColor = 'var(--game-color)';
                particle1.style.animationDelay = `${i * 0.7}s`;
                pathGameTool.appendChild(particle1);

                const particle2 = document.createElement('div');
                particle2.className = 'traffic-particle';
                particle2.id = `particleToolServer_${i}`;
                particle2.style.backgroundColor = 'var(--tool-color)';
                particle2.style.animationDelay = `${i * 0.7 + 0.3}s`;
                pathGameTool.appendChild(particle2);
            }
        }

        // Activate tool path particles
        pathGameTool.querySelectorAll('.traffic-particle').forEach(particle => {
            particle.style.animation = 'moveParticle 2s infinite linear';
            particle.style.opacity = '1';
            particle.style.display = 'block';
        });
    } async function loadCharacters() {
        try {
            const response = await fetch('/api/characters');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            const characters = await response.json();

            // Check if characters is null, undefined, or an error object
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

            characters.forEach(char => {
                const card = document.createElement('div');
                card.className = 'character-card';

                const classImageSrc = getClassImage(char.class);

                card.innerHTML = `
                    <div class="card-header">
                        <img src="${classImageSrc}" 
                             alt="${char.class}" 
                             class="class-image"
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                        <span class="material-icons class-icon-fallback" style="display: none;">person</span>
                        <div class="character-title">
                            <div class="character-name">${char.nickname}</div>
                            <div class="character-subtitle">${char.class} • Level ${char.level}</div>
                        </div>
                    </div>
                `;
                card.onclick = () => window.location.href = `/character/${char.id}`;
                characterGrid.appendChild(card);
            });

            if (characters.length > 0) {
                characterSection.style.display = 'block';
            }
        } catch (error) {
            console.error('Failed to load characters:', error);
            showNotification('Failed to load characters', 'error');
        }
    } function getClassImage(className) {
        if (!className) return '/assets/classes/fighter.png';

        // Convert class name to lowercase and handle potential variations
        const classMap = {
            'fighter': 'fighter.png',
            'ranger': 'ranger.png',
            'rogue': 'rogue.png',
            'wizard': 'wizard.png',
            'cleric': 'cleric.png',
            'warlock': 'warlock.png',
            'barbarian': 'barbarian.png',
            'bard': 'bard.png',
            'druid': 'druid.png',
            'sorcerer': 'sorcerer.png'
        };

        const classKey = className.toLowerCase();
        const imageName = classMap[classKey] || 'fighter.png'; // Default to fighter if not found

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

    function updateToggleUI(isOn) {
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
    } async function updateCaptureState(isRunning) {
        try {
            // Update UI to show processing state
            captureToggle.style.pointerEvents = 'none';

            if (isRunning) {
                statusIndicator.className = 'status-indicator starting';
                captureStatus.textContent = 'Starting capture...';
            } else {
                statusIndicator.className = 'status-indicator stopping';
                captureStatus.textContent = 'Stopping capture...';
            }

            // Animate sidebar indicator: yellow when stopping, green when starting
            const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
            if (sidebarCaptureIndicator) {
                if (!isRunning) {
                    // Stopping - yellow
                    sidebarCaptureIndicator.classList.remove('active');
                    sidebarCaptureIndicator.classList.add('stopping');
                } else {
                    // Starting - in progress transition
                    sidebarCaptureIndicator.classList.remove('stopping');
                    // Don't add active yet until we confirm the state change
                }
            }

            const endpoint = isRunning ? '/api/capture/switch/start' : '/api/capture/switch/stop';
            const response = await fetch(endpoint, { method: 'POST' });
            const result = await response.json();

            if (!result.success) {
                throw new Error('Operation failed');
            }

            // Verify the state actually changed by checking the server repeatedly
            await verifyState(isRunning);

            // Update UI with successful state
            captureSwitch.checked = isRunning;
            captureToggle.style.pointerEvents = 'auto';
            updateToggleUI(isRunning);

            if (isRunning) {
                statusIndicator.className = 'status-indicator capturing';
                captureStatus.textContent = 'Capture is running';
                activateToolPath();
            } else {
                statusIndicator.className = 'status-indicator';
                captureStatus.textContent = 'Capture is currently off';
                activateDirectPath();
            }            // Sidebar indicator: green if running, off if stopped
            if (sidebarCaptureIndicator) {
                if (isRunning) {
                    sidebarCaptureIndicator.classList.add('active');
                    sidebarCaptureIndicator.classList.remove('stopping');
                } else {
                    sidebarCaptureIndicator.classList.remove('active', 'stopping');
                }
            }

            if (isRunning) {
                startPolling();
                showNotification('Capture started', 'success');
            } else {
                stopPolling();
                showNotification('Capture stopped', 'info');
            }
        } catch (error) {
            console.error('Failed to update capture state:', error);
            // Revert UI to previous state
            captureSwitch.checked = !isRunning;
            captureToggle.style.pointerEvents = 'auto';
            updateToggleUI(!isRunning);
            statusIndicator.className = 'status-indicator';
            captureStatus.textContent = 'Capture error';
            showNotification(`Failed to ${isRunning ? 'start' : 'stop'} capture`, 'error');

            // Sidebar indicator: always red on error
            const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
            if (sidebarCaptureIndicator) {
                sidebarCaptureIndicator.classList.remove('active', 'stopping');
            }
        }
    } async function verifyState(expectedState) {
        const maxAttempts = 3; // Reduced from 5
        const delayMs = 300; // Reduced from 500

        for (let i = 0; i < maxAttempts; i++) {
            try {
                const resp = await fetch('/api/capture/state');
                const state = await resp.json();

                if (state.running === expectedState) {
                    return true;
                }

                await new Promise(resolve => setTimeout(resolve, delayMs));
            } catch (error) {
                console.warn('Error verifying state, retrying...', error);
            }
        }
        throw new Error('Failed to verify capture state change');
    }

    // Wait for pywebview to be ready
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
            const result = await response.json();

            if (!result.success) {
                throw new Error('Restart failed');
            }

            // Verify the state is running
            await verifyState(true);

            // Update UI with running state
            captureSwitch.checked = true;
            captureToggle.style.pointerEvents = 'auto';
            updateToggleUI(true);
            statusIndicator.className = 'status-indicator capturing';
            captureStatus.textContent = 'Capture is running';
            activateToolPath();
            startPolling();

        } catch (error) {
            console.error('Failed to restart capture:', error);
            captureSwitch.checked = false;
            captureToggle.style.pointerEvents = 'auto';
            updateToggleUI(false);
            statusIndicator.className = 'status-indicator';
            captureStatus.textContent = 'Capture error';
            showNotification('Failed to restart capture', 'error');
            activateDirectPath();
        }
    }

    // Handle toggle click
    captureToggle.addEventListener('click', () => {
        const newState = !captureSwitch.checked;
        updateCaptureState(newState);
    });    // Initialize state on page load
    async function init() {
        try {
            await waitForPywebview();
            const resp = await fetch('/api/capture/state');
            const state = await resp.json();

            // Update UI to reflect current running state
            captureSwitch.checked = state.running;
            updateToggleUI(state.running);

            // Update sidebar indicator to reflect current state
            const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
            if (sidebarCaptureIndicator) {
                if (state.running) {
                    sidebarCaptureIndicator.classList.add('active');
                    sidebarCaptureIndicator.classList.remove('stopping');
                } else {
                    sidebarCaptureIndicator.classList.remove('active', 'stopping');
                }
            }

            if (state.running) {
                statusIndicator.className = 'status-indicator capturing';
                captureStatus.textContent = 'Capture is running';
                activateToolPath();
                startPolling();
            } else {
                statusIndicator.className = 'status-indicator';
                captureStatus.textContent = 'Capture is currently off';
                activateDirectPath();
            }

            // Load initial character list
            await loadCharacters();
        } catch (error) {
            console.error('Failed to get initial capture state:', error);
            // Default to off state
            captureSwitch.checked = false;
            updateToggleUI(false);
            statusIndicator.className = 'status-indicator';
            captureStatus.textContent = 'Capture is currently off';
            activateDirectPath();

            // Ensure sidebar indicator is also set to off state
            const sidebarCaptureIndicator = document.getElementById('sidebarCaptureIndicator');
            if (sidebarCaptureIndicator) {
                sidebarCaptureIndicator.classList.remove('active', 'stopping');
            }

            showNotification('Failed to get capture state', 'error');        }
    }    // Character capture animation function
    window.showCharacterCaptureAnimation = function(characterClass, characterNickname) {
        console.log(`Showing character capture animation for ${characterClass} (${characterNickname})`);
        
        // Get class image path
        function getClassImage(className) {
            if (!className) return '/assets/classes/fighter.png';
            
            const classMap = {
                'Fighter': 'fighter.png',
                'Ranger': 'ranger.png',
                'Rogue': 'rogue.png',
                'Wizard': 'wizard.png',
                'Cleric': 'cleric.png',
                'Warlock': 'warlock.png',
                'Barbarian': 'barbarian.png',
                'Bard': 'bard.png',
                'Druid': 'druid.png',
                'Sorcerer': 'sorcerer.png'
            };
            
            const imageName = classMap[className] || 'fighter.png';
            return `/assets/classes/${imageName}`;
        }
        
        const trafficVisualization = document.getElementById('trafficVisualization');
        if (!trafficVisualization) {
            console.log('Traffic visualization not found, skipping animation');
            return;
        }
        
        // Create character icon element
        const charIcon = document.createElement('div');
        charIcon.className = 'character-capture-icon';
        
        const charImg = document.createElement('img');
        charImg.src = getClassImage(characterClass);
        charImg.alt = characterClass;
        charImg.onerror = function() {
            // Fallback to a default icon if image fails to load
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
        
        // Start animation
        setTimeout(() => {
            charIcon.classList.add('flying');
        }, 100);
        
        // Remove icon after animation completes
        setTimeout(() => {
            if (charIcon.parentNode) {
                trafficVisualization.removeChild(charIcon);
            }
        }, 2500);
        
        // Pulse the DnDTools node to show it received the character
        const nodeTool = document.getElementById('nodeTool');
        if (nodeTool) {
            nodeTool.classList.add('pulse');
            setTimeout(() => {
                nodeTool.classList.remove('pulse');
            }, 2500);
        }
    };

    // Initialize
    init();

    // Start direct path animation by default (capture off state)
    activateDirectPath();
});
