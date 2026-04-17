import asyncio
import logging
from crewai import Agent
from langchain_core.messages import HumanMessage

from ..brain import get_writer_brain, get_llm, TOKEN_MANAGER, EXECUTION_MANAGER, TaskPriority
from ..core_logic.event_bus import EVENT_BUS
from ..core_logic.mission_control import MISSION_CONTROL
from .utils import _file_read_tool, _directory_read_tool

logger = logging.getLogger("ghostcore.writer")

the_writer = Agent(
    role="Senior Technical Documentation Engineer & Git Workflow Specialist",
    goal="Generate professional, developer-grade documentation and manage Git workflows. Be proactive in enhancing code quality.",
    backstory="You are The Writer — the documentation engineer and git historian of GhostCore.",
    verbose=True,
    allow_delegation=False,
    llm=get_writer_brain(),
    tools=[_file_read_tool, _directory_read_tool],
)

async def writer_worker():
    queue = EVENT_BUS.subscribe(["VERSION_STAGED"])
    await EVENT_BUS.publish("LOG", {"agent": "writer", "message": "Writer initialized and ready."})
    
    while True:
        try:
            event = await queue.get()
            mission_id = event.metadata.get("mission_id")
            code = event.payload.get("code", "")
            force_local = event.payload.get("force_local", False)

            state = MISSION_CONTROL.get_mission(mission_id)
            if not state:
                continue

            await EVENT_BUS.publish("LOG", {"agent": "writer", "message": "Dokümantasyon ve kod iyileştirme analizi..."})

            llm = get_llm(task_type="readme_write", force_local=force_local)
            prompt = (
                "Role: Writer. Review this staged code.\n"
                f"Code:\n{code}\n\n"
                "If it needs documentation (docstrings, comments) or small cleanups, provide a 'PATCH'. "
                "If it looks perfect, return 'APPROVED'."
            )
            try:
                writer_out = ""
                for attempt in range(3):
                    try:
                        # NORMAL öncelik: Sentinel'den sonra, Architect/Hunter ile eşit
                        async with EXECUTION_MANAGER.slot(TaskPriority.NORMAL):
                            resp = await asyncio.to_thread(llm.invoke, [HumanMessage(content=prompt)])
                        writer_out = resp.content
                        break
                    except Exception as e:
                        if attempt == 2: raise e
                        await EVENT_BUS.publish("LOG", {"agent": "writer", "message": f"Ollama meşgul (Writer), yeniden deneniyor ({attempt+1}/3)..."})
                        await asyncio.sleep(5)

                TOKEN_MANAGER.record("writer", TOKEN_MANAGER.estimate(prompt), TOKEN_MANAGER.estimate(writer_out))


                approved = writer_out.strip().upper().startswith("APPROVED")

                await EVENT_BUS.broadcast("REVIEW_POSTED", mission_id, {
                    "agent": "writer",
                    "approved": approved,
                    "content": writer_out
                })
            except Exception as e:
                await EVENT_BUS.publish("LOG", {"agent": "error", "message": f"Writer LLM error: {e}"})
        except Exception as e:
            await EVENT_BUS.publish("LOG", {"agent": "error", "message": f"Writer error: {e}"})
            await asyncio.sleep(2)
