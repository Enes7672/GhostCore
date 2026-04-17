import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class Event:
    topic: str
    payload: Any
    metadata: dict = field(default_factory=dict)

# Kaç adet olay RAM'de tutulacak (Circular Buffer boyutu)
EVENT_HISTORY_MAXLEN: int = 100

class EventBus:
    def __init__(self) -> None:
        self.subscribers: dict[str, list[asyncio.Queue]] = {}
        # Circular buffer: maxlen aşıldığında eski olaylar otomatik silinir
        self.history: deque[Event] = deque(maxlen=EVENT_HISTORY_MAXLEN)

    def subscribe(self, topics: list[str]) -> asyncio.Queue:
        """Belirtilen topic veya topic'ler için bir Queue döndürür."""
        queue: asyncio.Queue = asyncio.Queue()
        for topic in topics:
            if topic not in self.subscribers:
                self.subscribers[topic] = []
            self.subscribers[topic].append(queue)
        return queue

    async def publish(self, topic: str, payload: Any, metadata: Optional[dict] = None) -> None:
        """Bir olayı yayınlar ve o konuyla ilgilenen tüm kuyruklara ekler."""
        event = Event(topic=topic, payload=payload, metadata=metadata or {})
        self.history.append(event)  # deque otomatik olarak maxlen'i aşan eski olayları atar

        if topic in self.subscribers:
            for queue in self.subscribers[topic]:
                await queue.put(event)

    async def broadcast(self, topic: str, mission_id: str, payload: Any, metadata: Optional[dict] = None) -> None:
        """Mission ID ile zenginleştirilmiş yayın."""
        meta = metadata or {}
        meta["mission_id"] = mission_id
        await self.publish(topic, payload, meta)

    def get_recent_events(self, topic: str | None = None, limit: int = 20) -> list[Event]:
        """Son N olayı döndürür; isteğe bağlı olarak topic'e göre filtreler."""
        events = list(self.history)
        if topic:
            events = [e for e in events if e.topic == topic]
        return events[-limit:]

EVENT_BUS = EventBus()

