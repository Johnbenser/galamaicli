"""Keep an owned launch alive until its shell and service descendants exit.

Executed in the launch's new session. The supervisor remains its group leader,
so group identity cannot be reused while a background service is tracked.
No ports, executable names or unrelated processes establish ownership.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def group_members(group, adopted=False):
    """Linux/Android process-group membership, excluding zombies and ourselves.

    A missing /proc entry is a normal exit race. Other users' unreadable
    entries do not establish membership in our owned process group.
    """
    members = []
    # Orphan adoption normally gives a cheap direct-child proof of life. Avoid
    # repeatedly walking every system process while a service is healthy.
    try:
        children = Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()
        if adopted and not children:
            return []  # With orphan adoption, no children means no descendants.
        for pid in children:
            raw = Path('/proc', pid, 'stat').read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            if int(fields[2]) == group and fields[0] not in ('Z', 'X'):
                return [int(pid)]
    except (OSError, ValueError, IndexError):
        pass
    try:
        Path('/proc/self/stat').read_text()
        entries = list(Path('/proc').iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / 'stat').read_text()
            # comm may contain spaces and parentheses; fields follow its final ')'.
            fields = raw[raw.rfind(')') + 2:].split()
            if int(fields[2]) == group and fields[0] not in ('Z', 'X'):
                members.append(int(entry.name))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, IndexError):
            continue
    return members


def _adopt_children():
    try:
        import ctypes
        return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    except (ImportError, AttributeError, OSError):
        return False  # Group ownership still works without orphan adoption.


def supervise(command):
    adopted = _adopt_children()
    group = os.getpgrp()
    stopping = []

    def stop(signum, frame):
        if not stopping:
            stopping.append(time.monotonic())
        # The caller signals the owned group, including this supervisor. Keep
        # ownership until children exit; escalate if one ignores graceful stop.

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    shell = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL)
    child_status = 0
    while True:
        status = shell.poll()
        if status is not None:
            # Reap adopted descendants only after Popen reaped the direct shell.
            while True:
                try:
                    pid, result = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if not pid:
                    break
                code = os.waitstatus_to_exitcode(result)
                child_status = child_status or code
            members = group_members(group, adopted)
            if members == []:
                return status or child_status
            if members is None:
                # Never silently lose a service because process visibility failed.
                # Keep the owned group stoppable and diagnose in the launch log.
                if not getattr(supervise, '_warned', False):
                    print('[Galam supervisor: process-group visibility unavailable; retaining ownership until explicit stop]',
                          file=sys.stderr, flush=True)
                    supervise._warned = True
        if stopping and time.monotonic() - stopping[0] > 2:
            os.killpg(group, signal.SIGKILL)
        time.sleep(.2)


if __name__ == '__main__':
    try:
        result = supervise(sys.argv[1])
    except Exception as error:
        print(f'[Galam supervisor failed: {type(error).__name__}: {error}]', file=sys.stderr, flush=True)
        # Retain no untracked descendants after an internal supervisor failure.
        os.killpg(os.getpgrp(), signal.SIGKILL)
        raise
    sys.exit(result if result >= 0 else 128 - result)
