"""
main.py — GhostCore v4 / Nova Jarvis: Redline Architecture
===========================================================
GhostCore'un kalbi: War Room Terminal Arayüzü.

Ekran Düzeni (Rich Layout):
    ┌─────────────────────────────────────────────────────────────┐
    │  GhostCore v4 — System Online | CPU: XX% | RAM: X.X/20GB   │  ← Üst Bar
    ├──────────────────────────────────┬──────────────────────────┤
    │                                  │  [SİSTEM DURUMU]         │
    │   [İŞLEM GÜNLÜĞÜ]                │  Backend: LOCAL/CLOUD    │
    │   [Mimar]: Plan hazır.           │  Model: llama3:8b        │
    │   [Avcı]:  Reddedildi. Sat42...  │  Token: 1,234            │
    │   [Yazar]: README oluşturuluyor  │  Maliyet: $0.0012        │
    │                                  │  Cache: 3 hit / %60      │
    ├──────────────────────────────────┴──────────────────────────┤
    │  > Ghost, emrini buraya yaz...                              │  ← Alt Komut Satırı
    └─────────────────────────────────────────────────────────────┘

Async Mimari:
    - asyncio event loop ile ajanlar düşünürken arayüz donmaz
    - Ajan yanıtları asyncio.Queue üzerinden log paneline akar
    - Sistem istatistikleri ayrı bir async task ile her saniye güncellenir

Değişiklikler (v4.1):
    • run.py kaldırıldı — tek giriş noktası: python main.py
    • ensure_vision_module() ve tüm vision referansları temizlendi
    • verify_ollama_connection() artık SystemExit atmaz (soft-fail)
      → Ollama kapalıysa sistem "STANDBY" modunda açık kalır
      → /reconnect komutuyla yeniden bağlantı denenebilir
    • Sidebar'a canlı CPU sparkline ve Ollama durum göstergesi eklendi
    • Bağlantı bekleme ekranı (STANDBY banner) eklendi

Author: GhostCore Architecture Team
"""

import asyncio
import sys
import time
import os
import io

# Windows ortamında cp1254/cp1252 kaynaklı UnicodeEncodeError (✓ vb.) hatalarını önlemek için:
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[union-attr]
    except Exception:
        pass

import importlib
import subprocess
from collections import deque
from datetime import datetime
from typing import Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt
from rich.columns import Columns
from rich import box
from rich.progress_bar import ProgressBar
from rich.align import Align
from rich.rule import Rule

from ghost_core import GhostCore
from ghost_core.brain import (
    get_system_stats,
    TOKEN_MANAGER,
    GLOBAL_CACHE,
    verify_ollama_connection,
    get_llm,
    has_internet,
)
from ghost_core.agents import ENHANCED_UTILITIES, SESSION_MEMORY, SessionState
from ghost_core.core_logic.event_bus import EVENT_BUS
from ghost_core.core_logic.mission_control import MISSION_CONTROL

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

console = Console()

LOG_QUEUE: asyncio.Queue = asyncio.Queue()
CONVERSATION_HISTORY: list[dict] = []
START_TIME = time.time()

# Agent inter-message buffer (broadcast-style)
AGENT_MESSAGES: dict[str, list[dict]] = {
    "architect": [],
    "hunter": [],
    "writer": [],
    "maestro": [],
    "designer": [],
}
AGENT_MSG_LOCK = asyncio.Lock()

ACTIVE_AGENT: str = "—"
VERBOSE_MODE: bool = True
SILENT_MODE: bool = False

# GhostCore Engine Instance
GC = GhostCore()
SESSION_STATE: SessionState = GC.session_state
TASK_QUEUE: asyncio.Queue = asyncio.Queue()
LAST_GENERATED_HTML: str = "preview.html"

# Canlı CPU geçmişi (sparkline için son 60 örnek)
CPU_HISTORY: deque = deque([0.0] * 20, maxlen=60)

# ---------------------------------------------------------------------------
# Renk Paleti & Stil Sabitleri
# ---------------------------------------------------------------------------

COLORS = {
    "architect": "bold cyan",
    "sentinel":  "bold yellow",
    "hunter":    "bold red",
    "writer":    "bold green",
    "maestro":   "bold green",
    "designer":  "bold magenta",
    "system":    "bold white",
    "warning":   "bold orange3",
    "error":     "bold red",
    "success":   "bold green",
    "dim":       "dim white",
}

AGENT_PREFIX = {
    "architect": "⚡ [Mimar]",
    "sentinel":  "👂 [Sentinel]",
    "hunter":    "🔍 [Avcı]",
    "writer":    "📝 [Yazar]",
    "maestro":   "📊 [Maestro]",
    "designer":  "🎨 [Designer]",
    "system":    "⚙  [Sistem]",
    "ghost":     "👻 [Ghost]",
}

