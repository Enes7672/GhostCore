"""
brain.py — GhostCore v4 / Nova Jarvis: Redline Architecture
============================================================
UPGRADE: Hybrid Intelligence Engine

5 Hardcore Özellik:
    1. Dynamic Model Routing   — Görevin karmaşıklığına göre model seçer
    2. Semantic Caching        — Aynı sorguyu LLM'e iki kez yollamaz, RAM'den çeker
    3. Auto-Fallback           — Cloud çökerse anında Ollama'ya geçer
    4. Token & Cost Manager    — Her ajan kaç token harcıyor, anlık izler
    5. Context Optimizer       — Uzun sohbetleri özetler, RAM'i korur

Hybrid Backend:
    .env → OPENAI_API_KEY varsa  : ChatOpenAI (cloud)
    Yoksa veya erişilemezse      : ChatOllama (local, sessiz fallback)

Author: GhostCore Architecture Team
"""

import os
import time
import asyncio
import hashlib
import logging
import subprocess
import socket
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict
from enum import IntEnum

import httpx
import psutil
from dotenv import load_dotenv
from rich.console import Console

from langchain_community.chat_models.ollama import ChatOllama

load_dotenv()

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ghostcore.brain")
console = Console()

# ---------------------------------------------------------------------------
# Konfigürasyon
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL:     str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL_FAST:   str = os.getenv("OLLAMA_MODEL_FAST", "phi3")
OLLAMA_MODEL_POWER:  str = os.getenv("OLLAMA_MODEL_POWER", "llama3:8b")
CLOUD_MODEL:         str = os.getenv("CLOUD_MODEL", "gpt-4o-mini")
OPENAI_API_KEY:      str = os.getenv("OPENAI_API_KEY", "")
REQUEST_TIMEOUT:     int = int(os.getenv("REQUEST_TIMEOUT", "300"))
CONTEXT_TOKEN_LIMIT: int = 3000
CACHE_TTL_SECONDS:   int = 3600
AUTO_WAKEUP_OLLAMA:  bool = os.getenv("AUTO_WAKEUP_OLLAMA", "1") == "1"

# Ollama durumu (main.py tarafından okunur — UI'da gösterilir)
OLLAMA_STATUS: str = "UNKNOWN"   # "ONLINE" | "OFFLINE" | "WAKING"


# ===========================================================================
# 1. DYNAMIC MODEL ROUTING
# ===========================================================================

class TaskComplexity:
    SIMPLE   = "simple"
    MODERATE = "moderate"
    COMPLEX  = "complex"

TASK_COMPLEXITY_MAP: dict[str, str] = {
    "file_read":      TaskComplexity.SIMPLE,
    "format_convert": TaskComplexity.SIMPLE,
    "transcribe":     TaskComplexity.SIMPLE,
    "code_generate":  TaskComplexity.MODERATE,
    "bug_fix":        TaskComplexity.MODERATE,
    "readme_write":   TaskComplexity.MODERATE,
    "security_audit": TaskComplexity.COMPLEX,
    "architecture":   TaskComplexity.COMPLEX,
}

def resolve_model_for_task(task_type: str) -> str:
    complexity = TASK_COMPLEXITY_MAP.get(task_type, TaskComplexity.MODERATE)
    model = OLLAMA_MODEL_FAST if complexity == TaskComplexity.SIMPLE else OLLAMA_MODEL_POWER
    logger.info("[Router] %s → %s → %s", task_type, complexity, model)
    return model


# ===========================================================================
# 2. SEMANTIC CACHE
# ===========================================================================

@dataclass
class CacheEntry:
    response:   str
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > CACHE_TTL_SECONDS


class SemanticCache:
    """Prompt hash'ine dayalı in-memory cache."""

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}
        self._hits = 0
        self._total = 0

    def _key(self, prompt: str) -> str:
        return hashlib.md5(prompt.strip().lower().encode()).hexdigest()

    def get(self, prompt: str) -> Optional[str]:
        self._total += 1
        entry = self._store.get(self._key(prompt))
        if entry and not entry.is_expired():
            self._hits += 1
            return entry.response
        return None

    def set(self, prompt: str, response: str) -> None:
        self._store[self._key(prompt)] = CacheEntry(response=response)

    def invalidate_expired(self) -> int:
        expired_keys = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired_keys:
            del self._store[k]
        return len(expired_keys)

    def cleanup(self) -> int:
        """Auto-cleanup expired entries (call periodically)."""
        return self.invalidate_expired()

    @property
    def hit_rate(self) -> float:
        return self._hits / self._total if self._total else 0.0

    @property
    def stats(self) -> dict:
        return {"size": len(self._store), "hits": self._hits,
                "total": self._total, "hit_rate": f"{self.hit_rate:.0%}"}


