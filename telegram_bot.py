#!/usr/bin/env python3
"""membrillo-telegram - Bot argentino sencillo con memoria."""

import logging
import os
import random
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Cargar .env
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Faltan tokens")

# Componentes
from memory import GlobalMemory
from llm import (
    parse_intent, generate_response, build_system_prompt,
    needs_web_search, format_search_results, web_search
)

memory = GlobalMemory()
bot_client = None

# ─── Configuración ───
BOT_NAMES = ["membrillo", "membri"]  # Nombres que activa al bot (sin @)

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("membrillo")


# ─── Saludos estilo argentino ───
def get_saludo():
    return random.choice([
        "¡Hola! ¿Cómo andás?",
        "¡Eh! ¿Qué tal?",
        "¡Qué tal! Hace rato no nos vemos.",
        "¡Holaaaa! ¿Qué nueva?",
        "¡Oye! ¿Qué hacés?",
    ])


# ─── Comandos en español argentino ───

async def start_cmd(update: Update, context) -> None:
    await update.message.reply_text(f"{get_saludo()}\n\nSoy Membrillo. Decí algo para comenzar.")


async def help_cmd(update: Update, context) -> None:
    await update.message.reply_text(
        "Soy Membrillo, tu agente conversacional.\n"
        "Decí lo que querés y yo me acuerdo.\n\n"
        "Comandos en español:\n"
        "- `membrillo recuerda que X` → guardá X en memoria\n"
        "- `membrillo qué guardás de X` → yo recuerdo X\n"
        "- `membrillo mis listas` → mostrá tus listas\n"
        "- `membrillo mis notas` → mostrá tus notas\n"
        "- `membrillo agrega X a la lista de Y` → guardá en lista\n"
        "- `/listas` → ver todo lo guardado\n"
        "- `/reset_lista <nombre>` → borrar una lista\n\n"
        "¡Decí algo!"
    )


async def listas_cmd(update: Update, context) -> None:
    """Handler /listas."""
    lst_names = memory.list_names()
    note_names = memory.list_notes()
    lines = ["📋 **Tu memoria:**"]

    if lst_names:
        lines.append("\n📦 **Listas:**")
        for name in lst_names:
            items = memory.get_list(name)
            lines.append(f"• **{name}** ({len(items)} items)")
    else:
        lines.append("\n📦 **Listas:** Ninguna aún.")

    if note_names:
        lines.append("\n💬 **Notas guardadas:**")
        for key in note_names:
            note = memory.get_note(key)
            if note:
                latest = note[-1]
                lines.append(f"• **{key}**: {latest['content'][:80]}...")
    else:
        lines.append("\n💬 **Notas:** Ninguna aún.")

    await update.message.reply_text("\n".join(lines))


async def reset_lista_cmd(update: Update, context) -> None:
    """Handler /reset_lista."""
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


async def historial_cmd(update: Update, context) -> None:
    """Handler /historial."""
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


# ─── Lógica principal ───

async def handle_message(update: Update, context) -> None:
    """Handler principal - Conversación argentina."""
    global bot_client
    message = update.message
    if not message or not message.text:
        return

    user_text = message.text.strip()
    user_id = message.from_user.id if message.from_user else "unknown"
    user_name = message.from_user.full_name if message.from_user else "unknown"

    # Guardar en historial
    memory.add_history("user", user_text, user_name)

    # Detectar si el bot es mencionado por nombre al INICIO
    bot_mentioned = False
    normalized = user_text

    for bot_name in BOT_NAMES:
        if user_text.lower().startswith(bot_name):
            bot_mentioned = True
            normalized = user_text[len(bot_name):].strip()
            break

    # Si no empezó con nombre, verificar mención @
    if not bot_mentioned:
        mention = f"@{context.bot.username}" if context.bot else ""
        if mention and mention in user_text:
            bot_mentioned = True
            normalized = user_text.replace(mention, "").strip()

    # Si NO mencionó el nombre, verificar historial previo
    history_len = len(memory.get_history(last_n=3))
    has_history = history_len >= 3

    # Lógica de respuesta:
    # 1. Si mencionó el nombre -> responder siempre
    # 2. Si ya hay historial (>3 msgs) -> responder con probabilidad
    # 3. Caso contrario -> ignorar
    # Además, revisar patrones de "guardar" y "consultar"
    should_respond = bot_mentioned or (has_history and random.random() < 0.7)

    # Revisar patrones de "guardar" y "consultar" en el texto
    lower = user_text.lower()
    if ("guardá" in lower or "recordá" in lower) and len(user_text) > 10:
        should_respond = True
    elif ("sabés" in lower or "sabes" in lower or "querés" in lower) and len(user_text) > 10:
        should_respond = True

    if not should_respond:
        if len(user_text) < 5:
            return
        # Para mensajes más largos, responder igualmente si hay contexto
        if history_len > 0 and len(user_text) > 20:
            should_respond = True

    if not should_respond:
        return

    # Parsear intención
    intent = parse_intent(normalized, memory)

    # Generar respuesta
    conversation_history = memory.get_history(last_n=5)
    system_prompt = build_system_prompt(intent, memory)

    try:
        response = await generate_response(
            system_prompt=system_prompt,
            user_message=normalized,
            conversation_history=conversation_history,
            api_key=OPENROUTER_API_KEY,
        )
    except Exception as e:
        logger.error(f"Error LLM: {e}")
        await update.message.reply_text("❌ Manejé un error, intentá de nuevo.")
        memory.add_history("assistant", "Error LLM", "bot")
        return

    # Guardar y responder
    memory.add_history("assistant", response, "bot")
    await update.message.reply_text(response, parse_mode="Markdown")


def main() -> None:
    """Start the bot."""
    global bot_client

    application = Application.builder().token(TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("listas", listas_cmd))
    application.add_handler(CommandHandler("reset_lista", reset_lista_cmd))
    application.add_handler(CommandHandler("historial", historial_cmd))

    # Message handler - conversación argentina
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    bot_client = application.bot
    application.run_polling(drop_pending_updates=True, timeout=30)
    logger.info("Membrillo Telegram bot started")


if __name__ == "__main__":
    main()