import asyncio
import os
import pytest
from ghost_core import GhostCore

@pytest.mark.asyncio
async def test_import():
    print("Testing GhostCore import...")
    try:
        engine = GhostCore()
        print(f"Engine initialized with URL: {engine.ollama_base_url}")
        
        # Check system stats
        stats = engine.get_stats()
        print(f"System Stats: CPU {stats['cpu_percent']}%, RAM {stats['ram_percent']}%")
        
        # Test if we can start (this will run in background)
        # Note: We won't actually run LLM tasks here to save time/tokens unless necessary
        await engine.start()
        print("Engine workers started successfully.")
        
        await engine.stop()
        print("Engine workers stopped successfully.")
        print("Test PASSED.")
    except Exception as e:
        print(f"Test FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_import())
