"""FIFO prompts: pending messages enter conversation context only when they run."""
import queue
import threading
import uuid
from dataclasses import dataclass


@dataclass
class QueuedMessage:
    id: str
    text: str
    conversation_id: str
    messages: list
    staged: bool = False


class MessageQueue:
    def __init__(self):
        self.queue = queue.Queue()
        self.lock = threading.RLock()
        self.pending = {}
        self.active = None

    def enqueue(self, text, conversation_id, messages, staged=False):
        item = QueuedMessage(uuid.uuid4().hex[:8], text, conversation_id, messages, staged)
        with self.lock:
            self.pending[item.id] = item
            self.queue.put(item)
            self.publish()
        return item

    def put(self, item):
        # Preserve the existing approved-plan dispatch interface.
        if item is None:
            self.queue.put(None)
            return
        return self.enqueue(*item)

    def publish(self):
        from config import AGENT_STATE
        AGENT_STATE['queued_messages'] = [{'id':i.id,'text':i.text} for i in self.pending.values()]
        AGENT_STATE['active_message'] = self.active.id if self.active else None
        app = AGENT_STATE.get('app_ref')
        if app:
            try:
                app.invalidate()
            except Exception:
                pass

    def get(self):
        while True:
            item = self.queue.get()
            with self.lock:
                if item is None:
                    self.queue.task_done()
                    return None
                if item.id not in self.pending:
                    self.queue.task_done()
                    continue
                self.pending.pop(item.id)
                self.active = item
                self.publish()
                return item

    def finish(self):
        with self.lock:
            self.active = None
            self.queue.task_done()
            self.publish()

    def clear(self):
        with self.lock:
            count = len(self.pending)
            self.pending.clear()
            self.publish()
            return count