# Sparkline karakterleri (düşük → yüksek)
SPARK_CHARS = " ▁▂▃▄▅▆▇█"


# ===========================================================================
# Startup Helpers
# ===========================================================================

def _safe_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception as exc:
        # Hata anında LOG_QUEUE henüz başlatılmamış olabilir; put_nowait ile güvenli ekle
        try:
            LOG_QUEUE.put_nowait(
                f"[dim]--:--:--[/dim]  [bold red]⚙  [Sistem][/bold red]  "
                f"Eksik modül: [bold]{module_name}[/bold] — {exc}"
            )
        except Exception:
            pass
        return False


def auto_install_missing_dependencies() -> None:
    required = ["pandas", "openpyxl", "psutil", "rich", "httpx"]
    missing = [m for m in required if not _safe_import(m)]
    if not missing:
        return
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", *missing],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        pass


async def cinematic_boot_sequence() -> None:
    logo = r"""
   ____ _               _    ____
  / ___| |__   ___  ___| |_ / ___|___  _ __ ___
 | |  _| '_ \ / _ \/ __| __| |   / _ \| '__/ _ \
 | |_| | | | | (_) \__ \ |_| |__| (_) | | |  __|
  \____|_| |_|\___/|___/\__|\____\___/|_|  \___|
"""
    console.print(f"[bold cyan]{logo}[/bold cyan]")
    for step in ("Booting agents", "Initializing war room", "System Initialized"):
        console.print(f"[dim]>> {step}...[/dim]")
        await asyncio.sleep(0.25)


def auto_git_commit(message: str) -> None:
    if not os.path.isdir(".git"):
        return
    try:
        subprocess.run(["git", "add", "."], check=False, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], check=False, capture_output=True)
    except Exception:
        pass


# ===========================================================================
# Sparkline Oluşturucu
# ===========================================================================

def _sparkline(values, width: int = 20) -> str:
    """CPU_HISTORY listesinden veya deque'dan tek satır sparkline üretir."""
    vals = list(values)          # deque[-n:] desteklemez; önce list'e çevir
    if not vals:
        return " " * width
    window = vals[-width:]
    max_v  = max(window) or 1.0
    return "".join(
        SPARK_CHARS[int((v / max_v) * (len(SPARK_CHARS) - 1))]
        for v in window
    )


# ===========================================================================
# War Room Layout Oluşturucu
# ===========================================================================

def build_header(stats: dict) -> Panel:
    uptime_secs = int(time.time() - START_TIME)
    uptime_str  = f"{uptime_secs // 3600:02d}:{(uptime_secs % 3600) // 60:02d}:{uptime_secs % 60:02d}"

    ram_color = (
        "bold green"  if stats["ram_percent"] < 60 else
        "bold yellow" if stats["ram_percent"] < 80 else
        "bold red"
    )
    cpu_color = (
        "bold green"  if stats["cpu_percent"] < 50 else
        "bold yellow" if stats["cpu_percent"] < 80 else
        "bold red"
    )
    backend_color = "bold cyan" if stats["backend"] == "LOCAL" else "bold magenta"

    # Ollama durum rozeti
    ollama_st = stats.get("ollama_status", "UNKNOWN")
    ollama_badge = {
        "ONLINE":  "[bold green]● ONLINE[/bold green]",
        "OFFLINE": "[bold red]● OFFLINE[/bold red]",
        "WAKING":  "[bold yellow]◌ WAKING[/bold yellow]",
    }.get(ollama_st, "[dim]? UNKNOWN[/dim]")

    header_text = Text()
    header_text.append("👻 GhostCore v4", style="bold white")
    header_text.append("  —  ", style="dim white")
    header_text.append("SYSTEM ONLINE", style="bold green")
    header_text.append("   │   CPU: ", style="dim white")
    header_text.append(f"{stats['cpu_percent']:.0f}%", style=cpu_color)
    header_text.append("   │   RAM: ", style="dim white")
    header_text.append(
        f"{stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.0f}GB",
        style=ram_color,
    )
    header_text.append("   │   Backend: ", style="dim white")
    header_text.append(stats["backend"], style=backend_color)
    header_text.append("   │   Uptime: ", style="dim white")
    header_text.append(uptime_str, style="bold white")
    header_text.append("   │   Ollama: ", style="dim white")
    ollama_style = {"ONLINE": "bold green", "OFFLINE": "bold red", "WAKING": "bold yellow"}.get(ollama_st, "dim")
    header_text.append(ollama_st, style=ollama_style)

    return Panel(header_text, style="on grey11", border_style="bright_black")


