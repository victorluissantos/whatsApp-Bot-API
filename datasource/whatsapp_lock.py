"""Lock compartilhado para serializar mutações no WhatsApp Web (Selenium)."""
from __future__ import annotations

import threading

send_lock = threading.Lock()
