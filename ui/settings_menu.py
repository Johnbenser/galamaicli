import sys
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import Window, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout import Layout
from prompt_toolkit.styles import Style
from prompt_toolkit.filters import to_filter
from config import CONFIG, save_settings
from engine.execution_policy import next_mode
from storage.endpoints import ENDPOINT_REGISTRY

class InlineSettingsApp:
    def __init__(self, initial_screen="main"):
        self.initial_screen = initial_screen
        self.screen = initial_screen
        self.selected_ep = CONFIG.get("active_endpoint") or CONFIG.get("endpoint", "Direct")
        if self.selected_ep == "Local Endpoint" and initial_screen == "models":
            from engine.local_models import refresh_local_models
            refresh_local_models(CONFIG)
        from engine.local_ui import LocalScreens
        self.local_screens = LocalScreens(CONFIG, save_settings)
        self.cursor_idx = 0
        self.kb = KeyBindings()
        self._setup_bindings()

    def _get_items(self):
        active_ep = self.selected_ep
        active_m = CONFIG.get("active_model") or CONFIG.get("model", "")
        clean_m = active_m.split("/")[-1].replace("@cf/", "").replace("meta/", "")
        mode = CONFIG.get("mode", "REVIEW").upper().replace("_", "-")
        fallback = CONFIG.get("fallback_policy", "ASK").upper()
        stream = "ON" if CONFIG.get("stream", True) else "OFF"

        if self.screen == "main":
            items = [
                {"label": "Active Endpoint", "hint": f"[{active_ep}]", "action": "to_endpoints"},
                {"label": "Active Model",    "hint": f"[{clean_m}]",  "action": "to_models"},
                {"label": "Execution Mode",   "hint": f"[{mode}]",     "action": "toggle_mode"},
                {"label": "Fallback Policy", "hint": f"[{fallback}]", "action": "toggle_fallback"},
                {"label": "Stream Output",    "hint": f"[{stream}]",   "action": "toggle_stream"},
                {"label": "Back to Chat",     "hint": "",              "action": "exit"}
            ]
            if CONFIG.get("local_manager", True) is not False:
                items.insert(-1, self.local_screens.main_item())
            items.insert(-1, {"label":"Model Connections", "hint":"[Add / Edit]", "action":"to_connections"})
            return items
        elif self.screen == "endpoints":
            eps = list(ENDPOINT_REGISTRY.keys())
            return [
                {"label": ep, "hint": "(active)" if ep == self.selected_ep else "", "id": ep}
                for ep in eps
            ]
        elif self.screen == "models":
            ep = self.selected_ep
            if ep not in ENDPOINT_REGISTRY:
                ep = list(ENDPOINT_REGISTRY.keys())[0]
            models = list(ENDPOINT_REGISTRY[ep]["models"].keys())
            return [
                {
                    "label": m.split("/")[-1].replace("@cf/", "").replace("meta/", ""),
                    "hint": "(active)" if m == active_m else "",
                    "id": m
                }
                for m in models
            ]
        elif self.screen in ("local", "local_add", "local_result"):
            return self.local_screens.items(self.screen)
        return []

    def _setup_bindings(self):
        @self.kb.add('up')
        @self.kb.add('k')
        def _up(event):
            items = self._get_items()
            if items:
                self.cursor_idx = (self.cursor_idx - 1) % len(items)
                event.app.invalidate()

        @self.kb.add('down')
        @self.kb.add('j')
        def _down(event):
            items = self._get_items()
            if items:
                self.cursor_idx = (self.cursor_idx + 1) % len(items)
                event.app.invalidate()

        @self.kb.add('enter')
        def _enter(event):
            items = self._get_items()
            if not items or self.cursor_idx >= len(items):
                return
            item = items[self.cursor_idx]

            if self.screen == "main":
                act = item.get("action")
                if act == "to_endpoints":
                    self.screen = "endpoints"
                    eps = list(ENDPOINT_REGISTRY.keys())
                    self.cursor_idx = eps.index(self.selected_ep) if self.selected_ep in eps else 0
                elif act == "to_models":
                    self.screen = "models"
                    if self.selected_ep == "Local Endpoint":
                        from engine.local_models import refresh_local_models
                        refresh_local_models(CONFIG)
                    ep_models = list(ENDPOINT_REGISTRY.get(self.selected_ep, {}).get("models", {}).keys())
                    cur_m = CONFIG.get("active_model", "")
                    self.cursor_idx = ep_models.index(cur_m) if cur_m in ep_models else 0
                elif act == "toggle_mode":
                    new_mode = next_mode(CONFIG.get("mode"))
                    save_settings(mode=new_mode)
                    CONFIG["mode"] = new_mode
                elif act == "toggle_fallback":
                    cur = CONFIG.get("fallback_policy", "ASK").upper()
                    new_fb = "ALWAYS_PROCEED" if cur == "ASK" else "ASK"
                    save_settings(fallback_policy=new_fb)
                    CONFIG["fallback_policy"] = new_fb
                elif act == "toggle_stream":
                    cur = CONFIG.get("stream", True)
                    save_settings(stream=not cur)
                    CONFIG["stream"] = not cur
                elif act == "to_local":
                    self.screen = "local"
                    self.cursor_idx = 0
                elif act == "to_connections":
                    event.app.exit(result='connections')
                elif act == "exit":
                    event.app.exit()

            elif self.screen == "endpoints":
                self.selected_ep = item["id"]
                self.screen = "models"
                if self.selected_ep == "Local Endpoint":
                    from engine.local_models import refresh_local_models
                    refresh_local_models(CONFIG)
                self.cursor_idx = 0

            elif self.screen == "models":
                new_m = item["id"]
                save_settings(
                    active_endpoint=self.selected_ep,
                    endpoint=self.selected_ep,
                    active_model=new_m,
                    model=new_m
                )
                CONFIG["active_endpoint"] = self.selected_ep
                CONFIG["endpoint"] = self.selected_ep
                CONFIG["active_model"] = new_m
                CONFIG["model"] = new_m

                clean_m = new_m.split("/")[-1].replace("@cf/", "").replace("meta/", "")
                sys.stdout.write(f"\n\033[38;5;39m•\033[0m \033[1;38;5;75mEngine Updated:\033[0m \033[1;38;5;141m[{self.selected_ep}]\033[0m \033[1;38;5;120m{clean_m}\033[0m\n")
                sys.stdout.flush()

                if self.initial_screen == "models":
                    event.app.exit()
                else:
                    self.screen = "main"
                    self.cursor_idx = 1

            elif self.screen in ("local", "local_add", "local_result"):
                destination = self.local_screens.enter(item, event.app.invalidate)
                if item.get("action") in ("local_activate", "local_add_profile"):
                    self.selected_ep = CONFIG.get("active_endpoint", self.selected_ep)
                if destination:
                    self.screen = destination
                    self.cursor_idx = 0

            event.app.invalidate()

        @self.kb.add('escape')
        @self.kb.add('q')
        @self.kb.add('c-c')
        def _back(event):
            if self.screen in ("local_add", "local_result"):
                self.local_screens.enter({"action": "local_back"}, event.app.invalidate)
                self.screen = "local"
                self.cursor_idx = 0
                event.app.invalidate()
            elif self.screen != "main" and self.initial_screen != "models":
                self.screen = "main"
                self.cursor_idx = 0
                event.app.invalidate()
            else:
                event.app.exit()

    def get_formatted_text(self):
        items = self._get_items()
        if self.screen == "main":
            title = "Galam.AI Settings"
        elif self.screen == "endpoints":
            title = "Select Endpoint"
        elif self.screen in ("local", "local_add", "local_result"):
            title = {"local": "Local Models", "local_add": "Select GGUF", "local_result": "Local Model Status"}[self.screen]
        else:
            title = f"Select Model [{self.selected_ep}]"

        lines = [("class:title", f"╭── {title}\n")]
        start_idx = max(0, min(self.cursor_idx - 3, len(items) - 7))
        visible_items = items[start_idx:start_idx + 7]

        for i, itm in enumerate(visible_items):
            actual_idx = start_idx + i
            label = itm.get("label", "")
            hint = itm.get("hint", "")
            if actual_idx == self.cursor_idx:
                lines.append(("class:cursor", "│ ▶ "))
                lines.append(("class:selected", f"{label:<24}"))
                if hint:
                    lines.append(("class:selected_hint", f" {hint}"))
                lines.append(("", "\n"))
            else:
                lines.append(("class:border", "│   "))
                lines.append(("class:unselected", f"{label:<24}"))
                if hint:
                    lines.append(("class:hint", f" {hint}"))
                lines.append(("", "\n"))

        for _ in range(7 - len(visible_items)):
            lines.append(("class:border", "│\n"))

        lines.append(("class:border", "╰──────────────────────────────────────────\n"))
        lines.append(("class:help", "  [↑/↓] Navigate  •  [Enter] Select  •  [Esc] Back\n"))
        return lines

    def run(self):
        style = Style.from_dict({
            'title': 'fg:#38bdf8 bold',
            'border': 'fg:#3f3f46',
            'cursor': 'fg:#38bdf8 bold',
            'selected': 'fg:#ffffff bg:#1e293b bold',
            'selected_hint': 'fg:#38bdf8 bg:#1e293b bold',
            'unselected': 'fg:#94a3b8',
            'hint': 'fg:#64748b',
            'help': 'fg:#52525b',
        })
        app = Application(
            layout=Layout(HSplit([Window(
                content=FormattedTextControl(self.get_formatted_text),
                height=10,
                dont_extend_height=to_filter(True)
            )])),
            key_bindings=self.kb,
            style=style,
            full_screen=False
        )
        while True:
            result=app.run()
            sys.stdout.write("\033[10A\r\033[J")
            sys.stdout.flush()
            if result!='connections':break
            from ui.endpoint_manager import EndpointManager
            EndpointManager().run()
            self.selected_ep=CONFIG.get('active_endpoint',self.selected_ep)
            self.screen='main'
            self.cursor_idx=1

def handle_settings_menu():
    InlineSettingsApp(initial_screen="main").run()

def handle_model_menu():
    InlineSettingsApp(initial_screen="models").run()

open_settings_menu = handle_settings_menu
open_settings = handle_settings_menu
select_model = handle_model_menu
handle_model_select = handle_model_menu
