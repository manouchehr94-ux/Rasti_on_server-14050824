"""Read only. Output sanitized resource and database aggregates."""
import json
from pathlib import Path
import re
import subprocess


def run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=8, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def fields(path, allowed):
    try:
        return {k: int(v) for k, v in re.findall(r"(?m)^(\w+)[: ]\s*(\d+)", path.read_text()) if k in allowed}
    except OSError:
        return None


unit = "rastisi-rc-20261111.service"
group = run(["systemctl", "show", "--value", "-p", "ControlGroup", unit])
out = {"service": unit,
       "host_memory_kib": fields(Path("/proc/meminfo"), {"MemAvailable", "SwapTotal", "SwapFree"})}
out["pressure_avg10"] = {}
for resource in ("cpu", "memory", "io"):
    try:
        lines = Path("/proc/pressure", resource).read_text().splitlines()
        out["pressure_avg10"][resource] = {
            line.split()[0]: float(re.search(r"avg10=(\d+\.\d+)", line)[1]) for line in lines
        }
    except (OSError, IndexError, TypeError):
        out["pressure_avg10"][resource] = None
if group and group.startswith("/") and ".." not in Path(group).parts and group.endswith("/" + unit):
    cg = Path("/sys/fs/cgroup") / group.lstrip("/")
    events = {"high", "max", "oom", "oom_kill"}
    out["service_oom_local"] = fields(cg / "memory.events.local", events)
    out["parent_oom_hierarchical"] = fields(cg.parent / "memory.events", events)
    out["service_cpu"] = fields(cg / "cpu.stat", {"nr_periods", "nr_throttled", "throttled_usec"})
sql = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '2s';
SELECT json_build_object(
 'active', count(*) FILTER (WHERE state = 'active' AND pid <> pg_backend_pid()),
 'lock_waits', count(*) FILTER (WHERE wait_event_type = 'Lock')
) FROM pg_stat_activity WHERE datname = current_database();
COMMIT;"""
db = run(["runuser", "-u", "postgres", "--", "psql", "-XqAt", "-v", "ON_ERROR_STOP=1",
          "-d", "rastisi_rc_20261111", "-c", sql])
out["postgres_snapshot"] = json.loads(db) if db else None
state = run(["systemctl", "show", "--value", "-p", "ActiveState", "rastisi-redis.service"])
out["rastisi_redis_state"] = state if state in {"active", "inactive", "failed", "activating", "deactivating"} else None
print(json.dumps(out, indent=2))
