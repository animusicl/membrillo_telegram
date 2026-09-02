#!/usr/bin/env python3
"""membrillo-telegram - OpenRouter Client + Intent Parser + Response Generator + /remember command."""

import aiohttp
import json
import re
import time
import urllib.parse
from typing import Dict, List, Optional, Any

# Model configuration
DEFAULT_MODEL = "openrouter/auto"


def build_messages(system_prompt: str, user_message: str, conversation_history: List[dict]) -> List[dict]:
    """Construye la lista de mensajes para OpenRouter."""
    msgs = [{"role": "system", "content": system_prompt}]
    msgs.extend(conversation_history)
    msgs.append({"role": "user", "content": user_message})
    return msgs


async def http_post(url: str, json_data: dict, headers: dict, timeout: int = 30) -> dict:
    """POST HTTP genérico con aiohttp."""
    async with aiohttp.ClientSession() as sess:
        async with sess.post(url, json=json_data, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 429:
                raise RuntimeError("RATE_LIMIT")
            else:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")


# ─── Search Keywords ───
SEARCH_KEYWORDS = [
    "busca", "buscar", "google", "internet", "web", "última", "actual", "hoy",
    "precio", "noticia", "link", "enlace", "url", "fuente", "recomienda",
    "sugiere", "maridaje", "marida", "combina", "ve con", "para", "con", "qué"
]


def needs_web_search(message: str) -> bool:
    """Detecta si el mensaje necesita búsqueda web (keywords + intención de pregunta)."""
    msg_low = message.lower().strip()
    has_keyword = any(kw in msg_low for kw in SEARCH_KEYWORDS)
    question_patterns = [
        r"qué.*va.*con", r"cuál.*sugiere", r"recomienda.*para",
        r"mejora.*con", r"maridaje.*con", r"ve.*con", r"para.*pescado",
        r"para.*carne", r"para.*queso", r"maridar.*con"
    ]
    is_question = any(re.search(p, msg_low) for p in question_patterns)
    return has_keyword or is_question


def extract_list_reference(message: str, memory: Any) -> Optional[str]:
    """Intenta extraer el nombre de lista referenciado en el mensaje."""
    patterns = [
        r"de.*lista.*?(\w+)",
        r"de.*mis.*?(\w+)",
        r"la lista de (\w+)",
        r"mi lista de (\w+)",
    ]
    msg_low = message.lower()
    for pattern in patterns:
        match = re.search(pattern, msg_low)
        if match:
            return match.group(1)
    for list_name in memory.list_names():
        if list_name.lower() in msg_low:
            return list_name
    return None


# ─── Web Search (DuckDuckGo) ───

async def web_search(query: str, max_results: int = 5) -> List[dict]:
    """Busca en DuckDuckGo y devuelve [{title, url, snippet}, ...]"""
    url = "https://html.duckduckgo.com/html/"
    params = {"q": query, "kl": "es-es"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, data=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html = await resp.text()
    except Exception as e:
        print(f"Error búsqueda web: {e}")
        return []

    result_blocks = re.findall(
        r'class="result__title".*?href="([^"]+)".*?>(.*?)</a>.*?class="result__snippet".*?>(.*?)</a>',
        html, re.DOTALL
    )
    results = []
    for href, title_html, snippet_html in result_blocks[:max_results]:
        title = re.sub(r'<[^>]+>', '', title_html).strip()
        snippet = re.sub(r'<[^>]+>', '', snippet_html).strip()[:300]
        clean_url = href
        if clean_url.startswith("//"):
            clean_url = "https:" + clean_url
        elif not clean_url.startswith("http"):
            clean_url = "https://" + clean_url
        if "/l/?uddg=" in clean_url:
            match = re.search(r'uddg=([^&]+)', clean_url)
            if match:
                clean_url = urllib.parse.unquote(match.group(1))
        results.append({"title": title, "url": clean_url, "snippet": snippet})
    return results


def format_search_results(results: List[dict]) -> str:
    """Formatea resultados markdown."""
    if not results:
        return "Sin resultados de búsqueda."
    lines = ["🔍 **Resultados de búsqueda:**"]
    for i, r in enumerate(results, 1):
        title = r['title'].replace('|', '')
        snippet = r['snippet'].replace('|', '')
        url = r['url']
        lines.append(f"{i}. **[{title}]({url})**\n   {snippet}")
    return "\n".join(lines)


# ─── LLM Response Generator ───

async def generate_response(system_prompt: str, user_message: str,
                            conversation_history: List[dict],
                            model: str = DEFAULT_MODEL,
                            api_key: str = "") -> str:
    """Genera respuesta via OpenRouter API."""
    messages = build_messages(system_prompt, user_message, conversation_history)
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://membrillo-telegram.onrender.com",
        "X-Title": "membrillo-telegram",
    }
    json_data = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    try:
        result = await http_post(url, json_data, headers=headers)
        if result.get("error", {}).get("code") == "RATE_LIMIT":
            raise RuntimeError("RATE_LIMIT")
        if result.get("choices") and len(result["choices"]) > 0:
            return result["choices"][0]["message"]["content"].strip()
        else:
            return "❌ Error: Sin respuesta del modelo."
    except RuntimeError as e:
        if str(e) == "RATE_LIMIT":
            return "⚠️ Cuota gratuita agotada (429). Intenta en unos minutos."
        raise
    except Exception as e:
        print(f"Error LLM: {e}")
        return f"❌ Error conectando con el modelo: {str(e)[:200]}"


# ─── Prompt builder ───

def build_system_prompt(intent: dict, memory: Any) -> str:
    """Construye system prompt con contexto de listas y búsqueda."""
    lists_info = ""
    for list_name, items in memory._data.get("lists", {}).items():
        if items:
            list_type = list_name.replace("_", " ")
            items_sample = items[:3]
            names = [i.get("name", i.get("title", str(i))) for i in items_sample]
            lists_info += f"- Lista **{list_type}**: {', '.join(names)}\n"
    
    first5 = SEARCH_KEYWORDS[:5]
    
    # Información de notas para el sistema
    notes_info = ""
    note_keys = memory.list_notes()
    if note_keys:
        notes_info = "\n**Tus recuerdos guardados:**\n"
        for key in note_keys[:3]:  # Mostrar max 3 notas más recientes
            note = memory.get_note(key)
            if note:
                latest = note[-1]
                notes_info += f"- **{key}**: {latest['content']}\n"
    
    return f"""Eres Membrillo, asistente amigable en Telegram.

**Tus listas actuales:**
{lists_info if lists_info else "- No hay listas definidas aún"}

{notes_info}

**Historia reciente:** {len(intent.get('history', []))} mensajes recientes

**Intención detectada:** {intent.get('action', 'general')}

**Instrucciones:**
- Gestiona listas: ADD items, REMOVE, CLEAR lists
- Cuando haya keywords de búsqueda ({first5}) + intención de pregunta:
  1. Ejecuta web_search() con la consulta del usuario
  2. Inyecta resultados como system message adicional
  3. Recomienda DE LA LISTA DEL USUARIO si hay match, sino los mejores generales
- Siempre que hagas búsqueda web: incluye links (URLs) en la respuesta
- Sé natural, no corrijas al usuario, conversa como amigo en español
- Si no sabes algo, dilo con humildad
- Si el usuario pregunta por maridaje/comida: busca en internet y conecta con su lista
- Usa markdown para formato (negrita, enlaces [texto](url))
- **Para preguntas sobre recuerdos guardados:** Si el usuario pregunta por información guardada con /remember, busca en las notas del usuario y priorízala en tu respuesta
- Si no tienes información guardada, dilo con humildad y ofrece buscar en internet
"""


def parse_intent(message: str, memory: Any) -> dict:
    """Parsea el mensaje del usuario para detectar intencionalidad y entidades."""
    msg_low = message.lower().strip()
    intent = {
        "action": "general",
        "list_name": None,
        "item_name": None,
        "needs_search": needs_web_search(msg_low),
        "search_query": None,
        "note_key": None,
        "note_content": None,
    }
    
    # Extraer nombre de lista
    list_ref = extract_list_reference(message, memory)
    if list_ref:
        intent["list_name"] = list_ref
    
    # Extraer nombre de ítem (después de "agrega", "añade", "pon", "add")
    item_patterns = [
        r"agrega?\s+(\w+\s*\w*)",
        r"añade\s+(\w+\s*\w*)",
        r"pon\s+(\w+\s*\w*)",
        r"add\s+(\w+\s*\w*)",
        r"crea\s+lista\s+(\w+)",
    ]
    for pattern in item_patterns:
        match = re.search(pattern, msg_low, re.IGNORECASE)
        if match:
            intent["item_name"] = match.group(1).strip()
            break
    
    # Detectar acción específica
    if re.search(r"borra|elimina|resetea|reset", msg_low):
        intent["action"] = "reset"
        if intent["list_name"] is None:
            list_ref = extract_list_reference(message, memory)
            if list_ref:
                intent["list_name"] = list_ref
    elif re.search(r"muestra|mostr|ver.*lista|listas", msg_low):
        intent["action"] = "show"
    elif re.search(r"agrega|añade|add|pon", msg_low):
        intent["action"] = "add"
    elif re.search(r"crea|nueva lista", msg_low):
        intent["action"] = "create"
    # NUEVO: Detectar intención de guardar nota
    elif re.search(r"remember\s+guardar|guardo|anoto|guardo que", msg_low):
        intent["action"] = "add_note"
        # Extraer la clave y el contenido
        # Patrones: "remember guardar que X", "guardo que X", "anoto X en Y"
        for pattern in [r"remember\s+guardar\s+que\s+(.+)", r"guardo?\s+que\s+(.+)", r"anoto?\s+(?:en\s+)?(\w+)?\s+(.+)", r"anotame?\s+(?:en\s+)?(\w+)?\s+(.+)"]:
            match = re.search(pattern, msg_low, re.IGNORECASE)
            if match:
                key_val = match.group(1).strip() if match.group(1) else "general"
                content_val = match.group(2).strip() if match.lastindex and match.lastindex > 0 and match.group(match.lastindex) else match.group(1).strip()
                # Separar clave de contenido si hay dos partes
                if " en " in key_val.lower() or " de " in key_val.lower():
                    parts = key_val.lower().split(" en " if " en " in key_val.lower() else " de ")
                    intent["note_key"] = parts[0].strip()
                    intent["note_content"] = parts[1].strip() if len(parts) > 1 else content_val.strip()
                else:
                    # Si solo hay una parte, usarla como contenido con clave por defecto
                    intent["note_content"] = key_val
                break
    
    # NUEVO: Detectar intención de consultar nota
    elif re.search(r"remember\s+pregunta|consulta|dime.*remember|what.*remember", msg_low):
        intent["action"] = "show_note"
        # Extraer qué se quiere consultar
        for pattern in [r"remember\s+(?:sobre|de)\s+(\w+)", r"consultar\s+(\w+)", r"dime.*de\s+(\w+)"]:
            match = re.search(pattern, msg_low, re.IGNORECASE)
            if match:
                intent["note_key"] = match.group(1).strip()
                break
    
    # Si necesita búsqueda, extraer query
    if intent["needs_search"]:
        query = msg_low
        for kw in ["busca", "buscar", "google", "internet", "web"]:
            query = query.replace(kw, "").strip()
        for kw in ["con", "para", "ve", "maridaje", "sugiere"]:
            query = query.replace(kw, "").strip()
        intent["search_query"] = query.strip() or "búsqueda general"
    
    return intent