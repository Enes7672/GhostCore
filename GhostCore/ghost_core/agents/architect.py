import asyncio
import logging
from crewai import Agent
from langchain_core.messages import HumanMessage

from ..brain import get_architect_brain, get_llm, TOKEN_MANAGER, GLOBAL_CACHE, EXECUTION_MANAGER, TaskPriority
from ..core_logic.event_bus import EVENT_BUS
from ..core_logic.mission_control import MISSION_CONTROL, MissionStatus
from .utils import MEMORY, PATTERN_ADVISOR, FILE_SYNC, _file_read_tool, _directory_read_tool, WEB_SEARCH_TOOL

logger = logging.getLogger("ghostcore.architect")

the_architect = Agent(
    role="Lead AI Systems Architect",
    goal="Receive task, deconstruct, apply Design Pattern, Sync logic. Use web search for library research.",
    backstory="You are The Architect — command intelligence of GhostCore v4.",
    verbose=True,
    allow_delegation=True,
    llm=get_architect_brain(),
    tools=[_file_read_tool, _directory_read_tool, WEB_SEARCH_TOOL],
)

def build_architect_system_prompt(task: str = "", changed_file: str = "") -> str:
    sections = []
    if task:
        memory_ctx = MEMORY.context_for_prompt(task)
        if memory_ctx: sections.append(memory_ctx)
        sections.append(PATTERN_ADVISOR.suggest(task))
    if changed_file:
        sync_ctx = FILE_SYNC.sync_report(changed_file)
        if sync_ctx: sections.append(sync_ctx)
    sections.append(
        "\n[COLLABORATIVE MODE]\n"
        "  You are working in a War Room. Other agents (Hunter, Writer) will review your output.\n"
        "  If you receive refinement feedback, incorporate it professionally.\n"
    )
    return "\n".join(sections)

async def architect_worker():
    queue = EVENT_BUS.subscribe(["TASK_CREATED", "REFINEMENT_REQUESTED"])
    await EVENT_BUS.publish("LOG", {"agent": "architect", "message": "Architect initialized and ready."})
    
    while True:
        try:
            event = await queue.get()
            mission_id = event.metadata.get("mission_id") or event.payload.get("task_id")
            task_desc = event.payload.get("task_description", "")
            force_local = event.payload.get("force_local", False)
            
            state = MISSION_CONTROL.get_mission(mission_id)
            if not state:
                state = MISSION_CONTROL.start_mission(mission_id, task_desc, force_local)
            
            await EVENT_BUS.publish("LOG", {"agent": "architect", "message": f"Mimar çalışıyor (Tur {state.round_num})..." })
            
            llm = get_llm(task_type="architecture", force_local=force_local)
            
            if event.topic == "REFINEMENT_REQUESTED":
                feedback = event.payload.get("feedback", "")
                prompt = (
                    f"Role: Architect. Task: {state.description}\n\n"
                    f"Previous Code:\n{state.current_code}\n\n"
                    f"Feedback from Peers:\n{feedback}\n\n"
                    "Fix and return only the corrected final output."
                )
            else:
                sys_prompt = build_architect_system_prompt(task_desc)
                prompt = f"Role: Architect. Task:\n{task_desc}\n\n{sys_prompt}\nOutput: concise, secure, production-ready."

            import re
            
            content = ""
            for attempt in range(3):
                try:
                    # LOW öncelik: Sentinel/Whisper yüklüyse bu çağrı sıraya girer
                    async with EXECUTION_MANAGER.slot(TaskPriority.LOW):
                        resp = await asyncio.to_thread(llm.invoke, [HumanMessage(content=prompt)])
                    content = resp.content
                    break
                except Exception as e:
                    if attempt == 2: raise e
                    await EVENT_BUS.publish("LOG", {"agent": "architect", "message": f"Ollama meşgul, yeniden deneniyor ({attempt+1}/3)..."})
                    await asyncio.sleep(5)

            TOKEN_MANAGER.record("architect", TOKEN_MANAGER.estimate(prompt), TOKEN_MANAGER.estimate(content))

            # XML parsing for Elite Codegen
            plan_match = re.search(r"<plan>(.*?)</plan>", content, re.DOTALL)
            code_match = re.search(r"<code>(.*?)</code>", content, re.DOTALL)
            tests_match = re.search(r"<tests>(.*?)</tests>", content, re.DOTALL)

            state.plan = plan_match.group(1).strip() if plan_match else ""
            state.current_code = code_match.group(1).strip() if code_match else content.strip()
            state.tests = tests_match.group(1).strip() if tests_match else ""

            state.status = MissionStatus.REVIEWING

            await EVENT_BUS.publish("LOG", {"agent": "architect", "message": "Plan ve taslak Blackboard'a asıldı."})
            await EVENT_BUS.broadcast("VERSION_STAGED", mission_id, {
                "agent": "architect",
                "code": state.current_code,
                "tests": state.tests,
                "round_num": state.round_num
            })
        except Exception as e:
            await EVENT_BUS.publish("LOG", {"agent": "error", "message": f"Architect error: {e}"})
            await asyncio.sleep(2)
