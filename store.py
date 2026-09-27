from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class Conversation:
    merchant_id: str | None = None
    customer_id: str | None = None
    trigger_id: str | None = None

    turns: list[dict[str, Any]] = field(default_factory=list)
    bot_bodies: list[str] = field(default_factory=list)

    auto_reply_count: int = 0
    ended: bool = False


class ContextStore:
    """
    Thread-safe in-memory state store.

    The challenge keeps the bot process alive during the judging window,
    so an in-memory store is sufficient and keeps latency very low.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._started = time.time()

        # (scope, context_id) -> {"version": int, "payload": dict}
        self.contexts: dict[tuple[str, str], dict[str, Any]] = {}

        # conversation_id -> Conversation
        self.conversations: dict[str, Conversation] = {}

        # Used to prevent duplicate outbound messages.
        self.sent_suppression_keys: set[str] = set()

        # Merchant-level opt-out / suppression.
        self.suppressed_merchants: set[str] = set()

        # Some auto-replies can arrive on fresh conversation IDs.
        self.auto_reply_by_merchant: dict[str, int] = {}

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self._started)

    def put_context(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: dict[str, Any],
    ) -> tuple[bool, int | None]:
        """
        Store only newer versions.

        Returns:
            (True, None)  -> stored successfully
            (False, old_version) -> rejected as stale
        """
        key = (scope, context_id)

        with self._lock:
            current = self.contexts.get(key)

            if current is not None and current["version"] >= version:
                return False, current["version"]

            self.contexts[key] = {
                "version": version,
                "payload": payload,
            }

            return True, None

    def get_context(
        self,
        scope: str,
        context_id: str | None,
    ) -> dict[str, Any] | None:
        if not context_id:
            return None

        with self._lock:
            entry = self.contexts.get((scope, context_id))

            if entry is None:
                return None

            return entry["payload"]

    def counts(self) -> dict[str, int]:
        counts = {
            "category": 0,
            "merchant": 0,
            "customer": 0,
            "trigger": 0,
        }

        with self._lock:
            for scope, _ in self.contexts:
                if scope in counts:
                    counts[scope] += 1

        return counts

    def get_conversation(self, conversation_id: str) -> Conversation:
        with self._lock:
            if conversation_id not in self.conversations:
                self.conversations[conversation_id] = Conversation()

            return self.conversations[conversation_id]

    def mark_sent(
        self,
        conversation_id: str,
        body: str,
        merchant_id: str | None,
        customer_id: str | None,
        trigger_id: str | None,
    ) -> None:
        with self._lock:
            conversation = self.get_conversation(conversation_id)

            conversation.merchant_id = merchant_id
            conversation.customer_id = customer_id
            conversation.trigger_id = trigger_id

            conversation.bot_bodies.append(body)

    def has_sent_suppression(self, key: str) -> bool:
        if not key:
            return False

        with self._lock:
            return key in self.sent_suppression_keys

    def mark_suppression(self, key: str) -> None:
        if not key:
            return

        with self._lock:
            self.sent_suppression_keys.add(key)

    def end_conversation(
        self,
        conversation_id: str,
        merchant_id: str | None = None,
    ) -> None:
        with self._lock:
            conversation = self.get_conversation(conversation_id)
            conversation.ended = True

            if merchant_id:
                self.suppressed_merchants.add(merchant_id)

    def is_merchant_suppressed(
        self,
        merchant_id: str | None,
    ) -> bool:
        if not merchant_id:
            return False

        with self._lock:
            return merchant_id in self.suppressed_merchants

    def clear(self) -> None:
        with self._lock:
            self.contexts.clear()
            self.conversations.clear()
            self.sent_suppression_keys.clear()
            self.suppressed_merchants.clear()
            self.auto_reply_by_merchant.clear()