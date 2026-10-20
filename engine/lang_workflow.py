"""Optional LangGraph planning stage with LangChain tool argument validation.

Execution stays in Galam's existing policy controlled tool runner.  This module
only prepares live context, queries the configured model, and validates any
structured calls before the main loop considers executing them.
"""

from functools import lru_cache
from typing import Any, TypedDict


class PlanningState(TypedDict, total=False):
    messages: list
    directory: str
    snapshot: str
    response: Any


def _typed_tools():
    from langchain_core.tools import StructuredTool

    def execute_bash(command: str, reason: str = "") -> str:
        """Run a command after Galam checks its path and execution policy."""
        return command

    def view_file(path: str, start_line: int = 1, end_line: int = 200) -> str:
        """Inspect an existing file before a code edit."""
        return path

    def write_file(path: str, content: str, overwrite: bool = True) -> str:
        """Create a file after Galam reviews the proposed write."""
        return path

    def replace_file_content(path: str, target_content: str,
                             replacement_content: str) -> str:
        """Replace an exact block in an inspected file."""
        return path

    def search_files(query: str, directory: str = ".") -> str:
        """Search files in the current workspace."""
        return query

    def manage_tasks(action: str = "list", target: str = "all") -> str:
        """List or stop background tasks after policy review."""
        return action

    def make_directory(path: str) -> str:
        """Create a directory after policy review."""
        return path

    functions = (make_directory, execute_bash, view_file, write_file,
                 replace_file_content, search_files, manage_tasks)
    return {function.__name__: StructuredTool.from_function(function)
            for function in functions}


def validate_tool_arguments(name: str, arguments: dict) -> dict:
    """Use the same argument contract with and without the optional graph."""
    from engine.tool_registry import validate_arguments
    return validate_arguments(name, arguments)


@lru_cache(maxsize=1)
def _tools():
    return _typed_tools()


@lru_cache(maxsize=1)
def _graph():
    from langchain_core.runnables import RunnableLambda
    from langgraph.graph import StateGraph, END
    from engine.task_runner import session_directory, workspace_snapshot
    from routers.manager import query_with_fallback

    model = RunnableLambda(lambda state: query_with_fallback(state["messages"]))

    def inspect(state: PlanningState) -> dict:
        directory = session_directory()
        return {"directory": directory,
                "snapshot": workspace_snapshot(directory)}

    def propose(state: PlanningState) -> dict:
        return {"response": model.invoke(state)}

    graph = StateGraph(PlanningState)
    graph.add_node("inspect", inspect)
    graph.add_node("propose", propose)
    graph.set_entry_point("inspect")
    graph.add_edge("inspect", "propose")
    graph.add_edge("propose", END)
    return graph.compile()


def query_planning_graph(messages: list):
    """Return the model response through the inspect -> propose graph."""
    return _graph().invoke({"messages": messages})["response"]


def is_available() -> bool:
    try:
        _graph()
        _tools()
        return True
    except ImportError:
        return False
