import re
from pathlib import Path

def log_path(instance_path, algorithm):
    slug={"MILP":"milp","Improved GA":"improved-ga","MLPSO":"mlpso","CP-SAT":"cp-sat","SS Micro-GA Fast":"ss-micro-ga-fast"}[algorithm]
    return Path(instance_path).parent / (slug+".log")

def read_progress(instance_path, algorithm):
    path=log_path(instance_path,algorithm)
    if not path.exists():
        return None
    try:
        with path.open("rb") as file:
            file.seek(max(0,path.stat().st_size-16384))
            lines=file.read().decode("utf-8",errors="replace").splitlines()
        for line in reversed(lines):
            match=re.search(r"(?:generation|epoch|iter)\s+(\d+)/\s*(\d+).*?(?:best|Gbest)=([\d.]+)",line)
            if match:
                return {"iteration":int(match[1]),"limit":int(match[2]),"best_makespan_min":float(match[3]),
                        "scope":"Current independent run"}
    except OSError:
        pass
    return None
