from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings,ConditionalKeyBindings,DynamicKeyBindings,merge_key_bindings
from prompt_toolkit.layout.containers import Window, HSplit,DynamicContainer
from prompt_toolkit.application.current import get_app
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout import Layout
from prompt_toolkit.filters import Condition
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
        self.status = 'Choose a section to configure your workspace.'
        self.kb = KeyBindings()
        self._setup_bindings()

    def _get_items(self):
        active_ep = CONFIG.get("active_endpoint") or CONFIG.get("endpoint", self.selected_ep)
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
            eps = sorted(ENDPOINT_REGISTRY,key=str.casefold)
            return [
                {"label": ep, "hint": "(active)" if ep == active_ep else "", "id": ep}
                for ep in eps
            ]
        elif self.screen == "models":
            ep = self.selected_ep
            models = list(ENDPOINT_REGISTRY.get(ep,{}).get("models",{}))
            return [
                {
                    "label": m.split("/")[-1].replace("@cf/", "").replace("meta/", ""),
                    "hint": "(active)" if m == active_m and self.selected_ep == active_ep else "",
                    "id": m
                }
                for m in models
            ]
        elif self.screen in ("local", "local_profiles", "local_tuning", "local_add", "local_result"):
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
                    self.selected_ep=CONFIG.get("active_endpoint",self.selected_ep)
                    self.screen = "endpoints"
                    eps = sorted(ENDPOINT_REGISTRY,key=str.casefold)
                    self.cursor_idx = eps.index(self.selected_ep) if self.selected_ep in eps else 0
                elif act == "to_models":
                    self.selected_ep=CONFIG.get("active_endpoint",self.selected_ep)
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
                    self.open_connections(event.app)
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

                self.status = 'Selected: '+self.selected_ep+' / '+new_m

                if self.initial_screen == "models":
                    event.app.exit()
                else:
                    self.screen = "main"
                    self.cursor_idx = 1

            elif self.screen in ("local", "local_profiles", "local_tuning", "local_add", "local_result"):
                destination = self.local_screens.enter(item, event.app.invalidate)
                if item.get("action") in ("local_activate", "local_add_profile"):
                    self.selected_ep = CONFIG.get("active_endpoint", self.selected_ep)
                if destination:
                    self.screen = destination
                    self.cursor_idx = 0
                    if destination == "local_server_editor":
                        self.open_local_server_editor(event.app)

            event.app.invalidate()

        @self.kb.add('escape')
        @self.kb.add('q')
        @self.kb.add('c-c')
        def _back(event):
            if self.screen in ("local_add", "local_result"):
                destination = self.local_screens.enter({"action": "local_back"}, event.app.invalidate)
                self.screen = destination or "local"
                self.cursor_idx = 0
                event.app.invalidate()
            elif self.screen in ("local_profiles", "local_tuning"):
                self.screen = "local"
                self.cursor_idx = 0
                event.app.invalidate()
            elif self.screen != "main" and self.initial_screen != "models":
                self.screen = "main"
                self.cursor_idx = 0
                event.app.invalidate()
            else:
                event.app.exit()

    def page_title(self):
        return {'main':'Workspace settings','endpoints':'Choose endpoint','models':'Choose model',
                'local':'Local models','local_profiles':'Local model profiles','local_tuning':'Local model tuning',
                'local_add':'Select GGUF','local_result':'Local model status'}.get(self.screen,'Settings')

    def get_formatted_text(self):
        """Original compact ten-line menu, with cell-safe rows on narrow terminals."""
        items=self._get_items()
        width=max(12,get_app().output.get_size().columns-1)
        def crop(text,limit):
            result=''
            for char in text:
                if get_cwidth(result+char)>limit-1:return result+'…'
                result+=char
            return result
        title={'main':'Galam.AI Settings','endpoints':'Select Endpoint','models':'Select Model ['+self.selected_ep+']',
               'local':'Local Models','local_profiles':'Model Profiles','local_tuning':'Local Tuning',
               'local_add':'Select GGUF','local_result':'Local Model Status'}.get(self.screen,'Settings')
        lines=[('class:title',crop('╭── '+title,width)+'\n')]
        start=max(0,min(self.cursor_idx-3,len(items)-7))
        for index,item in enumerate(items[start:start+7],start):
            selected=index==self.cursor_idx
            label=item.get('label','');hint=item.get('hint','')
            available=width-4
            # Keep the old aligned label/value layout when it fits.
            label_width=min(24,max(len(label),available-len(hint)-1))
            text=label.ljust(label_width)+((' '+hint) if hint else '')
            lines.append(('class:cursor' if selected else 'class:border','│ ▶ ' if selected else '│   '))
            shown=crop(text,available)
            split=min(len(shown),len(label.ljust(label_width)))
            lines.append(('class:selected' if selected else 'class:unselected',shown[:split]))
            if shown[split:]:lines.append(('class:selected_hint' if selected else 'class:hint',shown[split:]))
            lines.append(('', '\n'))
        for _ in range(max(0,7-len(items[start:start+7]))):lines.append(('class:border','│\n'))
        lines.append(('class:border','╰'+'─'*min(42,width-1)+'\n'))
        lines.append(('class:help',crop('  ↑/↓ Navigate · Enter Select · Esc Back',width)))
        return lines

    def close_connections(self):
        self.selected_ep=CONFIG.get('active_endpoint',self.selected_ep)
        self.screen='main'
        self.cursor_idx=next((index for index,item in enumerate(self._get_items()) if item.get('action')=='to_connections'),0)
        self.status='Connection editor closed. Saved changes are applied.'
        self.app.layout.focus(self.menu_window);self.app.invalidate()

    def open_connections(self,app):
        from ui.endpoint_manager import EndpointManager
        self.editor=EndpointManager(on_close=self.close_connections)
        self.editor.app=app
        self.screen='connections'
        app.layout.focus(self.editor.endpoint_button if self.editor.page=='form' else self.editor.body)
        app.invalidate()

    def close_local_server_editor(self):
        self.screen='local'
        self.cursor_idx=0
        self.app.layout.focus(self.menu_window)
        self.app.invalidate()

    def open_local_server_editor(self,app):
        from ui.local_server_editor import LocalServerEditor
        self.editor=LocalServerEditor(CONFIG,save_settings,self.close_local_server_editor)
        self.editor.app=app
        app.layout.focus(self.editor.fields['url'])
        app.invalidate()

    def build_application(self):
        from ui.settings_theme import SETTINGS_STYLE
        self.menu_window=Window(FormattedTextControl(self.get_formatted_text),height=10,wrap_lines=False)
        root=HSplit([self.menu_window])
        keys=merge_key_bindings([
            ConditionalKeyBindings(self.kb,Condition(lambda:self.screen not in ('connections','local_server_editor'))),
            ConditionalKeyBindings(DynamicKeyBindings(lambda:self.editor.key_bindings if hasattr(self,'editor') else None),
                                   Condition(lambda:self.screen in ('connections','local_server_editor')))])
        app=Application(layout=Layout(DynamicContainer(lambda:self.editor.root if self.screen in ('connections','local_server_editor') else root)),
                        key_bindings=keys,style=SETTINGS_STYLE,full_screen=False,erase_when_done=True,mouse_support=False)
        app.ttimeoutlen=.1
        self.app=app
        return app

    def run(self):
        app=self.build_application()
        try:app.run()
        finally:
            if hasattr(self,'editor'):self.editor.clear_key()

def handle_settings_menu():
    InlineSettingsApp(initial_screen="main").run()

def handle_model_menu():
    InlineSettingsApp(initial_screen="models").run()

open_settings_menu = handle_settings_menu
open_settings = handle_settings_menu
select_model = handle_model_menu
handle_model_select = handle_model_menu
