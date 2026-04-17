from crewai import Agent
from ..brain import get_writer_brain
from .utils import _file_read_tool, _directory_read_tool

data_maestro = Agent(
    role="Data Analysis & Excel Reporting Specialist",
    goal="Read complex data sources (CSV, JSON, SQL), perform fast pandas analysis, and deliver professional Excel reports.",
    backstory="You are Data Maestro — GhostCore's operational intelligence.",
    verbose=True,
    allow_delegation=False,
    llm=get_writer_brain(),
    tools=[_file_read_tool, _directory_read_tool],
)
