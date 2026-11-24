"""Review and compose these hooks with the effective Gunicorn configuration."""
import json
import re
import time

from apps.core.availability_gate import evidence_enabled

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


def install_hooks(namespace):
    """At the end of the effective Python config, chain existing hooks once."""
    if not evidence_enabled():
        return

    from gunicorn.config import Config

    config = Config()
    for name in ("pre_request", "post_request", "worker_abort", "post_worker_init", "child_exit"):
        if name in namespace:
            config.set(name, namespace[name])

    def before_request(worker, req):
        config.pre_request(worker, req)
        pre_request(worker, req)

    def after_request(worker, req, environ, resp):
        config.post_request(worker, req, environ, resp)
        post_request(worker, req, environ, resp)

    def on_abort(worker):
        config.worker_abort(worker)
        worker_abort(worker)

    def on_ready(worker):
        config.post_worker_init(worker)
        post_worker_init(worker)

    def on_exit(server, worker):
        config.child_exit(server, worker)
        child_exit(server, worker)

    namespace.update(pre_request=before_request, post_request=after_request,
                     worker_abort=on_abort, post_worker_init=on_ready, child_exit=on_exit)
