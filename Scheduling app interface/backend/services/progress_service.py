import re
from pathlib import Path

def log_path(instance_path, algorithm):
    slug={"MILP":"milp","Improved GA":"improved-ga","MLPSO":"mlpso","CP-SAT":"cp-sat","SS Micro-GA Fast":"ss-micro-ga-fast","MIN_TARDY_GROUPED_LNS_V5":"min-tardy-grouped-lns-v5"}[algorithm]
    path=Path(instance_path).parent / (slug+".log")
    if algorithm=='MIN_TARDY_GROUPED_LNS_V5':
        from backend.services.algorithms.grouped_lns_adapter import long_path
        return long_path(path)
    return path

def read_progress(instance_path, algorithm):
    path=log_path(instance_path,algorithm)
    if not path.exists():
        return None
    try:
        with path.open("rb") as file:
            file.seek(max(0,path.stat().st_size-16384))
            lines=file.read().decode("utf-8",errors="replace").splitlines()
        for line in reversed(lines):
            if algorithm=='MIN_TARDY_GROUPED_LNS_V5':
                grouped=re.search(r'calls=(\d+)/(\d+) best=\((\d+), ([\d.eE+\-]+), ([\d.eE+\-]+)\)',line)
                if grouped:
                    return {'iteration':int(grouped[1]),'limit':int(grouped[2]),'best_tardy_jobs':int(grouped[3]),
                            'best_total_tardiness_min':float(grouped[4]),'best_makespan_min':float(grouped[5]),
                            'scope':'Unique full decodes'}
            match=re.search(r"(?:generation|epoch|iter)\s+(\d+)/\s*(\d+).*?(?:best|Gbest)=([\d.]+)",line)
            if match:
                return {"iteration":int(match[1]),"limit":int(match[2]),"best_makespan_min":float(match[3]),
                        "scope":"Current independent run"}
    except OSError:
        pass
    return None