def build_log_panel(log_lines: list[str]) -> Panel:
    visible = log_lines[-30:]
    text = Text()
    for line in visible:
        text.append(line + "\n")

    return Panel(
        text,
        title="[bold cyan]◈ İŞLEM GÜNLÜĞÜ[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    )


def build_status_panel(stats: dict) -> Panel:
    """
    Sağ panel — Canlı dashboard:
      • Ollama durum rozeti
      • CPU sparkline (son 60 ölçüm)
      • RAM ve CPU progress bar
      • Backend / Model / Aktif Ajan
      • Token tablosu
      • Cache istatistikleri
    """

    # --- Ollama durum satırı ---
    ollama_st = stats.get("ollama_status", "UNKNOWN")
    ollama_color = {"ONLINE": "bold green", "OFFLINE": "bold red", "WAKING": "bold yellow"}.get(ollama_st, "dim")
    ollama_icon  = {"ONLINE": "●", "OFFLINE": "✗", "WAKING": "◌"}.get(ollama_st, "?")

    status_lines = Text()

    status_lines.append("LLM DURUMU\n", style="bold dim")
    status_lines.append(f"  {ollama_icon} Ollama: {ollama_st}\n\n", style=ollama_color)

    if ollama_st == "OFFLINE":
        status_lines.append("  ⚠ Kısıtlı mod — /reconnect\n\n", style="bold yellow")

    # --- Backend / Model ---
    backend_color = "cyan" if stats["backend"] == "LOCAL" else "magenta"
    status_lines.append("MODEL\n",   style="bold dim")
    status_lines.append(f"  {stats['active_model']}\n\n", style=f"bold {backend_color}")
    status_lines.append("BACKEND\n", style="bold dim")
    status_lines.append(f"  {stats['backend']}\n\n",      style=f"bold {backend_color}")

    # --- Aktif ajan ---
    status_lines.append("AKTİF AJAN\n", style="bold dim")
    status_lines.append(f"  {ACTIVE_AGENT}\n\n",          style="bold yellow")

    # --- Mode ---
    status_lines.append("MODE\n",  style="bold dim")
    status_lines.append(f"  {'SILENT' if SILENT_MODE else 'VERBOSE'}\n\n", style="bold white")

    # --- CPU sparkline ---
    spark = _sparkline(CPU_HISTORY, width=20)
    status_lines.append("CPU AKTİVİTESİ\n", style="bold dim")
    status_lines.append(f"  {spark}\n", style="bold cyan")
    status_lines.append(f"  {stats['cpu_percent']:.0f}%\n\n", style="bold cyan")

    # --- Sıcaklık ---
    temp_c   = stats.get("cpu_temp_c", 0)
    temp_pct = min(100, int(temp_c))
    status_lines.append("CPU SICAKLIK (temsili)\n", style="bold dim")
    status_lines.append(f"  {temp_c}°C\n", style="bold yellow")

    # --- RAM ---
    status_lines.append("RAM DOLULUK\n", style="bold dim")
    status_lines.append(f"  {stats['ram_percent']:.0f}%\n\n", style="bold yellow")

    # --- Cache ---
    status_lines.append("CACHE\n", style="bold dim")
    status_lines.append(
        f"  {GLOBAL_CACHE.stats['hits']} hit / {GLOBAL_CACHE.stats['hit_rate']}\n\n",
        style="bold green",
    )

    # --- Token kullanım ---
    status_lines.append("TOKEN\n", style="bold dim")
    if stats["backend"] == "CLOUD":
        status_lines.append(f"  Harcanan: ${TOKEN_MANAGER.total_cost:.4f}\n\n", style="bold red")
    else:
        status_lines.append(f"  Savings: ${TOKEN_MANAGER.total_cost:.4f}\n\n", style="bold green")

    # --- Token tablosu ---
    token_table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )
    token_table.add_column("Ajan",    style="cyan",   width=12)
    token_table.add_column("Token",   style="yellow", width=8,  justify="right")
    token_table.add_column("Maliyet", style="green",  width=10, justify="right")

    for name, inp, out, cost in TOKEN_MANAGER.summary():
        token_table.add_row(name.capitalize(), str(inp + out), cost)

    if not TOKEN_MANAGER.summary():
        token_table.add_row("[dim]—[/dim]", "[dim]—[/dim]", "[dim]—[/dim]")

    content = Group(
        status_lines,
        ProgressBar(total=100, completed=temp_pct,                 width=28),  # CPU sıcaklık
        ProgressBar(total=100, completed=int(stats["ram_percent"]), width=28),  # RAM
        token_table,
    )

    return Panel(
        content,
        title="[bold magenta]◈ DURUM[/bold magenta]",
        border_style="magenta",
        padding=(0, 1),
    )


