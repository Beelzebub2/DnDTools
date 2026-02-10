"""
Backward-compatibility shim.

The old SortLearningTrainer is replaced by SortSyncService.
This module provides a thin adapter so existing app.py wiring keeps working.
"""

import logging
from typing import Optional

from src.models.sort_sync_service import SortSyncService


class SortLearningTrainer:
    """
    Adapter that wraps SortSyncService with the old SortLearningTrainer API.
    
    app.py creates one of these; we delegate to the unified sync service.
    """

    def __init__(
        self,
        *,
        settings_manager,
        app_version: str,
        learning_manager=None,
        base_url: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._sync = SortSyncService(
            settings_manager=settings_manager,
            app_version=app_version,
            base_url=base_url,
        )

    def start(self) -> None:
        self._sync.start()

    def stop(self) -> None:
        self._sync.stop()


def start_sort_learning_trainer(
    *,
    settings_manager,
    app_version: str,
    base_url: Optional[str] = None,
) -> Optional[SortLearningTrainer]:
    trainer = SortLearningTrainer(
        settings_manager=settings_manager,
        app_version=app_version,
        base_url=base_url,
    )
    trainer.start()
    return trainer
