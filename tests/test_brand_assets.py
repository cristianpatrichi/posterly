from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class BrandAssetsTest(unittest.TestCase):
    def test_brand_is_wired_and_default_is_posterly(self):
        mark = FRONTEND / "src" / "assets" / "mark.svg"
        brand = FRONTEND / "src" / "brand.ts"
        app = FRONTEND / "src" / "App.tsx"
        html = FRONTEND / "index.html"

        # A brand-neutral mark asset ships and is referenced everywhere.
        self.assertTrue(mark.exists())
        self.assertIn('"Posterly"', brand.read_text())  # default brand name
        self.assertIn("import.meta.env.VITE_BRAND_NAME", brand.read_text())
        self.assertIn("import.meta.env.VITE_BRAND_TAGLINE", brand.read_text())
        self.assertIn("BrandLogo", app.read_text())
        self.assertIn("Posterly", html.read_text())  # static title fallback


if __name__ == "__main__":
    unittest.main()
