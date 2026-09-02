#!/usr/bin/env python3
"""membrillo-telegram - Global Memory (JSON persistente)."""
import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Any, Optional


class GlobalMemory:
    def __init__(self, path: Path = Path("memory.json")):
        self.path = path
        self._lock = threading.Lock()
        self._data: Optional[dict] = None
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                if "lists" not in self._data:
                    self._data["lists"] = {}
                if "conversation_history" not in self._data:
                    self._data["conversation_history"] = []
                if "chat_id" not in self._data:
                    self._data["chat_id"] = 0
            except (json.JSONDecodeError, IOError):
                self._data = self._default()
        else:
            self._data = self._default()
            self._save()

    def _default(self):
        return {"lists": {}, "conversation_history": [], "chat_id": 0}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    @property
    def chat_id(self):
        return self._data.get("chat_id", 0) if self._data else 0

    @chat_id.setter
    def chat_id(self, value):
        if self._data:
            self._data["chat_id"] = value

    def get_list(self, name):
        if self._data:
            return self._data.get("lists", {}).get(name, [])
        return []

    def add_item(self, list_name, item, added_by="unknown"):
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

    def remove_item(self, list_name, item_name):
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

    def clear_list(self, list_name):
        with self._lock:
            if self._data and list_name in self._data.get("lists", {}):
                self._data["lists"][list_name] = []
                self._save()
                return True
            return False

    def list_names(self):
        if self._data:
            return list(self._data.get("lists", {}).keys())
        return []

    def add_history(self, role, content, user="unknown"):
        with self._lock:
            if self._data:
                self._data["conversation_history"].append(
                    {"role": role, "content": content, "user": user, "timestamp": time.time()}
                )
                if len(self._data["conversation_history"]) > 500:
                    self._data["conversation_history"] = self._data["conversation_history"][-500:]
                self._save()

    def get_history(self, last_n=10):
        with self._lock:
            if self._data:
                return self._data["conversation_history"][-last_n:]
            return []

    def __str__(self):
        if self._data:
            return f"GlobalMemory(chat_id={self.chat_id}, lists={list(self._data.get('lists', {}).keys())})"
        return "GlobalMemory(empty)"