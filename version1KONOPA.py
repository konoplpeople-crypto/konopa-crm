import os
import sqlite3
import uuid
import json
import html
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse, urlencode, quote
from pathlib import Path

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8080))
DB_NAME = "konopa_orders_v2.db"
DB_PATH = Path(DB_NAME)

CATEGORIES = [
    "Електротовар",
    "Побутова техніка",
    "Товар для дому",
    "М'яка іграшка",
    "Іграшки",
    "Одяг",
    "Взуття",
    "Аксесуари",
    "Косметика",
    "Посуд",
    "Текстиль",
    "Декор",
    "Господарські товари",
    "Товари для кухні",
    "Товари для ванної",
    "Дитячі товари",
    "Сезонні товари",
    "Подарунки",
    "Інше",
]

STATUSES = {
    "WAITING": "Очікує оплату",
    "PAID": "Оплачено",
    "CHECK_CREATED": "Чек створено",
}

STATUS_COLORS = {
    "WAITING": "#b45309",
    "PAID": "#1d4ed8",
    "CHECK_CREATED": "#047857",
}

# Реквізити
RECEIVER = "ФОП Козій Ольга Іванівна"
IBAN = "UA943052990000026000035109214"
BANK_NAME = "АТ КБ «ПриватБанк»"
EDRPOU = "2196427266"
PAYMENT_PURPOSE_TEMPLATE = "Оплата за замовлення {order_id}"

# Шаблони посилань на оплату. Якщо поки немає — залиш порожніми.
MONO_PAYMENT_BASE = ""
PRIVAT_PAYMENT_BASE = ""

# Заготовка під Checkbox
CHECKBOX_ENABLED = False
CHECKBOX_CASHIER_NAME = "MANAGER_1"
CHECKBOX_SELLER_TOKEN = ""
CHECKBOX_API_BASE = "https://api.checkbox.in.ua"


def esc(value):
    return html.escape(str(value))


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_query(base_url, params):
    if not base_url:
        return ""
    sep = "&" if "?" in base_url else "?"
    query = "&".join(f"{quote(str(k))}={quote(str(v))}" for k, v in params.items())
    return f"{base_url}{sep}{query}"


def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            buyer_nick TEXT NOT NULL,
            payer_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            receipt_number TEXT NOT NULL DEFAULT '',
            fiscal_number TEXT NOT NULL DEFAULT '',
            receipt_id TEXT NOT NULL DEFAULT '',
            receipt_link TEXT NOT NULL DEFAULT '',
            receipt_qr TEXT NOT NULL DEFAULT '',
            receipt_text TEXT NOT NULL DEFAULT '',
            checkbox_status TEXT NOT NULL DEFAULT '',
            checkbox_raw TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            product_number TEXT NOT NULL,
            product_title TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


