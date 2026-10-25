"""اختبار حقيقي في متصفح Chromium (بدون شاشة). يُتخطّى إن لم تكن Playwright مثبتة."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
import market  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

SHOTS = os.environ.get("SHOTS_DIR")


@unittest.skipIf(sync_playwright is None, "playwright غير مثبتة")
class TestBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from werkzeug.serving import make_server
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = make_server("127.0.0.1", 0, app_module.create_app(os.path.join(cls.tmp.name, "b.db")), threaded=True)
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def shot(self, page, name):
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, name), full_page=True)

    def login(self, page, name):
        page.goto(self.url + "/")
        self.shot(page, "1-login.png")
        page.fill("#username", name)
        page.click("button[type=submit]")
        page.wait_for_url("**/trade")

    def canvas_ink(self, page):
        return page.evaluate("""() => { const c=document.getElementById('chart'), x=c.getContext('2d');
            const d=x.getImageData(0,0,c.width,c.height).data; let n=0;
            for (let i=0;i<d.length;i+=16) if (d[i]!==255||d[i+1]!==255||d[i+2]!==255) n++; return n; }""")

    def test_desktop_flow(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.login(page, "Browser Student")
        self.assertIn("مرحبًا", page.inner_text(".flash"))
        self.assertGreater(self.canvas_ink(page), 2000)  # الرسم مرسوم فعلًا
        self.assertEqual(page.evaluate("window.__chart.count"), market.START_VISIBLE)
        # التمرير على شمعة يُظهر OHLC
        box = page.locator("#chart").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.4)
        self.assertIn("افتتاح", page.inner_text("#tip"))
        # التكبير والتصغير
        n0 = page.evaluate("window.__chart.view.n")
        page.click("[data-zoom=in]")
        self.assertLess(page.evaluate("window.__chart.view.n"), n0)
        page.click("[data-zoom=all]")
        self.assertEqual(page.evaluate("window.__chart.view.n"), market.START_VISIBLE)
        # الرسم بسيط: لا أدوات دعم/مقاومة
        self.assertEqual(page.locator("#lvl").count(), 0)
        self.shot(page, "2-trade-desktop.png")
        # شراء بالنقر الحقيقي
        page.fill("#qty", "25")
        with page.expect_navigation():
            page.click("button[value=buy]")
        self.assertIn("تم شراء 25 سهم", page.inner_text(".flash"))
        self.assertEqual(page.inner_text("#shares"), "25")
        self.assertEqual(page.evaluate("window.__chart.count"), market.START_VISIBLE + 1)
        with page.expect_navigation():
            page.click("button[value=wait]")
        page.fill("#qty", "5")
        with page.expect_navigation():
            page.click("button[value=sell]")
        self.assertIn("تم بيع 5 سهم", page.inner_text(".flash"))
        self.assertEqual(page.inner_text("#shares"), "20")
        self.shot(page, "3-after-trades.png")
        page.click("text=النتيجة والترتيب")
        page.wait_for_url("**/result")
        self.assertIn("Browser Student", page.inner_text("table.board"))
        self.assertEqual(errors, [])

    def test_mobile_layout_no_horizontal_scroll(self):
        ctx = self.browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        self.login(page, "Mobile Student")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0)
        self.assertGreater(self.canvas_ink(page), 1000)
        self.shot(page, "4-trade-mobile.png")
        page.fill("#qty", "3")
        with page.expect_navigation():
            page.tap("button[value=buy]")
        self.assertIn("تم شراء 3 سهم", page.inner_text(".flash"))
        ctx.close()

    def test_tablet_layout(self):
        page = self.browser.new_page(viewport={"width": 768, "height": 1024})
        self.login(page, "Tablet Student")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0)
        self.shot(page, "5-trade-tablet.png")


if __name__ == "__main__":
    unittest.main()
