"""Side chat on its own worker, sharing the dock but not main task context."""
import queue
import threading
import time
import uuid
from engine.cancellation import independent_task, TaskCancelled
from engine.transparency import safe_display

SYSTEM = ("You are an agile sub-agent within Galam.AI answering inquiries in an isolated incubator container. "
          "Keep answers direct, structured, helpful, and natural. Do not produce verbose internal monologues.")


class BtwSession:
    def __init__(self, query, emit=print):
        self.query, self.emit = query, emit
        self.messages = [{'role':'system','content':SYSTEM}]
        self.pending = queue.Queue()
        self.cancelled = threading.Event()
        self.id = 'btw-'+uuid.uuid4().hex[:6]
        self.worker = threading.Thread(target=self.run,daemon=True,name='galam-btw')
        self.worker.start()

    def submit(self, text):
        if text.strip() and not self.cancelled.is_set():
            self.pending.put(text.strip())

    def close(self):
        self.cancelled.set()
        self.pending.put(None)

    def run(self):
        from config import AGENT_STATE
        while not self.cancelled.is_set():
            question = self.pending.get()
            if question is None or self.cancelled.is_set():
                return
            entry = {'id':self.id,'start_time':time.time(),'elapsed':0,'task':'btw'}
            AGENT_STATE.setdefault('active_subagents',[]).append(entry)
            from engine import usage
            from engine.response_stats import KEY,footer
            from engine.tool_parser import render_markdown_response
            usage_start=len(usage.ledger()['requests'])
            started=time.monotonic()
            def stats(status):
                return {'elapsed':max(0,time.monotonic()-started),'finished_at':time.time(),
                        'status':status,'usage':usage.totals(usage.snapshot(usage_start))}
            try:
                self.messages.append({'role':'user','content':question})
                with independent_task(self.cancelled):
                    answer = self.query(self.messages)
                if not self.cancelled.is_set():
                    timing=stats('completed')
                    self.messages.append({'role':'assistant','content':str(answer),KEY:timing})
                    self.emit('\n\033[1;35m⬡ Galam.ai · /btw\033[0m\n\n'+render_markdown_response(str(answer))+'\n\n'+footer(timing)+'\n')
            except TaskCancelled:
                return
            except Exception as error:
                if not self.cancelled.is_set():
                    self.emit('\n/btw could not complete: '+safe_display(error)+'\n\n'+footer(stats('failed'))+'\n')
            finally:
                if entry in AGENT_STATE.get('active_subagents',[]):
                    AGENT_STATE['active_subagents'].remove(entry)
                app=AGENT_STATE.get('app_ref')
                if app:
                    try:app.invalidate()
                    except Exception:pass