def create_order_with_first_item(buyer_nick, payer_name, product_number, product_title, category, amount):
    order_id = uuid.uuid4().hex[:8].upper()
    item_id = uuid.uuid4().hex[:10].upper()
    ts = now_str()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO orders (id, buyer_nick, payer_name, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (order_id, buyer_nick, payer_name, "WAITING", ts, ts),
    )
    conn.execute(
        """
        INSERT INTO order_items (id, order_id, product_number, product_title, category, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, order_id, product_number, product_title, category, amount, ts),
    )
    conn.commit()
    conn.close()
    return order_id


def add_item(order_id, product_number, product_title, category, amount):
    item_id = uuid.uuid4().hex[:10].upper()
    ts = now_str()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO order_items (id, order_id, product_number, product_title, category, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, order_id, product_number, product_title, category, amount, ts),
    )
    conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (ts, order_id))
    conn.commit()
    conn.close()


def update_order(order_id, buyer_nick, payer_name):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET buyer_nick = ?, payer_name = ?, updated_at = ? WHERE id = ?",
        (buyer_nick, payer_name, now_str(), order_id),
    )
    conn.commit()
    conn.close()


def update_item(item_id, product_number, product_title, category, amount):
    conn = get_conn()
    row = conn.execute("SELECT order_id FROM order_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return
    conn.execute(
        """
        UPDATE order_items
        SET product_number = ?, product_title = ?, category = ?, amount = ?
        WHERE id = ?
        """,
        (product_number, product_title, category, amount, item_id),
    )
    conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (now_str(), row["order_id"]))
    conn.commit()
    conn.close()


def delete_item(item_id):
    conn = get_conn()
    row = conn.execute("SELECT order_id FROM order_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return
    conn.execute("DELETE FROM order_items WHERE id = ?", (item_id,))
    conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (now_str(), row["order_id"]))
    conn.commit()
    conn.close()


def delete_order(order_id):
    conn = get_conn()
    conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()


def set_status(order_id, status):
    conn = get_conn()
    conn.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (status, now_str(), order_id))
    conn.commit()
    conn.close()


def get_order(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        return None
    items = conn.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY created_at ASC", (order_id,)).fetchall()
    conn.close()
    order_dict = dict(order)
    order_dict["items"] = [dict(x) for x in items]
    order_dict["total_amount"] = sum(float(x["amount"]) for x in items)
    return order_dict


def list_orders(search="", status=""):
    conn = get_conn()
    query = """
        SELECT o.*, COALESCE(SUM(i.amount), 0) AS total_amount, COUNT(i.id) AS items_count
        FROM orders o
        LEFT JOIN order_items i ON i.order_id = o.id
        WHERE 1=1
    """
    params = []
    if search:
        term = f"%{search}%"
        query += " AND (o.id LIKE ? OR o.buyer_nick LIKE ? OR o.payer_name LIKE ? OR i.product_title LIKE ? OR i.product_number LIKE ?)"
        params.extend([term, term, term, term, term])
    if status:
        query += " AND o.status = ?"
        params.append(status)
    query += " GROUP BY o.id ORDER BY o.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(x) for x in rows]


def compute_stats():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT o.id, o.status, o.created_at, COALESCE(SUM(i.amount), 0) AS total_amount
        FROM orders o
        LEFT JOIN order_items i ON i.order_id = o.id
        GROUP BY o.id
        """
    ).fetchall()
    conn.close()

    now = datetime.now()
    buckets = {
        "day": {"orders": 0, "sum": 0.0, "paid_orders": 0, "paid_sum": 0.0},
        "week": {"orders": 0, "sum": 0.0, "paid_orders": 0, "paid_sum": 0.0},
        "month": {"orders": 0, "sum": 0.0, "paid_orders": 0, "paid_sum": 0.0},
    }

    for row in rows:
        created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
        amount = float(row["total_amount"])
        delta = now - created
        periods = []
        if created.date() == now.date():
            periods.append("day")
        if delta <= timedelta(days=7):
            periods.append("week")
        if delta <= timedelta(days=30):
            periods.append("month")
        for p in periods:
            buckets[p]["orders"] += 1
            buckets[p]["sum"] += amount
            if row["status"] in ("PAID", "CHECK_CREATED"):
                buckets[p]["paid_orders"] += 1
                buckets[p]["paid_sum"] += amount
    return buckets


def build_message_for_client(order):
    lines = [
        "Вітаємо!",
        "",
        "Ваше замовлення прийнято ✅",
        "",
        f"📦 № замовлення: {order['id']}",
        f"👤 Нік: {order['buyer_nick']}",
        "",
        "🛍 Склад замовлення:",
    ]
    for idx, item in enumerate(order["items"], start=1):
        lines.append(f"{idx}. {item['product_title']} / лот #{item['product_number']} / {float(item['amount']):.2f} грн")
    lines.extend([
        "",
        f"💰 Загальна сума: {order['total_amount']:.2f} грн",
        "",
        "━━━━━━━━━━━━━━━",
        "💳 Реквізити для оплати (гривні):",
        "",
        RECEIVER,
        f"📌 IBAN: {IBAN}",
        f"🏦 Банк: {BANK_NAME}",
        f"ЄДРПОУ: {EDRPOU}",
        "",
        "📄 Призначення платежу:",
        PAYMENT_PURPOSE_TEMPLATE.format(order_id=order['id']),
        "",
        "━━━━━━━━━━━━━━━",
        "",
        "Після оплати, будь ласка, надішліть підтвердження 🙏",
    ])
    mono = build_mono_link(order)
    privat = build_privat_link(order)
    if mono:
        lines.extend(["", f"Mono: {mono}"])
    if privat:
        lines.extend(["", f"Privat: {privat}"])
    return "\n".join(lines)


