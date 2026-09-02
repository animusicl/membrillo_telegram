#!/usr/bin/env python3
"""membrillo-telegram - Bot principal."""
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
HERMES_MODEL = os.getenv("HERMES_MODEL", "nousresearch/hermes-3-llama-3.1-8b")

if not TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Faltan tokens")

from memory import GlobalMemory
from llm import (
    parse_intent, generate_response, build_system_prompt,
    needs_web_search, format_search_results, web_search
)

memory = GlobalMemory()
bot_client = Bot(token=TOKEN)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("membrillo-telegram")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy Membrillo. Puedo ayudarte con listas de vinos, supermercado, películas, "
        "búsqueda en internet y maridajes.\n"
        "Escribe `membrillo agrega X a la lista de Y` o `membrillo hola`"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comandos:\n"
        "/start - Bienvenida\n"
        "/help - Ayuda\n"
        "/listas - Ver todas las listas\n"
        "/reset_lista <nombre> - Borrar una lista\n"
        "/historial - Últimos mensajes\n\n"
        "O escribe naturalmente:\n"
        "- `membrillo agrega Malbec a la lista de vinos`\n"
        "- `membrillo qué vino va con pescado`\n"
        "- `membrillo muéstrame mi lista de vinos`"
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lst_names = memory.list_names()
    if not lst_names:
        await update.message.reply_text("📂 No hay listas guardadas todavía.")
        return
    lines = ["📋 **Tus listas:**"]
    for name in lst_names:
        items = memory.get_list(name)
        lines.append(f"• **{name}** ({len(items)} items)")
    await update.message.reply_text("\n".join(lines))


async def reset_lista_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("❌ Uso: `/reset_lista <nombre>`", parse_mode="Markdown")
        return
    list_name = args[0]
    ok = memory.clear_list(list_name)
    if ok:
        await update.message.reply_text(f"✅ Lista **{list_name}** borrada.")
    else:
        await update.message.reply_text(f"❌ Lista **{list_name}** no existe.")


async def historial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = memory.get_history(last_n=5)
    if not history:
        await update.message.reply_text("📭 No hay historial.")
        return
    lines = ["📜 **Últimos 5 mensajes:**"]
    for msg in history:
        role = "Tú" if msg["role"] == "user" else "Membrillo"
        content = msg["content"][:80] + ("..." if len(msg["content"]) > 80 else "")
        lines.append(f"{role}: {content}")
    await update.message.reply_text("\n".join(lines))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    user_text = message.text.strip()
    user_id = message.from_user.id if message.from_user else "unknown"
    user_name = message.from_user.full_name if message.from_user else "unknown"

    memory.add_history("user", user_text, user_name)

    bot_info = await bot_client.get_me()
    bot_username = bot_info.username
    mention = f"@{bot_username}"

    normalized = user_text
    if user_text.lower().startswith("membri"):
        normalized = user_text[6:].strip()
    elif mention in user_text:
        normalized = user_text.replace(mention, "").strip()
    elif not user_text.lower().startswith("membri") and mention not in user_text:
        return

    if not normalized:
        await update.message.reply_text("👋 ¡Hola! Escribe `membrillo ayuda` para ver qué puedo hacer.")
        memory.add_history("assistant", "Saludo inicial", "bot")
        return

    intent = parse_intent(normalized, memory)
    conversation_history = memory.get_history(last_n=3)
    system_prompt = build_system_prompt(intent, memory)
    response = await generate_response(
        system_prompt=system_prompt,
        user_message=normalized,
        conversation_history=conversation_history,
        api_key=OPENROUTER_API_KEY,
    )

    memory.add_history("assistant", response, "bot")
    await update.message.reply_text(response, parse_mode="Markdown")


def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("listas", list_cmd))
    application.add_handler(CommandHandler("reset_lista", reset_lista_cmd))
    application.add_handler(CommandHandler("historial", historial_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()