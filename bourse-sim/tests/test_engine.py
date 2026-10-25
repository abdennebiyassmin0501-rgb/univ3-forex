import hashlib
import os
import random
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import market  # noqa: E402

S0 = market.START_VISIBLE


def unit(i):
    return engine.to_cents(market.CANDLES[i].open)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        engine.init_db(self.path)
        self.conn = engine.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def user(self, name="Ahmed"):
        u, _ = engine.login(self.conn, name)
        return u["id"]

    def u(self, uid):
        return engine.get_user(self.conn, uid)

    def invariant(self, uid):
        u = self.u(uid)
        self.assertEqual(u["cash"] + u["cost_basis"], engine.INITIAL + u["realized"])
        self.assertGreaterEqual(u["cash"], 0)
        self.assertGreaterEqual(u["shares"], 0)


class TestAccounts(Base):
    def test_new_account_has_100000(self):
        u, created = engine.login(self.conn, "Sara")
        self.assertTrue(created)
        self.assertEqual(u["cash"], 500 * 100)
        self.assertEqual(u["shares"], 0)
        self.assertEqual(u["step"], S0)

    def test_same_name_case_and_spaces_reuse_account(self):
        u1, c1 = engine.login(self.conn, "Ahmed Ali")
        engine.act(self.conn, u1["id"], "buy", 3)
        for variant in ["  ahmed   ali ", "AHMED ALI", "Ahmed\tAli", "ahmed ali"]:
            u2, c2 = engine.login(self.conn, variant)
            self.assertFalse(c2)
            self.assertEqual(u2["id"], u1["id"])
            self.assertEqual(u2["shares"], 3)  # الحساب السابق مع محفظته، دون رصيد إضافي
        self.assertTrue(c1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_arabic_names_and_validation(self):
        u1, _ = engine.login(self.conn, "محمد أمين")
        u2, _ = engine.login(self.conn, " محمد   أمين ")
        self.assertEqual(u1["id"], u2["id"])
        for bad in ["", "   ", "a", "x" * 31]:
            with self.assertRaises(engine.TradeError):
                engine.login(self.conn, bad)

    def test_persistence_after_reopen(self):
        uid = self.user("Lina")
        engine.act(self.conn, uid, "buy", 5)
        self.conn.close()
        self.conn = engine.connect(self.path)  # كأن الموقع أُعيد تشغيله
        u, created = engine.login(self.conn, "lina")
        self.assertFalse(created)
        self.assertEqual(u["shares"], 5)
        self.assertEqual(len(engine.get_trades(self.conn, uid)), 1)

    def test_concurrent_logins_same_name_create_one_account(self):
        errors = []

        def go():
            try:
                c = engine.connect(self.path)
                engine.login(c, "Rami")
                c.close()
            except Exception as e:  # pragma: no cover
                errors.append(e)
        ts = [threading.Thread(target=go) for _ in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        n = self.conn.execute("SELECT COUNT(*) FROM users WHERE username_key='rami'").fetchone()[0]
        self.assertEqual(n, 1)


class TestTrading(Base):
    def test_buy_executes_at_next_candle_open(self):
        uid = self.user()
        engine.act(self.conn, uid, "buy", 10, expected_step=S0)
        u = self.u(uid)
        t = engine.get_trades(self.conn, uid)[0]
        nxt = market.CANDLES[S0]
        self.assertEqual(t["price_usd"], nxt.open)
        self.assertEqual(t["trade_date"], nxt.date)
        self.assertEqual(t["amount"], 10 * unit(S0))
        self.assertEqual(u["cash"], engine.INITIAL - 10 * unit(S0))
        self.assertEqual(u["shares"], 10)
        self.assertEqual(u["step"], S0 + 1)  # الشمعة كُشفت بعد التنفيذ
        self.invariant(uid)

    def test_cannot_overspend_partial_fill(self):
        uid = self.user()
        engine.act(self.conn, uid, "buy", 1_000_000)
        u = self.u(uid)
        self.assertEqual(u["shares"], engine.INITIAL // unit(S0))
        self.assertGreaterEqual(u["cash"], 0)
        self.assertLess(u["cash"], unit(S0))
        # لا يكفي الرصيد لسهم واحد: لا تُنفَّذ صفقة والرصيد لا يصبح سالبًا
        n_before = len(engine.get_trades(self.conn, uid))
        msg = engine.act(self.conn, uid, "buy", 1)
        self.assertIn("لم يُنفَّذ", msg)
        self.assertEqual(len(engine.get_trades(self.conn, uid)), n_before)
        self.assertGreaterEqual(self.u(uid)["cash"], 0)
        self.invariant(uid)

    def test_cannot_sell_more_than_owned(self):
        uid = self.user()
        with self.assertRaises(engine.TradeError):
            engine.act(self.conn, uid, "sell", 1)
        engine.act(self.conn, uid, "buy", 4)
        step = self.u(uid)["step"]
        with self.assertRaises(engine.TradeError):
            engine.act(self.conn, uid, "sell", 5)
        self.assertEqual(self.u(uid)["step"], step)  # الرفض لا يكشف أي شمعة
        self.assertEqual(self.u(uid)["shares"], 4)

    def test_bad_quantities_rejected(self):
        uid = self.user()
        for q in [0, -3, "abc", None, "2.5"]:
            with self.assertRaises(engine.TradeError):
                engine.act(self.conn, uid, "buy", q)
        with self.assertRaises(engine.TradeError):
            engine.act(self.conn, uid, "short", 1)
        self.assertEqual(self.u(uid)["step"], S0)

    def test_realized_pnl_uses_cost_not_proceeds(self):
        uid = self.user()
        engine.act(self.conn, uid, "buy", 10)            # بسعر افتتاح S0
        engine.act(self.conn, uid, "buy", 6)             # بسعر افتتاح S0+1
        basis = 10 * unit(S0) + 6 * unit(S0 + 1)
        engine.act(self.conn, uid, "sell", 4)            # بسعر افتتاح S0+2
        removed = round(basis * 4 / 16)
        expected_gain = 4 * unit(S0 + 2) - removed
        t = engine.get_trades(self.conn, uid)[-1]
        self.assertEqual(t["realized"], expected_gain)
        u = self.u(uid)
        self.assertEqual(u["realized"], expected_gain)
        self.assertEqual(u["cost_basis"], basis - removed)
        self.assertNotEqual(u["realized"], t["amount"])  # ليس كامل مبلغ البيع ربحًا
        engine.act(self.conn, uid, "sell", 12)           # بيع الباقي كله
        u = self.u(uid)
        self.assertEqual(u["shares"], 0)
        self.assertEqual(u["cost_basis"], 0)
        self.assertEqual(u["cash"], engine.INITIAL + u["realized"])
        self.invariant(uid)

    def test_double_submit_executes_once(self):
        uid = self.user()
        engine.act(self.conn, uid, "buy", 2, expected_step=S0)
        with self.assertRaises(engine.TradeError):
            engine.act(self.conn, uid, "buy", 2, expected_step=S0)  # نفس النموذج مرة ثانية
        self.assertEqual(self.u(uid)["shares"], 2)
        self.assertEqual(self.u(uid)["step"], S0 + 1)

    def test_concurrent_same_decision_executes_once(self):
        uid = self.user()
        results = []

        def go():
            c = engine.connect(self.path)
            try:
                engine.act(c, uid, "buy", 1, expected_step=S0)
                results.append("ok")
            except engine.TradeError:
                results.append("rejected")
            finally:
                c.close()
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(self.u(uid)["shares"], 1)
        self.assertEqual(self.u(uid)["step"], S0 + 1)

    def test_no_trading_after_end(self):
        uid = self.user()
        for _ in range(market.N - S0):
            engine.act(self.conn, uid, "wait")
        self.assertEqual(self.u(uid)["step"], market.N)
        with self.assertRaises(engine.TradeError):
            engine.act(self.conn, uid, "wait")
        with self.assertRaises(engine.TradeError):
            engine.act(self.conn, uid, "buy", 1)


class TestReport(Base):
    def run_random(self, uid, seed):
        rnd = random.Random(seed)
        while self.u(uid)["step"] < market.N:
            u = self.u(uid)
            a = rnd.choice(["buy", "sell", "wait", "wait"])
            try:
                if a == "sell" and u["shares"]:
                    engine.act(self.conn, uid, "sell", rnd.randint(1, u["shares"]))
                elif a == "buy":
                    engine.act(self.conn, uid, "buy", rnd.randint(1, 30))
                else:
                    engine.act(self.conn, uid, "wait")
            except engine.TradeError:
                engine.act(self.conn, uid, "wait")
            self.invariant(uid)

    def test_final_report_formulas(self):
        for seed in range(6):
            uid = self.user(f"student{seed}")
            self.run_random(uid, seed)
            r = engine.report(self.conn, uid)
            last = market.CANDLES[-1].close
            mv = r["shares"] * engine.to_cents(last)
            self.assertTrue(r["finished"])
            self.assertEqual(r["initial"], 500 * 100)
            self.assertEqual(r["market_value"], mv)                       # بسعر آخر إغلاق
            self.assertEqual(r["total"], r["cash"] + mv)                   # دون احتساب مزدوج
            self.assertEqual(r["pnl"], r["total"] - engine.INITIAL)
            self.assertAlmostEqual(r["return_pct"], r["pnl"] / engine.INITIAL * 100)
            # الربح الكلي = المحقق + غير المحقق
            self.assertEqual(r["pnl"], r["realized"] + (mv - r["cost_basis"]))
            trades = r["trades"]
            self.assertEqual(r["n_trades"], len(trades))
            sells = [t for t in trades if t["side"] == "sell"]
            self.assertEqual(r["n_win"], sum(t["realized"] > 0 for t in sells))
            self.assertEqual(r["n_loss"], sum(t["realized"] < 0 for t in sells))
            self.assertEqual(sum(t["realized"] for t in sells), r["realized"])

    def test_buy_and_hold_matches_price_change(self):
        uid = self.user()
        engine.act(self.conn, uid, "buy", 30)
        while self.u(uid)["step"] < market.N:
            engine.act(self.conn, uid, "wait")
        r = engine.report(self.conn, uid)
        expected = 30 * (engine.to_cents(market.CANDLES[-1].close) - unit(S0))
        self.assertEqual(r["pnl"], expected)


class TestIsolationAndMarket(Base):
    def test_students_are_independent(self):
        a, b = self.user("A-student"), self.user("B-student")
        before_b = dict(self.u(b))
        engine.act(self.conn, a, "buy", 20)
        engine.act(self.conn, a, "sell", 5)
        self.assertEqual(dict(self.u(b)), before_b)
        self.assertEqual(engine.get_trades(self.conn, b), [])
        # المرحلة مستقلة لكل طالب، والسعر نفسه للجميع
        engine.act(self.conn, b, "buy", 20)
        tb = engine.get_trades(self.conn, b)[0]
        ta = engine.get_trades(self.conn, a)[0]
        self.assertEqual(ta["price_usd"], tb["price_usd"])
        self.assertEqual(ta["amount"], tb["amount"])

    def test_leaderboard_shows_only_name_and_return(self):
        a, b = self.user("Alpha"), self.user("Beta")
        engine.act(self.conn, a, "buy", 30)
        board = engine.leaderboard(self.conn)
        self.assertEqual({r["name"] for r in board}, {"Alpha", "Beta"})
        for r in board:
            self.assertEqual(set(r), {"name", "return_pct", "finished", "progress", "total"})
        pcts = [r["return_pct"] for r in board]
        self.assertEqual(pcts, sorted(pcts, reverse=True))

    def test_trading_never_changes_market_data(self):
        def digest():
            with open(market.DATA_FILE, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        file_before, mem_before = digest(), tuple(market.CANDLES)
        for i in range(5):
            uid = self.user(f"s{i}")
            for _ in range(8):
                engine.act(self.conn, uid, "buy", 7)
                engine.act(self.conn, uid, "sell", 7)
        self.assertEqual(digest(), file_before)
        self.assertEqual(tuple(market.CANDLES), mem_before)
        with self.assertRaises(TypeError):
            market.CANDLES[0] = None  # البيانات غير قابلة للتعديل
        with self.assertRaises(TypeError):
            market.STOCK["symbol"] = "X"
        # قاعدة البيانات لا تحتوي أي جدول للأسعار
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables - {"sqlite_sequence"}, {"users", "trades"})

    def test_no_lookahead(self):
        uid = self.user()
        for _ in range(10):
            step = self.u(uid)["step"]
            vis = market.visible(step)
            self.assertEqual(len(vis), step)
            nxt = market.CANDLES[step]
            self.assertTrue(all(c.date < nxt.date for c in vis))
            engine.act(self.conn, uid, "buy", 1, expected_step=step)
            t = engine.get_trades(self.conn, uid)[-1]
            # سعر التنفيذ هو افتتاح شمعة لم تكن ظاهرة عند القرار، وليس إغلاقها
            self.assertEqual(t["price_usd"], nxt.open)
            self.assertNotIn(nxt.date, [c.date for c in vis])
        self.assertEqual(len(market.visible(10_000)), market.N)
        self.assertEqual(len(market.visible(-5)), 0)


class TestMarketData(unittest.TestCase):
    def test_real_data_shape(self):
        c = market.CANDLES
        self.assertEqual(len(market.ALL_CANDLES), 138)
        self.assertEqual(market.N, 40)
        self.assertEqual(market.N - market.START_VISIBLE, 21)
        self.assertEqual(c[0].date, "2023-06-01")
        self.assertEqual(c[-1].date, "2023-07-28")
        self.assertEqual(c[market.START_VISIBLE - 1].date, "2023-06-28")
        for x in c:
            self.assertGreaterEqual(x.high, max(x.open, x.close))
            self.assertLessEqual(x.low, min(x.open, x.close))
            self.assertGreater(x.volume, 0)

    def test_data_fingerprint_matches_yahoo_download(self):
        # بصمة SHA-256 حُسبت داخل المتصفح مباشرة على استجابة Yahoo Finance
        s = ";".join(",".join([c.date] + [f"{v:g}" for v in c[1:5]] + [str(c[5])]) for c in market.ALL_CANDLES)
        self.assertEqual(hashlib.sha256(s.encode()).hexdigest()[:16], "c86a6aa7f53133be")


if __name__ == "__main__":
    unittest.main()
