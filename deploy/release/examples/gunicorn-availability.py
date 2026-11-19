"""Review and compose these hooks with the effective Gunicorn configuration."""
import json
import re
import time

_REQUEST_ID = re.compile(r"[a-f0-9]{32}")


def _event(worker, event, *, logger=None, **evidence):
    fields = {"format": "rastisi_worker_v1", "event": event,
              "unix_ms": time.time_ns() // 1_000_000, "pid": worker.pid, **evidence}
    (logger or worker.log).info("%s", json.dumps(fields, separators=(",", ":")))


def pre_request(worker, req):
    request_ids = [value for name, value in req.headers if name.lower() == "x-request-id"]
    if len(request_ids) == 1 and _REQUEST_ID.fullmatch(request_ids[0]):
        req._rastisi_availability = request_ids[0], time.monotonic_ns()
        _event(worker, "request_started", request_id=request_ids[0])


def post_request(worker, req, environ, resp):
    evidence = getattr(req, "_rastisi_availability", None)
    if evidence is not None:
        request_id, started = evidence
        status = str(getattr(resp, "status", "")).split(" ", 1)[0]
        _event(worker, "request_finished", request_id=request_id,
               status=int(status) if re.fullmatch(r"[1-5][0-9]{2}", status) else None,
               duration_ms=(time.monotonic_ns() - started) // 1_000_000)


def worker_abort(worker):
    _event(worker, "worker_abort")


def post_worker_init(worker):
    _event(worker, "worker_ready")


def child_exit(server, worker):
    _event(worker, "worker_exited", logger=server.log)


# If access logging is enabled, keep it limited to safe numeric fields.
access_log_format = "rastisi_access_v1 %(p)s %(s)s %(D)s"