def build_standby_panel() -> Panel:
    """
    Ollama kapalıyken gösterilen tam ekran uyarı paneli.
    Sistem kilitlenmez; kullanıcı /reconnect veya exit yazabilir.
    """
    msg = Text(justify="center")
    msg.append("\n\n")
    msg.append("⚠  LOKAL LLM BULUNAMADI\n\n", style="bold yellow")
    msg.append("GhostCore STANDBY modunda çalışıyor.\n", style="dim white")
    msg.append("AI görevleri Ollama olmadan çalışmaz;\n", style="dim white")
    msg.append("ancak arayüz açık kalır ve komutları kabul eder.\n\n", style="dim white")
    msg.append("Çözüm:\n",          style="bold white")
    msg.append("  1. Yeni terminalde → ", style="white")
    msg.append("ollama serve\n",    style="bold green")
    msg.append("  2. Burada → ",    style="white")
    msg.append("/reconnect\n\n",    style="bold cyan")
    msg.append("Çıkmak için → ",    style="dim white")
    msg.append("exit\n",            style="bold red")

    return Panel(
        Align.center(msg, vertical="middle"),
        title="[bold red]◈ STANDBY — OLLAMa OFFLINE[/bold red]",
        border_style="red",
        padding=(1, 4),
    )


def build_layout(log_lines: list[str], stats: dict, standby: bool = False) -> Layout:
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )

    if standby:
        layout["body"].update(build_standby_panel())
    else:
        layout["body"].split_row(
            Layout(name="log",    ratio=2),
            Layout(name="status", ratio=1),
        )
        layout["body"]["log"].update(build_log_panel(log_lines))
        layout["body"]["status"].update(build_status_panel(stats))

    layout["header"].update(build_header(stats))
    return layout


# ===========================================================================
# Log Yardımcıları
# ===========================================================================

def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def compress_prompt(prompt: str, max_len: int = 2000) -> str:
    """Compress prompt by removing extra whitespace and reducing length if needed."""
    # Remove extra whitespace
    compressed = " ".join(prompt.split())
    if len(compressed) > max_len:
        # Keep first part + summary
        part = compressed[:max_len - 100]
        compressed = part + f"\n...[compressed {len(prompt) - max_len} chars truncated]"
    return compressed


async def log(agent: str, message: str) -> None:
    prefix    = AGENT_PREFIX.get(agent, f"[{agent}]")
    color     = COLORS.get(agent, "white")
    timestamp = _timestamp()
    line      = f"[dim]{timestamp}[/dim]  [{color}]{prefix}[/{color}]  {message}"
    await LOG_QUEUE.put(line)


async def broadcast_to_agent(target: str, message: str, context: Optional[dict] = None) -> None:
    """Bir ajandan diğerine mesaj gönder."""
    async with AGENT_MSG_LOCK:
        AGENT_MESSAGES[target].append({
            "message": message,
            "context": context or {},
            "timestamp": _timestamp(),
        })
        if len(AGENT_MESSAGES[target]) > 20:
            AGENT_MESSAGES[target] = AGENT_MESSAGES[target][-20:]


async def get_agent_messages(agent: str) -> list[dict]:
    """Belirli bir ajanın bekleyen mesajlarını al."""
    async with AGENT_MSG_LOCK:
        msgs = AGENT_MESSAGES.get(agent, [])
        AGENT_MESSAGES[agent] = []
        return msgs


def format_agent_context(agent_name: str, messages: list[dict]) -> str:
    """Agent message queue'yu prompt formatına çevir."""
    if not messages:
        return ""
    lines = [f"\n[{agent_name.upper()} MESSAGES]"]
    for m in messages:
        lines.append(f"  - {m['message']}")
        if m.get("context"):
            for k, v in m["context"].items():
                lines.append(f"    [{k}]: {v}")
    return "\n".join(lines)


# ===========================================================================
# Agent Self-Correction Loop (Event-Driven)
# ===========================================================================

