"""Handles quest-related packets captured from the game network traffic.

Listens for merchant quest list, quest log, quest select, quest complete,
quest progress (content value stack), and merchant list packets. Extracts
quest progress data and feeds it into QuestService so the UI can display
automatically-tracked progress.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from google.protobuf.json_format import MessageToDict

if TYPE_CHECKING:
    from src.quest_service import QuestService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quest flag constants from SMERCHANT_QUEST_INFO.FLAG
# ---------------------------------------------------------------------------
QUEST_FLAG_NONE = 0
QUEST_FLAG_PROGRESS = 1
QUEST_FLAG_SUCCESS = 2       # all objectives met, ready to turn in
QUEST_FLAG_COMPLETE = 3      # already turned in / completed
QUEST_FLAG_LOCKED = 4
QUEST_FLAG_AVAILABLE = 5

QUEST_FLAG_LABELS = {
    QUEST_FLAG_NONE: "none",
    QUEST_FLAG_PROGRESS: "progress",
    QUEST_FLAG_SUCCESS: "success",
    QUEST_FLAG_COMPLETE: "complete",
    QUEST_FLAG_LOCKED: "locked",
    QUEST_FLAG_AVAILABLE: "available",
}

# Merchant flag from SMERCHANT_INFO.FLAG
MERCHANT_FLAG_NONE = 0
MERCHANT_FLAG_QUEST = 1
MERCHANT_FLAG_SUCCESS = 2
MERCHANT_FLAG_RECOVERY = 3
MERCHANT_FLAG_EXPRESS = 4
MERCHANT_FLAG_NOTIFY = 5
MERCHANT_FLAG_PARCEL = 6

# ---------------------------------------------------------------------------
# Game-ID → DarkerDB short-ID normalisation
# The game uses fully-qualified IDs like
#   "DesignDataQuest:Id_Quest_TavernMaster_Tuto_01"
# while DarkerDB uses the short form  "TavernMaster_Tuto_01".
# We strip known prefixes so reconciliation works.
# ---------------------------------------------------------------------------
_GAME_ID_PREFIXES = (
    "DesignDataQuest:Id_Quest_",
    "DesignDataQuestChapter:Id_QuestChapter_",
    "DesignDataMerchant:Id_Merchant_",
    "DesignDataItem:Id_Item_",
    "DesignDataMonster:Id_Monster_",
    "DesignDataObject:Id_Object_",
)


def _normalize_game_id(raw_id: str) -> str:
    """Strip game-engine prefixes from an ID so it matches DarkerDB format."""
    if not raw_id:
        return raw_id
    for prefix in _GAME_ID_PREFIXES:
        if raw_id.startswith(prefix):
            return raw_id[len(prefix):]
    # Some IDs use a generic "DesignData*:Id_*_" pattern we haven't listed.
    # Fall back to splitting on the last colon-then-Id_ segment.
    if ":Id_" in raw_id:
        _, _, after = raw_id.partition(":Id_")
        # after = "Quest_TavernMaster_Tuto_01"  → strip category prefix
        idx = after.find("_")
        if idx >= 0:
            return after[idx + 1:]
    return raw_id


class QuestPacketHandler:
    """Processes captured quest packets and updates QuestService state.

    Thread-safe — all mutations go through a lock so the capture thread
    and Flask request threads don't race.
    """

    def __init__(
        self,
        quest_service: "QuestService",
        ui_notify: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._quest_service = quest_service
        self._ui_notify = ui_notify  # callback(event_name, json_payload)
        self._lock = threading.RLock()
        self._last_update: float = 0.0

        # Captured state
        self._merchant_quests: Dict[str, List[dict]] = {}   # merchantId → quest list
        self._merchant_chapters: Dict[str, List[dict]] = {}  # merchantId → chapter list
        self._merchant_flags: Dict[str, int] = {}            # merchantId → flag
        self._quest_completions: List[dict] = []             # recent completions
        self._captured_quest_log: Optional[List[dict]] = None  # full quest log snapshot

    # ------------------------------------------------------------------
    # Public API — called by Flask routes
    # ------------------------------------------------------------------
    def get_captured_state(self) -> dict:
        """Return the latest captured quest state for the UI."""
        with self._lock:
            return {
                "merchant_quests": dict(self._merchant_quests),
                "merchant_chapters": dict(self._merchant_chapters),
                "merchant_flags": dict(self._merchant_flags),
                "quest_log": self._captured_quest_log,
                "recent_completions": list(self._quest_completions[-20:]),
                "last_update": self._last_update,
            }

    def get_auto_progress(self) -> dict:
        """Build a progress payload compatible with QuestService / quest.js.

        Maps captured packet data to the same objective key format used by
        the JS frontend:  ``questId::type::index::item_id``

        Since we don't know the DarkerDB objective *type* or *index* from
        the packet alone, we use the ``contentId`` and quest structure to
        produce a mapping keyed by ``(questId, contentId)`` pairs that the
        frontend can reconcile with its own quest definitions.
        """
        with self._lock:
            return self._build_auto_progress()

    def clear(self) -> None:
        """Reset all captured quest state."""
        with self._lock:
            self._merchant_quests.clear()
            self._merchant_chapters.clear()
            self._merchant_flags.clear()
            self._quest_completions.clear()
            self._captured_quest_log = None
            self._last_update = 0.0

    # ------------------------------------------------------------------
    # Packet handlers — registered in capture_info
    # ------------------------------------------------------------------
    def handle_merchant_list(self, message: Any) -> bool:
        """S2C_MERCHANT_LIST_RES — merchant flags (quest ready, success, etc.)."""
        try:
            data = _msg_to_dict(message)
            merchant_list = data.get("merchantList") or []
            with self._lock:
                for merchant in merchant_list:
                    mid = _normalize_game_id(merchant.get("merchantId") or "")
                    if not mid:
                        continue
                    flag = _safe_int(merchant.get("merchantFlag"), 0)
                    self._merchant_flags[mid] = flag
                self._touch()
            logger.info(
                "Captured merchant list: %d merchants, flags=%s",
                len(merchant_list),
                {m.get("merchantId"): m.get("merchantFlag") for m in merchant_list if m.get("merchantId")},
            )
            self._notify_ui("quest_merchant_list", {"count": len(merchant_list)})
            return True
        except Exception as exc:
            logger.error("Error handling merchant list packet: %s", exc, exc_info=True)
            return False

    def handle_quest_list(self, message: Any) -> bool:
        """S2C_MERCHANT_QUEST_LIST_INFO_RES — quests for a specific merchant."""
        try:
            data = _msg_to_dict(message)
            quests = data.get("quests") or []
            chapters = data.get("chapters") or []

            # Derive merchantId from the first quest if available
            merchant_id = None
            for q in quests:
                # The merchantId is not in the response directly, but
                # we can check if there's a merchant context from existing
                # captured data or use a placeholder.
                pass

            quest_entries = []
            for q in quests:
                entry = self._parse_quest_info(q)
                if entry:
                    quest_entries.append(entry)
                    if not merchant_id and entry.get("merchant_id"):
                        merchant_id = entry["merchant_id"]

            chapter_entries = []
            for ch in chapters:
                chapter_entries.append({
                    "chapter_id": ch.get("chapterId") or "",
                    "remain_ms_time": _safe_int(ch.get("remainMSTime"), 0),
                })

            with self._lock:
                # Store keyed by best-guess merchant id
                if merchant_id:
                    self._merchant_quests[merchant_id] = quest_entries
                    self._merchant_chapters[merchant_id] = chapter_entries
                else:
                    # Use a synthetic key; we'll merge into the log
                    self._merchant_quests["_last_merchant"] = quest_entries
                    self._merchant_chapters["_last_merchant"] = chapter_entries

                self._touch()
                self._sync_progress_to_service()

            logger.info(
                "Captured quest list: %d quests, %d chapters (merchant=%s)",
                len(quest_entries), len(chapter_entries), merchant_id or "unknown",
            )
            # Log each quest entry for debugging
            for entry in quest_entries:
                logger.info(
                    "  Quest: id=%s flag=%d(%s) missions=%d",
                    entry.get("quest_id"), entry.get("quest_flag", 0),
                    entry.get("quest_flag_label", "?"),
                    len(entry.get("missions", [])),
                )
            # Count quests with progress to notify meaningfully
            in_progress = sum(1 for q in quest_entries if q.get("quest_flag") == QUEST_FLAG_PROGRESS)
            self._notify_ui("quest_list_update", {
                "quest_count": len(quest_entries),
                "in_progress": in_progress,
            })
            return True
        except Exception as exc:
            logger.error("Error handling quest list packet: %s", exc, exc_info=True)
            return False

    def handle_quest_log(self, message: Any) -> bool:
        """S2C_MERCHANT_QUEST_LOG_LIST_RES — full quest log across all merchants."""
        try:
            data = _msg_to_dict(message)
            quest_log_raw = data.get("questList") or []

            all_entries: List[dict] = []
            for log_entry in quest_log_raw:
                merchant_id = _normalize_game_id(log_entry.get("merchantId") or "")
                quests = log_entry.get("quests") or []
                chapters = log_entry.get("chapters") or []

                merchant_quests = []
                for q in quests:
                    entry = self._parse_quest_info(q, merchant_id=merchant_id)
                    if entry:
                        merchant_quests.append(entry)

                merchant_chapters = []
                for ch in chapters:
                    merchant_chapters.append({
                        "chapter_id": ch.get("chapterId") or "",
                        "remain_ms_time": _safe_int(ch.get("remainMSTime"), 0),
                    })

                all_entries.append({
                    "merchant_id": merchant_id,
                    "quests": merchant_quests,
                    "chapters": merchant_chapters,
                })

                # Also update the per-merchant cache
                with self._lock:
                    self._merchant_quests[merchant_id] = merchant_quests
                    self._merchant_chapters[merchant_id] = merchant_chapters

            with self._lock:
                self._captured_quest_log = all_entries
                self._touch()
                self._sync_progress_to_service()

            total_quests = sum(len(e["quests"]) for e in all_entries)
            total_in_progress = sum(
                1 for e in all_entries for q in e.get("quests", [])
                if q.get("quest_flag") == QUEST_FLAG_PROGRESS
            )
            logger.info(
                "Captured full quest log: %d merchants, %d total quests",
                len(all_entries), total_quests,
            )
            self._notify_ui("quest_log_update", {
                "quest_count": total_quests,
                "in_progress": total_in_progress,
            })
            return True
        except Exception as exc:
            logger.error("Error handling quest log packet: %s", exc, exc_info=True)
            return False

    def handle_quest_select(self, message: Any) -> bool:
        """S2C_MERCHANT_QUEST_SELECT_RES — quest acceptance confirmation."""
        try:
            data = _msg_to_dict(message)
            result = _safe_int(data.get("result"), -1)
            logger.info("Quest select result: %d", result)
            # Always notify UI — a quest select response means the player
            # interacted with the quest board and data may have changed.
            self._notify_ui("quest_accepted", {"result": result})
            return True
        except Exception as exc:
            logger.error("Error handling quest select packet: %s", exc, exc_info=True)
            return False

    def handle_quest_complete(self, message: Any) -> bool:
        """S2C_MERCHANT_QUEST_COMPLETE_RES — quest turned in with rewards."""
        try:
            data = _msg_to_dict(message)
            result = _safe_int(data.get("result"), -1)
            merchant_id = _normalize_game_id(data.get("givenMerchantId") or "")
            quest_id = _normalize_game_id(data.get("givenQuestId") or "")
            chapter_id = _normalize_game_id(data.get("givenChapterId") or "")
            rewards_raw = data.get("rewards") or []

            rewards = []
            for r in rewards_raw:
                rewards.append({
                    "reward_type": r.get("rewardType") or "",
                    "stock_id": r.get("stockId") or "",
                    "reward_count": _safe_int(r.get("rewardCount"), 0),
                })

            completion = {
                "result": result,
                "merchant_id": merchant_id,
                "quest_id": quest_id,
                "chapter_id": chapter_id,
                "rewards": rewards,
                "timestamp": time.time(),
            }

            with self._lock:
                self._quest_completions.append(completion)
                # Keep only last 50
                if len(self._quest_completions) > 50:
                    self._quest_completions = self._quest_completions[-50:]

                # Mark quest as complete in our tracked state
                if merchant_id and merchant_id in self._merchant_quests:
                    for q in self._merchant_quests[merchant_id]:
                        if q.get("quest_id") == quest_id:
                            q["quest_flag"] = QUEST_FLAG_COMPLETE
                            q["quest_flag_label"] = "complete"
                            # Mark all missions as fully completed
                            for m in q.get("missions", []):
                                m["completed"] = True
                            break

                self._touch()
                self._sync_progress_to_service()

            logger.info(
                "Quest completed: merchant=%s quest=%s chapter=%s result=%d rewards=%d",
                merchant_id, quest_id, chapter_id, result, len(rewards),
            )
            self._notify_ui("quest_completed", {
                "quest_id": quest_id,
                "merchant_id": merchant_id,
                "reward_count": len(rewards),
            })
            return True
        except Exception as exc:
            logger.error("Error handling quest complete packet: %s", exc, exc_info=True)
            return False

    def handle_quest_content_value_stack(self, message: Any) -> bool:
        """S2C_MERCHANT_QUEST_CONTENT_VALUE_STACK_RES — item turn-in confirmation."""
        try:
            data = _msg_to_dict(message)
            result = _safe_int(data.get("result"), -1)
            logger.info("Quest content value stack result: %d", result)
            # Always notify UI — item submission happened
            self._notify_ui("quest_items_submitted", {"result": result})
            return True
        except Exception as exc:
            logger.error("Error handling quest content value stack packet: %s", exc, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _parse_quest_info(self, raw: dict, merchant_id: str = "") -> Optional[dict]:
        """Parse a SMERCHANT_QUEST_INFO dict into our internal format."""
        quest_id_raw = raw.get("questId") or ""
        if not quest_id_raw:
            return None

        quest_id = _normalize_game_id(quest_id_raw)
        chapter_id = _normalize_game_id(raw.get("chapterId") or "")
        norm_merchant = _normalize_game_id(merchant_id) if merchant_id else ""

        if quest_id != quest_id_raw:
            logger.debug("Normalised quest ID: %s → %s", quest_id_raw, quest_id)

        flag = _safe_int(raw.get("questFlag"), 0)
        missions_raw = raw.get("missions") or []

        missions = []
        for m in missions_raw:
            content_id = _normalize_game_id(m.get("contentId") or "")
            current_value = _safe_int(m.get("contentCurrentValue"), 0)
            missions.append({
                "content_id": content_id,
                "current_value": current_value,
                "completed": False,  # will be derived
            })

        return {
            "merchant_id": norm_merchant,
            "quest_id": quest_id,
            "quest_order": _safe_int(raw.get("questOrder"), 0),
            "chapter_id": chapter_id,
            "quest_flag": flag,
            "quest_flag_label": QUEST_FLAG_LABELS.get(flag, "unknown"),
            "already_get_affinity": _safe_int(raw.get("alreadyGetAffinity"), 0),
            "missions": missions,
        }

    def _touch(self) -> None:
        """Update last-modified timestamp (must hold lock)."""
        self._last_update = time.time()

    def _build_auto_progress(self) -> dict:
        """Build a progress dict from captured packet data.

        Returns a dict shaped like:
        {
            "quests": {
                "<questId>": {
                    "quest_id": str,
                    "merchant_id": str,
                    "chapter_id": str,
                    "quest_flag": int,
                    "quest_flag_label": str,
                    "missions": [
                        {
                            "content_id": str,
                            "current_value": int,
                        }, ...
                    ]
                }, ...
            },
            "completions": [...],
            "merchant_flags": {...},
            "last_update": float,
        }
        """
        all_quests: Dict[str, dict] = {}

        # Prefer the full quest log if available
        if self._captured_quest_log:
            for log_entry in self._captured_quest_log:
                for q in log_entry.get("quests", []):
                    qid = q.get("quest_id")
                    if qid:
                        all_quests[qid] = q
        else:
            # Fallback to per-merchant captures
            for _mid, quests in self._merchant_quests.items():
                for q in quests:
                    qid = q.get("quest_id")
                    if qid:
                        all_quests[qid] = q

        return {
            "quests": all_quests,
            "completions": list(self._quest_completions[-20:]),
            "merchant_flags": dict(self._merchant_flags),
            "last_update": self._last_update,
        }

    def _sync_progress_to_service(self) -> None:
        """Persist captured progress to QuestService disk storage.

        Builds a progress payload in the same shape that quest.js uses
        and writes it via QuestService.save_progress(). This way, even
        if the user hasn't opened the quest page, the data is saved.

        Must be called while holding self._lock.
        """
        try:
            auto_progress = self._build_auto_progress()
            captured_quests = auto_progress.get("quests", {})
            if not captured_quests:
                return

            # Load existing progress to merge
            existing_progress, _ = self._quest_service.load_progress()
            objectives = dict(existing_progress.get("objectives", {}))

            for quest_id, quest_data in captured_quests.items():
                flag = quest_data.get("quest_flag", 0)
                is_complete = flag in (QUEST_FLAG_SUCCESS, QUEST_FLAG_COMPLETE)
                missions = quest_data.get("missions", [])

                for idx, mission in enumerate(missions):
                    content_id = mission.get("content_id", "")
                    current_value = mission.get("current_value", 0)

                    # Build a key that can be matched by the frontend
                    # Format: captured::<questId>::<idx>::<contentId>
                    # The frontend will reconcile these with its own keys
                    key = f"captured::{quest_id}::{idx}::{content_id}"

                    existing = objectives.get(key, {})
                    existing_submitted = 0
                    try:
                        existing_submitted = int(existing.get("submitted", 0))
                    except (TypeError, ValueError):
                        pass

                    # Only update if captured value is higher (never regress)
                    new_submitted = max(existing_submitted, current_value)

                    objectives[key] = {
                        "quest_id": quest_id,
                        "objective_index": idx,
                        "type": "captured",
                        "item_id": content_id,
                        "submitted": new_submitted,
                        "completed": is_complete,
                    }

            merged = {
                "objectives": objectives,
                "items": existing_progress.get("items", {}),
            }
            self._quest_service.save_progress(merged)
        except Exception as exc:
            logger.warning("Failed to sync captured quest progress to service: %s", exc, exc_info=True)

    def _notify_ui(self, event_name: str, detail: Optional[dict] = None) -> None:
        """Notify the UI of a quest-related event if a callback is set."""
        if self._ui_notify:
            try:
                import json as _json
                payload = _json.dumps(detail or {}, ensure_ascii=True)
                self._ui_notify(event_name, payload)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _msg_to_dict(message: Any) -> dict:
    """Convert a protobuf message to dict preserving field names."""
    try:
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
            including_default_value_fields=True,
        )
    except TypeError:
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
        )


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely cast a value to int."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
