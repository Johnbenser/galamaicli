import sys
import time
import threading
import itertools
from ui.styles import C_PURPLE, C_GRAY, C_RESET

try:
    import termios
    import tty
    import select
    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

class EscListener:
    def __init__(self):
        self.cancelled = False
        self.running = False
        self.thread = None
        self.old_settings = None

    def start(self):
        self.cancelled = False
        self.running = True
        if HAS_TERMIOS and sys.stdin.isatty():
            try:
                self.old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except Exception:
                self.old_settings = None

        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()

    def _listen(self):
        while self.running and not self.cancelled:
            if not (HAS_TERMIOS and sys.stdin.isatty()):
                time.sleep(0.1)
                continue
            try:
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    ch = sys.stdin.read(1)
                    if ch == '\x1b':
                        r2, _, _ = select.select([sys.stdin], [], [], 0.02)
                        if r2:
                            sys.stdin.read(2)
                        else:
                            self.cancelled = True
                            break
                    elif ch == '\x03':
                        self.cancelled = True
                        break
            except Exception:
                break

    def stop(self):
        self.running = False
        if HAS_TERMIOS and self.old_settings and sys.stdin.isatty():
            try:
                termios.tcflush(sys.stdin, termios.TCIFLUSH)
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.1)

class AnimatedSpinner:
    def __init__(self, message="Thinking..."):
        self.message = message
        self.running = False
        self.thread = None
        self.esc_listener = EscListener()

    def _spin(self):
        spinner_chars = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
        while self.running:
            char = next(spinner_chars)
            sys.stdout.write(f"\r\033[K{C_PURPLE}{char}{C_RESET} {C_GRAY}{self.message} (Press ESC to cancel){C_RESET}")
            sys.stdout.flush()
            time.sleep(0.08)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def update_message(self, new_msg):
        self.message = new_msg

    def start(self):
        self.running = True
        self.esc_listener.start()
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def stop(self):
        if self.running:
            self.running = False
            self.esc_listener.stop()
            if self.thread:
                self.thread.join()

    @property
    def cancelled(self):
        return self.esc_listener.cancelled
