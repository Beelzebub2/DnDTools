import json
from pathlib import Path

cache_path = Path(r"c:\Users\ricar\AppData\Local\DnDTools\data\quests_cache.json")
if not cache_path.exists():
    print('Cache file not found:', cache_path)
    raise SystemExit(1)

data = json.loads(cache_path.read_text(encoding='utf-8'))
raw_quests = data.get('quests') if isinstance(data, dict) else None
if raw_quests is None:
    # try to assume file itself is list of quests
    if isinstance(data, list):
        raw_quests = data
    else:
        raw_quests = []

all_merchants = []
if isinstance(data, dict) and 'merchants' in data and isinstance(data['merchants'], list):
    all_merchants = data['merchants']
else:
    # derive from quests
    seen = set()
    for q in raw_quests:
        m = q.get('merchant') or q.get('merchant_original')
        if m and m not in seen:
            seen.add(m)
            all_merchants.append(m)


def test_string(val):
    try:
        return (str(val)).lower()
    except Exception:
        return ''

KEYWORDS = ['daily','weekly','seasonal','season']

def is_quest_time_limited(q):
    if not isinstance(q, dict):
        return False
    merchant = test_string(q.get('merchant') or q.get('merchant_original') or '')
    title = test_string(q.get('title') or q.get('id') or '')
    _id = test_string(q.get('id') or '')
    freq = test_string(q.get('frequency') or q.get('repeat') or q.get('recurrence') or q.get('schedule') or '')
    for kw in KEYWORDS:
        if kw in merchant or kw in title or kw in _id or kw in freq:
            return True
    return False

import re

def normalize_merchant(m):
    if m is None:
        return ''
    s = str(m).lower().strip()
    s = re.sub(r'\b(daily|weekly|seasonal|season)\b', '', s)
    s = re.sub(r'[^a-z0-9]+',' ', s).strip()
    return s

# Build merchant->quests mapping (normalized)
merchant_map = {}
for q in raw_quests:
    raw_m = q.get('merchant') or q.get('merchant_original') or ''
    key = normalize_merchant(raw_m)
    merchant_map.setdefault(key, []).append(q)

visible = []
hidden = []

FORCED_HIDDEN_MERCHANTS = set(['huntress'])

frequency_re = re.compile(r'\b(daily|weekly|seasonal|season)\b', re.IGNORECASE)

for m in all_merchants:
    key = normalize_merchant(m)
    # forced blacklist
    if key in FORCED_HIDDEN_MERCHANTS:
        hidden.append(m)
        continue
    quests_for = merchant_map.get(key, [])
    if not quests_for:
        visible.append(m)
        continue
    literal_has_freq = bool(frequency_re.search(str(m)))
    if literal_has_freq:
        # only show if there is any non-time-limited quest in the normalized group
        if any(not is_quest_time_limited(q) for q in quests_for):
            visible.append(m)
        else:
            hidden.append(m)
    else:
        if any(not is_quest_time_limited(q) for q in quests_for):
            visible.append(m)
        else:
            hidden.append(m)

print('Visible merchants (kept):')
for v in visible:
    print('  -', v)

print('\nHidden merchants (only time-limited):')
for h in hidden:
    print('  -', h)

# Show some examples of time-limited detection failing
print('\nSample of quests detected as time-limited (first 20):')
count = 0
for q in raw_quests:
    if is_quest_time_limited(q):
        print('  *', q.get('merchant') or q.get('merchant_original'), '-', q.get('title') or q.get('id'))
        count += 1
        if count >= 20:
            break

print('\nTotal quests:', len(raw_quests))
print('Total merchants from cache:', len(all_merchants))
print('Merchant groups matched by normalization:', len(merchant_map))
