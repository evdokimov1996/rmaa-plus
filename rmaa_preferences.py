"""Local non-secret preferences. Atomic replace, no periodic writes."""
import json
import os
import sys
from pathlib import Path


def preference_path():
    # PyInstaller's __file__ lives inside _internal; persistent data belongs
    # beside the executable. No installer, registry setting or cwd dependence.
    root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    if (root/'portable.flag').is_file():
        return root/'data'/'preferences.json'
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home())))/"RMAAOpenLab"/"preferences.json"


def load_preferences(path=None):
    try:
        p=Path(path) if path else preference_path()
        if p.stat().st_size>65536:return {}
        result=json.loads(p.read_text(encoding="utf-8"))
        return result if isinstance(result,dict) else {}
    except (OSError,ValueError):
        return {}


def save_preferences(data,path=None):
    target=Path(path) if path else preference_path()
    target.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    fd,name=tempfile.mkstemp(prefix="preferences-",suffix=".tmp",dir=target.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as file:
            json.dump(data,file,ensure_ascii=False,indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)
