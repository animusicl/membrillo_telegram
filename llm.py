#!/usr/bin/env python3
"""membrillo-telegram - OpenRouter Client + Intent Parser + Response Generator."""
import aiohttp
import json
import re
import time
import urllib.parse
from typing import Dict, List, Optional, Any

DEFAULT_MODEL = "nousresearch/hermes-3-llama-3.1-8b"


def build_messages(system_prompt, user_message, conversation_history):
    msgs = [{"role": "system", "content": system_prompt}]
    msgs.extend(conversation_history)
    msgs.append({"role": "user", "content": user_message})
    return msgs


async def http_post(url, json_data, headers, timeout=30):
    async with aiohttp.ClientSession() as sess:
        async with sess.post(url, json=json_data, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 429:
                raise RuntimeError("RATE_LIMIT")
            else:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")


SEARCH_KEYWORDS = [
    "busca", "buscar", "google", "internet", "web", "última", "actual", "hoy",
    "precio", "noticia", "link", "enlace", "url", "fuente", "recomienda",
    "sugiere", "maridaje", "marida", "combina", "ve con", "para", "con", "qué"
]


def needs_web_search(message):
    msg_low = message.lower().strip()
    has_keyword = any(kw in msg_low for kw in SEARCH_KEYWORDS)
    question_patterns = [
        r"qué.*va.*con", r"cuál.*sugiere", r"recomienda.*para",
        r"mejora.*con", r"maridaje.*con", r"ve.*con", r"para.*pescado",
        r"para.*carne", r"para.*queso", r"maridar.*con"
    ]
    is_question = any(re.search(p, msg_low) for p in question_patterns)
    return has_keyword or is_question


def extract_list_reference(message, memory):
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


async def web_search(query, max_results=5):
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


def format_search_results(results):
    if not results:
        return "Sin resultados de búsqueda."
    lines = ["🔍 **Resultados de búsqueda:**"]
    for i, r in enumerate(results, 1):
        title = r['title'].replace('|', '')
        snippet = r['snippet'].replace('|', '')
        url = r['url']
        lines.append(f"{i}. **[{title}]({url})**\n   {snippet}")
    return "\n".join(lines)


async def generate_response(system_prompt, user_message, conversation_history, model=DEFAULT_MODEL, api_key=""):
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


def build_system_prompt(intent, memory):
    lists_info = ""
    for list_name, items in memory._data.get("lists", {}).items():
        if items:
            list_type = list_name.replace("_", " ")
            items_sample = items[:3]
            names = [i.get("name", i.get("title", str(i))) for i in items_sample]
            lists_info += f"- Lista **{list_type}**: {', '.join(names)}\n"
    first5 = SEARCH_KEYWORDS[:5]
    return f"""Eres Membrillo, asistente amigable en Telegram.

**Tus listas actuales:**
{lists_info if lists_info else "- No hay listas definidas aún"}

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
"""


def parse_intent(message, memory):
    msg_low = message.lower().strip()
    intent = {
        "action": "general",
        "list_name": None,
        "item_name": None,
        "needs_search": needs_web_search(msg_low),
        "search_query": None,
    }
    list_ref = extract_list_reference(message, memory)
    if list_ref:
        intent["list_name"] = list_ref
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
    if intent["needs_search"]:
        query = msg_low
        for kw in ["busca", "buscar", "google", "internet", "web"]:
            query = query.replace(kw, "").strip()
        for kw in ["con", "para", "ve", "maridaje", "sugiere"]:
            query = query.replace(kw, "").strip()
        intent["search_query"] = query.strip() or "búsqueda general"
    return intent