async def run_with_critique(
    task_description: str,
    log_lines: list[str],
) -> str:
    global ACTIVE_AGENT
    import uuid
    task_id = str(uuid.uuid4())

    loop = asyncio.get_running_loop()
    future_result = loop.create_future()

    async def listen_for_completion():
        """
        TASK_COMPLETED   → Başarılı sonuç; future'ı kodu ile çözümle.
        HUMAN_INTERVENTION_REQUIRED → Ajanlar başarısız oldu;
            War Room kullanıcıya danışır ve future'ı uyarı mesajıyla çözümler.
        """
        q = EVENT_BUS.subscribe(["TASK_COMPLETED", "HUMAN_INTERVENTION_REQUIRED"])
        while True:
            event = await q.get()
            payload_task_id = event.payload.get("task_id")
            if payload_task_id != task_id:
                continue

            if event.topic == "TASK_COMPLETED":
                if not future_result.done():
                    future_result.set_result(event.payload.get("final_code", ""))
                break

            elif event.topic == "HUMAN_INTERVENTION_REQUIRED":
                # ─── Human-in-the-loop köprüsü ──────────────────────────────
                rejection = event.payload.get("rejection_summary", "")
                current   = event.payload.get("current_code", "")
                await log("warning", "⛔ Ajanlar konsensüs sağlayamadı — İnsan müdahalesi gerekiyor.")
                await log("system",  f"Mevcut kod (son taslak):\n{current[:600]}...")
                await log("system",  f"Red gerekçeleri:\n{rejection[:400]}")
                await log("system",  "Ne yapmak istersiniz? [k]abul et / [i]ptal et / yeni talimat yaz:")
                # Kullanıcı girdisini async olarak al
                try:
                    user_decision = await asyncio.wait_for(
                        asyncio.to_thread(input, ""),
                        timeout=120.0   # 2 dakika içinde yanıt gelmezse iptal
                    )
                    user_decision = user_decision.strip()
                except asyncio.TimeoutError:
                    user_decision = "i"
                    await log("warning", "⏱ Süre doldu — görev iptal edildi.")

                if user_decision.lower() in ("k", "kabul", "accept", "y", "evet"):
                    if not future_result.done():
                        future_result.set_result(current)
                    await log("system", "✅ Kullanıcı mevcut kodu kabul etti.")
                elif user_decision.lower() in ("i", "iptal", "cancel", "n", "hayır"):
                    if not future_result.done():
                        future_result.set_result("⚠ Görev kullanıcı tarafından iptal edildi.")
                    await log("system", "🚫 Görev iptal edildi.")
                else:
                    # Kullanıcı yeni bir talimat yazdı → görevi yenilenen talimatla yeniden başlat
                    await log("system", f"🔄 Yeni talimatla yeniden başlatılıyor: {user_decision}")
                    if not future_result.done():
                        future_result.set_result(f"RETRY: {user_decision}")
                break

    listener_task = asyncio.create_task(listen_for_completion())

    local_keywords = ("excel", "csv", "sql", "json", "rapor", "veri")
    force_local = any(k in task_description.lower() for k in local_keywords)

    MISSION_CONTROL.start_mission(task_id, task_description, force_local)

    await EVENT_BUS.broadcast("TASK_CREATED", task_id, {
        "task_id": task_id,
        "task_description": task_description,
        "force_local": force_local,
        "round_num": 1
    })

    ACTIVE_AGENT = "War Room"
    result = await future_result
    ACTIVE_AGENT = "—"
    return result


async def ui_logger_worker():
    """EventBus üzerinden gelen LOG olaylarını arayüz loglarına aktarır."""
    global ACTIVE_AGENT
    queue = EVENT_BUS.subscribe(["LOG"])
    while True:
        event = await queue.get()
        agent = event.payload.get("agent", "system")
        msg = event.payload.get("message", "")
        if agent in ["architect", "hunter", "writer"]:
            ACTIVE_AGENT = agent.capitalize() # Update UI active agent
        await log(agent, msg)


# ===========================================================================
# Async Görevler
# ===========================================================================

async def stats_updater(stats_holder: list) -> None:
    """Her saniye sistem istatistiklerini ve CPU geçmişini günceller."""
    cleanup_counter = 0
    while True:
        try:
            new_stats = get_system_stats()
            stats_holder[0] = new_stats
            CPU_HISTORY.append(new_stats["cpu_percent"])
            
            # Cache cleanup every 60 seconds
            cleanup_counter += 1
            if cleanup_counter >= 60:
                cleaned = GLOBAL_CACHE.cleanup()
                cleanup_counter = 0
        except Exception:
            pass
        await asyncio.sleep(1)


async def log_collector(log_lines: list[str]) -> None:
    while True:
        try:
            line = await asyncio.wait_for(LOG_QUEUE.get(), timeout=0.1)
            log_lines.append(line)
            if len(log_lines) > 500:
                log_lines[:] = log_lines[-500:]
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass


# ===========================================================================
# Ana Giriş Noktası
# ===========================================================================

