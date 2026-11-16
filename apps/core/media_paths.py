"""Store-owned upload paths. Existing database names are never rewritten.

The store UUID (not its editable name, slug or domain) is the namespace.
Public and private roots use the same relative layout; storage controls access.
"""
import re
import uuid
from pathlib import PurePosixPath

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils.deconstruct import deconstructible


def store_media_path(instance, filename, directory):
    """Fail closed without an owner; never infer ownership from a request."""
    owner = instance
    label = instance._meta.label_lower
    try:
        if label == "catalog.productimage":
            owner = instance.product
        elif label == "catalog.attributevalue":
            owner = instance.attribute
    except ObjectDoesNotExist as exc:
        raise ValidationError("A store-owned parent is required before uploading media.") from exc
    if not getattr(owner, "store_id", None):
        raise ValidationError("A saved store is required before uploading media.")
    store = owner.store
    namespace = uuid.UUID(str(store.public_id)).hex
    suffix = PurePosixPath(str(filename).replace("\\", "/")).suffix.lower()
    # A supplied name can never contribute a directory or executable basename.
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", suffix):
        suffix = ""
    return f"stores/{namespace}/{directory}/{uuid.uuid4().hex}{suffix}"


@deconstructible
class StoreMediaPath:
    """Migration-serializable upload_to callable, independent of storage root."""

    def __init__(self, directory):
        if not re.fullmatch(r"[a-z0-9_-]+(?:/[a-z0-9_-]+)*", directory):
            raise ValueError("Invalid media directory")
        self.directory = directory

    def __call__(self, instance, filename):
        return store_media_path(instance, filename, self.directory)

    def __eq__(self, other):
        return isinstance(other, StoreMediaPath) and self.directory == other.directory


def blog_cover_upload_path(instance, filename):
    """Legacy platform editorial posts have an explicit nullable store owner."""
    if instance.store_id is not None:
        return StoreMediaPath("blog/covers")(instance, filename)
    # Reuse the same filename policy without treating other missing owners as platform data.
    suffix = PurePosixPath(str(filename).replace("\\", "/")).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", suffix):
        suffix = ""
    return f"platform/blog/covers/{uuid.uuid4().hex}{suffix}"
