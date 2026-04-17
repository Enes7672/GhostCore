from crewai import Agent
from ..brain import get_sentinel_brain

sentinel = Agent(
    role="Real-Time Audio Intelligence Specialist",
    goal="Continuously capture live microphone or system audio streams.",
    backstory="You are Sentinel — a silent, ever-watchful intelligence embedded in the developer's environment.",
    verbose=False,
    allow_delegation=False,
    llm=get_sentinel_brain(),
    tools=[],
)
