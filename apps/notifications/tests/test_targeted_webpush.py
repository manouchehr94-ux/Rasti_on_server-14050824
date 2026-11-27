import json
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.test import SimpleTestCase

from apps.notifications import registry
from apps.notifications.models import NotificationPreference
from apps.notifications.services.inbox_engine import decide
from apps.notifications.services.webpush import _b64u, _encrypted_body


class TargetedPushTests(SimpleTestCase):
    def test_body_decrypts_with_subscription_and_only_contains_opaque_id(self):
        device = ec.generate_private_key(ec.SECP256R1())
        receiver = device.public_key().public_bytes(serialization.Encoding.X962,
                                                    serialization.PublicFormat.UncompressedPoint)
        auth = b"a" * 16
        payload = b'{"item":"00000000-0000-0000-0000-000000000001"}'
        body = _encrypted_body({"p256dh": _b64u(receiver), "auth": _b64u(auth)}, payload)
        record_size, key_length = struct.unpack("!IB", body[16:21])
        salt, sender = body[:16], body[21:21 + key_length]
        self.assertEqual(record_size, 4096)
        shared = device.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))

        def derive(material, salt, info, length):
            return HKDF(algorithm=hashes.SHA256(), salt=salt, info=info, length=length).derive(material)

        ikm = derive(shared, auth, b"WebPush: info\x00" + receiver + sender, 32)
        key = derive(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = derive(ikm, salt, b"Content-Encoding: nonce\x00", 12)
        clear = AESGCM(key).decrypt(nonce, body[21 + key_length:], None)
        self.assertEqual(clear[-1:], b"\x02")
        self.assertEqual(json.loads(clear[:-1]), json.loads(payload))

    def test_disabling_payment_push_preserves_inbox_and_other_events(self):
        pref = NotificationPreference(browser_enabled=True, disabled_push_events=["payment.succeeded"])
        paid = decide(registry.get_type("payment.succeeded"), registry.IMPORTANT, pref)
        new = decide(registry.get_type("order.created"), registry.NORMAL, pref)
        self.assertTrue(paid["inbox"])
        self.assertFalse(paid["push"])
        self.assertTrue(new["push"])
