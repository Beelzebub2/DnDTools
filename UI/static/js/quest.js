/* global showNotification */
(() => {
    const state = {
        quests: [],
        merchants: [],
        aggregatedItems: [],
        selectedMerchant: '',
        itemSearch: '',
        questsLoaded: false,
        itemsLoaded: false
    };

    const elements = {
        questList: document.getElementById('questList'),
        questLoading: document.getElementById('questLoading'),
        merchantSelect: document.getElementById('merchantSelect'),
        merchantStats: document.getElementById('merchantStats'),
        merchantRefresh: document.getElementById('merchantRefresh'),
        questTabs: document.querySelectorAll('.quest-tab'),
        views: {
            merchant: document.getElementById('merchantView'),
            items: document.getElementById('itemsView')
        },
        refreshAll: document.getElementById('refreshAll'),
        itemsLoading: document.getElementById('itemsLoading'),
        itemsList: document.getElementById('itemsList'),
        itemsSearch: document.getElementById('itemSearch'),
        clearItemSearch: document.getElementById('clearItemSearch'),
        itemsMeta: document.getElementById('itemsMeta'),
        itemsRefresh: document.getElementById('itemsRefresh')
    };

    if (!elements.questList || !elements.itemsList) {
        return;
    }

    function toggleLoading(el, isLoading) {
        if (!el) {
            return;
        }
        el.style.display = isLoading ? 'flex' : 'none';
    }

    function renderError(container, message) {
        if (!container) {
            return;
        }
        container.innerHTML = `
            <div class="error-state">
                <span class="material-icons" aria-hidden="true">error_outline</span>
                <h3>Something went wrong</h3>
                <p>${message || 'Please try refreshing the data.'}</p>
            </div>
        `;
    }

    function renderEmpty(container, icon, title, message) {
        if (!container) {
            return;
        }
        container.innerHTML = `
            <div class="empty-state">
                <span class="material-icons" aria-hidden="true">${icon}</span>
                <h3>${title}</h3>
                <p>${message}</p>
            </div>
        `;
    }

    function rarityClass(rarity) {
        if (!rarity) {
            return 'rarity-common';
        }
        return `rarity-${rarity.toLowerCase()}`;
    }

    function createMetaChip(icon, label) {
        const span = document.createElement('span');
        span.innerHTML = `<span class="material-icons" aria-hidden="true">${icon}</span>${label}`;
        return span;
    }

    function createQuestCard(quest) {
        const card = document.createElement('article');
        card.className = 'quest-card';

        const header = document.createElement('header');
        const title = document.createElement('h3');
        title.textContent = quest.title || quest.id;
        header.appendChild(title);

        if (quest.chapter) {
            const subtitle = document.createElement('div');
            subtitle.className = 'quest-meta';
            subtitle.appendChild(createMetaChip('book', quest.chapter));
            if (quest.prerequisite) {
                subtitle.appendChild(createMetaChip('flag', `Prerequisite: ${quest.prerequisite}`));
            }
            if (quest.dungeons && quest.dungeons.length) {
                subtitle.appendChild(createMetaChip('map', quest.dungeons.join(', ')));
            }
            header.appendChild(subtitle);
        }

        card.appendChild(header);

        if (quest.text) {
            const description = document.createElement('div');
            description.className = 'quest-description';
            description.textContent = quest.text;
            card.appendChild(description);
        }

        const objectives = document.createElement('div');
        objectives.innerHTML = '<h4>Objectives</h4>';
        const objectiveList = document.createElement('ul');
        objectiveList.className = 'objective-list';

        (quest.objectives || []).forEach(obj => {
            const item = document.createElement('li');
            item.className = 'objective-item';

            const icon = document.createElement('div');
            icon.className = 'objective-icon';
            const iconName = ({
                Fetch: 'inventory_2',
                Kill: 'gavel',
                Props: 'build',
                Explore: 'travel_explore',
                Survive: 'health_and_safety'
            })[obj.type] || 'task';
            icon.innerHTML = `<span class="material-icons" aria-hidden="true">${iconName}</span>`;

            const content = document.createElement('div');
            content.className = 'objective-content';

            let titleText = '';
            if (obj.type === 'Fetch') {
                const targetName = obj.item && obj.item.name ? obj.item.name : (obj.item_id || 'Unknown Item');
                titleText = `Collect ${obj.count || 0}× ${targetName}`;
            } else if (obj.type === 'Kill') {
                titleText = `Eliminate ${obj.count || 0}× ${obj.monster || 'enemies'}`;
            } else if (obj.type === 'Props') {
                titleText = `Interact with ${obj.count || 0}× ${obj.interact || 'objects'}`;
            } else if (obj.type === 'Explore') {
                titleText = `Explore ${obj.module || 'the objective area'}`;
            } else if (obj.type === 'Survive') {
                titleText = `Survive ${obj.count || 0} waves`;
            } else {
                titleText = `${obj.type || 'Objective'} – ${obj.count || 0}`;
            }

            content.innerHTML = `<strong>${titleText}</strong>`;

            if (obj.item && obj.item.rarity) {
                const rarity = document.createElement('span');
                rarity.className = `rarity-badge ${rarityClass(obj.item.rarity)}`;
                rarity.textContent = obj.item.rarity;
                content.appendChild(rarity);
            }

            if (obj.must_escape) {
                const meta = document.createElement('div');
                meta.className = 'objective-meta';
                meta.textContent = 'Must extract after completing objective';
                content.appendChild(meta);
            }

            item.appendChild(icon);
            item.appendChild(content);
            objectiveList.appendChild(item);
        });

        if (!objectiveList.children.length) {
            const emptyObjective = document.createElement('li');
            emptyObjective.className = 'objective-item';
            emptyObjective.innerHTML = '<div class="objective-content">No specific objectives listed.</div>';
            objectiveList.appendChild(emptyObjective);
        }

        objectives.appendChild(objectiveList);
        card.appendChild(objectives);

        if (quest.rewards && quest.rewards.length) {
            const rewardSection = document.createElement('div');
            rewardSection.innerHTML = '<h4>Rewards</h4>';
            const chips = document.createElement('div');
            chips.className = 'reward-chips';

            quest.rewards.forEach(reward => {
                const chip = document.createElement('span');
                chip.className = 'reward-chip';
                let iconName = 'card_giftcard';
                if (reward.type === 'Experience') iconName = 'military_tech';
                if (reward.type === 'Affinity') iconName = 'favorite';
                if (reward.type === 'Item') iconName = 'inventory';
                if (reward.type === 'Random') iconName = 'auto_awesome';

                const parts = [];
                parts.push(`<span class="material-icons" aria-hidden="true">${iconName}</span>`);
                if (reward.type === 'Item' && reward.item) {
                    parts.push(`<strong>${reward.count || 1}×</strong> ${reward.item.name}`);
                    if (reward.item.rarity) {
                        parts.push(`<span class="rarity-badge ${rarityClass(reward.item.rarity)}">${reward.item.rarity}</span>`);
                    }
                } else if (reward.type === 'Random') {
                    const rarity = reward.rarity ? `<span class="rarity-badge ${rarityClass(reward.rarity)}">${reward.rarity}</span>` : '';
                    parts.push(`${reward.count || 1}× Random ${reward.item_type || 'Reward'} ${rarity}`);
                } else if (reward.type === 'Affinity') {
                    parts.push(`${reward.count || 0} Affinity${reward.merchant ? ` (${reward.merchant})` : ''}`);
                } else if (reward.type === 'Experience') {
                    parts.push(`${reward.count || 0} Experience`);
                } else {
                    parts.push(`${reward.count || 0} ${reward.type}`);
                }

                chip.innerHTML = parts.join(' ');
                chips.appendChild(chip);
            });

            rewardSection.appendChild(chips);
            card.appendChild(rewardSection);
        }

        return card;
    }

    function updateMerchantStats(quests) {
        if (!elements.merchantStats) {
            return;
        }
        if (!quests.length) {
            elements.merchantStats.innerHTML = 'No quests available for this merchant.';
            return;
        }

        const objectiveCount = quests.reduce((total, quest) => total + (quest.objectives ? quest.objectives.length : 0), 0);
        const itemObjectives = quests.reduce((total, quest) => total + (quest.objectives || []).filter(obj => obj.item_id).length, 0);

        elements.merchantStats.innerHTML = `
            <strong>${quests.length}</strong> quests •
            <strong>${objectiveCount}</strong> objectives •
            <strong>${itemObjectives}</strong> item turn-ins
        `;
    }

    function renderMerchantOptions() {
        if (!elements.merchantSelect) {
            return;
        }
        const previousSelection = state.selectedMerchant;
        const fragment = document.createDocumentFragment();

        state.merchants.forEach(merchant => {
            const option = document.createElement('option');
            option.value = merchant;
            option.textContent = merchant;
            fragment.appendChild(option);
        });

        elements.merchantSelect.innerHTML = '';
        elements.merchantSelect.appendChild(fragment);

        if (state.merchants.length) {
            if (!state.selectedMerchant || !state.merchants.includes(previousSelection)) {
                state.selectedMerchant = state.merchants[0];
            }
            elements.merchantSelect.value = state.selectedMerchant;
        }
    }

    function getFilteredQuests() {
        if (!state.selectedMerchant) {
            return state.quests;
        }
        return state.quests.filter(quest => quest.merchant === state.selectedMerchant);
    }

    function renderMerchantView() {
        if (!state.questsLoaded) {
            return;
        }
        const questsToRender = getFilteredQuests();

        if (!questsToRender.length) {
            renderEmpty(elements.questList, 'inventory', 'No quests yet', 'Try refreshing or selecting another merchant.');
            updateMerchantStats([]);
            return;
        }

        const fragment = document.createDocumentFragment();
        questsToRender.forEach(quest => fragment.appendChild(createQuestCard(quest)));
        elements.questList.innerHTML = '';
        elements.questList.appendChild(fragment);
        updateMerchantStats(questsToRender);
    }

    function renderItemsList() {
        if (!state.itemsLoaded) {
            return;
        }

        const searchTerm = state.itemSearch.trim().toLowerCase();
        const filtered = state.aggregatedItems.filter(item => {
            if (!searchTerm) {
                return true;
            }
            const merchantMatch = (item.merchants || []).some(entry => (entry.name || '').toLowerCase().includes(searchTerm));
            return (item.name || '').toLowerCase().includes(searchTerm) || merchantMatch;
        });

        const totalNeeded = filtered.reduce((total, item) => total + (item.total_required || 0), 0);
        if (elements.itemsMeta) {
            elements.itemsMeta.innerHTML = `Showing <strong>${filtered.length}</strong> of ${state.aggregatedItems.length} items • Total required: <strong>${totalNeeded}</strong>`;
        }

        if (!filtered.length) {
            renderEmpty(elements.itemsList, 'checklist_rtl', 'No matching items', 'Try a different search term or refresh the data.');
            return;
        }

        const fragment = document.createDocumentFragment();
        filtered.forEach(item => {
            const row = document.createElement('div');
            row.className = 'quest-item';

            const main = document.createElement('div');
            main.className = 'item-main';

            const iconWrapper = document.createElement('div');
            iconWrapper.className = 'item-icon';
            if (item.icon) {
                const img = document.createElement('img');
                img.src = item.icon;
                img.alt = `${item.name} icon`;
                iconWrapper.appendChild(img);
            } else {
                iconWrapper.innerHTML = '<span class="material-icons" aria-hidden="true">inventory_2</span>';
            }

            const info = document.createElement('div');
            info.className = 'item-info';
            const name = document.createElement('div');
            name.className = 'item-name';
            name.textContent = item.name || item.item_id;
            info.appendChild(name);

            const meta = document.createElement('div');
            meta.className = 'item-meta';
            if (item.rarity) {
                const rarity = document.createElement('span');
                rarity.className = `rarity-badge ${rarityClass(item.rarity)}`;
                rarity.textContent = item.rarity;
                meta.appendChild(rarity);
            }
            if (item.type) {
                const type = document.createElement('span');
                type.textContent = item.type;
                meta.appendChild(type);
            }
            info.appendChild(meta);

            main.appendChild(iconWrapper);
            main.appendChild(info);

            const required = document.createElement('div');
            required.className = 'item-required';
            required.innerHTML = `Total needed: <span>${item.total_required || 0}</span>`;

            const quests = document.createElement('div');
            quests.className = 'item-quests';

            if (item.merchants && item.merchants.length) {
                const merchantTags = document.createElement('div');
                merchantTags.className = 'merchant-tags';
                item.merchants.forEach(entry => {
                    const tag = document.createElement('span');
                    tag.className = 'merchant-tag';
                    tag.innerHTML = `<strong>${entry.name}</strong> • ${entry.count}x`;
                    merchantTags.appendChild(tag);
                });
                quests.appendChild(merchantTags);
            }

            if (item.quests && item.quests.length) {
                const questTags = document.createElement('div');
                questTags.className = 'quest-tags';
                item.quests.forEach(entry => {
                    const tag = document.createElement('span');
                    tag.className = 'quest-tag';
                    const questTitle = entry.title || entry.id;
                    tag.innerHTML = `${entry.merchant ? `<strong>${entry.merchant}</strong> — ` : ''}${questTitle} (<strong>${entry.count}×</strong>)`;
                    questTags.appendChild(tag);
                });
                quests.appendChild(questTags);
            }

            row.appendChild(main);
            row.appendChild(required);
            row.appendChild(quests);
            fragment.appendChild(row);
        });

        elements.itemsList.innerHTML = '';
        elements.itemsList.appendChild(fragment);
    }

    async function fetchQuests({ force = false, silent = false } = {}) {
        toggleLoading(elements.questLoading, true);
        try {
            const response = await fetch(force ? '/api/quests?refresh=1' : '/api/quests');
            const data = await response.json();
            if (!response.ok || data.success === false) {
                throw new Error(data.error || 'Failed to fetch quest data');
            }

            state.quests = data.quests || [];
            state.merchants = data.merchants || [];
            state.questsLoaded = true;
            renderMerchantOptions();
            renderMerchantView();

            if (!silent) {
                updateMerchantStats(getFilteredQuests());
            }
        } catch (error) {
            console.error(error);
            state.questsLoaded = false;
            renderError(elements.questList, error.message);
        } finally {
            toggleLoading(elements.questLoading, false);
        }
    }

    async function fetchItems({ force = false } = {}) {
        toggleLoading(elements.itemsLoading, true);
        try {
            const response = await fetch(force ? '/api/quests/items?refresh=1' : '/api/quests/items');
            const data = await response.json();
            if (!response.ok || data.success === false) {
                throw new Error(data.error || 'Failed to fetch quest requirements');
            }

            state.aggregatedItems = data.items || [];
            state.itemsLoaded = true;
            renderItemsList();
        } catch (error) {
            console.error(error);
            state.itemsLoaded = false;
            renderError(elements.itemsList, error.message);
        } finally {
            toggleLoading(elements.itemsLoading, false);
        }
    }

    async function refreshAll({ force = false } = {}) {
        await Promise.all([
            fetchQuests({ force, silent: force }),
            fetchItems({ force })
        ]);
        if (force && typeof showNotification === 'function') {
            showNotification('Quest data refreshed', 'success');
        }
    }

    function switchView(view) {
        Object.entries(elements.views).forEach(([name, element]) => {
            if (!element) return;
            element.classList.toggle('hidden', name !== view);
        });
        elements.questTabs.forEach(tab => {
            const isActive = tab.dataset.view === view;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
    }

    function registerEvents() {
        if (elements.merchantSelect) {
            elements.merchantSelect.addEventListener('change', () => {
                state.selectedMerchant = elements.merchantSelect.value;
                renderMerchantView();
                renderItemsList();
            });
        }

        if (elements.merchantRefresh) {
            elements.merchantRefresh.addEventListener('click', () => refreshAll({ force: true }));
        }

        if (elements.itemsRefresh) {
            elements.itemsRefresh.addEventListener('click', () => fetchItems({ force: true }));
        }

        if (elements.refreshAll) {
            elements.refreshAll.addEventListener('click', () => refreshAll({ force: true }));
        }

        if (elements.questTabs.length) {
            elements.questTabs.forEach(tab => {
                tab.addEventListener('click', () => switchView(tab.dataset.view));
            });
        }

        if (elements.itemsSearch) {
            elements.itemsSearch.addEventListener('input', (event) => {
                state.itemSearch = event.target.value;
                if (elements.clearItemSearch) {
                    elements.clearItemSearch.classList.toggle('visible', Boolean(state.itemSearch));
                }
                renderItemsList();
            });
        }

        if (elements.clearItemSearch) {
            elements.clearItemSearch.addEventListener('click', () => {
                state.itemSearch = '';
                elements.itemsSearch.value = '';
                elements.clearItemSearch.classList.remove('visible');
                renderItemsList();
            });
        }
    }

    registerEvents();
    refreshAll({ force: false });
})();
