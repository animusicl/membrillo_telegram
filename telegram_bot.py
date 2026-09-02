#!/usr/bin/env python3
"""membrillo-telegram - Bot principal con /remember y conversación fluida."""

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

# ─── Saludos variados y DIVERTIDOS ───
VARIED_SALUTATIONS = [
    "👋 ¡Hola! ¿Cómo vas? El bot dice que estás lindo hoy.",
    "👋 Hey! Make time to chat, how are you? (the bot agrees)",
    "👋 ¡Qué tal! Hace rato no nos vemos por acá, dice el bot.",
    "👋 Holaa! Extrañaba esta charla contigo, dice el bot.",
    "👋 ¡Holaa! ¿Qué novedad? El bot pregunta.",
    "👋 Hello there! How's your day going? (el bot lo confirma)",
    "👋 ¡Oye! ¿Qué tal te va? (según el bot, bien)",
    "👋 Salom! El bot dice que es tiempo de saludar.",
    "👋 Hola! (dice el bot: hola de vuelta).",
    "👋 ¿Qué pasa? El bot quiere saber.",
]
# ─── Enable logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("membrillo-telegram")


# ─── Comandos /remember ───

async def remember_cmd(update: Update, context) -> None:
    """Handler /remember - Guardar o consultar información."""
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "❓ Uso del comando `/remember`:\n\n"
            "`/remember guardar <texto>` → Guarda ese texto en tu memoria\n"
            "   Ej: `/remember guardar mi novio se llama Habib`\n\n"
            "`/remember pregunta <texto>` → Bot busca en tu memoria\n"
            "   Ej: `/remember pregunta mi novio`\n\n"
            "`/remember listar` → Muestra todo lo guardado\n"
            "`/remember borrar <clave>` → Borra una nota específica\n\n"
            "💡 Los datos se guardan en tu sesión y persisten mientras el bot esté activo.",
            parse_mode="Markdown"
        )
        return

    comando = args[0].lower()
    resto = " ".join(args[1:]) if len(args) > 1 else ""

    if comando == "guardar":
        if not resto:
            await update.message.reply_text("❌ Escribe algo para guardar. Ej: `/remember guardar mi novio se llama Habib`", parse_mode="Markdown")
            return
        
        # Determinar la clave automática o usar una sintética
        # Patrones: "mi novio se llama Habib", "guardo que hoy llueve", etc.
        msg_low = resto.lower()
        
        # Intentar extraer una clave significativa
        note_key = "general"
        note_content = resto
        
        # Si hay "en" o "de", intentar separar
        if " en " in msg_low:
            parts = msg_low.split(" en ", 1)
            note_key = parts[0].replace("mi ", "").replace("el ", "").replace("mis ", "").strip()
            note_content = parts[1].strip()
        elif " de " in msg_low:
            parts = msg_low.split(" de ", 1)
            note_key = parts[0].strip()
            note_content = parts[1].strip()
        else:
            # Usar tema principal: primeras 3 palabras significativas
            palabras = [w for w in msg_low.split() if w not in ["que", "el", "un", "una", "sus", "sus"]]
            note_key = " ".join(palabras[:3]) if palabras else "general"
            note_content = resto
        
        # Guardar en memoria
        ok = memory.add_note(note_key, note_content, user=update.from_user.full_name if update.from_user else "unknown")
        if ok:
            await update.message.reply_text(
                f"✅ **Guardado.**\n"
                f"🔑 Clave: **{note_key}**\n"
                f"📝 Contenido: *{note_content[:80]}{'...' if len(note_content) > 80 else ''}*",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ Error al guardar. Intenta de nuevo.")

    elif comando == "pregunta":
        if not resto:
            await update.message.reply_text("❌ Escribe qué quieres preguntar. Ej: `/remember pregunta mi novio`", parse_mode="Markdown")
            return
        
        # Parsear qué se quiere preguntar
        msg_low = resto.lower()
        
        # Extraer la clave de consulta
        note_key = None
        for pattern in [r"recordarme.*(\w+)", r"sobre\s+(\w+)", r"de\s+(\w+)"]:
            match = re.search(pattern, msg_low)
            if match:
                note_key = match.group(1).strip()
                break
        
        if not note_key:
            note_key = "general"
        
        # Obtener la nota
        note = memory.get_note(note_key)
        if note:
            # Usar el contenido más reciente
            latest = note[-1]
            await update.message.reply_text(
                f"💬 **Sobre '{note_key}':** {latest['content']}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"ℹ️ No tengo nada guardado bajo la clave **{note_key}**. "
                f"Usa `/remember guardar <texto>` para guardar algo primero.",
                parse_mode="Markdown"
            )

    elif comando == "listar":
        note_keys = memory.list_notes()
        if not note_keys:
            await update.message.reply_text("📭 No tienes notas guardadas aún.\nUsa `/remember guardar <texto>` para comenzar a guardar cosas.")
            return
        
        lines = ["📋 **Tus notas guardadas:**"]
        for key in note_keys:
            note = memory.get_note(key)
            if note:
                latest = note[-1]
                lines.append(f"• **{key}**: {latest['content'][:100]}{'...' if len(latest['content']) > 100 else ''}")
        
        await update.message.reply_text("\n".join(lines))

    elif comando == "borrar":
        if not resto:
            await update.message.reply_text("❌ Escribe qué clave borrar. Ej: `/remember borrar parejia`", parse_mode="Markdown")
            return
        
        ok = memory.delete_note(resto)
        if ok:
            await update.message.reply_text(f"🗑️ **Borrado.** La nota '{resto}' ha sido eliminada de tu memoria.")
        else:
            await update.message.reply_text(f"ℹ️ No tienes ninguna nota guardada bajo la clave **{resto}**.")

    else:
        await update.message.reply_text(
            "❓ Comando no reconocido. Usa `/remember` sin argumentos para ver las opciones.",
            parse_mode="Markdown"
        )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /start - Bienvenida con saludo variado y divertido."""
    saludo = random.choice(VARIED_SALUTATIONS)  # Ahora sí es aleatorio y divertido
    await update.message.reply_text(
        f"{saludo}\n\n"
        "Soy Membrillo, tu agente conversacional. Estoy aquí para charlar, "
        "recordar cosas y ayudarte con listas o temas que se te ocurran.\n\n"
        "Puedo:\n"
        "• Guardar tus listas (vinos, supermercado, películas, custom)\n"
        "• Recordar tus sentimientos, experiencias y notas\n"
        "• Buscar información en internet cuando pregunten\n"
        "• Consejos amigables sobre vida y relaciones\n\n"
        "Escribe algo para comenzar o dice `/remember` para ver mis capacidades de memoria.",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /help."""
    await update.message.reply_text(
        "🤖 **Membrillo - Ayuda**\n\n"
        "Soy un agente conversacional que recuerda las cosas importantes.\n\n"
        "**Basta con escribirme.** Empieza con cualquier cosa o usa `@membrillo` si prefieres mencionarme.\n\n"
        "**Comandos principales:**\n"
        "- Escribe cualquier cosa y puedo recordar contexto\n"
        "- `/remember guardar <texto>` → guarda ese texto en tu memoria\n"
        "- `/remember pregunta <texto>` → bot busca en tu memoria\n"
        "- `/remember listar` → muestra todo lo guardado\n"
        "- `/remember borrar <clave>` → borra una nota específica\n\n"
        "**O simplemente charla:**\n"
        "- `membri agrega X a la lista de Y` → guarda en tus listas\n"
        "- `membri qué vino va con pescado` → busca en internet\n"
        "- `membri cómo te va` → conversación fluida\n\n"
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
                lines.append(f"• **{key}**: {latest['content'][:80]}...")
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
    """Handler principal - Conversación fluida sin necesidad de 'membri' cada vez."""
    global bot_client
    message = update.message
    if not message or not message.text:
        return

    user_text = message.text.strip()
    user_id = message.from_user.id if message.from_user else "unknown"
    user_name = message.from_user.full_name if message.from_user else "unknown"

    # Siempre guardar en historial (contexto conversacional)
    memory.add_history("user", user_text, user_name)

    # Detectar si es para nosotros - estrategia de fluidez:
    # 1. Si menciona @membrillo -> responder siempre
    # 2. Si el bot tiene historial previo (>3 mensajes) -> responder también (contexto)
    # 3. Si el mensaje tiene comando o estructura clara -> responder
    # 4. Caso contrario -> ignorar para no spamear
    
    bot_info = await bot_client.get_me()
    bot_username = bot_info.username
    mention = f"@{bot_username}"

    # Verificar indicadores
    has_mention = mention in user_text
    started_with_at = user_text.lower().startswith("@membrillo")
    
    # Historial previo
    history_len = len(memory.get_history(last_n=3))
    
    # Determinar si responder
    should_respond = False
    
    if has_mention or started_with_at:
        # Mención directa -> siempre responder
        should_respond = True
    elif history_len >= 3:
        # Ya hay conversación -> responder sin mención (fluidez)
        should_respond = True
    elif any(user_text.lower().startswith(greeting.lower().split()[0]) 
             for greeting in ["¡hola", "hey", "hola", "holaa"]):
        # Saludos iniciales -> responder
        should_respond = True
    elif len(user_text) > 100:
        # Mensajes largos suelen importar -> responder
        should_respond = True

    if not should_respond:
        # Silencio activo para mensajes triviales sin contexto
        return

    # Normalizar el texto quitando menciones
    normalized = user_text
    if has_mention:
        normalized = user_text.replace(mention, "").strip()
    elif started_with_at:
        normalized = user_text.replace(mention, "").strip()
    elif history_len < 3 and user_text.lower().startswith("membri"):
        # Solo si explicitamente dice "membri" al principio y poco historial
        normalized = user_text[6:].strip()
    else:
        normalized = user_text

    # Si después de normalizar no hay texto, saludar
    if not normalized:
        await update.message.reply_text(
            "👋 ¡Hola! Soy Membrillo. Escribe algo o @mencioname para charlar."
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
    application.add_handler(CommandHandler("remember", remember_cmd))

    # Message handler - conversación fluida
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