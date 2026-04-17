import asyncio
import logging
import uuid
from typing import Dict, Optional
from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ghost_core import GhostCore
from ghost_core.core_logic.event_bus import EVENT_BUS
from ghost_core.core_logic.mission_control import MISSION_CONTROL, MissionStatus

# ---------------------------------------------------------------------------
# Setup & Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ghost_gateway")

app = FastAPI(
    title="GhostCore v4 Gateway",
    description="Autonomous Agent Orchestration API",
    version="1.0.0"
)

# CORS Support — Enabling cross-origin access for remote dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Singleton GhostCore Engine
engine = GhostCore()

# ---------------------------------------------------------------------------
# Lifespan Management
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    logger.info("Starting GhostCore Engine...")
    await engine.start()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Stopping GhostCore Engine...")
    await engine.stop()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class MissionRequest(BaseModel):
    description: str
    force_local: bool = False

class MissionResponse(BaseModel):
    mission_id: str
    status: str

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def get_health():
    """Returns system health and resource statistics."""
    return {
        "status": "online" if engine.is_running else "offline",
        "stats": engine.get_stats(),
        "engine_version": "v4.1.0-Stabilized"
    }

@app.post("/missions", response_model=MissionResponse)
async def create_mission(request: MissionRequest, background_tasks: BackgroundTasks):
    """
    Triggers a new autonomous mission.
    Returns immediately with a mission_id.
    """
    # 1. Generate ID
    mission_id = str(uuid.uuid4())
    
    # 2. Add to background tasks so API doesn't hang
    background_tasks.add_task(engine.execute_task, request.description, request.force_local, mission_id)
    
    return MissionResponse(mission_id=mission_id, status="accepted")


@app.get("/missions/{mission_id}")
async def get_mission_status(mission_id: str):
    """Retrieves the current state of a specific mission."""
    state = MISSION_CONTROL.get_mission(mission_id)
    if not state:
        # Check if we should search missions by different IDs or if there was a mismatch
        # For now, if not found, we return 404
        raise HTTPException(status_code=444, detail="Mission not found in local registry")
    
    return {
        "id": state.task_id,
        "status": state.status.value,
        "round": state.round_num,
        "current_code": state.current_code,
        "plan": state.plan,
        "consensus": state.get_consensus_report()
    }

# ---------------------------------------------------------------------------
# WebSocket — Real-time Log Streaming
# ---------------------------------------------------------------------------
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    log_queue = engine.subscribe_to_logs()
    
    try:
        while True:
            # Get next log from GhostCore EventBus
            log_event = await log_queue.get()
            # Send to websocket client
            await websocket.send_json({
                "agent": log_event.payload.get("agent", "system"),
                "message": log_event.payload.get("message", ""),
                "timestamp": log_event.timestamp
            })
    except WebSocketDisconnect:
        logger.info("Log WebSocket disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Properly unsubscribe logic would go here
        pass

# ---------------------------------------------------------------------------
# Static Dashboard
# ---------------------------------------------------------------------------
try:
    app.mount("/dashboard", StaticFiles(directory="web", html=True), name="static")
except Exception:
    logger.warning("Web dashboard directory not found. Skipping mount.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
