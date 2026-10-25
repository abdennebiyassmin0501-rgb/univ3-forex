"""منطق المحاكاة وقاعدة البيانات (SQLite).

ترتيب الأحداث لكل طالب (نفس القاعدة للجميع):
  1) يرى الطالب الشموع 0 .. step-1 فقط.
  2) يتخذ قرارًا: شراء / بيع / انتظار.
  3) يُنفَّذ القرار بسعر افتتاح الشمعة رقم step (الشمعة التالية التي لم تظهر بعد).
  4) تُكشف الشمعة رقم step (step += 1).
عندما يصبح step == N تنتهي المحاكاة وتُقيَّم الأسهم المتبقية بسعر إغلاق آخر شمعة.

كل المبالغ بالدولار وتُخزَّن بالسنت (عدد صحيح) لتفادي أخطاء الكسور العشرية.
"""
import re
import sqlite3
import unicodedata

import market

INITIAL = market.INITIAL_CASH_USD * 100  # بالسنت

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    cash INTEGER NOT NULL,
    shares INTEGER NOT NULL DEFAULT 0,
    cost_basis INTEGER NOT NULL DEFAULT 0,
    realized INTEGER NOT NULL DEFAULT 0,
    step INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (cash >= 0), CHECK (shares >= 0), CHECK (cost_basis >= 0)
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    candle_index INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy','sell')),
    qty INTEGER NOT NULL CHECK (qty > 0),
    price_usd REAL NOT NULL,
    amount INTEGER NOT NULL,
    realized INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id);
