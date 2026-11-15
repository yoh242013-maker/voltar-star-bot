"""Digital shop bot: Mini App catalog -> order -> manual payment confirm -> delivery.
Setup: pip install "python-telegram-bot>=21"
Env vars: BOT_TOKEN, ADMIN_ID (your numeric Telegram id), WEBAPP_URL (https link to index.html)
"""
import base64
import json
import os
import uuid

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update, WebAppInfo)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
WEBAPP_URL = os.environ["WEBAPP_URL"]
SHOP = "VOLTAR SHOP"
CHANNEL = "https://t.me/shopofwms"
SUPPORT = "https://t.me/YOUR_SUPPORT_USERNAME"

# Edit the text below with YOUR payment details.
PAYMENT_TEXT = (
    "Send the exact total to:\n"
    "Bank/Wallet: YOUR_ACCOUNT_DETAILS_HERE\n\n"
    "Put your order number in the note, then tap 'I've paid'."
)

# deliver = what the buyer receives. Plain text/link, or "file:<telegram_file_id>".
PRODUCTS = {
    "1": {"name": "Sample item", "cat": "Watermarks", "price": 5, "desc": "Ready-made file",
          "deliver": "Here is your link: https://example.com/ebook"},
}
ORDERS = {}  # in memory: lost on restart. Move to SQLite when you go live.


def shop_keyboard():
    catalog = [{"id": k, "n": v["name"], "p": v["price"], "d": v["desc"], "k": v["cat"]}
               for k, v in PRODUCTS.items()]
    blob = base64.urlsafe_b64encode(json.dumps(catalog).encode()).decode()
    btn = KeyboardButton("🛍 Open shop", web_app=WebAppInfo(f"{WEBAPP_URL}?c={blob}"))
    rows = [[btn], ["❓ Questions", "💬 Support"], ["📢 Channel", "🧾 Purchased"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def summary(order):
    lines = [f"{PRODUCTS[i]['name']} x{q}" for i, q in order["items"].items()]
    return "\n".join(lines) + f"\nTotal: ${order['total']}"


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Welcome to {SHOP}! Tap Open shop to browse.",
                                    reply_markup=shop_keyboard())


async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cart = json.loads(update.message.web_app_data.data)["items"]
    except (ValueError, KeyError):
        return
    # Rebuild from OUR prices; never trust the client.
    items = {i: int(q) for i, q in cart.items()
             if i in PRODUCTS and 0 < int(q) <= 99}
    if not items:
        await update.message.reply_text("Your cart was empty.")
        return
    total = sum(PRODUCTS[i]["price"] * q for i, q in items.items())
    oid = uuid.uuid4().hex[:6].upper()
    u = update.effective_user
    ORDERS[oid] = {"user": u.id, "name": u.full_name, "items": items,
                   "total": total, "status": "pending"}
    handle = f"@{u.username}" if u.username else f"id {u.id}"
    await ctx.bot.send_message(
        ADMIN_ID, f"🆕 New order #{oid}\nFrom: {u.full_name} ({handle})\n"
                  f"{summary(ORDERS[oid])}\nStatus: waiting for payment")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ I've paid", callback_data=f"paid:{oid}")]])
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
        order["status"] = "claimed"
        await q.edit_message_text(f"Order #{oid}: waiting for confirmation...")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data=f"ok:{oid}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"no:{oid}")]])
        await ctx.bot.send_message(
            ADMIN_ID, f"Payment claimed\nOrder #{oid} from {order['name']}\n"
                      f"{summary(order)}", reply_markup=kb)

    elif action in ("ok", "no") and q.from_user.id == ADMIN_ID \
            and order["status"] == "claimed":
        if action == "no":
            order["status"] = "rejected"
            await q.edit_message_text(f"Order #{oid} rejected.")
            await ctx.bot.send_message(order["user"],
                                       f"Order #{oid}: payment not found. Contact support.")
            return
        order["status"] = "delivered"
        await q.edit_message_text(f"Order #{oid} confirmed and delivered.")
        await ctx.bot.send_message(order["user"], f"Payment confirmed for #{oid}. Thank you!")
        for pid in order["items"]:
            d = PRODUCTS[pid]["deliver"]
            if d.startswith("file:"):
                await ctx.bot.send_document(order["user"], d[5:])
            else:
                await ctx.bot.send_message(order["user"], f"{PRODUCTS[pid]['name']}:\n{d}")


async def add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin: /add Category | Name | price | description | delivery (text or file:<file_id>)"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        catg, name, price, desc, deliver = [s.strip() for s in
                                            update.message.text.split(" ", 1)[1].split("|", 4)]
        pid = str(max(map(int, PRODUCTS), default=0) + 1)
        PRODUCTS[pid] = {"name": name, "cat": catg, "price": float(price) if "." in price else int(price),
                         "desc": desc, "deliver": deliver}
    except (IndexError, ValueError):
        await update.message.reply_text("Format: /add Category | Name | price | description | delivery")
        return
    await update.message.reply_text(f"Added '{name}'. Send /start to refresh the shop.",
                                    reply_markup=shop_keyboard())


async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin: send any file to the bot to get its file_id for use in /add."""
    if update.effective_user.id == ADMIN_ID and update.message.document:
        await update.message.reply_text(f"file:{update.message.document.file_id}")


async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t, uid = update.message.text, update.effective_user.id
    if t == "❓ Questions":
        await update.message.reply_text(
            "Items arrive in this chat after your payment is confirmed. "
            "Digital goods are non-refundable once delivered.")
    elif t == "💬 Support":
        await update.message.reply_text(f"Support: {SUPPORT}")
    elif t == "📢 Channel":
        await update.message.reply_text(CHANNEL)
    elif t == "🧾 Purchased":
        done = [f"#{o} - ${d['total']}" for o, d in ORDERS.items()
                if d["user"] == uid and d["status"] == "delivered"]
        await update.message.reply_text("\n".join(done) or "No purchases yet.")


app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("add", add))
app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))
app.add_handler(MessageHandler(filters.Document.ALL, on_file))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu))
app.add_handler(CallbackQueryHandler(on_button))
app.run_polling()
