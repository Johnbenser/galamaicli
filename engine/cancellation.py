"""Cooperative task cancellation with isolated, disposable network readers."""
import queue
import threading
import time
import requests
import subprocess
import os
import signal
from contextlib import contextmanager

_LOCAL = threading.local()


class TaskCancelled(requests.exceptions.RequestException):
    pass


def begin_task():
    from config import AGENT_STATE
    event = threading.Event()
    _LOCAL.event = event
    AGENT_STATE['cancel_event'] = event
    AGENT_STATE['abort'] = False
    return event


def current_event():
    return getattr(_LOCAL, 'event', None)


def end_task():
    _LOCAL.event = None


@contextmanager
def independent_task(event):
    previous, independent = current_event(), getattr(_LOCAL, 'independent', False)
    _LOCAL.event, _LOCAL.independent = event, True
    try:
        yield
    finally:
        _LOCAL.event, _LOCAL.independent = previous, independent


def check(event=None):
    from config import AGENT_STATE
    event = event or current_event()
    if (event and event.is_set()) or _aborted():
        raise TaskCancelled('Task cancelled')


def cancel_current():
    from config import AGENT_STATE
    AGENT_STATE['abort'] = True
    AGENT_STATE['is_thinking'] = False
    AGENT_STATE['is_streaming'] = False
    event = AGENT_STATE.get('cancel_event')
    if event:
        event.set()
    AGENT_STATE['review_decision'] = 'cancel'
    review = AGENT_STATE.get('review_event')
    if review:
        review.set()
    AGENT_STATE['review_pending'] = False


def wait(seconds):
    event = current_event()
    if event is None:
        check()
        time.sleep(seconds)
        check()
        return
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        check()
        event.wait(min(.05, max(0, end-time.monotonic())))
    check()


def call(function, dispose=None):
    """Only the caller processes responses; abandoned I/O cannot update a new task."""
    event = current_event()
    if event is None:
        check()
        return function()
    result = queue.Queue(maxsize=1)
    abandoned = threading.Event()
    lock = threading.Lock()

    def run():
        try:
            value, error = function(), None
        except BaseException as exc:
            value, error = None, exc
        with lock:
            if abandoned.is_set():
                if value is not None and dispose:
                    dispose(value)
            else:
                result.put((value, error))
    check(event)
    threading.Thread(target=run, daemon=True, name='galam-network-wait').start()
    try:
        while True:
            check(event)
            try:
                value, error = result.get(timeout=.05)
                if value is not None and (event.is_set() or _aborted()):
                    if dispose:
                        dispose(value)
                    value = None
                check(event)
                if error:
                    raise error
                return value
            except queue.Empty:
                continue
    except TaskCancelled:
        with lock:
            abandoned.set()
            try:
                value, _ = result.get_nowait()
            except queue.Empty:
                value = None
            if value is not None and dispose:
                dispose(value)
        raise


def _aborted():
    from config import AGENT_STATE
    return AGENT_STATE.get('abort', False) and not getattr(_LOCAL, 'independent', False)


def lines(iterator, close=None):
    """Bounded producer lets a stalled SSE read release the main worker promptly."""
    event = current_event()
    if event is None:
        yield from iterator
        return
    items = queue.Queue(maxsize=32)
    stopped = threading.Event()
    def put(item):
        while not stopped.is_set() and not event.is_set():
            try:
                items.put(item, timeout=.05)
                return
            except queue.Full:
                pass
    def produce():
        try:
            for line in iterator:
                if stopped.is_set() or event.is_set():
                    break
                put(('line', line))
        except Exception as error:
            put(('error', error))
        finally:
            put(('end', None))
            if close:
                close()
    threading.Thread(target=produce, daemon=True, name='galam-stream-reader').start()
    try:
        while True:
            check(event)
            try:
                kind, value = items.get(timeout=.05)
            except queue.Empty:
                continue
            if kind == 'end':
                break
            if kind == 'error':
                raise value
            yield value
    finally:
        stopped.set()


def run_process(*args, timeout=120, **kwargs):
    """Cancel only the foreground process group launched by this action."""
    if current_event() is None:
        return subprocess.run(*args, timeout=timeout, **kwargs)
    check()
    process = subprocess.Popen(*args, start_new_session=True, **kwargs)
    started = time.monotonic()
    try:
        while True:
            check()
            if time.monotonic()-started >= timeout:
                raise subprocess.TimeoutExpired(args[0], timeout)
            try:
                stdout, stderr = process.communicate(timeout=.1)
                return subprocess.CompletedProcess(args[0], process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=.5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            except ProcessLookupError:
                pass
