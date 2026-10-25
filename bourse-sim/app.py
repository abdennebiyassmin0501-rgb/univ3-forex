"""منصة محاكاة التداول التعليمية — تطبيق Flask.

التشغيل محليًا:  python app.py   ثم افتح http://127.0.0.1:5000
"""
import os
import secrets

from flask import Flask, flash, g, redirect, render_template, request, session, url_for

import engine
import market

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _secret_key(instance_dir):
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(instance_dir, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


def create_app(db_path=None):
    app = Flask(__name__)
    instance_dir = os.path.join(BASE_DIR, "instance")
    os.makedirs(instance_dir, exist_ok=True)
    app.config["DB_PATH"] = db_path or os.environ.get("BOURSE_DB") or os.path.join(instance_dir, "bourse.db")
    app.secret_key = _secret_key(instance_dir)
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 60
    engine.init_db(app.config["DB_PATH"])

    def db():
        if "db" not in g:
            g.db = engine.connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def current_user():
        uid = session.get("uid")
        return engine.get_user(db(), uid) if uid else None

    @app.context_processor
    def _globals():
        return {"stock": market.STOCK,
                "usd": lambda c: f"\u2066{c / 100:,.2f} $\u2069",
                "pct": lambda x: f"\u2066{x:+.2f}%\u2069", "initial_usd": market.INITIAL_CASH_USD}

    # ---------- الصفحة 1: الدخول ----------
    @app.route("/", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            try:
                user, created = engine.login(db(), request.form.get("username", ""))
            except engine.TradeError as e:
                flash(str(e), "error")
                return render_template("login.html", username=request.form.get("username", "")), 400
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            if created:
                flash(f"مرحبًا {user['display_name']}! تم إنشاء حسابك برصيد 500$.", "ok")
            else:
                flash(f"مرحبًا بعودتك {user['display_name']}! تم استرجاع حسابك.", "ok")
            return redirect(url_for("trade"))
        if current_user():
            return redirect(url_for("trade"))
        return render_template("login.html", username="")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- الصفحة 2: التداول ----------
    @app.route("/trade", methods=["GET", "POST"])
    def trade():
        u = current_user()
        if u is None:
            return redirect(url_for("login"))
        if request.method == "POST":
            try:
                msg = engine.act(db(), u["id"], request.form.get("action"),
                                 request.form.get("qty", 0), request.form.get("step"))
                flash(msg, "ok")
            except engine.TradeError as e:
                flash(str(e), "error")
            return redirect(url_for("trade"))
        p = engine.portfolio(u)
        if p["finished"]:
            return redirect(url_for("result"))
        trades = engine.get_trades(db(), u["id"])
        candles = market.visible(u["step"])          # الماضي فقط
        chart = {
            "candles": [c.as_dict() for c in candles],
            "trades": [{"i": t["candle_index"], "side": t["side"], "qty": t["qty"]} for t in trades],
            "user": u["username_key"],
        }
        return render_template("trade.html", u=u, p=p, trades=trades, chart=chart,
                               last=candles[-1])

    # ---------- الصفحة 3: النتيجة النهائية + الترتيب ----------
    @app.route("/result")
    def result():
        u = current_user()
        if u is None:
            return redirect(url_for("login"))
        rep = engine.report(db(), u["id"])
        return render_template("result.html", u=u, r=rep, board=engine.leaderboard(db()),
                               last=market.CANDLES[-1])

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
