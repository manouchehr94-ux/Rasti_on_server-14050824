from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.catalog.models import IndustryTemplate
from apps.catalog.services.industry_authoring import create_version


class IndustryAuthoringTests(TestCase):
    def definition(self):
        return {
            "slug": "local-qa-industry", "name": "Synthetic trade", "display_order": 7,
            "categories": [{"code": "main", "name": "Parent"},
                           {"code": "child", "name": "Child", "parent_code": "main"}],
            "attributes": [],
        }

    def test_creating_a_version_leaves_prior_content_and_state_intact(self):
        original = IndustryTemplate.objects.create(slug="local-qa-industry", name="Original")
        version = create_version(self.definition(), source=original)
        original.refresh_from_db()
        self.assertEqual(original.name, "Original")
        self.assertTrue(original.is_active)
        self.assertFalse(original.categories.exists())
        self.assertFalse(version.is_active)
        self.assertEqual(version.version, 2)
        self.assertEqual(version.display_order, 7)
        self.assertEqual(version.categories.get(code="child").parent.code, "main")

    def test_invalid_relationship_rolls_back_the_entire_version(self):
        definition = self.definition()
        definition["categories"][1]["parent_code"] = "missing"
        with self.assertRaises(ValidationError):
            create_version(definition)
        self.assertFalse(IndustryTemplate.objects.filter(slug=definition["slug"]).exists())
