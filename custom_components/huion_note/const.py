"""Constants for the Huion Note X10 integration."""
from __future__ import annotations

DOMAIN = "huion_note"

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
DATA_CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"  # page data notifications
CMD_CHAR_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"   # command write + indications

CONF_PIN = "pin"
CONF_DELETE_AFTER_SYNC = "delete_after_sync"
CONF_COOLDOWN = "cooldown"
CONF_OUTPUT_DIR = "output_dir"

DEFAULT_COOLDOWN = 5  # minutes between automatic syncs after a successful one
FAILURE_BACKOFF = 60  # seconds before an automatic retry after a failed sync
DEFAULT_SUBDIR = "huion_notes"

EVENT_PAGE_SAVED = f"{DOMAIN}_page_saved"
EVENT_SYNC_FINISHED = f"{DOMAIN}_sync_finished"

SERVICE_SYNC = "sync"

STATUS_IDLE = "idle"
STATUS_SYNCING = "syncing"
STATUS_ERROR = "error"
