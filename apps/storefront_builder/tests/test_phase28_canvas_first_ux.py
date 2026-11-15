from pathlib import Path



from .test_views import StorefrontBuilderViewsTestCase


class Phase28CanvasFirstUXTemplateTests(StorefrontBuilderViewsTestCase):
    """Current R3 canvas-first editor; V3 chrome remains hidden for compatibility."""


    def test_desktop_editor_follows_the_current_canvas_first_r3_contract(self):
        import re
        root = Path(__file__).resolve().parents[1]
        css = (root / "static/css/storefront_builder_r3.css").read_text(encoding="utf-8")
        editor = (root / "templates/dashboard/storefront_builder/editor.html").read_text(encoding="utf-8")
        self.assertIn("css/storefront_builder_r3.css", editor)
        shell = re.search(r"\.sfb-r3-shell\{([^}]+)\}", css).group(1)
        self.assertIn("grid-template-rows:auto minmax(0,1fr)", shell)
        self.assertIn("display:grid", shell)
        canvas = re.search(r"\.sfb-r3-shell \.sfb-v3-main\{([^}]+)\}", css).group(1)
        for declaration in ("display:block", "width:100%", "height:100%"):
            self.assertIn(declaration, canvas)
        hidden = next(body for selectors, body in re.findall(r"([^{}]+)\{([^{}]+)\}", css)
                      if ".sfb-v3-sidebar-right" in selectors and ".sfb-v3-inspector" in selectors)
        self.assertIn("display:none", hidden)
        self.assertNotIn("sfb-overlay-drawer-open", css)