"""


class TradeError(Exception):
    """خطأ يُعرض للطالب دون تنفيذ أي شيء ودون الانتقال للشمعة التالية."""


def connect(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(path):
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    conn.executescript(SCHEMA)
    conn.close()


# ---------- الحسابات ----------

def clean_name(raw):
    name = unicodedata.normalize("NFKC", raw or "")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def username_key(raw):
    """مفتاح موحّد: يمنع تكرار الحساب بسبب الأحرف الكبيرة/الصغيرة أو المسافات."""
    return clean_name(raw).casefold()


def validate_name(raw):
    name = clean_name(raw)
    if len(name) < 2:
        raise TradeError("اسم المستخدم قصير جدًا (حرفان على الأقل).")
    if len(name) > 30:
        raise TradeError("اسم المستخدم طويل جدًا (30 حرفًا على الأكثر).")
    return name


def login(conn, raw_name):
    """يفتح الحساب إن وُجد، وإلا ينشئه برصيد 500$. يُرجع (user, created)."""
    name = validate_name(raw_name)
    key = username_key(name)
    cur = conn.execute(
        "INSERT OR IGNORE INTO users (username_key, display_name, cash, step) VALUES (?,?,?,?)",
        (key, name, INITIAL, market.START_VISIBLE),
    )
    created = cur.rowcount == 1
    user = conn.execute("SELECT * FROM users WHERE username_key=?", (key,)).fetchone()
    return user, created


def get_user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def get_trades(conn, user_id):
    return conn.execute(
        "SELECT * FROM trades WHERE user_id=? ORDER BY id", (user_id,)).fetchall()


# ---------- التداول ----------

def to_cents(usd):
    return round(usd * 100)


def act(conn, user_id, action, qty=0, expected_step=None):
    """ينفذ قرار الطالب ثم يكشف الشمعة التالية.

    action: 'buy' | 'sell' | 'wait'
    expected_step: رقم الشمعة التي رآها الطالب عند اتخاذ القرار؛ يمنع تنفيذ
    القرار مرتين عند الضغط المزدوج أو فتح الصفحة في نافذتين.
    يُرجع رسالة نصية تصف ما حدث.
    """
    if action not in ("buy", "sell", "wait"):
        raise TradeError("قرار غير معروف.")
    if action in ("buy", "sell"):
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            raise TradeError("أدخل كمية صحيحة (عدد صحيح).")
        if qty <= 0:
            raise TradeError("يجب أن تكون الكمية أكبر من صفر.")

    conn.execute("BEGIN IMMEDIATE")
    try:
        u = get_user(conn, user_id)
        if u is None:
            raise TradeError("الحساب غير موجود.")
        step = u["step"]
        if step >= market.N:
            raise TradeError("انتهت المحاكاة؛ لا يمكن تنفيذ صفقات جديدة.")
        if expected_step is not None and int(expected_step) != step:
            raise TradeError("تم تحديث الصفحة: هذا القرار نُفِّذ أو تغيّرت المرحلة. راجع الوضع الحالي.")
        if action == "sell" and qty > u["shares"]:
            raise TradeError(f"لا يمكنك بيع {qty} سهمًا؛ تملك {u['shares']} فقط.")

        candle = market.CANDLES[step]          # الشمعة التالية (لم يرها الطالب بعد)
        price = candle.open                    # سعر التنفيذ = سعر الافتتاح
        unit = to_cents(price)
        cash, shares, basis, realized = u["cash"], u["shares"], u["cost_basis"], u["realized"]
        msg = "انتظار: لم تُنفَّذ أي صفقة."

        if action == "buy":
            affordable = cash // unit
            filled = min(qty, affordable)
            if filled == 0:
                msg = (f"لم يُنفَّذ الشراء: الرصيد لا يكفي لسهم واحد بسعر الافتتاح "
                       f"{price:.2f}$.")
            else:
                amount = filled * unit
                cash -= amount
                shares += filled
                basis += amount
                conn.execute(
                    "INSERT INTO trades (user_id,candle_index,trade_date,side,qty,price_usd,amount,realized)"
                    " VALUES (?,?,?,?,?,?,?,NULL)",
                    (user_id, step, candle.date, "buy", filled, price, amount))
                msg = f"تم شراء {filled} سهم بسعر الافتتاح {price:.2f}$ (التكلفة {amount/100:,.2f}$)."
                if filled < qty:
                    msg += f" نُفِّذ {filled} فقط من {qty} لأن الرصيد لا يكفي."
        elif action == "sell":
            amount = qty * unit
            removed = basis if qty == shares else round(basis * qty / shares)
            gain = amount - removed
            cash += amount
            shares -= qty
            basis -= removed
            realized += gain
            conn.execute(
                "INSERT INTO trades (user_id,candle_index,trade_date,side,qty,price_usd,amount,realized)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user_id, step, candle.date, "sell", qty, price, amount, gain))
            msg = (f"تم بيع {qty} سهم بسعر الافتتاح {price:.2f}$ (المبلغ {amount/100:,.2f}$، "
                   f"الربح/الخسارة المحققة {gain/100:+,.2f}$).")

        assert cash >= 0 and shares >= 0 and basis >= 0
        cur = conn.execute(
            "UPDATE users SET cash=?, shares=?, cost_basis=?, realized=?, step=step+1"
            " WHERE id=? AND step=?",
            (cash, shares, basis, realized, user_id, step))
        if cur.rowcount != 1:
            raise TradeError("تعذّر تحديث الحساب، أعد المحاولة.")
        conn.execute("COMMIT")
        return msg
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# ---------- التقييم والتقارير ----------

def mark_price(step):
    """آخر سعر معروف للطالب: إغلاق آخر شمعة ظاهرة."""
    return market.CANDLES[max(step, 1) - 1].close


def portfolio(u):
    step = u["step"]
    price = mark_price(step)
    market_value = u["shares"] * to_cents(price)
    total = u["cash"] + market_value
    pnl = total - INITIAL
    avg = (u["cost_basis"] / u["shares"]) if u["shares"] else 0
    return {
        "finished": step >= market.N,
        "step": step,
        "decisions_done": step - market.START_VISIBLE,
        "decisions_total": market.N - market.START_VISIBLE,
        "last_date": market.CANDLES[step - 1].date,
        "mark_price_usd": price,
        "cash": u["cash"],
        "shares": u["shares"],
        "cost_basis": u["cost_basis"],
        "avg_cost": avg,
        "market_value": market_value,
        "unrealized": market_value - u["cost_basis"],
        "realized": u["realized"],
        "total": total,
        "pnl": pnl,
        "return_pct": pnl / INITIAL * 100,
    }


def report(conn, user_id):
    u = get_user(conn, user_id)
    p = portfolio(u)
    trades = get_trades(conn, user_id)
    sells = [t for t in trades if t["side"] == "sell"]
    p.update({
        "initial": INITIAL,
        "n_trades": len(trades),
        "n_buys": len(trades) - len(sells),
        "n_sells": len(sells),
        "n_win": sum(1 for t in sells if t["realized"] > 0),
        "n_loss": sum(1 for t in sells if t["realized"] < 0),
        "trades": trades,
    })
    return p


def leaderboard(conn):
    """أسماء الطلبة ونسبة العائد فقط — دون أي تفاصيل عن صفقاتهم."""
    rows = []
    for u in conn.execute("SELECT * FROM users").fetchall():
        p = portfolio(u)
        rows.append({"name": u["display_name"], "return_pct": p["return_pct"],
                     "finished": p["finished"], "progress": p["decisions_done"],
                     "total": p["decisions_total"]})
    rows.sort(key=lambda r: (-r["return_pct"], r["name"]))
    return rows
