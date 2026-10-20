import os
import subprocess
import time
from ui.styles import C_GRAY, C_RED, C_RESET

def launch_terminal_editor(filepath):
    editor = os.getenv("EDITOR", "nano")
    print(f"\n{C_GRAY}[Opening {filepath} in {editor}] (Ctrl+O to save, Ctrl+X to exit)...{C_RESET}")
    time.sleep(0.4)
    try:
        subprocess.run([editor, filepath])
    except FileNotFoundError:
        try:
            subprocess.run(["vi", filepath])
        except Exception as e:
            print(f"{C_RED}No editor found: {e}. Run 'pkg install nano'.{C_RESET}")
    except Exception as e:
        print(f"{C_RED}Editor error: {e}{C_RESET}")
    print()
