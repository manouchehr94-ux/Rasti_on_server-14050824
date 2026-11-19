"""Opt-in synchronous request evidence. Never serialize SQL or exception details."""
import json
import logging
import os
import re
import time
from contextlib import ExitStack

from django.db import connections
from django.utils.deprecation import MiddlewareMixin

REQUEST_ID = re.compile(r"[a-f0-9]{32}")
SLOW_MS = 250
logger = logging.getLogger("rastisi.availability")


class AvailabilityEvidenceMiddleware(MiddlewareMixin):
    # The production application is WSGI. Django adapts this middleware for ASGI.
    async_capable = False

    def __call__(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID", "")
        if not REQUEST_ID.fullmatch(request_id):
            return self.get_response(request)
        slow_count = slow_total_ms = slow_max_ms = 0

        def measure(execute, sql, params, many, context):
            nonlocal slow_count, slow_total_ms, slow_max_ms
            started = time.monotonic_ns()
            try:
                return execute(sql, params, many, context)
            finally:
                elapsed = (time.monotonic_ns() - started) // 1_000_000
                if elapsed >= SLOW_MS:
                    slow_count += 1
                    slow_total_ms += elapsed
                    slow_max_ms = max(slow_max_ms, elapsed)

        with ExitStack() as stack:
            for connection in connections.all():
                stack.enter_context(connection.execute_wrapper(measure))
            response = self.get_response(request)
        if slow_count or response.status_code >= 500:
            event = {
                "format": "rastisi_app_v1", "event": "request_summary",
                "unix_ms": time.time_ns() // 1_000_000, "pid": os.getpid(),
                "request_id": request_id, "status": response.status_code,
                "slow_db_count": slow_count, "slow_db_total_ms": slow_total_ms,
                "slow_db_max_ms": slow_max_ms,
            }
            logger.warning("%s", json.dumps(event, separators=(",", ":")))
        return response