def build_checkbox_payload(order):
    goods = []
    for item in order["items"]:
        goods.append({
            "code": item["product_number"],
            "name": item["product_title"],
            "price": round(float(item["amount"]), 2),
            "quantity": 1,
            "category": item["category"],
        })
    payload = {
        "order_id": order["id"],
        "buyer_nick": order["buyer_nick"],
        "payer_name": order["payer_name"],
        "total_amount": round(float(order["total_amount"]), 2),
        "goods": goods,
        "payments": [{"type": "CASHLESS", "value": round(float(order["total_amount"]), 2)}],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_mono_link(order):
    if not MONO_PAYMENT_BASE:
        return ""
    return append_query(MONO_PAYMENT_BASE, {
        "order_id": order["id"],
        "amount": f"{order['total_amount']:.2f}",
        "buyer": order["buyer_nick"],
        "payer": order["payer_name"],
    })


def build_privat_link(order):
    if not PRIVAT_PAYMENT_BASE:
        return ""
    return append_query(PRIVAT_PAYMENT_BASE, {
        "order_id": order["id"],
        "amount": f"{order['total_amount']:.2f}",
        "buyer": order["buyer_nick"],
        "payer": order["payer_name"],
    })


def create_checkbox_receipt(order_id):
    order = get_order(order_id)
    if not order:
        return False, "Замовлення не знайдено"
    if not order["items"]:
        return False, "У замовленні немає товарів"
    if order["status"] not in ("PAID", "CHECK_CREATED"):
        return False, "Спочатку потрібно підтвердити оплату"

    payload = {
        "order_id": order["id"],
        "receipt_number": f"R-{order['id']}",
        "fiscal_number": f"FISCAL-{order['id']}",
        "receipt_id": f"RCPT-{order['id']}",
        "receipt_link": f"https://checkbox.local/receipt/{order['id']}",
        "receipt_qr": f"QR-{order['id']}",
        "receipt_text": f"Чек за замовленням {order['id']} на суму {order['total_amount']:.2f} грн",
        "checkbox_status": "mock_saved" if not CHECKBOX_ENABLED else "sent_to_checkbox",
        "checkbox_raw": build_checkbox_payload(order),
    }

    conn = get_conn()
    conn.execute(
        """
        UPDATE orders
        SET status = ?, receipt_number = ?, fiscal_number = ?, receipt_id = ?, receipt_link = ?,
            receipt_qr = ?, receipt_text = ?, checkbox_status = ?, checkbox_raw = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            "CHECK_CREATED",
            payload["receipt_number"],
            payload["fiscal_number"],
            payload["receipt_id"],
            payload["receipt_link"],
            payload["receipt_qr"],
            payload["receipt_text"],
            payload["checkbox_status"],
            payload["checkbox_raw"],
            now_str(),
            order_id,
        ),
    )
    conn.commit()
    conn.close()
    return True, "Чек створено"


def mark_paid_and_create_receipt(order_id):
    order = get_order(order_id)
    if not order:
        return False, "Замовлення не знайдено"
    if not order["items"]:
        return False, "У замовленні немає товарів"
    set_status(order_id, "PAID")
    return create_checkbox_receipt(order_id)


def page_template(title, content):
    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(title)}</title>
    <style>
        :root {{
            --bg: #0f172a;
            --bg-soft: #111827;
            --panel: #ffffff;
            --panel-soft: #f8fafc;
            --line: #e5e7eb;
            --text: #0f172a;
            --muted: #64748b;
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --success: #059669;
            --success-dark: #047857;
            --danger: #dc2626;
            --warning: #d97706;
            --shadow: 0 18px 40px rgba(15, 23, 42, 0.12);
            --radius: 18px;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: Arial, sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at top left, rgba(37,99,235,0.18), transparent 28%),
                radial-gradient(circle at top right, rgba(5,150,105,0.15), transparent 24%),
                linear-gradient(180deg, #0b1220 0%, #101b31 18%, #eef2f7 18%, #f4f7fb 100%);
            min-height: 100vh;
        }}
        .topbar {{ color: white; padding: 24px 0 28px; }}
        .wrap {{ max-width: 1320px; margin: 0 auto; padding: 0 18px 28px; }}
        .topbar-inner {{ display: flex; justify-content: space-between; gap: 16px; align-items: center; }}
        .brand-title {{ font-size: 30px; font-weight: 700; margin: 0 0 8px; }}
        .brand-sub {{ margin: 0; color: rgba(255,255,255,0.82); }}
        .card {{ background: var(--panel); border-radius: var(--radius); padding: 20px; margin-bottom: 18px; box-shadow: var(--shadow); border: 1px solid rgba(255,255,255,0.6); }}
        .card-soft {{ background: var(--panel-soft); }}
        h1, h2, h3 {{ margin: 0 0 12px; }}
        p {{ margin: 0 0 10px; }}
        .muted {{ color: var(--muted); }}
        .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
        .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }}
        .stats-card {{ border-radius: 18px; padding: 18px; color: white; min-height: 128px; box-shadow: var(--shadow); }}
        .s1 {{ background: linear-gradient(135deg, #2563eb, #1d4ed8); }}
        .s2 {{ background: linear-gradient(135deg, #059669, #047857); }}
        .s3 {{ background: linear-gradient(135deg, #7c3aed, #5b21b6); }}
        .s4 {{ background: linear-gradient(135deg, #ea580c, #c2410c); }}
        .stat-label {{ font-size: 13px; opacity: 0.9; }}
        .stat-number {{ font-size: 28px; font-weight: 700; margin: 12px 0 6px; }}
        .stat-sub {{ font-size: 13px; opacity: 0.9; }}
        label {{ display: block; font-weight: 700; margin: 0 0 6px; }}
        input, select, textarea {{ width: 100%; padding: 11px 12px; border-radius: 12px; border: 1px solid #dbe2ea; font-size: 14px; background: white; }}
        textarea {{ min-height: 220px; resize: vertical; }}
        .row-6 {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }}
        .row-5 {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }}
        .row-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
        .row-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }}
        button, .button-link {{ border: none; border-radius: 12px; padding: 11px 14px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }}
        .btn {{ background: var(--primary); color: white; }}
        .btn:hover {{ background: var(--primary-dark); }}
        .btn-success {{ background: var(--success); color: white; }}
        .btn-success:hover {{ background: var(--success-dark); }}
        .btn-secondary {{ background: #e5e7eb; color: #111827; }}
        .btn-danger {{ background: var(--danger); color: white; }}
        .btn-warning {{ background: var(--warning); color: white; }}
        .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }}
        .actions-inline {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
        .inline-form {{ margin: 0; }}
        .small-btn {{ padding: 8px 10px; font-size: 13px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
        th {{ color: var(--muted); font-size: 13px; }}
        .status-pill {{ display: inline-block; padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 700; color: white; }}
        .mono {{ white-space: pre-wrap; background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px; border-radius: 14px; font-family: Consolas, monospace; }}
        .item-box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 14px; padding: 14px; margin-bottom: 10px; }}
        .total-box {{ background: linear-gradient(135deg, #eff6ff, #ecfeff); border: 1px solid #bfdbfe; border-radius: 16px; padding: 16px; }}
        a {{ color: var(--primary); text-decoration: none; }}
        .badge-note {{ display: inline-block; font-size: 12px; color: white; background: rgba(255,255,255,0.16); padding: 6px 10px; border-radius: 999px; }}
        @media (max-width: 1100px) {{ .grid-4, .row-6, .row-5, .row-3 {{ grid-template-columns: repeat(2, 1fr); }} }}
        @media (max-width: 760px) {{
            .grid-4, .grid-2, .row-6, .row-5, .row-3, .row-2 {{ grid-template-columns: 1fr; }}
            .topbar-inner {{ flex-direction: column; align-items: flex-start; }}
            table, thead, tbody, th, td, tr {{ display: block; width: 100%; }}
            thead {{ display: none; }}
            td {{ border-bottom: none; padding: 6px 0; }}
            tr {{ border-bottom: 1px solid var(--line); padding: 12px 0; }}
        }}
    </style>
</head>
<body>
    <div class="topbar">
        <div class="wrap">
            <div class="topbar-inner">
                <div>
                    <h1 class="brand-title">Konopa CRM v2</h1>
                    <p class="brand-sub">Мультизамовлення, красивий інтерфейс, статистика та підготовка під Checkbox.</p>
                </div>
                <div class="badge-note">База: {esc(DB_PATH)}</div>
            </div>
        </div>
    </div>
    <div class="wrap">{content}</div>
</body>
</html>
"""


def render_home(search="", status_filter="", message=""):
    orders = list_orders(search, status_filter)
    stats = compute_stats()
    category_options = "".join(f'<option value="{esc(x)}">{esc(x)}</option>' for x in CATEGORIES)
    status_options = '<option value="">Усі статуси</option>' + ''.join(
        f'<option value="{esc(k)}" {"selected" if status_filter == k else ""}>{esc(v)}</option>' for k, v in STATUSES.items()
    )

    rows = ""
    for order in orders:
        color = STATUS_COLORS.get(order["status"], "#334155")
        rows += f"""
        <tr>
            <td><a href="/order?id={esc(order['id'])}">{esc(order['id'])}</a></td>
            <td>{esc(order['buyer_nick'])}</td>
            <td>{esc(order['payer_name']) if order['payer_name'] else '-'}</td>
            <td>{int(order['items_count'])}</td>
            <td>{float(order['total_amount']):.2f} грн</td>
            <td><span class="status-pill" style="background:{esc(color)};">{esc(STATUSES.get(order['status'], order['status']))}</span></td>
            <td>{esc(order['created_at'])}</td>
            <td>
                <div class="actions-inline">
                    <a class="button-link btn-secondary small-btn" href="/order?id={esc(order['id'])}">Відкрити</a>
                    <form class="inline-form" method="POST" action="/paid_and_receipt">
                        <input type="hidden" name="order_id" value="{esc(order['id'])}">
                        <button class="btn-success small-btn" type="submit">Оплачено + чек</button>
                    </form>
                </div>
            </td>
        </tr>
        """

    msg_html = f'<div class="card" style="border-left:4px solid #2563eb;"><strong>{esc(message)}</strong></div>' if message else ''

    content = f"""
    {msg_html}
    <div class="grid-4">
        <div class="stats-card s1"><div class="stat-label">Сьогодні</div><div class="stat-number">{stats['day']['orders']}</div><div class="stat-sub">Замовлень · {stats['day']['sum']:.2f} грн</div></div>
        <div class="stats-card s2"><div class="stat-label">Сьогодні оплачено</div><div class="stat-number">{stats['day']['paid_orders']}</div><div class="stat-sub">Оплачених · {stats['day']['paid_sum']:.2f} грн</div></div>
        <div class="stats-card s3"><div class="stat-label">Останні 7 днів</div><div class="stat-number">{stats['week']['orders']}</div><div class="stat-sub">Замовлень · {stats['week']['sum']:.2f} грн</div></div>
        <div class="stats-card s4"><div class="stat-label">Останні 30 днів</div><div class="stat-number">{stats['month']['orders']}</div><div class="stat-sub">Замовлень · {stats['month']['sum']:.2f} грн</div></div>
    </div>

    <div class="card">
        <h2>Створити нове замовлення</h2>
        <form method="POST" action="/create_order">
            <div class="row-6">
                <div><label>Нік покупця</label><input name="buyer_nick" placeholder="Наприклад: @ivan" required></div>
                <div><label>ПІБ платника</label><input name="payer_name" placeholder="Можна заповнити пізніше"></div>
                <div><label>Номер товару / лота</label><input name="product_number" placeholder="Наприклад: 25" required></div>
                <div><label>Назва товару</label><input name="product_title" placeholder="Наприклад: Лампа настільна" required></div>
                <div><label>Категорія</label><select name="category" required>{category_options}</select></div>
                <div><label>Сума, грн</label><input name="amount" placeholder="870" required></div>
            </div>
            <div class="actions"><button class="btn" type="submit">Створити замовлення</button></div>
        </form>
    </div>

    <div class="card card-soft">
        <h2>Пошук і фільтр</h2>
        <form method="GET" action="/">
            <div class="row-3">
                <div><label>Пошук</label><input name="search" value="{esc(search)}" placeholder="ID, нік, ПІБ, назва товару, лот"></div>
                <div><label>Статус</label><select name="status">{status_options}</select></div>
                <div style="display:flex;align-items:end;"><div style="width:100%;"><button class="btn-secondary" type="submit">Застосувати</button></div></div>
            </div>
        </form>
    </div>

    <div class="card">
        <h2>Замовлення</h2>
        <table>
            <thead><tr><th>ID</th><th>Нік покупця</th><th>ПІБ платника</th><th>Позицій</th><th>Сума</th><th>Статус</th><th>Створено</th><th>Дії</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="8">Поки що замовлень немає</td></tr>'}</tbody>
        </table>
    </div>
    """
    return page_template("Konopa CRM v2", content)


def render_order(order, message=""):
    if not order:
        return page_template("Не знайдено", '<div class="card"><h2>Замовлення не знайдено</h2><p><a href="/">Повернутися назад</a></p></div>')

    color = STATUS_COLORS.get(order["status"], "#334155")
    client_message = build_message_for_client(order)
    checkbox_payload = build_checkbox_payload(order)
    mono_link = build_mono_link(order)
    privat_link = build_privat_link(order)

    items_html = ""
    for item in order["items"]:
        items_html += f"""
        <div class="item-box">
            <div class="row-5">
                <div><strong>Назва товару</strong><br>{esc(item['product_title'])}</div>
                <div><strong>Лот</strong><br>#{esc(item['product_number'])}</div>
                <div><strong>Категорія</strong><br>{esc(item['category'])}</div>
                <div><strong>Сума</strong><br>{float(item['amount']):.2f} грн</div>
                <div>
                    <strong>Дії</strong><br>
                    <div class="actions-inline" style="margin-top:8px;">
                        <a class="button-link btn-secondary small-btn" href="/edit_item?id={esc(item['id'])}">Редагувати</a>
                        <form class="inline-form" method="POST" action="/delete_item" onsubmit="return confirm('Видалити товар із замовлення?');">
                            <input type="hidden" name="item_id" value="{esc(item['id'])}">
                            <input type="hidden" name="order_id" value="{esc(order['id'])}">
                            <button class="btn-danger small-btn" type="submit">Видалити</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
        """

    links_block = '<p class="muted">Mono не налаштовано</p>' if not mono_link else f'<p><a href="{esc(mono_link)}" target="_blank">{esc(mono_link)}</a></p>'
    links_block += '<p class="muted">Privat не налаштовано</p>' if not privat_link else f'<p><a href="{esc(privat_link)}" target="_blank">{esc(privat_link)}</a></p>'

    receipt_block = f"""
        <p><strong>Номер чека:</strong> {esc(order['receipt_number']) if order['receipt_number'] else '-'}</p>
        <p><strong>Фіскальний номер:</strong> {esc(order['fiscal_number']) if order['fiscal_number'] else '-'}</p>
        <p><strong>Receipt ID:</strong> {esc(order['receipt_id']) if order['receipt_id'] else '-'}</p>
        <p><strong>Посилання на чек:</strong> {f'<a href="{esc(order["receipt_link"])}" target="_blank">{esc(order["receipt_link"])}</a>' if order['receipt_link'] else '-'}</p>
        <p><strong>QR:</strong> {esc(order['receipt_qr']) if order['receipt_qr'] else '-'}</p>
        <p><strong>Статус Checkbox:</strong> {esc(order['checkbox_status']) if order['checkbox_status'] else '-'}</p>
    """

    msg_html = f'<div class="card" style="border-left:4px solid #2563eb;"><strong>{esc(message)}</strong></div>' if message else ''

    content = f"""
    {msg_html}
    <div class="card">
        <p><a href="/">← Назад до списку</a></p>
        <div class="row-3">
            <div>
                <h2>Замовлення {esc(order['id'])}</h2>
                <p class="muted">Створено: {esc(order['created_at'])}</p>
                <p class="muted">Оновлено: {esc(order['updated_at'])}</p>
            </div>
            <div>
                <p><strong>Нік покупця:</strong> {esc(order['buyer_nick'])}</p>
                <p><strong>ПІБ платника:</strong> {esc(order['payer_name']) if order['payer_name'] else '-'}</p>
            </div>
            <div class="total-box">
                <p><strong>Статус:</strong> <span class="status-pill" style="background:{esc(color)};">{esc(STATUSES.get(order['status'], order['status']))}</span></p>
                <p><strong>Позицій:</strong> {len(order['items'])}</p>
                <p><strong>Загальна сума:</strong> {order['total_amount']:.2f} грн</p>
            </div>
        </div>
        <div class="actions">
            <a class="button-link btn-secondary" href="/edit_order?id={esc(order['id'])}">Редагувати замовлення</a>
            <form class="inline-form" method="POST" action="/paid_and_receipt"><input type="hidden" name="order_id" value="{esc(order['id'])}"><button class="btn-success" type="submit">Оплачено + чек</button></form>
            <form class="inline-form" method="POST" action="/create_receipt"><input type="hidden" name="order_id" value="{esc(order['id'])}"><button class="btn-warning" type="submit">Створити чек окремо</button></form>
            <form class="inline-form" method="POST" action="/delete_order" onsubmit="return confirm('Видалити все замовлення?');"><input type="hidden" name="order_id" value="{esc(order['id'])}"><button class="btn-danger" type="submit">Видалити замовлення</button></form>
        </div>
    </div>

    <div class="card">
        <h2>Товари в замовленні</h2>
        {items_html if items_html else '<p class="muted">У замовленні ще немає товарів.</p>'}
        <div class="actions"><a class="button-link btn" href="/add_item?order_id={esc(order['id'])}">+ Додати товар</a></div>
    </div>

    <div class="grid-2">
        <div class="card">
            <h2>Повідомлення клієнту</h2>
            <textarea readonly>{esc(client_message)}</textarea>
        </div>
        <div class="card">
            <h2>Посилання на оплату</h2>
            {links_block}
        </div>
    </div>

    <div class="grid-2">
        <div class="card">
            <h2>Дані для Checkbox</h2>
            <textarea readonly>{esc(checkbox_payload)}</textarea>
        </div>
        <div class="card">
            <h2>Дані чека</h2>
            {receipt_block}
            <div class="mono">{esc(order['receipt_text']) if order['receipt_text'] else 'Текст чека поки що відсутній'}</div>
        </div>
    </div>

    <div class="card">
        <h2>Службова відповідь Checkbox</h2>
        <textarea readonly>{esc(order['checkbox_raw']) if order['checkbox_raw'] else 'Поки що немає даних'}</textarea>
    </div>
    """
    return page_template(f"Замовлення {order['id']}", content)


def render_order_edit(order):
    if not order:
        return page_template("Не знайдено", '<div class="card"><h2>Замовлення не знайдено</h2></div>')
    content = f"""
    <div class="card">
        <p><a href="/order?id={esc(order['id'])}">← Назад до замовлення</a></p>
        <h2>Редагування замовлення {esc(order['id'])}</h2>
        <form method="POST" action="/update_order">
            <input type="hidden" name="order_id" value="{esc(order['id'])}">
            <div class="row-2">
                <div><label>Нік покупця</label><input name="buyer_nick" value="{esc(order['buyer_nick'])}" required></div>
                <div><label>ПІБ платника</label><input name="payer_name" value="{esc(order['payer_name'])}"></div>
            </div>
            <div class="actions"><button class="btn" type="submit">Зберегти зміни</button></div>
        </form>
    </div>
    """
    return page_template("Редагування замовлення", content)


def render_item_form(order_id, item=None):
    title = "Редагування товару" if item else "Додати товар"
    action = "/update_item" if item else "/create_item"
    item_id_field = f'<input type="hidden" name="item_id" value="{esc(item["id"])}">' if item else ""
    category_options = ""
    current_cat = item["category"] if item else ""
    for cat in CATEGORIES:
        selected = "selected" if current_cat == cat else ""
        category_options += f'<option value="{esc(cat)}" {selected}>{esc(cat)}</option>'
    amount_value = f"{float(item['amount']):.2f}" if item else ""

    content = f"""
    <div class="card">
        <p><a href="/order?id={esc(order_id)}">← Назад до замовлення</a></p>
        <h2>{esc(title)}</h2>
        <form method="POST" action="{esc(action)}">
            <input type="hidden" name="order_id" value="{esc(order_id)}">
            {item_id_field}
            <div class="row-2">
                <div><label>Номер товару / лота</label><input name="product_number" value="{esc(item['product_number']) if item else ''}" required></div>
                <div><label>Назва товару</label><input name="product_title" value="{esc(item['product_title']) if item else ''}" required></div>
            </div>
            <div class="row-2">
                <div><label>Категорія</label><select name="category" required>{category_options}</select></div>
                <div><label>Сума, грн</label><input name="amount" value="{esc(amount_value)}" required></div>
            </div>
            <div class="actions"><button class="btn" type="submit">Зберегти</button></div>
        </form>
    </div>
    """
    return page_template(title, content)


class Handler(BaseHTTPRequestHandler):
    def send_html(self, html_text, status=200):
        data = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/":
            search = params.get("search", [""])[0].strip()
            status = params.get("status", [""])[0].strip()
            message = params.get("message", [""])[0].strip()
            self.send_html(render_home(search, status, message))
            return

        if parsed.path == "/order":
            order_id = params.get("id", [""])[0].strip()
            message = params.get("message", [""])[0].strip()
            self.send_html(render_order(get_order(order_id), message))
            return

        if parsed.path == "/edit_order":
            order_id = params.get("id", [""])[0].strip()
            self.send_html(render_order_edit(get_order(order_id)))
            return

        if parsed.path == "/add_item":
            order_id = params.get("order_id", [""])[0].strip()
            self.send_html(render_item_form(order_id))
            return

        if parsed.path == "/edit_item":
            item_id = params.get("id", [""])[0].strip()
            conn = get_conn()
            item = conn.execute("SELECT * FROM order_items WHERE id = ?", (item_id,)).fetchone()
            conn.close()
            if not item:
                self.send_html(page_template("Не знайдено", '<div class="card"><h2>Товар не знайдено</h2></div>'), 404)
                return
            self.send_html(render_item_form(item["order_id"], dict(item)))
            return

        self.send_html(page_template("404", '<div class="card"><h2>Сторінку не знайдено</h2></div>'), 404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        data = parse_qs(body)

        try:
            if self.path == "/create_order":
                buyer_nick = data.get("buyer_nick", [""])[0].strip()
                payer_name = data.get("payer_name", [""])[0].strip()
                product_number = data.get("product_number", [""])[0].strip()
                product_title = data.get("product_title", [""])[0].strip()
                category = data.get("category", [""])[0].strip()
                amount_text = data.get("amount", [""])[0].strip().replace(",", ".")
                if not buyer_nick or not product_number or not product_title or not category or not amount_text:
                    raise ValueError("Заповніть усі обов'язкові поля")
                amount = float(amount_text)
                if amount <= 0:
                    raise ValueError("Сума має бути більшою за 0")
                order_id = create_order_with_first_item(buyer_nick, payer_name, product_number, product_title, category, amount)
                self.redirect(f"/order?{urlencode({'id': order_id, 'message': 'Замовлення створено'})}")
                return

            if self.path == "/update_order":
                order_id = data.get("order_id", [""])[0].strip()
                buyer_nick = data.get("buyer_nick", [""])[0].strip()
                payer_name = data.get("payer_name", [""])[0].strip()
                if not order_id or not buyer_nick:
                    raise ValueError("Не заповнено обов'язкові поля")
                update_order(order_id, buyer_nick, payer_name)
                self.redirect(f"/order?{urlencode({'id': order_id, 'message': 'Замовлення оновлено'})}")
                return

            if self.path == "/create_item":
                order_id = data.get("order_id", [""])[0].strip()
                product_number = data.get("product_number", [""])[0].strip()
                product_title = data.get("product_title", [""])[0].strip()
                category = data.get("category", [""])[0].strip()
                amount_text = data.get("amount", [""])[0].strip().replace(",", ".")
                if not order_id or not product_number or not product_title or not category or not amount_text:
                    raise ValueError("Заповніть усі поля товару")
                amount = float(amount_text)
                if amount <= 0:
                    raise ValueError("Сума має бути більшою за 0")
                add_item(order_id, product_number, product_title, category, amount)
                self.redirect(f"/order?{urlencode({'id': order_id, 'message': 'Товар додано'})}")
                return

            if self.path == "/update_item":
                order_id = data.get("order_id", [""])[0].strip()
                item_id = data.get("item_id", [""])[0].strip()
                product_number = data.get("product_number", [""])[0].strip()
                product_title = data.get("product_title", [""])[0].strip()
                category = data.get("category", [""])[0].strip()
                amount_text = data.get("amount", [""])[0].strip().replace(",", ".")
                if not item_id or not product_number or not product_title or not category or not amount_text:
                    raise ValueError("Заповніть усі поля товару")
                amount = float(amount_text)
                if amount <= 0:
                    raise ValueError("Сума має бути більшою за 0")
                update_item(item_id, product_number, product_title, category, amount)
                self.redirect(f"/order?{urlencode({'id': order_id, 'message': 'Товар оновлено'})}")
                return

            if self.path == "/delete_item":
                item_id = data.get("item_id", [""])[0].strip()
                order_id = data.get("order_id", [""])[0].strip()
                delete_item(item_id)
                self.redirect(f"/order?{urlencode({'id': order_id, 'message': 'Товар видалено'})}")
                return

            if self.path == "/delete_order":
                order_id = data.get("order_id", [""])[0].strip()
                delete_order(order_id)
                self.redirect(f"/?{urlencode({'message': 'Замовлення видалено'})}")
                return

            if self.path == "/paid_and_receipt":
                order_id = data.get("order_id", [""])[0].strip()
                _, message = mark_paid_and_create_receipt(order_id)
                self.redirect(f"/order?{urlencode({'id': order_id, 'message': message})}")
                return

            if self.path == "/create_receipt":
                order_id = data.get("order_id", [""])[0].strip()
                _, message = create_checkbox_receipt(order_id)
                self.redirect(f"/order?{urlencode({'id': order_id, 'message': message})}")
                return

            self.send_html(page_template("400", '<div class="card"><h2>Невірний запит</h2></div>'), 400)
        except Exception as e:
            self.send_html(page_template("Помилка", f'<div class="card"><h2>Помилка</h2><p>{esc(e)}</p><p><a href="/">Назад</a></p></div>'), 500)


if __name__ == "__main__":
    init_db()
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Запущено: http://{HOST}:{PORT}")
    print("Щоб зупинити, натисни Ctrl + C")
    server.serve_forever()
