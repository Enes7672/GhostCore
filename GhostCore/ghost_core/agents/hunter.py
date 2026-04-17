import asyncio
import logging
from crewai import Agent
from langchain_core.messages import HumanMessage

from ..brain import get_hunter_brain, get_llm, TOKEN_MANAGER, EXECUTION_MANAGER, TaskPriority
from ..core_logic.event_bus import EVENT_BUS
from ..core_logic.mission_control import MISSION_CONTROL
from .utils import PROFILER, SANDBOX, _file_read_tool, _directory_read_tool, WEB_SEARCH_TOOL

logger = logging.getLogger("ghostcore.hunter")

the_hunter = Agent(
    role="Senior Security Engineer, QA Specialist & Auto-Patcher",
    goal="Perform rigorous security audits. Use web search for CVE research and latest security best practices.",
    backstory="You are The Hunter — a paranoid, methodical, and uncompromising security engineer.",
    verbose=True,
    allow_delegation=False,
    llm=get_hunter_brain(),
    tools=[_file_read_tool, _directory_read_tool, WEB_SEARCH_TOOL],
)

async def hunter_worker():
    queue = EVENT_BUS.subscribe(["VERSION_STAGED"])
    await EVENT_BUS.publish("LOG", {"agent": "hunter", "message": "Hunter initialized and ready."})
    
    while True:
        try:
            event = await queue.get()
            mission_id = event.metadata.get("mission_id")
            code = event.payload.get("code", "")
            tests = event.payload.get("tests", "")
            round_num = event.payload.get("round_num", 1)
            
            state = MISSION_CONTROL.get_mission(mission_id)
            if not state: continue
            
            await EVENT_BUS.publish("LOG", {"agent": "hunter", "message": f"Güvenlik & Kalite denetimi (Tur {round_num}) [{SANDBOX.mode.upper()} sandbox]..."})

            # 1) Sandbox çalıştırma (mode .env'den okunur: manual / subprocess / docker)
            sandbox_result = await asyncio.to_thread(SANDBOX.execute, code, tests)

            prompt = (
                "Role: Hunter. Audit this staged code for security vulnerabilities, static analysis violations, and test failures.\n"
                f"Code:\n{code}\n\n"
                f"Sandbox Result (Static / TDD):\n{sandbox_result.safe_summary}\n\n"
                "If unsafe or has failing tests/complexity issues, start with 'REJECTED:' followed by why and how to fix it. "
                "If safe and high quality, return 'APPROVED'."
            )
            
            try:
                out = ""
                for attempt in range(3):
                    try:
                        llm = get_llm(task_type="security_audit", force_local=True)
                        # LOW öncelik: Sentinel/Whisper yüklüyse güvenlik denetimi sıraya girer
                        async with EXECUTION_MANAGER.slot(TaskPriority.LOW):
                            resp = await asyncio.to_thread(llm.invoke, [HumanMessage(content=prompt)])
                        out = resp.content
                        break
                    except Exception as e:
                        if attempt == 2: raise e
                        await EVENT_BUS.publish("LOG", {"agent": "hunter", "message": f"Ollama meşgul (Hunter), yeniden deneniyor ({attempt+1}/3)..."})
                        await asyncio.sleep(5)

                TOKEN_MANAGER.record("hunter", TOKEN_MANAGER.estimate(prompt), TOKEN_MANAGER.estimate(out))
                
                approved = out.strip().upper().startswith("APPROVED")
                
                await EVENT_BUS.broadcast("REVIEW_POSTED", mission_id, {
                    "agent": "hunter",
                    "approved": approved,
                    "content": out
                })
            except Exception as e:
                await EVENT_BUS.publish("LOG", {"agent": "error", "message": f"Hunter LLM error: {e}"})
        except Exception as e:
            await EVENT_BUS.publish("LOG", {"agent": "error", "message": f"Hunter error: {e}"})
            await asyncio.sleep(2)
