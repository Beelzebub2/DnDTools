"""
Backward-compatibility shim.

The old SortFeedbackSyncService is replaced by SortSyncService.
This module re-exports the new service under the old name so any
remaining imports keep working.
"""

from src.models.sort_sync_service import SortSyncService as SortFeedbackSyncService

__all__ = ["SortFeedbackSyncService"]
