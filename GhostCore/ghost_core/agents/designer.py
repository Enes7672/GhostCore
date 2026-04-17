from crewai import Agent
from ..brain import get_architect_brain
from .utils import _file_read_tool, _directory_read_tool

designer = Agent(
    role="UI/UX, Tailwind and Visual Asset Specialist",
    goal="Design elegant Tailwind CSS dashboard components, create coherent color palettes.",
    backstory="You are The Designer — GhostCore's esthetic and UI brain.",
    verbose=True,
    allow_delegation=False,
    llm=get_architect_brain(),
    tools=[_file_read_tool, _directory_read_tool],
)
