#!/usr/bin/env python3
"""membrillo-telegram - Global Memory (JSON persistente expandido)."""

import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Any, Optional


class GlobalMemory:
    """Memoria global persistente en JSON para el chat de Telegram.

    Almacena tres tipos de información:
    1. lists: Listas estructuradas (vinos, super, peliculas, custom)
    2. notes: Notas libres/recuerdos que el usuario guarda (temas, sentimientos, etc.)
    3. conversation_history: Historial completo para contexto conversacional
    """

    def __init__(self, path: Path = Path("memory.json")):
        self.path = path
        self._lock = threading.Lock()
        self._data: Optional[dict] = None
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                if "lists" not in self._data:
                    self._data["lists"] = {}
                if "notes" not in self._data:
                    self._data["notes"] = {}
                if "conversation_history" not in self._data:
                    self._data["conversation_history"] = []
                if "chat_id" not in self._data:
                    self._data["chat_id"] = 0
            except (json.JSONDecodeError, IOError):
                self._data = self._default()
        else:
            self._data = self._default()
            self._save()

    def _default(self) -> dict:
        return {
            "lists": {},
            "notes": {},
            "conversation_history": [],
            "chat_id": 0,
        }

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    # ─── Properties ───

    @property
    def chat_id(self) -> int:
        return self._data.get("chat_id", 0) if self._data else 0

    @chat_id.setter
    def chat_id(self, value: int) -> None:
        if self._data:
            self._data["chat_id"] = value

    # ─── List operations ───

    def get_list(self, name: str) -> List[dict]:
        if self._data:
            return self._data.get("lists", {}).get(name, [])
        return []

    def add_item(self, list_name: str, item: dict, added_by: str = "unknown") -> bool:
        with self._lock:
            lst = self.get_list(list_name)
            if "name" not in item and "title" not in item:
                item["name"] = str(item.get("item", item.get("product", "artículo")))
            item["added_by"] = added_by
            item["added_at"] = time.time()
            if any(i.get("name") == item.get("name") for i in lst):
                return False
            lst.append(item)
            if self._data:
                self._data["lists"][list_name] = lst
                self._save()
            return True
        return False

    def remove_item(self, list_name: str, item_name: str) -> bool:
        with self._lock:
            lst = self.get_list(list_name)
            original_len = len(lst)
            lst = [i for i in lst if i.get("name", "") != item_name]
            if len(lst) < original_len:
                if self._data:
                    self._data["lists"][list_name] = lst
                    self._save()
                return True
            return False

    def clear_list(self, list_name: str) -> bool:
        with self._lock:
            if self._data and list_name in self._data.get("lists", {}):
                self._data["lists"][list_name] = []
                self._save()
                return True
            return False

    def list_names(self) -> List[str]:
        if self._data:
            return list(self._data.get("lists", {}).keys())
        return []

    # ─── NUEVO: Note operations (recuerdos libres) ───

    def add_note(self, key: str, content: str, user: str = "unknown") -> bool:
        """Añade una nota/libre memoria. Ejemplos: key='sentimientos', key='viajes', key='favoritos'."""
        with self._lock:
            if self._data:
                if key not in self._data.get("notes", {}):
                    self._data["notes"][key] = []
                self._data["notes"][key].append(
                    {"content": content, "user": user, "timestamp": time.time()}
                )
                # Mantener solo los últimos 50 mensajes por nota
                notes = self._data["notes"][key]
                if len(notes) > 50:
                    self._data["notes"][key] = notes[-50:]
                self._save()
                return True
            return False

    def get_note(self, key: str) -> Optional[List[dict]]:
        """Obtiene todas las entradas de una nota por clave."""
        if self._data:
            return self._data.get("notes", {}).get(key)
        return None

    def list_notes(self) -> List[str]:
        """Lista todas las claves de notas existentes."""
        if self._data:
            return list(self._data.get("notes", {}).keys())
        return []

    def delete_note(self, key: str) -> bool:
        """Borra una nota completa por clave."""
        with self._lock:
            if self._data and key in self._data.get("notes", {}):
                del self._data["notes"][key]
                self._save()
                return True
            return False

    # ─── Conversation history ───

    def add_history(self, role: str, content: str, user: str = "unknown") -> None:
        with self._lock:
            if self._data:
                self._data["conversation_history"].append(
                    {"role": role, "content": content, "user": user, "timestamp": time.time()}
                )
                # Mantener solo los últimos 500 mensajes globally
                if len(self._data["conversation_history"]) > 500:
                    self._data["conversation_history"] = self._data["conversation_history"][-500:]
                self._save()

    def get_history(self, last_n: int = 100) -> List[dict]:
        """Obtiene los últimos N mensajes del historial."""
        with self._lock:
            if self._data:
                return self._data["conversation_history"][-last_n:]
            return []

    def clear_history(self) -> None:
        """Borra todo el historial de conversación."""
        with self._lock:
            if self._data:
                self._data["conversation_history"] = []
                self._save()

    def __str__(self) -> str:
        if self._data:
            lists_k = list(self._data.get('lists', {}).keys())
            notes_k = list(self._data.get('notes', {}).keys())
            hist_len = len(self._data.get('conversation_history', []))
            return f"GlobalMemory(chat_id={self.chat_id}, lists={lists_k}, notes={notes_k}, history={hist_len}msgs)"
        return "GlobalMemory(empty)"