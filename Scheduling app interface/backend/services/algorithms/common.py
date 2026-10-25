import importlib
import sys
from backend.config import ROOT

# Algorithms stay in /code unchanged. Heavy numerical libraries are imported only by workers.
def solver_module(name):
    code_path = str(ROOT / "code")
    if code_path not in sys.path:
        sys.path.insert(0, code_path)
    return importlib.import_module(name)
