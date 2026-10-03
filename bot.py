import logging
from telegram import Update, LabeledPrice
from telegram.ext import Application, CommandHandler, PreCheckoutQueryHandler, ContextTypes, MessageHandler, filters

# Configure logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# CONFIGURATION
TOKEN = "8961385482:AAF4x0lRJj_s80bSvO61yIqRNIDc5hFQEIs"
PRIVATE_CHANNEL_LINK = "https://t.me"

# 1. Define the /start command which sends the 500 Stars invoice directly
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_invoice(
        chat_id=update.message.chat_id,
        title="Voltars Exclusive Private",
        description="Unlock exclusive AEPs, signature DaVinci/CapCut/Mobile AM shakes, transitions, and premium editing resources.",
        payload="tier-500-stars-payload",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="VIP Exclusive Access", amount=500)]
    )

# 2. Answer the pre-checkout query to clear the transaction
async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if query.invoice_payload == "tier-500-stars-payload":
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Something went wrong with the transaction.")

# 3. Handle successful payment and deliver the private link
async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    success_text = (
        "🎉 **Payment Successful! Welcome to Voltars Exclusive Private.**\n\n"
        "Your transaction was verified successfully. Click the link below to join:\n\n"
        f"👉 **[CLICK HERE TO JOIN THE PRIVATE CHANNEL]({PRIVATE_CHANNEL_LINK})**\n\n"
        "⚠️ *Note: Do not share this link with anyone else.*"
    )
    await update.message.reply_text(success_text, parse_mode="Markdown")

def main():
    application = Application.builder().token(TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # Start the bot
    print("Bot is running with 500 Stars single tier...")
    application.run_polling()

if __name__ == "__main__":
    main()
