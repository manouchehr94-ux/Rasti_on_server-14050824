"""Read Web Push permissions from the database, including in long-lived workers."""
from django.conf import settings
from django.db import transaction

from apps.notifications.models import WebPushPolicy
from apps.notifications.services.scope import key_for

GLOBAL_SCOPE = "platform"


def scope_enabled(scope_key):
    return WebPushPolicy.objects.filter(scope_key=scope_key, enabled=True).exists()


def allowed(store):
    if store is None or not settings.RASTISI_NOTIF_WEBPUSH_ENABLED:
        return False
    return (WebPushPolicy.objects
            .filter(scope_key__in=(GLOBAL_SCOPE, key_for(store)), enabled=True).count() == 2)


@transaction.atomic
def set_enabled(*, actor, enabled, store=None):
    from apps.portal.services.platform_config_service import record_platform_audit_event

    scope_key = key_for(store)
    policy, _ = WebPushPolicy.objects.select_for_update().get_or_create(scope_key=scope_key)
    if policy.enabled != enabled:
        before = policy.enabled
        policy.enabled = enabled
        policy.save(update_fields=["enabled"])
        record_platform_audit_event(
            actor=actor, action_code="webpush.policy_updated", object_type="WebPushPolicy",
            object_id=scope_key, before={"enabled": before}, after={"enabled": enabled},
        )