async def main() -> None:
    """
    GhostCore v4 War Room — Ana async event loop.

    Başlatma sırası:
        1. Bağımlılıkları kur (zero-touch)
        2. Sinematik boot ekranı
        3. Ollama bağlantısını dene (soft-fail)
        4. Rich Live dashboard'u başlat
        5. Arka plan task'larını çalıştır
        6. Komut döngüsüne gir
    """
    auto_install_missing_dependencies()
    await cinematic_boot_sequence()

    # Soft-fail Ollama kontrolü
    ollama_ok = verify_ollama_connection()
    standby_mode = not ollama_ok

    if not ollama_ok:
        console.print(Panel(
            "[bold yellow]⚠  Ollama çevrimdışı — STANDBY modunda açılıyor.[/bold yellow]\n"
            "[dim]AI görevleri çalışmaz; ancak arayüz açık kalır.[/dim]\n"
            "[dim]Hazır olduğunda: /reconnect[/dim]",
            title="[yellow]Kısıtlı Mod[/yellow]",
            border_style="yellow",
        ))
        await asyncio.sleep(1.5)

    log_lines:      list[str]  = []
    stats_holder:   list[dict] = [get_system_stats()]
    standby_holder: list[bool] = [standby_mode]   # mutable container — closure için

    # Başlangıç mesajları
    await LOG_QUEUE.put(f"[dim]{_timestamp()}[/dim]  [bold green]⚙  [Sistem][/bold green]  GhostCore v4 başlatıldı.")
    if ollama_ok:
        await LOG_QUEUE.put(f"[dim]{_timestamp()}[/dim]  [bold green]⚙  [Sistem][/bold green]  The Phantom Four hazır.")
        await LOG_QUEUE.put(f"[dim]{_timestamp()}[/dim]  [bold cyan]⚡ [Mimar][/bold cyan]   Emirlerini bekliyorum.")
    else:
        await LOG_QUEUE.put(f"[dim]{_timestamp()}[/dim]  [bold yellow]⚙  [Sistem][/bold yellow]  STANDBY — Ollama bekleniyor.")
        await LOG_QUEUE.put(f"[dim]{_timestamp()}[/dim]  [bold yellow]⚙  [Sistem][/bold yellow]  Hazır olduğunda /reconnect yaz.")

    if SESSION_STATE.last_topic:
        await LOG_QUEUE.put(
            f"[dim]{_timestamp()}[/dim]  [bold green]⚙  [Sistem][/bold green]  "
            f"Kaldigimiz yerden devam ediyoruz: [dim]{SESSION_STATE.last_topic}[/dim]"
        )

    with Live(
        build_layout(log_lines, stats_holder[0], standby=standby_mode),
        console=console,
        refresh_per_second=4,
        screen=True,
    ) as live:

        asyncio.create_task(stats_updater(stats_holder))
        asyncio.create_task(log_collector(log_lines))
        asyncio.create_task(ui_logger_worker())
        # Start GhostCore Engine
        await GC.start()

        async def queue_worker():
            while True:
                task = await TASK_QUEUE.get()
                await log("system", f"Kuyruktaki gorev isleniyor: {task[:60]}...")
                try:
                    out = await run_with_critique(task, log_lines)
                    if SILENT_MODE:
                        await log("system", out.splitlines()[0] if out.splitlines() else out)
                    else:
                        await log("system", out[:400])
                    auto_git_commit("[GhostCore] Kuyruktaki gorev tamamlandi")
                except Exception as e:
                    await log("error", f"Kuyruk gorev hatasi: {e}")
                finally:
                    TASK_QUEUE.task_done()
                await asyncio.sleep(0.1)

        asyncio.create_task(queue_worker())

        async def refresh_loop():
            while True:
                live.update(build_layout(log_lines, stats_holder[0], standby=standby_holder[0]))
                await asyncio.sleep(0.25)

        asyncio.create_task(refresh_loop())

        # ---------------------------------------------------------------
        # Ana Komut Döngüsü
        # ---------------------------------------------------------------
        while True:
            try:
                console.print("\n[bold cyan]> Ghost[/bold cyan]", end=" ")
                user_input = await asyncio.to_thread(input, "")
            
            except (EOFError, KeyboardInterrupt):
                await log("system", "[bold red]Kapatma sinyali alındı. Çıkılıyor...[/bold red]")
                await asyncio.sleep(0.5)
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # Çıkış
            if user_input.lower() in ("exit", "quit", "q", "çıkış"):
                await log("system", "GhostCore kapatılıyor. İyi kodlamalar. 👻")
                await asyncio.sleep(1)
                break

            # Yeniden bağlanma (Ollama soft-fail sonrası)
            if user_input.lower() == "/reconnect":
                await log("system", "Ollama'ya yeniden bağlanılıyor...")
                from ghost_core import brain as _brain
                ok = _brain.verify_ollama_connection()
                if ok:
                    standby_holder[0] = False
                    live.update(build_layout(log_lines, stats_holder[0], standby=standby_holder[0]))
                    await log("system", "[bold green]✓ Ollama ONLINE — Tam mod aktif.[/bold green]")
                else:
                    await log("warning", "⚠ Ollama hâlâ çevrimdışı. STANDBY devam ediyor.")
                continue
                
            # --- (Buradan sonra ajanlara görevi ilettiğin asıl kodların devam etmeli) ---

            # STANDBY modda AI görevleri engelle
            if standby_holder[0] and user_input.lower() not in (
                "help", "yardım", "?", "exit", "quit", "q", "/reconnect",
                "stats", "durum", "cache", "tokens", "token",
                "/silent", "/verbose", "/offline",
            ):
                await log("warning", "⚠ Ollama çevrimdışı — AI görevleri kullanılamaz. /reconnect dene.")
                continue

            # Yardım
            if user_input.lower() in ("help", "yardım", "?"):
                await log(
                    "system",
                    "Komutlar: [dim]exit / stats / cache / tokens / /silent / /verbose / /warroom / "
                    "/warroom status / /offline / /reconnect / "
                    "/maestro merge <klasor> [cikti.xlsx] / /maestro sql <db>|<query>|[cikti.xlsx] / "
                    "/designer preview [html] / /schedule <saniye> <metin> / <görev>[/dim]"
                )
                continue

            if user_input.lower() == "/silent":
                global SILENT_MODE, VERBOSE_MODE
                SILENT_MODE = True
                VERBOSE_MODE = False
                await log("system", "Silent mode aktif.")
                continue

            if user_input.lower() == "/verbose":
                SILENT_MODE = False
                VERBOSE_MODE = True
                await log("system", "Verbose mode aktif.")
                continue

            if user_input.lower() == "/offline":
                await log("system", f"Internet: {'ONLINE' if has_internet() else 'OFFLINE'} (Local fallback hazir)")
                continue

            if user_input.lower() == "/warroom":
                lines = ENHANCED_UTILITIES["war_room_v2"].discuss("Mimari optimizasyon")  # type: ignore[attr-defined]
                for line in lines:
                    if "[Hunter]" in line:
                        await log("hunter", line)
                    elif "[Designer]" in line:
                        await log("designer", line)
                    elif "[Data Maestro]" in line:
                        await log("maestro", line)
                    elif "[Architect]" in line:
                        await log("architect", line)
                    else:
                        await log("system", line)
                continue

            if user_input.lower() == "/warroom status":
                s = stats_holder[0]
                await log("system", f"[WarRoom] Ajanlar: {', '.join(TOKEN_MANAGER._usage.keys()) or 'idle'}")
                await log(
                    "system",
                    f"[WarRoom] Donanim: Ryzen 7 | RAM {s['ram_used_gb']:.1f}/20GB ({s['ram_percent']:.0f}%) | CPU sicaklik {s.get('cpu_temp_c', 0)}C"
                )
                continue

            if user_input.lower().startswith("/maestro merge"):
                try:
                    parts = user_input.split()
                    folder = parts[2] if len(parts) >= 3 else "."
                    output = parts[3] if len(parts) >= 4 else "reports/data_maestro_merged.xlsx"

                    async with asyncio.TaskGroup() as tg:
                        maestro_task = tg.create_task(
                            asyncio.to_thread(
                                ENHANCED_UTILITIES["data_maestro_v2"].merge_all_to_excel,  # type: ignore[attr-defined]
                                folder,
                                output,
                            )
                        )
                        designer_task = tg.create_task(
                            asyncio.to_thread(ENHANCED_UTILITIES["designer_v2"].component_factory)  # type: ignore[attr-defined]
                        )
                    out_file = maestro_task.result()
                    comps = designer_task.result()
                    await log("maestro", f"Merge tamamlandi: {out_file}")
                    await log("designer", f"Arka planda UI taslaklari hazirlandi: {', '.join(comps.keys())}")
                    auto_git_commit("[GhostCore] Maestro merge raporu olusturdu")
                except Exception as e:
                    await log("error", f"/maestro merge hatasi: {e}")
                continue

            if user_input.lower().startswith("/maestro sql"):
                try:
                    payload = user_input[len("/maestro sql"):].strip()
                    db_path, query, *rest = [p.strip() for p in payload.split("|")]
                    output = rest[0] if rest and rest[0] else "reports/data_maestro_sql.xlsx"
                    out_file = await asyncio.to_thread(
                        ENHANCED_UTILITIES["data_maestro_v2"].sql_to_excel,  # type: ignore[attr-defined]
                        db_path,
                        query,
                        output,
                    )
                    await log("maestro", f"SQL-to-Excel tamamlandi: {out_file}")
                    auto_git_commit("[GhostCore] Maestro SQL raporu olusturdu")
                except Exception as e:
                    await log("error", f"Kullanim: /maestro sql <db>|<query>|[cikti.xlsx] | Hata: {e}")
                continue

            if user_input.lower().startswith("/designer preview"):
                try:
                    global LAST_GENERATED_HTML
                    parts = user_input.split(maxsplit=2)
                    html_path = parts[2] if len(parts) == 3 else LAST_GENERATED_HTML
                    ENHANCED_UTILITIES["designer_v2"].ensure_assets(".")  # type: ignore[attr-defined]
                    if not os.path.exists(html_path):
                        comps = ENHANCED_UTILITIES["designer_v2"].component_factory()  # type: ignore[attr-defined]
                        content = (
                            "<!doctype html><html><body style='background:#0b1020;color:#e5e7eb;font-family:sans-serif'>"
                            f"{comps['CryptoFeed']}{comps['WeatherPanel']}</body></html>"
                        )
                        os.makedirs(os.path.dirname(html_path) or ".", exist_ok=True)
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(content)
                    LAST_GENERATED_HTML = html_path
                    link = ENHANCED_UTILITIES["designer_v2"].one_click_preview(html_path=html_path, port=8000)  # type: ignore[attr-defined]
                    await log("designer", f"Tasarim yayinda: [link=http://localhost:8000]{link}[/link]")
                except Exception as e:
                    await log("error", f"/designer preview hatasi: {e}")
                continue

            if user_input.lower().startswith("/schedule "):
                try:
                    _, sec, msg = user_input.split(" ", 2)
                    delay = int(sec)
                    async def scheduled():
                        await asyncio.sleep(delay)
                        await log("system", f"[Cron] {msg}")
                    asyncio.create_task(scheduled())
                    await log("system", f"Cron ayarlandi: {delay}s sonra calisacak.")
                except Exception:
                    await log("error", "Kullanim: /schedule <saniye> <mesaj>")
                continue

            if user_input.lower() in ("stats", "durum"):
                s = stats_holder[0]
                await log("system",
                    f"CPU: {s['cpu_percent']:.0f}%  |  "
                    f"RAM: {s['ram_used_gb']:.1f}/{s['ram_total_gb']:.0f}GB  |  "
                    f"Backend: {s['backend']}  |  Model: {s['active_model']}  |  "
                    f"Ollama: {s.get('ollama_status','?')}"
                )
                continue

            if user_input.lower() == "cache":
                st = GLOBAL_CACHE.stats
                await log("system",
                    f"Cache: {st['size']} kayıt | {st['hits']} hit | Oran: {st['hit_rate']}"
                )
                continue

            if user_input.lower() in ("tokens", "token"):
                await log("system",
                    f"Toplam token: {TOKEN_MANAGER.total_tokens:,} | "
                    f"Tahmini maliyet: ${TOKEN_MANAGER.total_cost:.4f}"
                )
                continue

            # Genel görev → Self-Correction döngüsüne gönder
            await log("ghost", f"Görev: [bold]{user_input}[/bold]")
            SESSION_STATE.last_topic = user_input

            try:
                s = stats_holder[0]
                heavy = any(k in user_input.lower() for k in ("excel", "csv", "merge", "chart", "analysis"))
                if heavy and s["ram_percent"] >= 85:
                    await TASK_QUEUE.put(user_input)
                    await log("system", "RAM yüksek, görev kuyruğa alındı.")
                    continue

                result = await run_with_critique(user_input, log_lines)
                SESSION_STATE.last_result = result[:1500]
                SESSION_STATE.mode = "silent" if SILENT_MODE else "verbose"
                SESSION_MEMORY.save(SESSION_STATE)
                await log("system", "[bold green]─── SONUÇ ───[/bold green]")
                if SILENT_MODE:
                    await log("system", (result.splitlines()[0] if result.splitlines() else result))
                else:
                    for chunk in result.split("\n"):
                        if chunk.strip():
                            await log("system", chunk)
                await log("system", "[bold green]─────────────[/bold green]")
                auto_git_commit("[GhostCore] Basarili gorev tamamlandi")

            except Exception as e:
                await log("error", f"[bold red]Görev hatası: {e}[/bold red]")


# ---------------------------------------------------------------------------
# Entry Point — tek komut: python main.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[bold red]GhostCore zorla kapatıldı.[/bold red]")
        sys.exit(0)