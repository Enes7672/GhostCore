"""GhostCore production orchestrator."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from main import main as ghostcore_main

logger = logging.getLogger("ghostcore.orchestrator")

# Crash-loop eşikleri
_CRASH_WINDOW_SECS: int = 30   # Bu süre içinde...
_CRASH_LIMIT:       int = 3    # ...bu kadar crash olursa Isolated Mode devreye girer


class ProductionOrchestrator:
    """Single entry for production startup/restart."""

    def __init__(self) -> None:
        # Son N saniyedeki crash timestamp'leri
        self._crash_times: deque[float] = deque()

    def _record_crash(self) -> None:
        """Crash anını kaydet; eski kayıtları temizle."""
        now = time.monotonic()
        self._crash_times.append(now)
        # Pencere dışındaki eski crash'leri çıkar
        while self._crash_times and (now - self._crash_times[0]) > _CRASH_WINDOW_SECS:
            self._crash_times.popleft()

    def _is_crash_loop(self) -> bool:
        """Son CRASH_WINDOW_SECS saniyede CRASH_LIMIT kadar mı düştük?"""
        return len(self._crash_times) >= _CRASH_LIMIT

    def _enter_isolated_mode(self, last_error: Exception) -> None:
        """
        Crash loop tespit edildiğinde sistemi güvenli şekilde durdurur.

        Isolated Mode:
            • Sonsuz yeniden başlatma döngüsünü keser.
            • Data Maestro veya Sentinel gibi bağımsız modüller hâlâ kullanılabilir
              (ancak bu orchestrator katmanından değil, doğrudan import ile).
            • Kullanıcı hatayı görür ve müdahale edebilir.
        """
        logger.critical(
            "\n"
            "═══════════════════════════════════════════════════════════════\n"
            "  ⛔ GHOSTCORE — ISOLATED MODE (CRASH LOOP TESPİT EDİLDİ)\n"
            "═══════════════════════════════════════════════════════════════\n"
            "  Son %d saniyede %d crash gerçekleşti.\n"
            "  Sonsuz döngüyü önlemek için sistem durduruldu.\n\n"
            "  Son hata: %s\n\n"
            "  Çözüm önerileri:\n"
            "    1. Hata mesajını inceleyin (yukarıda).\n"
            "    2. Ollama servisinin çalışır durumda olduğunu doğrulayın.\n"
            "    3. .env dosyasındaki ayarları kontrol edin.\n"
            "    4. python main.py --debug ile tekrar deneyin.\n"
            "═══════════════════════════════════════════════════════════════",
            _CRASH_WINDOW_SECS,
            len(self._crash_times),
            last_error,
        )

    def run(self) -> None:
        last_error: Exception | None = None
        while True:
            try:
                asyncio.run(ghostcore_main())
                break   # Temiz çıkış (exit komutu vs.)
            except KeyboardInterrupt:
                logger.info("Kullanıcı çıkışı — GhostCore kapatıldı.")
                break
            except Exception as e:
                last_error = e
                self._record_crash()
                logger.exception("Runtime crash: %s", e)

                if self._is_crash_loop():
                    self._enter_isolated_mode(e)
                    break   # Sonsuz döngüye girme

                logger.warning(
                    "Crash #%d — %d saniye içinde %d daha olursa Isolated Mode devreye girer. "
                    "Yeniden başlatılıyor...",
                    len(self._crash_times),
                    _CRASH_WINDOW_SECS,
                    _CRASH_LIMIT - len(self._crash_times),
                )
                # Kısa bekleme — çok hızlı yeniden başlatmayı önle
                time.sleep(2)


def run() -> None:
    ProductionOrchestrator().run()
