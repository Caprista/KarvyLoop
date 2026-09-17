from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Protocol

from .models import PermanentDeliveryError, RateLimitedError, RetryableDeliveryError
from .outbox import OutboxStore


class ChannelAdapter(Protocol):
    channel: str
    def send(self, **kwargs: Any) -> Any: ...


@dataclass
class Dispatcher:
    store: OutboxStore
    adapters: dict[str, ChannelAdapter]
    owner: str = "dispatcher"
    max_attempts: int = 3

    async def dispatch_once(self, *, limit: int = 10) -> int:
        completed = 0
        for delivery in self.store.lease_deliveries(self.owner, limit=limit):
            try:
                adapter = self.adapters.get(delivery["channel"])
                if adapter is None:
                    raise RetryableDeliveryError(f"no adapter for {delivery['channel']}")
                payload = self.store.read_delivery(delivery["id"])
                result = adapter.send(**payload)
                if inspect.isawaitable(result): result = await result
                if result is False: raise RetryableDeliveryError("adapter returned false")
                provider_message_id = result.get("message_id", "") if isinstance(result, dict) else ""
                provider_request_id = result.get("request_id", "") if isinstance(result, dict) else ""
                self.store.mark_success(delivery["id"], self.owner, provider_message_id, provider_request_id); completed += 1
            except PermanentDeliveryError as exc:
                self.store.mark_permanent_failure(delivery["id"], self.owner, str(exc))
            except RateLimitedError as exc:
                self.store.mark_retry(delivery["id"], self.owner, str(exc), self.store.now() + exc.retry_after)
            except Exception as exc:
                if delivery["attempt_count"] >= self.max_attempts: self.store.mark_permanent_failure(delivery["id"], self.owner, str(exc))
                else: self.store.mark_retry(delivery["id"], self.owner, str(exc))
        return completed
