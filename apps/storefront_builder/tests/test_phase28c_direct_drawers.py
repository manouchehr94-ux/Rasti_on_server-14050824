from pathlib import Path

from django.test import SimpleTestCase


class Phase28CDirectDrawerStaticTests(SimpleTestCase):
    def test_active_r3_editor_has_no_offscreen_legacy_drawer_translation(self):
        # R3 superseded the V3 persistent-panel shell. Inspect the stylesheet
        # actually loaded by the editor, not the retired V3 comment stub.
        root = Path(__file__).resolve().parents[1]
        css = (root / "static/css/storefront_builder_r3.css").read_text(encoding="utf-8")
        editor = (root / "templates/dashboard/storefront_builder/editor.html").read_text(encoding="utf-8")
        self.assertIn("css/storefront_builder_r3.css", editor)
        self.assertIn("sfb-r3-shell", editor)
        self.assertIn(".sfb-r3-shell .sfb-v3-canvas-area", css)
        self.assertNotIn("translateX(104%)", css)
        self.assertNotIn("translateX(-104%)", css)
        self.assertNotIn("sfb-overlay-drawer-open", css)
