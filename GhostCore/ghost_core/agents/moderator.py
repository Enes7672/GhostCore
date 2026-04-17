import asyncio
import logging
from typing import Any

from ..core_logic.event_bus import EVENT_BUS
from ..core_logic.mission_control import MISSION_CONTROL, MissionStatus

logger = logging.getLogger("ghostcore.moderator")

# Kaç inceleme turu sonrası insan devreye girsin
MAX_REVIEW_ROUNDS: int = 3

async def _parallel_agent_review(mission_id: str, code: str, tests: str, force_local: bool, round_num: int) -> Any:
    """
    Writer ve Hunter ajanlarını paralel olarak çalıştırır.
    asyncio.gather kullanarak eşzamanlı yürütme sağlar.
    """
    from ..brain import EXECUTION_MANAGER, get_llm, TOKEN_MANAGER, TaskPriority
    from .hunter import SANDBOX
    from langchain_core.messages import HumanMessage

    async def writer_review():
        llm = get_llm(task_type="readme_write", force_local=force_local)
        prompt = (
            "Role: Writer. Review this staged code.\n"
            f"Code:\n{code}\n\n"
            "If it needs documentation or cleanups, provide a 'PATCH'. "
            "If it looks perfect, return 'APPROVED'."
        )
        async with EXECUTION_MANAGER.slot(TaskPriority.NORMAL):
            resp = await asyncio.to_thread(llm.invoke, [HumanMessage(content=prompt)])
        out = resp.content
        approved = out.strip().upper().startswith("APPROVED")
        TOKEN_MANAGER.record("writer", TOKEN_MANAGER.estimate(prompt), TOKEN_MANAGER.estimate(out))
        return {"agent": "writer", "approved": approved, "content": out}

    async def hunter_audit():
        sandbox_result = await asyncio.to_thread(SANDBOX.execute, code, tests)
        llm = get_llm(task_type="security_audit", force_local=True)
        prompt = (
            "Role: Hunter. Audit this code for security and quality.\n"
            f"Code:\n{code}\n\n"
            f"Sandbox Result:\n{sandbox_result.safe_summary}\n\n"
            "If unsafe or has issues, start with 'REJECTED:'. If safe, return 'APPROVED'."
        )
        async with EXECUTION_MANAGER.slot(TaskPriority.LOW):
            resp = await asyncio.to_thread(llm.invoke, [HumanMessage(content=prompt)])
        out = resp.content
        approved = out.strip().upper().startswith("APPROVED")
        TOKEN_MANAGER.record("hunter", TOKEN_MANAGER.estimate(prompt), TOKEN_MANAGER.estimate(out))
        return {"agent": "hunter", "approved": approved, "content": out}

    results = await EXECUTION_MANAGER.run_parallel(writer_review(), hunter_audit())
    for result in results:
        if isinstance(result, Exception):
            await EVENT_BUS.publish("LOG", {"agent": "error", "message": f"Parallel review error: {result}"})
            continue
        await EVENT_BUS.broadcast("REVIEW_POSTED", mission_id, result)


async def moderator_worker():
    """
    War Room Moderatörü: Blackboard'u (MissionControl) yönetir.
    Tüm ajanların görüşlerini toplar ve konsensüs sağlanıp sağlanmadığına karar verir.
    VERSION_STAGED event'inde Writer ve Hunter paralel başlatılır.
    """
    queue = EVENT_BUS.subscribe(["VERSION_STAGED", "REVIEW_POSTED", "PATCH_POSTED"])
    await EVENT_BUS.publish("LOG", {"agent": "moderator", "message": "Moderator initialized and ready."})

    while True:
        try:
            event = await queue.get()
            mission_id = event.metadata.get("mission_id")
            if not mission_id:
                continue

            state = MISSION_CONTROL.get_mission(mission_id)
            if not state:
                continue

            if event.topic == "VERSION_STAGED":
                state.status = MissionStatus.REVIEWING
                state.reset_reviews()
                code = event.payload.get("code", "")
                tests = event.payload.get("tests", "")
                force_local = event.payload.get("force_local", False)
                round_num = event.payload.get("round_num", 1)

                await EVENT_BUS.publish(
                    "LOG",
                    {"agent": "moderator", "message": f"Versiyon {round_num} incelemeye alındı. Writer + Hunter paralel başlatılıyor..."}
                )

                asyncio.create_task(_parallel_agent_review(mission_id, code, tests, force_local, round_num))

            elif event.topic == "REVIEW_POSTED":
                agent_name = event.payload.get("agent")
                approved   = event.payload.get("approved", False)
                content    = event.payload.get("content", "")

                state.reviews[agent_name]  = content
                state.approvals[agent_name] = approved

                await EVENT_BUS.publish(
                    "LOG",
                    {"agent": "moderator", "message": f"{agent_name.capitalize()} görüşünü bildirdi: {'✅ ONAY' if approved else '❌ RED'}"}
                )

                required = ["hunter", "writer"]

                if all(name in state.reviews for name in required):
                    if all(state.approvals.values()):
                        await EVENT_BUS.publish("LOG", {"agent": "moderator", "message": "Konsensüs sağlandı. Görev tamamlanıyor."})
                        state.status = MissionStatus.COMPLETED
                        await EVENT_BUS.broadcast("TASK_COMPLETED", mission_id, {
                            "final_code": state.current_code,
                            "task_id":    mission_id,
                        })
                    else:
                        rejected_feedback = "\n\n".join(
                            f"[{k.upper()}]: {v}"
                            for k, v in state.reviews.items()
                            if not state.approvals.get(k)
                        )

                        if state.round_num >= MAX_REVIEW_ROUNDS:
                            await EVENT_BUS.publish("LOG", {
                                "agent":   "moderator",
                                "message": f"⛔ {MAX_REVIEW_ROUNDS} tur tamamlandı; ajanlar konsensüs sağlayamadı. İnsan müdahalesi gerekiyor."
                            })
                            state.status = MissionStatus.FAILED
                            await EVENT_BUS.broadcast("HUMAN_INTERVENTION_REQUIRED", mission_id, {
                                "task_id":          mission_id,
                                "task_description": state.description,
                                "current_code":     state.current_code,
                                "rejection_summary": rejected_feedback,
                                "round_num":        state.round_num,
                            })
                        else:
                            await EVENT_BUS.publish("LOG", {
                                "agent":   "moderator",
                                "message": f"Eleştiriler toplandı (tur {state.round_num}/{MAX_REVIEW_ROUNDS}). Mimar yeniden düzenleme yapacak."
                            })
                            state.status    = MissionStatus.REFINING
                            state.round_num += 1
                            await EVENT_BUS.broadcast("REFINEMENT_REQUESTED", mission_id, {
                                "feedback":         rejected_feedback,
                                "current_code":     state.current_code,
                                "task_description": state.description,
                            })
        except Exception as e:
            await EVENT_BUS.publish("LOG", {"agent": "error", "message": f"Moderator error: {e}"})
            await asyncio.sleep(2)