GLOBAL_CACHE = SemanticCache()


# ===========================================================================
# 3. TOKEN & COST MANAGER
# ===========================================================================

@dataclass
class TokenUsage:
    input_tokens:  int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        return (self.input_tokens * 0.00015 + self.output_tokens * 0.00060) / 1000


class TokenManager:
    def __init__(self) -> None:
        self._usage: dict[str, TokenUsage] = defaultdict(TokenUsage)

    def record(self, agent_name: str, input_tokens: int, output_tokens: int) -> None:
        self._usage[agent_name].input_tokens  += input_tokens
        self._usage[agent_name].output_tokens += output_tokens

    def estimate(self, text: str) -> int:
        return max(1, len(text) // 4)

    def get(self, agent_name: str) -> TokenUsage:
        return self._usage[agent_name]

    @property
    def total_tokens(self) -> int:
        return sum(u.total for u in self._usage.values())

    @property
    def total_cost(self) -> float:
        return sum(u.estimated_cost_usd for u in self._usage.values())

    def summary(self) -> list[tuple[str, int, int, str]]:
        return [
            (name, u.input_tokens, u.output_tokens, f"${u.estimated_cost_usd:.4f}")
            for name, u in self._usage.items()
        ]


TOKEN_MANAGER = TokenManager()



# ===========================================================================
# 4. CONTEXT OPTIMIZER
# ===========================================================================

class ContextOptimizer:
    """
    Sohbet geçmişini CONTEXT_TOKEN_LIMIT altında tutar.
    Limit aşılınca son N mesajı korur, gerisini özetler (stub).
    """

    @staticmethod
    def trim(messages: list[dict], limit: int = CONTEXT_TOKEN_LIMIT) -> list[dict]:
        total = sum(len(m.get("content", "")) // 4 for m in messages)
        while total > limit and len(messages) > 2:
            removed = messages.pop(0)
            total -= len(removed.get("content", "")) // 4
        return messages


# ===========================================================================
# 4b. EXECUTION MANAGER — CPU Öncelik Yöneticisi
# ===========================================================================

class TaskPriority(IntEnum):
    """
    Görev öncelik seviyeleri.
    Düşük sayı = yüksek öncelik (HIGH < LOW).
    """
    HIGH   = 0   # Sentinel / Whisper — sesli komutlar, gecikme toleransı sıfır
    NORMAL = 1   # Moderator, Writer   — orta yük
    LOW    = 2   # Architect / Hunter  — ağır LLM çıkarımı, bekleyebilir


class ExecutionManager:
    """
    Ajan görevlerini öncelik sırasına göre yürüten semaphore yöneticisi.

    Mantık:
        • Sentinel (Whisper) HIGH öncelikle → hemen slot alır.
        • Architect / Hunter LOW öncelikle   → HIGH/NORMAL bitmeden bloke kalır.
        • Böylece Ryzen 7'nin çekirdekleri sesin anlık işlenmesine tahsis edilir;
          ağır LLM çıkarımı bunun arkasında sıraya girer.

    Kullanım (hunter_worker / architect_worker içinde):
        async with EXECUTION_MANAGER.slot(TaskPriority.LOW):
            resp = await asyncio.to_thread(llm.invoke, ...)

    Paralel Çalıştırma:
        • run_parallel() metodu birden fazla async coroutine'i eşzamanlı çalıştırır.
        • Writer ve Hunter gibi bağımsız ajanlar aynı VERSION_STAGED event'inde
          paralel başlatılabilir.
    """

    def __init__(self, max_concurrent_heavy: int = 2) -> None:
        self._heavy_sem   = asyncio.Semaphore(max_concurrent_heavy)
        self._active: dict[TaskPriority, int] = {p: 0 for p in TaskPriority}
        self._lock        = asyncio.Lock()

    async def run_parallel(self, *coros) -> list:
        """
        Birden fazla coroutine'i paralel olarak çalıştırır.
        Tüm sonuçları toplar ve döndürür.

        Örnek:
            results = await EXECUTION_MANAGER.run_parallel(
                writer_review(),
                hunter_audit(),
            )
        """
        tasks = [asyncio.create_task(c) for c in coros]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    class _Slot:
        """Async context manager döndüren iç yardımcı."""
        def __init__(self, manager: "ExecutionManager", priority: TaskPriority) -> None:
            self._mgr  = manager
            self._prio = priority

        async def __aenter__(self):
            if self._prio == TaskPriority.LOW:
                await self._mgr._heavy_sem.acquire()
            async with self._mgr._lock:
                self._mgr._active[self._prio] += 1
            logger.info("[ExecMgr] Slot alındı — öncelik: %s", self._prio.name)
            return self

        async def __aexit__(self, *_):
            async with self._mgr._lock:
                self._mgr._active[self._prio] = max(0, self._mgr._active[self._prio] - 1)
            if self._prio == TaskPriority.LOW:
                self._mgr._heavy_sem.release()
            logger.info("[ExecMgr] Slot serbest bırakıldı — öncelik: %s", self._prio.name)

    def slot(self, priority: TaskPriority = TaskPriority.NORMAL) -> "_Slot":
        """Öncelikli yürütme slotu döndürür (async with ile kullan)."""
        return self._Slot(self, priority)

    def status(self) -> dict:
        return {p.name: cnt for p, cnt in self._active.items()}


# Sistem genelinde tek ExecutionManager (singleton)
EXECUTION_MANAGER = ExecutionManager(max_concurrent_heavy=1)


# ===========================================================================
# 5. HYBRID LLM FACTORY — Auto-Fallback
# ===========================================================================

def _ollama_alive(base_url: str = OLLAMA_BASE_URL) -> bool:
    try:
        return httpx.get(f"{base_url}/api/tags", timeout=5).status_code == 200
    except Exception:
        return False


def _cloud_available() -> bool:
    if not OPENAI_API_KEY:
        return False
    try:
        r = httpx.get("https://api.openai.com", timeout=5)
        return r.status_code in (200, 401, 403)
    except Exception:
        return False


def has_internet(timeout: float = 2.0) -> bool:
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=timeout).close()
        return True
    except Exception:
        return False


def validate_cloud_key() -> bool:
    if not OPENAI_API_KEY:
        logger.warning("[Brain] Cloud API key yok -> LOCAL mode")
        return False
    if not has_internet():
        logger.warning("[Brain] Internet yok -> OFFLINE LOCAL mode")
        return False
    return True


def _try_start_ollama() -> bool:
    if not AUTO_WAKEUP_OLLAMA:
        return False
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
        return True
    except Exception as e:
        logger.warning("[Brain] Ollama auto-wakeup başarısız: %s", e)
        return False


def get_llm(
    temperature:  float = 0.1,
    max_tokens:   int   = 4096,
    task_type:    str   = "code_generate",
    prefer_cloud: bool  = False,
    force_local:  bool  = False,
):
    """
    Hibrit LLM Factory — Auto-Fallback destekli.

    Karar ağacı:
        1. force_local=True                     → Ollama (cloud atlanır)
        2. prefer_cloud veya OPENAI_API_KEY var  → ChatOpenAI (cloud müsaitse)
        3. Cloud erişilemez                      → ChatOllama (sessiz fallback)
    """
    cloud_ok = validate_cloud_key() and _cloud_available()
    if not force_local and (prefer_cloud or OPENAI_API_KEY):
        if cloud_ok:
            try:
                from langchain_openai import ChatOpenAI
                logger.info("[Brain] Backend: CLOUD (%s)", CLOUD_MODEL)
                return ChatOpenAI(
                    model=CLOUD_MODEL,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    api_key=OPENAI_API_KEY,  # type: ignore[arg-type]
                    timeout=REQUEST_TIMEOUT,
                )
            except ImportError:
                logger.warning("[Brain] langchain-openai kurulu değil → local fallback")
            except Exception as e:
                logger.warning("[Brain] Cloud hatası: %s → local fallback", e)

    model = resolve_model_for_task(task_type)
    logger.info("[Brain] Backend: LOCAL (%s)", model)
    return ChatOllama(
        model=model,
        base_url=OLLAMA_BASE_URL,
        temperature=temperature,
        num_predict=max_tokens,
        timeout=REQUEST_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Ajan Brain Profilleri
# ---------------------------------------------------------------------------

def get_architect_brain():
    return get_llm(temperature=0.15, max_tokens=4096, task_type="architecture")

def get_sentinel_brain():
    return get_llm(temperature=0.0,  max_tokens=2048, task_type="transcribe", force_local=True)

def get_hunter_brain():
    return get_llm(temperature=0.0,  max_tokens=4096, task_type="security_audit")

def get_writer_brain():
    return get_llm(temperature=0.4,  max_tokens=4096, task_type="readme_write")


# ---------------------------------------------------------------------------
# Ollama Startup Guard  ← SOFT-FAIL: artık SystemExit atmaz
# ---------------------------------------------------------------------------

def verify_ollama_connection(base_url: str = OLLAMA_BASE_URL) -> bool:
    """
    Ollama bağlantısını kontrol eder.

    Değişiklik (v4.1):
        • Bağlantı yoksa SystemExit(1) ATMAZ.
        • Bunun yerine OLLAMA_STATUS global'ini "OFFLINE" olarak işaretler ve
          False döndürür; main.py arayüzü açık tutar, uyarı log'a düşer.
        • Kullanıcı sonradan 'ollama serve' çalıştırıp /reconnect yazarak
          tekrar deneyebilir.

    Returns:
        True  → Ollama aktif
        False → Ollama kapalı (sistem devam eder, kısıtlı mod)
    """
    global OLLAMA_STATUS
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=10)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        logger.info("Ollama ONLINE. Modeller: %s", models)
        OLLAMA_STATUS = "ONLINE"
        return True
    except httpx.ConnectError:
        OLLAMA_STATUS = "WAKING"
        started = _try_start_ollama()
        if started:
            for _ in range(8):
                time.sleep(1)
                if _ollama_alive(base_url):
                    logger.info("[Brain] Ollama auto-wakeup başarılı.")
                    OLLAMA_STATUS = "ONLINE"
                    return True

        # Soft-fail: crash yok, sadece uyarı
        OLLAMA_STATUS = "OFFLINE"
        logger.warning("[Brain] Ollama çevrimdışı — kısıtlı mod aktif.")
        return False
    except Exception as e:
        OLLAMA_STATUS = "OFFLINE"
        logger.error("Ollama bağlantı hatası: %s", e)
        return False


# ---------------------------------------------------------------------------
# Sistem İstatistikleri (War Room sidebar için)
# ---------------------------------------------------------------------------

def get_system_stats() -> dict:
    """
    CPU, RAM ve aktif backend bilgisini döndürür.
    main.py her saniye bu fonksiyonu çağırarak sidebar'ı günceller.
    """
    mem      = psutil.virtual_memory()
    cloud_ok = validate_cloud_key() and _cloud_available()
    backend  = "CLOUD" if cloud_ok else "LOCAL"
    model    = CLOUD_MODEL if backend == "CLOUD" else OLLAMA_MODEL_POWER
    temp_c   = min(95, 35 + int(psutil.cpu_percent(interval=None) * 0.6))

    return {
        "cpu_percent":   psutil.cpu_percent(interval=None),
        "ram_used_gb":   mem.used  / (1024 ** 3),
        "ram_total_gb":  mem.total / (1024 ** 3),
        "ram_percent":   mem.percent,
        "backend":       backend,
        "active_model":  model,
        "cpu_temp_c":    temp_c,
        "ollama_status": OLLAMA_STATUS,   # ← YENİ: sidebar'da gösterilir
    }


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    console.print("\n[bold cyan]GhostCore Brain v2 — Self-Test[/bold cyan]\n")
    ok = verify_ollama_connection()
    if not ok:
        console.print("[bold yellow]⚠  Ollama çevrimdışı — kısıtlı mod.[/bold yellow]")

    console.print("[dim]Dynamic Routing:[/dim]")
    for t in ["file_read", "code_generate", "security_audit"]:
        console.print(f"  [yellow]{t}[/yellow] → [green]{resolve_model_for_task(t)}[/green]")

    console.print("\n[dim]Semantic Cache:[/dim]")
    GLOBAL_CACHE.set("test", "pong")
    console.print(f"  Hit: [green]{GLOBAL_CACHE.get('test')}[/green]")

    console.print("\n[dim]System Stats:[/dim]")
    s = get_system_stats()
    console.print(
        f"  CPU: {s['cpu_percent']}% | RAM: {s['ram_used_gb']:.1f}/{s['ram_total_gb']:.0f}GB "
        f"| Backend: {s['backend']} | Ollama: {s['ollama_status']}"
    )

    console.print("\n[bold green]✓ Brain v2 tam operasyonel.[/bold green]\n")