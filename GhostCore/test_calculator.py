import asyncio
import sys
from ghost_core import GhostCore

# Windows console encoding for UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[union-attr]
    except Exception:
        pass

async def monitor_logs(engine):
    q = engine.subscribe_to_logs()
    try:
        while True:
            event = await q.get()
            agent = str(event.payload.get("agent", "system")).capitalize()
            msg = event.payload.get("message", "")
            print(f"[{agent}] {msg}", flush=True)
    except asyncio.CancelledError:
        pass

async def run_calc():
    try:
        print("Initialize GhostCore...", flush=True)
        engine = GhostCore()
        print("Starting engine...", flush=True)
        await engine.start()
        await asyncio.sleep(2.0)  # Heartbeat synchronizer
        print("Engine started and synchronized.", flush=True)
        
        log_task = asyncio.create_task(monitor_logs(engine))
        
        print(">>> Görev GhostCore'a İletiliyor...", flush=True)
        
        # Task execution
        result = await engine.execute_task("Bana temiz kod prensiplerine (SOLID, Type Hinting, Docstrings) tam uyan, dört işlem yapan bir Python hesap makinesi sınıfı yaz. Ayrıca exception handling (Sıfıra bölme hatası vs) içersin.")
        
        print("\n" + "="*50, flush=True)
        print("🔥 GHOSTCORE ÇIKTISI (FINAL CODE)")
        print("="*50)
        print(result)
        print("="*50 + "\n")
        
        log_task.cancel()
        await engine.stop()
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_calc())
