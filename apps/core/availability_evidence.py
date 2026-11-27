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
SLOW_REQUEST_MS = 1000
_ROUTE_NAME = re.compile(r"[A-Za-z0-9_:-]{1,100}")
logger = logging.getLogger("rastisi.availability")


class AvailabilityEvidenceMiddleware(MiddlewareMixin):
    # The production application is WSGI. Django adapts this middleware for ASGI.
    async_capable = False

    @staticmethod
    def _event(request, event, **fields):
        request_id = request.META.get("HTTP_X_REQUEST_ID", "")
        if not REQUEST_ID.fullmatch(request_id):
            return
        match = getattr(request, "resolver_match", None)
        name = match.url_name if match else None
        row = {
            "format": "rastisi_app_v1", "event": event,
            "unix_ms": time.time_ns() // 1_000_000, "pid": os.getpid(),
            "request_id": request_id,
            "route": name if name and _ROUTE_NAME.fullmatch(name) else None,
            **fields,
        }
        logger.warning("%s", json.dumps(row, separators=(",", ":")))

    def process_view(self, request, view_func, view_args, view_kwargs):
        self._event(request, "view_entered")

    def process_exception(self, request, exception):
        self._event(request, "view_failed")

    def __call__(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID", "")
        if not REQUEST_ID.fullmatch(request_id):
            return self.get_response(request)
        started_request = time.monotonic_ns()
        slow_count = slow_total_ms = slow_max_ms = 0
        entered_db = False

        def measure(execute, sql, params, many, context):
            nonlocal entered_db, slow_count, slow_total_ms, slow_max_ms
            if not entered_db:
                entered_db = True
                self._event(request, "database_entered")
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
        duration_ms = (time.monotonic_ns() - started_request) // 1_000_000
        if slow_count or response.status_code >= 500 or duration_ms >= SLOW_REQUEST_MS:
            self._event(
                request, "request_summary", status=response.status_code, duration_ms=duration_ms,
                slow_db_count=slow_count, slow_db_total_ms=slow_total_ms, slow_db_max_ms=slow_max_ms,
            )
        return response
