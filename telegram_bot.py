#!/usr/bin/env python3
"""membrillo-telegram - Bot principal con polling robusto."""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Cargar .env
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
HERMES_MODEL = os.getenv("HERMES_MODEL", "nousresearch/hermes-3-llama-3.1-8b")

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

# Enable logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("membrillo-telegram")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /start - Bienvenida."""
    await update.message.reply_text(
        "👋 ¡Hola! Soy Membrillo. Estoy aquí para charlar y recordar cosas contigo.\n"
        "Puedo:\n"
        "• Guardar tus listas (vinos, supermercado, películas, custom)\n"
        "• Recordar tus sentimientos, experiencias y notas\n"
        "• Buscar información en internet cuando preguntes\n"
        "• Consejos amigables sobre vida y relaciones\n\n"
        "Escribe `membri hola` o cualquier cosa para comenzar."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /help."""
    await update.message.reply_text(
        "🤖 **Membrillo - Ayuda**\n\n"
        "Basta con escribirme. Empieza con `membri` o simplemente hablamos.\n\n"
        "**Qué puedo hacer:**\n"
        "- `membri agrega X a la lista de Y` → guarda en tus listas\n"
        "- `membri recuerda que...` → guardo como nota libre\n"
        "- `membri muéstrame mis listas` → ver todo guardado\n"
        "- `membri resetear todo` → borra todo el historial\n"
        "- `membri cómo voy hoy?` → reviso tu historia\n"
        "- Pregunta sobre sentimientos, vida, recomendaciones → busco en internet\n\n"
        "También puedo mencionarme: `@membrillo bot` si prefieres.\n\n"
        "¡Empecemos! ¿Cómo te va?"
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /listas - Ver todas las listas y notas."""
    lst_names = memory.list_names()
    note_names = memory.list_notes()
    lines = ["📋 **Tu memoria de Membrillo:**"]

    if lst_names:
        lines.append("\n📦 **Listas estructuradas:**")
        for name in lst_names:
            items = memory.get_list(name)
            lines.append(f"• **{name}** ({len(items)} items)")
    else:
        lines.append("\n📦 **Listas estructuradas:** Ninguna aún.")

    if note_names:
        lines.append("\n💬 **Tus notas libres:**")
        for key in note_names:
            note = memory.get_note(key)
            if note:
                latest = note[-1]
                lines.append(f"• **{key}** (última: por {latest['user']})")
    else:
        lines.append("\n💬 **Tus notas libres:** Ninguna aún.")

    await update.message.reply_text("\n".join(lines))


async def reset_lista_cmd(update: Update, context) -> None:
    """Handler /reset_lista <nombre>."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Uso: `/reset_lista <nombre>`", parse_mode="Markdown"
        )
        return

    list_name = args[0]
    ok = memory.clear_list(list_name)
    if ok:
        await update.message.reply_text(f"✅ Lista **{list_name}** borrada.")
    else:
        await update.message.reply_text(f"❌ Lista **{list_name}** no existe.")


async def historial_cmd(update: Update, context) -> None:
    """Handler /historial - Ver historia reciente."""
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler principal - Capturo TODO los mensajes pero respondo solo si es para mí."""
    global bot_client
    message = update.message
    if not message or not message.text:
        return

    user_text = message.text.strip()
    user_id = message.from_user.id if message.from_user else "unknown"
    user_name = message.from_user.full_name if message.from_user else "unknown"

    # Siempre guardar en historial (contexto conversacional)
    memory.add_history("user", user_text, user_name)

    # Detectar si es para nosotros DOS formas:
    # 1. Empieza con "membri" (case insensitive)
    # 2. Mención al bot @username
    # 3. Si no hay ningun indicador, aún así responder si hay historial previo

    bot_info = await bot_client.get_me()
    bot_username = bot_info.username
    mention = f"@{bot_username}"

    # Normalizar: quitar mención y prefijo "membri"
    normalized = user_text
    started_with_membri = user_text.lower().startswith("membri")
    has_mention = mention in user_text

    if started_with_membri:
        # Quitar el prefijo "membri" y usar lo que quede
        normalized = user_text[6:].strip()
    elif has_mention:
        # Quitar la mención
        normalized = user_text.replace(mention, "").strip()
    else:
        # Si no menciona "membri" ni @bot, solo responder si hay suficiente historial
        # o si el usuario lleva chateando un rato
        history_len = len(memory.get_history(last_n=3))
        if history_len < 3:
            # Pocas interacciones: ignorar (para no spamear)
            return
        # Mucho historial: responder de todos modos (conversación fluida)

    # Si después de limpiar no hay texto, saludar
    if not normalized:
        await update.message.reply_text(
            "👋 ¡Hola! Soy Membrillo. Escribe `membri ayuda` para ver qué puedo hacer."
        )
        memory.add_history("assistant", "Saludo inicial", "bot")
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

    # Message handler - CAPTURO TODO pero respondo solo si es para mí
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Store bot client for use in handlers
    bot_client = application.bot

    # Start polling
    application.run_polling(drop_pending_updates=True, interval=1.0, timeout=30)

    logger.info("Membrillo Telegram bot started polling")


if __name__ == "__main__":
    main()