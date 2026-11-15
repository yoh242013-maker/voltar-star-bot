"""VOLTAR SHOP bot: Mini App shop -> order -> manual payment confirm -> delivery.
Items are managed inside the mini app (Profile -> Admin panel, password protected).
Each item can be sold for Mobile, PC, or both, with its own price and delivery per platform.

Setup: pip install "python-telegram-bot>=21" aiohttp
Env vars: BOT_TOKEN, ADMIN_ID (your numeric Telegram id), WEBAPP_URL (https link to index.html),
          API_URL (public https link of THIS bot server), ADMIN_PASSWORD (12+ characters)
Optional: DATA_FILE (where products/orders are saved, default data.json), PORT
"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from urllib.parse import parse_qsl, quote

from aiohttp import web
from telegram import (InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup,
                      KeyboardButton, ReplyKeyboardMarkup, Update, WebAppInfo)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
WEBAPP_URL = os.environ["WEBAPP_URL"]
DATA_FILE = os.environ.get("DATA_FILE", "data.json")
API_URL = os.environ.get("API_URL", "").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
if ADMIN_PASSWORD and len(ADMIN_PASSWORD) < 12:
    raise SystemExit("ADMIN_PASSWORD must be at least 12 characters.")
SHOP = "VOLTAR SHOP"
CHANNEL = "https://t.me/shopofwms"
SUPPORT = "https://t.me/voltaryz"

PAYMENT_TEXT = (
    "To pay, message @voltaryz for the payment details and send the exact total.\n"
    "Put your order number in the note, then tap 'I've paid'."
)

LABEL = {"mobile": "Mobile", "pc": "PC", "both": "Mobile + PC"}

# Starting item. After the first run, manage items in the mini app admin panel.
PRODUCTS = {
    "1": {"name": "Sample item", "cat": "Watermarks", "desc": "Ready-made file",
          "opts": {"mobile": {"price": 3, "deliver": "Mobile file: https://example.com/m"},
                   "pc": {"price": 5, "deliver": "PC file: https://example.com/pc"}}},
}
ORDERS = {}


def picks(choice):
    return ["mobile", "pc"] if choice == "both" else [choice]


# ---------- storage ----------
def save():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"products": PRODUCTS, "orders": ORDERS}, f, ensure_ascii=False)


def normalize():
    for p in PRODUCTS.values():  # older single-price items become Mobile + PC items
        if "opts" not in p:
            o = {"price": p.pop("price", 1), "deliver": p.pop("deliver", "")}
            p["opts"] = {"mobile": dict(o), "pc": dict(o)}
    for o in ORDERS.values():
        o["items"] = {i: (c if c in LABEL else "mobile") for i, c in o["items"].items()}


def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        PRODUCTS.clear(); PRODUCTS.update(data.get("products", {}))
        ORDERS.clear(); ORDERS.update(data.get("orders", {}))
    normalize()


# ---------- shop ----------
def public_catalog(trim=None):
    return [{"id": k, "n": v["name"], "d": v["desc"][:trim] if trim else v["desc"],
             "k": v["cat"], "o": {pl: o["price"] for pl, o in v["opts"].items()}}
            for k, v in PRODUCTS.items()]


def shop_keyboard():
    if API_URL:
        url = f"{WEBAPP_URL}?api={quote(API_URL, safe='')}"
    else:
        import base64
        blob = base64.urlsafe_b64encode(json.dumps(public_catalog(40)).encode()).decode()
        url = f"{WEBAPP_URL}?c={blob}"
    btn = KeyboardButton("🛍 Open shop", web_app=WebAppInfo(url))
    rows = [[btn], ["❓ Questions", "💬 Support"], ["📢 Channel", "🧾 Purchased"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def summary(order):
    lines = [f"{PRODUCTS.get(i, {'name': '(removed item)'})['name']} - {LABEL.get(c, 'Mobile')}"
             for i, c in order["items"].items()]
    return "\n".join(lines) + f"\nTotal: ${order['total']}"


async def deliver_item(bot, uid, title, d):
    if d.startswith("doc:"):
        await bot.send_document(uid, d[4:], caption=title)
    elif d.startswith("photo:"):
        await bot.send_photo(uid, d[6:], caption=title)
    elif d.startswith("video:"):
        await bot.send_video(uid, d[6:], caption=title)
    else:
        await bot.send_message(uid, f"{title}:\n{d}")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Welcome to {SHOP}! Tap Open shop to browse.",
                                    reply_markup=shop_keyboard())


async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items = {}
    try:
        for i, c in json.loads(update.message.web_app_data.data)["items"].items():
            p = PRODUCTS.get(i)
            # Rebuild from OUR data; never trust the client.
            if p and c in LABEL and all(pl in p["opts"] for pl in picks(c)):
                items[i] = c
    except (ValueError, KeyError, TypeError, AttributeError):
        return
    if not items:
        await update.message.reply_text(
            "That option is no longer available. Send /start to refresh the shop.",
            reply_markup=shop_keyboard())
        return
    total = round(sum(PRODUCTS[i]["opts"][pl]["price"] for i, c in items.items()
                      for pl in picks(c)), 2)
    oid = uuid.uuid4().hex[:6].upper()
    u = update.effective_user
    ORDERS[oid] = {"user": u.id, "name": u.full_name, "items": items,
                   "total": total, "status": "pending"}
    save()
    handle = f"@{u.username}" if u.username else f"id {u.id}"
    await ctx.bot.send_message(
        ADMIN_ID, f"🆕 New order #{oid}\nFrom: {u.full_name} ({handle})\n"
                  f"{summary(ORDERS[oid])}\nStatus: waiting for payment")
    kb = Markup([[Btn("✅ I've paid", callback_data=f"paid:{oid}")]])
    await update.message.reply_text(
        f"Order #{oid}\n{summary(ORDERS[oid])}\n\n{PAYMENT_TEXT}", reply_markup=kb)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, oid = q.data.split(":")
    order = ORDERS.get(oid)
    if not order:
        await q.edit_message_text("Order not found.")
        return

    if action == "paid" and q.from_user.id == order["user"] and order["status"] == "pending":
        order["status"] = "claimed"; save()
        await q.edit_message_text(f"Order #{oid}: waiting for confirmation...")
        kb = Markup([[Btn("✅ Confirm", callback_data=f"ok:{oid}"),
                      Btn("❌ Reject", callback_data=f"no:{oid}")]])
        await ctx.bot.send_message(
            ADMIN_ID, f"💰 Payment claimed\nOrder #{oid} from {order['name']}\n"
                      f"{summary(order)}", reply_markup=kb)

    elif action in ("ok", "no") and q.from_user.id == ADMIN_ID and order["status"] == "claimed":
        if action == "no":
            order["status"] = "rejected"; save()
            await q.edit_message_text(f"Order #{oid} rejected.")
            await ctx.bot.send_message(order["user"],
                                       f"Order #{oid}: payment not found. Contact support.")
            return
        order["status"] = "delivered"; save()
        await q.edit_message_text(f"Order #{oid} confirmed and delivered.")
        await ctx.bot.send_message(order["user"], f"Payment confirmed for #{oid}. Thank you!")
        for pid, c in order["items"].items():
            p = PRODUCTS.get(pid)
            if not p:
                continue
            for pl in picks(c):
                if pl in p["opts"]:
                    await deliver_item(ctx.bot, order["user"], f"{p['name']} ({LABEL[pl]})",
                                       p["opts"][pl]["deliver"])


async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/admin lists payments waiting for you. Items are managed in the mini app."""
    if update.effective_user.id != ADMIN_ID:
        return
    waiting = [(o, d) for o, d in ORDERS.items() if d["status"] == "claimed"]
    if not waiting:
        await update.message.reply_text(
            "No payments waiting.\nManage items in the shop: Profile > Admin panel.")
        return
    for oid, d in waiting:
        kb = Markup([[Btn("✅ Confirm", callback_data=f"ok:{oid}"),
                      Btn("❌ Reject", callback_data=f"no:{oid}")]])
        await update.message.reply_text(f"Order #{oid} from {d['name']}\n{summary(d)}",
                                        reply_markup=kb)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t, uid = update.message.text, update.effective_user.id
    kb = shop_keyboard()
    if t == "❓ Questions":
        await update.message.reply_text(
            "Items arrive in this chat after your payment is confirmed. "
            "Digital goods are non-refundable once delivered.", reply_markup=kb)
    elif t == "💬 Support":
        await update.message.reply_text(f"Support: {SUPPORT}", reply_markup=kb)
    elif t == "📢 Channel":
        await update.message.reply_text(CHANNEL, reply_markup=kb)
    elif t == "🧾 Purchased":
        done = [f"#{o} - ${d['total']}" for o, d in ORDERS.items()
                if d["user"] == uid and d["status"] == "delivered"]
        await update.message.reply_text("\n".join(done) or "No purchases yet.", reply_markup=kb)


