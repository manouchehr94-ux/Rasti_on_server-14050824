"""Definitive subscription failures notify only permitted inboxes; money never depends on notifications."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.models import SubscriptionRefund
from apps.billing.services import refund_service
from apps.billing.tests.test_credit_refund import _paid_invoice
from apps.notifications.models import InboxItem, NotificationEvent, NotificationDelivery
from apps.notifications.services import admin_events
from apps.stores.models import Store, StoreMembership


class RefundFailureInboxTests(TestCase):
    def setUp(self):
        self.store, self.invoice = _paid_invoice("failure-notice")
        User = get_user_model()
        self.owner = User.objects.create_user(username="notice-owner")
        StoreMembership.objects.create(store=self.store, user=self.owner, role=StoreMembership.Role.OWNER, status=StoreMembership.MembershipStatus.ACTIVE, accepted_at=timezone.now())
        self.invited = User.objects.create_user(username="notice-invited")
        StoreMembership.objects.create(store=self.store, user=self.invited, role=StoreMembership.Role.OWNER)
        self.platform = User.objects.create_user(username="notice-platform", is_staff=True, is_superuser=True)
        self.other = User.objects.create_user(username="notice-other-owner")
        other_store = Store.objects.create(name="Other", slug="notice-other")
        StoreMembership.objects.create(store=other_store, user=self.other, role=StoreMembership.Role.OWNER, status=StoreMembership.MembershipStatus.ACTIVE, accepted_at=timezone.now())

    def refund(self, key="notice-refund"):
        return refund_service.request_refund(self.invoice, amount=Decimal("100000"), idempotency_key=key)

    def assert_recipients(self):
        store_event = NotificationEvent.objects.get(type_key="payment.refund_failed")
        platform_event = NotificationEvent.objects.get(type_key="platform.refund_failed")
        self.assertEqual(set(InboxItem.objects.filter(event=store_event).values_list("user_id", flat=True)), {self.owner.pk})
        self.assertEqual(set(InboxItem.objects.filter(event=platform_event).values_list("user_id", flat=True)), {self.platform.pk})
        self.assertFalse(InboxItem.objects.filter(user=self.other).exists())

    def test_manual_failure_notifies_exactly_once_and_preserves_financial_result(self):
        refund = self.refund()
        self.assertEqual(refund.status, SubscriptionRefund.Status.PENDING)
        self.assertFalse(NotificationEvent.objects.filter(type_key__endswith="refund_failed").exists())
        result = refund_service.fail_refund_manually(refund, actor=self.platform, reference="BANK-NO-DEBIT")
        self.assertEqual(result.status, SubscriptionRefund.Status.FAILED)
        self.assertEqual(refund_service.refundable_amount(self.invoice), self.invoice.amount_paid)
        refund_service.fail_refund_manually(result, actor=self.platform, reference="BANK-NO-DEBIT")
        self.assertEqual(NotificationEvent.objects.filter(type_key__endswith="refund_failed").count(), 2)
        self.assert_recipients()

    def test_definitive_not_sent_outcome_notifies_but_ambiguous_outcome_does_not(self):
        refund = self.refund()
        refund.status = SubscriptionRefund.Status.REQUESTED
        refund.save(update_fields=["status"])
        result = refund_service._record_outcome(refund.pk, "unknown", failure_code="unknown", now=timezone.now())
        self.assertEqual(result.status, SubscriptionRefund.Status.PENDING)
        self.assertFalse(NotificationEvent.objects.filter(type_key__endswith="refund_failed").exists())
        result.status = SubscriptionRefund.Status.REQUESTED
        result.save(update_fields=["status"])
        result = refund_service._record_outcome(refund.pk, "declined", failure_code="not_configured", now=timezone.now())
        self.assertEqual(result.status, SubscriptionRefund.Status.FAILED)
        self.assert_recipients()

    def test_rollback_removes_both_inboxes_and_restores_pending_refund(self):
        refund = self.refund()
        try:
            with transaction.atomic():
                refund_service.fail_refund_manually(refund, actor=self.platform, reference="BANK-NO-DEBIT")
                raise RuntimeError("outer transaction failed")
        except RuntimeError:
            pass
        refund.refresh_from_db()
        self.assertEqual(refund.status, SubscriptionRefund.Status.PENDING)
        self.assertFalse(NotificationEvent.objects.filter(type_key__endswith="refund_failed").exists())

    def test_inbox_outage_cannot_rollback_a_definitive_financial_failure(self):
        refund = self.refund()
        with mock.patch.object(admin_events, "subscription_refund_failed", side_effect=RuntimeError("inbox down")):
            refund_service.fail_refund_manually(refund, actor=self.platform, reference="BANK-NO-DEBIT")
        refund.refresh_from_db()
        self.assertEqual(refund.status, SubscriptionRefund.Status.FAILED)
        self.assertEqual(refund_service.refundable_amount(self.invoice), self.invoice.amount_paid)

    @override_settings(RASTISI_NOTIF_EMAIL_ENABLED=True, RASTISI_NOTIF_WEBPUSH_ENABLED=True, RASTISI_NOTIF_MOBILE_ENABLED=True)
    def test_failure_events_never_plan_external_deliveries_or_expose_bank_details(self):
        refund = self.refund()
        with mock.patch("apps.notifications.services.inbox_engine._create_deliveries") as delivery:
            refund_service.fail_refund_manually(refund, actor=self.platform, reference="BANK-PRIVATE-REFERENCE", note="private finance note")
            delivery.assert_not_called()
        self.assertFalse(NotificationDelivery.objects.exists())
        for event in NotificationEvent.objects.filter(type_key__endswith="refund_failed"):
            self.assertNotIn("BANK-PRIVATE-REFERENCE", event.title + event.summary + event.details)
            self.assertNotIn("private finance note", event.title + event.summary + event.details)
