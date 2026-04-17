import asyncio
import logging
import uuid
from typing import Optional, Dict

from .brain import (
    verify_ollama_connection,
    get_system_stats,
    TOKEN_MANAGER,
    GLOBAL_CACHE,
)
from .core_logic.event_bus import EVENT_BUS
from .core_logic.mission_control import MISSION_CONTROL
from .agents import (
    architect_worker,
    hunter_worker,
    writer_worker,
    moderator_worker,
    SESSION_MEMORY,
)

logger = logging.getLogger("ghostcore.engine")

class GhostCore:
    """
    GhostCore Engine — The central orchestrator for the autonomous agent system.
    Designed for both library integration and standalone TUI usage.
    """

    def __init__(
        self,
        ollama_base_url: str = "http://localhost:11434",
        openai_api_key: Optional[str] = None,
        context_token_limit: int = 3000,
        temp_dir: str = "data/temp",
    ):
        self.ollama_base_url = ollama_base_url
        self.openai_api_key = openai_api_key
        self.context_token_limit = context_token_limit
        self.temp_dir = temp_dir
        
        self.is_running = False
        self._workers: list[asyncio.Task] = []
        
        # Load session state
        self.session_state = SESSION_MEMORY.load()
        
    async def start(self):
        """Starts all background worker agents and monitors."""
        if self.is_running:
            return
            
        logger.info("Starting GhostCore Engine...")
        
        # Verify Ollama (soft-fail is handled in brain.py)
        verify_ollama_connection(self.ollama_base_url)
        
        # Initialize background tasks
        self._workers = [
            asyncio.create_task(architect_worker()),
            asyncio.create_task(hunter_worker()),
            asyncio.create_task(writer_worker()),
            asyncio.create_task(moderator_worker()),
        ]
        
        self.is_running = True
        logger.info("GhostCore Engine fully operational.")

    async def stop(self):
        """Gracefully stops all background workers."""
        if not self.is_running:
            return
            
        for task in self._workers:
            task.cancel()
            
        self.is_running = False
        logger.info("GhostCore Engine stopped.")

    async def execute_task(self, description: str, force_local: bool = False, task_id: Optional[str] = None) -> str:
        """
        Executes a high-level goal using the agent consensus loop.
        
        Args:
            description: What needs to be done.
            force_local: Whether to skip cloud models regardless of complexity.
            task_id: Optional UUID to use for this task (useful for API sync).
            
        Returns:
            The final output/code produced by the agents.
        """
        if not task_id:
            task_id = str(uuid.uuid4())

        
        # Wait for workers to finish subscribing (prevent race condition)
        await asyncio.sleep(1.0)
        
        # Broadcast start event
        await EVENT_BUS.broadcast("TASK_CREATED", task_id, {
            "task_id": task_id,
            "task_description": description,
            "force_local": force_local,
            "round_num": 1
        })
        
        # Wait for completion or failure via Event Bus
        completion_queue = EVENT_BUS.subscribe(["TASK_COMPLETED", "HUMAN_INTERVENTION_REQUIRED"])
        
        try:
            while True:
                event = await completion_queue.get()
                payload_task_id = event.payload.get("task_id")
                
                if payload_task_id != task_id:
                    continue
                    
                if event.topic == "TASK_COMPLETED":
                    return event.payload.get("final_code", "")
                    
                elif event.topic == "HUMAN_INTERVENTION_REQUIRED":
                    # For headless engine, we might return the current state or raise an error
                    # depending on how the caller wants to handle interaction.
                    # For now, we return a special status.
                    return f"INTERVENTION_REQUIRED: {event.payload.get('rejection_summary')}"
        finally:
            # Unsubscribe would be nice here if EventBus supported it properly
            pass

    def get_stats(self) -> dict:
        """Returns current system and token usage statistics."""
        stats = get_system_stats()
        stats["tokens"] = TOKEN_MANAGER.summary()
        stats["cache"] = GLOBAL_CACHE.stats
        return stats

    def subscribe_to_logs(self) -> asyncio.Queue:
        """Returns a queue that receives all system logs."""
        return EVENT_BUS.subscribe(["LOG"])