async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin sends a file to get a code to paste into a Delivery field."""
    if update.effective_user.id != ADMIN_ID:
        return
    m = update.message
    if m.document:
        val = "doc:" + m.document.file_id
    elif m.photo:
        val = "photo:" + m.photo[-1].file_id
    elif m.video:
        val = "video:" + m.video.file_id
    else:
        return
    await m.reply_text(f"File code (paste it into a Delivery field):\n{val}")


# ---------- web API for the mini app (catalog + password-protected admin) ----------
ORIGIN = "/".join(WEBAPP_URL.split("/")[:3])
SESSIONS = {}
LOCK = {"fails": 0, "until": 0.0}


def err(code, msg):
    return web.json_response({"error": msg}, status=code)


def tg_user(init_data):
    """Verify Telegram's signed initData. Returns the user dict or None."""
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        got = data.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not got or not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(data.get("auth_date", 0)) > 86400:
            return None
        return json.loads(data["user"])
    except (ValueError, KeyError, TypeError):
        return None


def authed(request):
    tok = request.headers.get("Authorization", "").removeprefix("Bearer ")
    exp = SESSIONS.get(tok)
    if not exp or exp < time.time():
        SESSIONS.pop(tok, None)
        return False
    SESSIONS[tok] = time.time() + 1800
    return True


