from contextlib import nullcontext
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.notifications.management.commands import process_inbox_deliveries as worker


class InboxWatchTests(SimpleTestCase):
    @override_settings(DEBUG=True)
    def test_watch_skips_deployment_and_polls_on_the_existing_lock(self):
        with (patch.object(worker, "release_cycle", side_effect=[
            nullcontext("busy"), nullcontext("current"), nullcontext("changed"),
        ]), patch.object(worker, "close_old_connections"),
              patch.object(worker, "single_instance", return_value=nullcontext(True)) as lock,
              patch.object(worker, "deliver_pending", return_value={"processed": 0}) as poll,
              patch.object(worker.Event, "wait") as wait):
            output = StringIO()
            call_command("process_inbox_deliveries", "--watch", stdout=output)
        lock.assert_called_once_with("process_inbox_deliveries")
        poll.assert_called_once_with(limit=10)
        self.assertEqual([call.args for call in wait.call_args_list], [(5,), (5,)])
        self.assertIn("Active release changed", output.getvalue())
        self.assertNotIn("0 processed", output.getvalue())

    @override_settings(DEBUG=False)
    def test_watch_requires_a_production_release_base(self):
        with self.assertRaises(CommandError):
            call_command("process_inbox_deliveries", "--watch")
