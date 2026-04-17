import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Dict, List

class MissionStatus(Enum):
    PENDING = "pending"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    REFINING = "refining"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class MissionState:
    task_id: str
    description: str
    origin_code: str = ""
    current_code: str = ""
    plan: str = ""
    tests: str = ""
    reviews: Dict[str, str] = field(default_factory=dict)  # agent_name -> review_text
    approvals: Dict[str, bool] = field(default_factory=dict) # agent_name -> is_approved
    patches: List[Dict[str, Any]] = field(default_factory=list) # List of patch metadata
    status: MissionStatus = MissionStatus.PENDING
    round_num: int = 1
    force_local: bool = False
    
    def reset_reviews(self):
        self.reviews = {}
        self.approvals = {}
        self.patches = []

    def get_consensus_report(self) -> str:
        report = []
        for agent, approved in self.approvals.items():
            status = "✅ APPROVED" if approved else "❌ REJECTED"
            report.append(f"{agent.capitalize()}: {status}")
        return "\n".join(report)

class MissionControl:
    def __init__(self):
        self.missions: Dict[str, MissionState] = {}

    def start_mission(self, task_id: str, desc: str, force_local: bool = False) -> MissionState:
        state = MissionState(task_id=task_id, description=desc, force_local=force_local)
        self.missions[task_id] = state
        return state

    def get_mission(self, task_id: str) -> Optional[MissionState]:
        return self.missions.get(task_id)

    def update_mission(self, task_id: str, **kwargs):
        if task_id in self.missions:
            for k, v in kwargs.items():
                setattr(self.missions[task_id], k, v)

MISSION_CONTROL = MissionControl()
