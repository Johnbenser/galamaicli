import shutil
from config import CONFIG
from ui.styles import C_CYAN, C_BOLD, C_RESET, C_BLUE, C_YELLOW, C_GRAY

def print_banner(conversation_id="session", endpoint_str=None, mode=None, model_str=None):
    cols = shutil.get_terminal_size((60, 24)).columns

    cur_ep = endpoint_str or CONFIG.get("active_endpoint", "Direct")
    if "[" in str(cur_ep) and "]" in str(cur_ep):
        cur_ep = cur_ep[cur_ep.find("[")+1 : cur_ep.find("]")]

    cur_model = model_str or CONFIG.get("active_model", "")
    cur_mode = (mode or CONFIG.get("mode", "REVIEW")).upper()

    banner = f"""{C_CYAN}{C_BOLD}
 ██████╗  █████╗ ██╗      █████╗ ███╗   ███╗     █████╗ ██╗
██╔════╝ ██╔══██╗██║     ██╔══██╗████╗ ████║    ██╔══██╗██║
██║  ███╗███████║██║     ███████║██╔████╔██║    ███████║██║
██║   ██║██╔══██║██║     ██╔══██║██║╚██╔╝██║    ██╔══██║██║
╚██████╔╝██║  ██║███████╗██║  ██║██║ ╚═╝ ██║██╗ ██║  ██║██║
 ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝ ╚═╝  ╚═╝╚═╝{C_RESET}
┌────────────────────────────────────────────────────────┐
│  Autonomous Android Terminal Agent Environment         │
│  Made possible by John Benser                          │
└────────────────────────────────────────────────────────┘
  {C_BLUE}• Engine:{C_RESET} [{cur_ep}] {cur_model}
  {C_BLUE}• Session ID:{C_RESET} {conversation_id}
  {C_BLUE}• Mode:{C_RESET} {C_YELLOW}{cur_mode}{C_RESET} (Change via /settings)
  {C_GRAY}Commands: /settings, /model, /plan, /artifact, /edit, /btw, or '!' for direct bash{C_RESET}
"""
    print(banner)