@web.middleware
async def cors(request, handler):
    resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


async def api_catalog(request):
    return web.json_response(public_catalog())


async def api_whoami(request):
    u = tg_user((await request.json()).get("initData", ""))
    return web.json_response({"admin": bool(u and u["id"] == ADMIN_ID and ADMIN_PASSWORD)})


async def api_login(request):
    body = await request.json()
    u = tg_user(body.get("initData", ""))
    if not u or u["id"] != ADMIN_ID or not ADMIN_PASSWORD:
        return err(403, "Not allowed")
    if time.time() < LOCK["until"]:
        return err(429, "Too many attempts. Try again in 15 minutes.")
    if not hmac.compare_digest(str(body.get("password", "")).encode(), ADMIN_PASSWORD.encode()):
        LOCK["fails"] += 1
        if LOCK["fails"] >= 5:
            LOCK.update(fails=0, until=time.time() + 900)
        await asyncio.sleep(1)
        return err(401, "Wrong password")
    LOCK["fails"] = 0
    tok = secrets.token_urlsafe(32)
    SESSIONS[tok] = time.time() + 1800
    return web.json_response({"token": tok})


async def api_admin_products(request):
    if not authed(request):
        return err(401, "Locked")
    return web.json_response(PRODUCTS)


def clean(body):
    try:
        opts = {}
        for pl in ("mobile", "pc"):
            o = (body.get("opts") or {}).get(pl)
            if o is None:
                continue
            price = float(o["price"])
            assert price > 0
            deliver = str(o["deliver"]).strip()[:3000]
            assert deliver
            opts[pl] = {"price": int(price) if price == int(price) else round(price, 2),
                        "deliver": deliver}
        p = {"name": str(body["name"]).strip()[:60], "cat": str(body["cat"]).strip()[:30],
             "desc": str(body.get("desc", "")).strip()[:200], "opts": opts}
        assert opts and p["name"] and p["cat"]
        return p
    except (KeyError, ValueError, AssertionError, TypeError, AttributeError, OverflowError):
        return None


async def api_admin_save(request):
    if not authed(request):
        return err(401, "Locked")
    body = await request.json()
    p = clean(body)
    if not p:
        return err(400, "Fill in name, category, and a price and delivery for each platform.")
    pid = str(body.get("id") or "")
    if pid not in PRODUCTS:
        pid = str(max(map(int, PRODUCTS), default=0) + 1)
    PRODUCTS[pid] = p
    save()
    return web.json_response({"id": pid})


async def api_admin_delete(request):
    if not authed(request):
        return err(401, "Locked")
    PRODUCTS.pop(str((await request.json()).get("id")), None)
    save()
    return web.json_response({"ok": True})


async def preflight(request):
    return web.Response()


async def start_web(application):
    w = web.Application(middlewares=[cors])
    w.router.add_get("/api/catalog", api_catalog)
    w.router.add_post("/api/whoami", api_whoami)
    w.router.add_post("/api/login", api_login)
    w.router.add_get("/api/admin/products", api_admin_products)
    w.router.add_post("/api/admin/save", api_admin_save)
    w.router.add_post("/api/admin/delete", api_admin_delete)
    w.router.add_route("OPTIONS", "/{tail:.*}", preflight)
    runner = web.AppRunner(w)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()


load()
app = Application.builder().token(TOKEN).post_init(start_web).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin_cmd))
app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))
app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, on_file))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(paid|ok|no):"))
app.run_polling()
