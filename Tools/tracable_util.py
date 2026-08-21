# Tools/tracing_utils.py
import sys
import types
from langsmith import traceable


def traceable_all(module_name: str, skip_private: bool = True):
    """Wrap every top-level function defined in this module with @traceable."""
    module = sys.modules[module_name]
    for name in dir(module):
        if skip_private and name.startswith("_"):
            continue
        obj = getattr(module, name)
        if isinstance(obj, types.FunctionType) and getattr(obj, "__module__", None) == module_name:
            setattr(module, name, traceable(obj))
