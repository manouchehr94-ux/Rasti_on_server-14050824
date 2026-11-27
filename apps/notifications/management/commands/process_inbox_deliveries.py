import signal
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.core.job_lock import single_instance
from apps.notifications.services.channel_delivery import deliver_pending

POLL_SECONDS = 5
WATCH_LIMIT = 10


@contextmanager
def release_cycle(base):
    """Yield busy/current/changed while holding the release's shared lock."""
    if base is None:
        yield "current"
        return
    import fcntl

    with (base / "state/deploy.lock").open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            yield "busy"
            return
        current_app = (base / "current/app").resolve()
        yield "current" if Path.cwd().resolve() == current_app else "changed"


class Command(BaseCommand):
    help = "پردازش صف اعلان‌های موجود. حالت watch هر پنج ثانیه صف را بررسی می‌کند."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--release-base", type=Path)

    def _poll(self, limit, *, quiet=False):
        with single_instance("process_inbox_deliveries") as acquired:
            if not acquired:
                if not quiet:
                    self.stdout.write("skipped: another process_inbox_deliveries is still running")
                return
            result = deliver_pending(limit=limit)
        if result["processed"] or not quiet:
            self.stdout.write(
                f"{result['processed']} processed — {result['delivered']} delivered, "
                f"{result['failed']} failed, {result['skipped']} skipped"
            )

    def handle(self, *args, **options):
        watch = options["watch"]
        limit = options["limit"]
        limit = (WATCH_LIMIT if watch else 200) if limit is None else limit
        if limit < 1 or (watch and limit > WATCH_LIMIT):
            raise CommandError(f"Use a positive limit (maximum {WATCH_LIMIT} in watch mode).")
        if not watch:
            self._poll(limit)
            return

        base = options["release_base"]
        from django.conf import settings

        if not settings.DEBUG and base is None:
            raise CommandError("Production watch mode requires --release-base.")
        if base is not None and not base.is_absolute():
            raise CommandError("--release-base must be absolute.")
        stop = Event()
        handlers = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            while not stop.is_set():
                with release_cycle(base) as state:
                    if state == "changed":
                        self.stdout.write("Active release changed; exiting so the supervisor can reload it.")
                        return
                    if state == "current":
                        close_old_connections()
                        try:
                            self._poll(limit, quiet=True)
                        finally:
                            close_old_connections()
                stop.wait(POLL_SECONDS)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
