import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
import market  # noqa: E402

S0 = market.START_VISIBLE


def chart_json(html):
    m = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


class TestWebApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "web.db")
        self.app = app_module.create_app(self.db)
        self.app.testing = True

    def tearDown(self):
        self.tmp.cleanup()

    def client_for(self, name):
        c = self.app.test_client()
        r = c.post("/", data={"username": name})
        self.assertEqual(r.status_code, 302)
        return c

    def step_of(self, html):
        return int(re.search(r'name="step" value="(\d+)"', html).group(1))

    def test_login_page_is_arabic_rtl_single_field(self):
        html = self.app.test_client().get("/").get_data(as_text=True)
        self.assertIn('dir="rtl"', html)
        self.assertIn('lang="ar"', html)
        self.assertEqual(len(re.findall(r"<input ", html)), 1)  # حقل اسم المستخدم فقط
        self.assertNotIn("password", html.lower())
        self.assertIn("Ford Motor Company", html)
        self.assertIn("Yahoo Finance", html)

    def test_empty_name_rejected(self):
        r = self.app.test_client().post("/", data={"username": "  "})
        self.assertEqual(r.status_code, 400)

    def test_trade_page_requires_login(self):
        r = self.app.test_client().get("/trade")
        self.assertEqual(r.status_code, 302)

    def test_trade_page_sends_only_visible_candles(self):
        c = self.client_for("Yacine")
        html = c.get("/trade").get_data(as_text=True)
        data = chart_json(html)
        self.assertEqual(len(data["candles"]), S0)
        self.assertEqual(data["candles"][-1]["d"], market.CANDLES[S0 - 1].date)
        # لا يظهر تاريخ أي شمعة مستقبلية في الصفحة إطلاقًا
        for fut in market.CANDLES[S0:]:
            self.assertNotIn(fut.date, html)
        self.assertIn("500.00 $\u2069", html)

    def test_full_flow_buy_sell_history_and_reveal(self):
        c = self.client_for("Nour")
        html = c.get("/trade").get_data(as_text=True)
        step = self.step_of(html)
        r = c.post("/trade", data={"action": "buy", "qty": "10", "step": step}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("تم شراء 10 سهم", html)
        self.assertEqual(self.step_of(html), step + 1)
        data = chart_json(html)
        self.assertEqual(len(data["candles"]), step + 1)  # كُشفت الشمعة بعد التنفيذ فقط
        self.assertEqual(data["trades"], [{"i": step, "side": "buy", "qty": 10}])
        self.assertIn(market.CANDLES[step].date, html)  # يظهر الآن في السجل
        # إعادة إرسال نفس النموذج (ضغط مزدوج) لا تُنفّذ مرة ثانية
        r = c.post("/trade", data={"action": "buy", "qty": "10", "step": step}, follow_redirects=True)
        self.assertIn("تم تحديث الصفحة", r.get_data(as_text=True))
        # بيع أكثر من المملوك مرفوض
        r = c.post("/trade", data={"action": "sell", "qty": "11", "step": step + 1}, follow_redirects=True)
        self.assertIn("تملك 10 فقط", r.get_data(as_text=True))
        r = c.post("/trade", data={"action": "sell", "qty": "10", "step": step + 1}, follow_redirects=True)
        self.assertIn("تم بيع 10 سهم", r.get_data(as_text=True))

    def test_play_to_end_then_result_page(self):
        c = self.client_for("Karim")
        c.post("/trade", data={"action": "buy", "qty": "20", "step": S0})
        for s in range(S0 + 1, market.N):
            c.post("/trade", data={"action": "wait", "step": s})
        r = c.get("/trade")
        self.assertEqual(r.status_code, 302)  # انتهت المحاكاة → صفحة النتيجة
        html = c.get("/result").get_data(as_text=True)
        for label in ["رأس المال الأولي", "الرصيد النقدي المتبقي", "القيمة السوقية للأسهم المتبقية",
                      "القيمة النهائية للمحفظة", "صافي الربح أو الخسارة", "نسبة العائد",
                      "عدد الصفقات المنفذة", "صفقات بيع رابحة / خاسرة", "سجل مختصر للعمليات"]:
            self.assertIn(label, html)
        self.assertIn(market.CANDLES[-1].date, html)  # توضيح سعر التقييم

    def test_two_students_separate_sessions_and_board(self):
        a = self.client_for("Amina")
        b = self.client_for("Bilal")
        a.post("/trade", data={"action": "buy", "qty": "33", "step": S0})
        hb = b.get("/trade").get_data(as_text=True)
        self.assertIn("500.00 $\u2069", hb)
        self.assertEqual(chart_json(hb)["trades"], [])
        board = b.get("/result").get_data(as_text=True)
        self.assertIn("Amina", board)
        self.assertIn("Bilal", board)
        # لا تظهر صفقات Amina في صفحة Bilal
        self.assertNotIn("شراء</td><td>33", board.replace("\n", ""))
        self.assertNotIn(market.CANDLES[S0].date, board)

    def test_relogin_restores_account(self):
        c = self.client_for("Hadjer")
        c.post("/trade", data={"action": "buy", "qty": "7", "step": S0})
        c.get("/logout")
        app2 = app_module.create_app(self.db)  # كأن الخادم أُعيد تشغيله
        c2 = app2.test_client()
        r = c2.post("/", data={"username": "  HADJER "}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("مرحبًا بعودتك", html)
        self.assertIn('id="shares">7<', html)


if __name__ == "__main__":
    unittest.main()
