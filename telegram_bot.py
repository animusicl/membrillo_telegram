#!/usr/bin/env python3
"""membrillo-telegram - Bot principal simple y fluido."""

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
HERMES_MODEL = os.getenv("HERMES_MODEL", "openrouter/auto")

if not TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Faltan TELEGRAM_BOT_TOKEN o OPENROUTER_API_KEY")

# Componentes
from memory import GlobalMemory
from llm import (
    parse_intent, generate_response, build_system_prompt,
    needs_web_search, format_search_results, web_search
)

memory = GlobalMemory()
bot_client = None

# ─── Configuración del bot ───
# Nombres que el usuario puede usar para mencionar al bot
BOT_NAMES = ["membrillo", "membri"]

# ─── Enable logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("membrillo-telegram")


# ─── Saludos simples ───
def get_saludo():
    """Retorna un saludo breve y aleatorio."""
    saludos = [
        "¡Hola! ¿Cómo vas?",
        "Hey! Make time to chat, how are you?",
        "¡Qué tal! Hace rato no nos vemos.",
        "Holaa! Extrañaba esta charla.",
        "¡Holaa! ¿Qué novedad?",
        "Hello there! How's your day going?",
        "¡Oye! ¿Qué tal te va?",
    ]
    return random.choice(saludos)


# ─── Comandos ───

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /start - Saludo simple."""
    saludo = get_saludo()
    await update.message.reply_text(
        f"{saludo}\n\nSoy Membrillo. Escribe algo para comenzar o dime tu nombre para el bot."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /help - Información breve."""
    await update.message.reply_text(
        "Soy Membrillo, tu agente conversacional.\n"
        "Escribe cualquier cosa para charlar.\n\n"
        "Comandos:\n"
        "- `/remember guardar <texto>` → guarda en memoria\n"
        "- `/remember pregunta <texto>` → bot recuerda\n"
        "- `/remember listar` → muestra todo guardado\n"
        "- `/remember borrar <clave>` → borra una nota\n"
        "- `membrillo` o `membri` → activa el bot por nombre\n"
        "- `membri agrega X a la lista de Y` → guarda en lista\n\n"
        "¡Empecemos!"
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


# ─── Manejador principal: responder al nombre del bot ───

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler principal - Responde cuando se menciona al bot por nombre."""
    global bot_client
    message = update.message
    if not message or not message.text:
        return

    user_text = message.text.strip()
    user_id = message.from_user.id if message.from_user else "unknown"
    user_name = message.from_user.full_name if message.from_user else "unknown"

    # Siempre guardar en historial
    memory.add_history("user", user_text, user_name)

    # Detectar si el bot es mencionado por nombre
    # Revisar si el mensaje empieza con el nombre del bot (sin @)
    bot_mentioned = False
    normalized = user_text

    # Verificar si el mensaje empieza con uno de los nombres del bot (sin @)
    for bot_name in BOT_NAMES:
        if user_text.lower().startswith(bot_name):
            bot_mentioned = True
            # Normalizar: quitar el nombre y quedarse con lo que sigue
            normalized = user_text[len(bot_name):].strip()
            break
    
    # También verificar mención con @
    mention = f"@{context.bot.username}" if context.bot else ""
    if mention and mention in user_text:
        bot_mentioned = True
        normalized = user_text.replace(mention, "").strip()

    # Si el bot no es mencionado, verificar si hay historial previo
    # para permitir conversación fluida
    history_len = len(memory.get_history(last_n=3)) if bot_mentioned else 0
    
    # Determinar si responder
    should_respond = bot_mentioned or (history_len >= 3 and random.random() < 0.3)
    
    if not should_respond:
        # Si no es para el bot y no hay historial, ignorar silenciosamente
        return

    # Parsear intent con LLM
    intent = parse_intent(normalized, memory)

    # Generar respuesta con contexto
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
        logger.error(f"Error en LLM: {e}")
        await update.message.reply_text(
            "❌ sorry, tuve un error conectando con mi cerebro. intentalo de nuevo en un momento."
        )
        memory.add_history("assistant", f"Error LLM: {str(e)[:100]}", "bot")
        return

    # Guardar respuesta en historial
    memory.add_history("assistant", response, "bot")

    # Enviar respuesta
    await update.message.reply_text(response, parse_mode="Markdown")


def main() -> None:
    """Start the bot."""
    global bot_client

    application = Application.builder().token(TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("listas", list_cmd))
    application.add_handler(CommandHandler("reset_lista", reset_lista_cmd))
    application.add_handler(CommandHandler("historial", historial_cmd))

    # Message handler - responder al nombre del bot o por contexto
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Store bot client for use in handlers
    bot_client = application.bot

    # Start polling
    application.run_polling(drop_pending_updates=True, timeout=30)

    logger.info("Membrillo Telegram bot started polling")


if __name__ == "__main__":
    main()