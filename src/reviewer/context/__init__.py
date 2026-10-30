"""The context engine: what a specialist sees besides the diff (changed functions, callers,
callees, team rules). `build_context` is the entry point."""
from reviewer.context.assemble import ContextBundle, build_context
from reviewer.context.rules import RulesError

__all__ = ["ContextBundle", "RulesError", "build_context"]
