"""ふたり精算ノート pairsettle

2人の立て替え支出を記録し、今どちらがどちらへいくら精算すればよいかを
常に表示する小さな Web ツール。単一ファイル Flask + SQLite。
"""
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, g, redirect, render_template_string, request, url_for, jsonify

app = Flask(__name__)
JST = ZoneInfo("Asia/Tokyo")
DB_PATH = os.environ.get("PAIRSETTLE_DB", os.path.join(os.path.dirname(__file__), "pairsettle.db"))

# 分担方法: キー -> (表示名, 相手が負担する割合)
SPLIT_OPTIONS = {
    "half": ("半々", 0.5),
    "7:3": ("7:3（払った人が7割）", 0.3),
    "6:4": ("6:4（払った人が6割）", 0.4),
    "3:7": ("3:7（払った人が3割）", 0.7),
    "4:6": ("4:6（払った人が4割）", 0.6),
    "other_full": ("相手が全額", 1.0),
}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payer TEXT NOT NULL,
            amount INTEGER NOT NULL,
            memo TEXT NOT NULL DEFAULT '',
            split TEXT NOT NULL DEFAULT 'half',
            created_at TEXT NOT NULL
        );
        """
    )
    db.commit()
    db.close()


def get_names(db):
    rows = dict(db.execute("SELECT key, value FROM settings").fetchall())
    return rows.get("member1", "Aさん"), rows.get("member2", "Bさん")


def calc_settlement(db):
    """戻り値: (債務者index, 債権者index, 金額)。精算不要なら (None, None, 0)。
    payer='1' の支出では member2 が負担分を member1 に返す、という向きで合算する。"""
    balance = 0  # 正なら member2 -> member1 への支払い額
    for row in db.execute("SELECT payer, amount, split FROM expenses"):
        share = SPLIT_OPTIONS.get(row["split"], SPLIT_OPTIONS["half"])[1]
        owed = round(row["amount"] * share)
        if row["payer"] == "1":
            balance += owed
        else:
            balance -= owed
    if balance > 0:
        return "2", "1", balance
    if balance < 0:
        return "1", "2", -balance
    return None, None, 0


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ふたり精算ノート</title>
<style>
body{font-family:sans-serif;max-width:640px;margin:2em auto;padding:0 1em}
.result{background:#eef7ee;border:1px solid #9c9;padding:1em;border-radius:8px;font-size:1.2em}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ccc;padding:.4em}
form{margin:1em 0}input,select{padding:.3em}
</style></head><body>
<h1>ふたり精算ノート</h1>
<div class="result">
{% if settle_amount %}
  <strong>{{ debtor_name }} → {{ creditor_name }} へ ¥{{ "{:,}".format(settle_amount) }}</strong>
{% else %}
  精算は不要です（差し引きゼロ）
{% endif %}
</div>

<h2>支出を登録</h2>
<form method="post" action="{{ url_for('add_expense') }}">
  <label>払った人
    <select name="payer">
      <option value="1">{{ names[0] }}</option>
      <option value="2">{{ names[1] }}</option>
    </select></label>
  <label>金額 <input type="number" name="amount" min="1" required> 円</label>
  <label>内容 <input type="text" name="memo" placeholder="スーパー など"></label>
  <label>分担
    <select name="split">
      {% for key, (label, _) in split_options.items() %}
      <option value="{{ key }}">{{ label }}</option>
      {% endfor %}
    </select></label>
  <button type="submit">登録</button>
</form>

<h2>支出一覧</h2>
<table>
<tr><th>日時</th><th>払った人</th><th>内容</th><th>金額</th><th>分担</th><th></th></tr>
{% for e in expenses %}
<tr>
  <td>{{ e.created_at }}</td>
  <td>{{ names[0] if e.payer == '1' else names[1] }}</td>
  <td>{{ e.memo }}</td>
  <td>¥{{ "{:,}".format(e.amount) }}</td>
  <td>{{ split_options.get(e.split, split_options['half'])[0] }}</td>
  <td><form method="post" action="{{ url_for('delete_expense', eid=e.id) }}"><button>削除</button></form></td>
</tr>
{% else %}
<tr><td colspan="6">まだ支出がありません</td></tr>
{% endfor %}
</table>

<h2>メンバーの名前</h2>
<form method="post" action="{{ url_for('save_settings') }}">
  <label>1人目 <input type="text" name="member1" value="{{ names[0] }}"></label>
  <label>2人目 <input type="text" name="member2" value="{{ names[1] }}"></label>
  <button type="submit">保存</button>
</form>
</body></html>"""


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    db = get_db()
    names = get_names(db)
    expenses = db.execute("SELECT * FROM expenses ORDER BY id DESC").fetchall()
    debtor, creditor, amount = calc_settlement(db)
    name_of = {"1": names[0], "2": names[1]}
    return render_template_string(
        PAGE,
        names=names,
        expenses=expenses,
        split_options=SPLIT_OPTIONS,
        settle_amount=amount,
        debtor_name=name_of.get(debtor, ""),
        creditor_name=name_of.get(creditor, ""),
    )


@app.route("/settings", methods=["POST"])
def save_settings():
    db = get_db()
    for key in ("member1", "member2"):
        value = (request.form.get(key) or "").strip()
        if value:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    db.commit()
    return redirect(url_for("index"))


@app.route("/expenses", methods=["POST"])
def add_expense():
    payer = request.form.get("payer", "1")
    if payer not in ("1", "2"):
        payer = "1"
    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        amount = 0
    split = request.form.get("split", "half")
    if split not in SPLIT_OPTIONS:
        split = "half"
    memo = (request.form.get("memo") or "").strip()
    if amount > 0:
        db = get_db()
        db.execute(
            "INSERT INTO expenses(payer, amount, memo, split, created_at) VALUES(?, ?, ?, ?, ?)",
            (payer, amount, memo, split, datetime.now(JST).strftime("%Y-%m-%d %H:%M")),
        )
        db.commit()
    return redirect(url_for("index"))


@app.route("/expenses/<int:eid>/delete", methods=["POST"])
def delete_expense(eid):
    db = get_db()
    db.execute("DELETE FROM expenses WHERE id = ?", (eid,))
    db.commit()
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
