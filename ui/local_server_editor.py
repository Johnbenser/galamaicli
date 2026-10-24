"""Small focused editor for the Local Models server connection."""
from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Button, Label, TextArea
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.layout.scrollable_pane import ScrollablePane

from engine.local_models import local_base_url, local_models_url, local_chat_url
from ui.endpoint_manager import button_row


class LocalServerEditor:
    def __init__(self, config, save, on_close):
        self.config, self.save, self.on_close = config, save, on_close
        self.status = "Edit where Galam connects and which OpenAI-compatible routes it uses."
        self.fields = {
            "url": TextArea(text=local_base_url(config), multiline=False, height=1,
                            style="class:input", history=DummyHistory()),
            "models": TextArea(text=config.get("local_models_path") or self._default_models(),
                               multiline=False, height=1, style="class:input", history=DummyHistory()),
            "chat": TextArea(text=config.get("local_chat_path") or self._default_chat(),
                             multiline=False, height=1, style="class:input", history=DummyHistory()),
        }
        self.keys = KeyBindings()
        self.keys.add("tab")(focus_next)
        self.keys.add("s-tab")(focus_previous)
        self.keys.add("c-s")(lambda event: self.commit())
        self.keys.add("escape", eager=True)(lambda event: self.close())
        self.keys.add("c-c")(lambda event: self.close())
        from ui.settings_theme import SETTINGS_STYLE
        self.root = HSplit([
            Window(FormattedTextControl("╭── Local Server Connection"), height=1, style="class:title"),
            ScrollablePane(HSplit([
                Label("SERVER  ·  URL includes host and port"),
                self.fields["url"],
                Label("Example: http://127.0.0.1:8080/v1"), Window(height=1),
                Label("MODEL DISCOVERY  ·  GET"), self.fields["models"], Window(height=1),
                Label("CHAT REQUEST  ·  POST"), self.fields["chat"], Window(height=1),
                Label("Only the Local Endpoint uses these routes."),
                Label(lambda: "Models: " + self._preview("models")),
                Label(lambda: "Chat:   " + self._preview("chat")),
            ]), show_scrollbar=True),
            button_row(Button("Save connection", handler=self.commit),
                       Button("Done", handler=self.close)),
            Window(FormattedTextControl(lambda: self.status), height=Dimension(min=1, max=2),
                   wrap_lines=True, style="class:status"),
            Window(FormattedTextControl("Tab: move · Ctrl+S: save · Esc: back"), height=1,
                   style="class:hint"),
        ])
        self.app = None
        self.key_bindings = self.keys
        self.page = "form"

    def _default_models(self):
        from urllib.parse import urlsplit
        path = urlsplit(local_base_url(self.config)).path.rstrip("/") or "/v1"
        return (path if path != "/" else "") + "/models"

    def _default_chat(self):
        from urllib.parse import urlsplit
        path = urlsplit(local_base_url(self.config)).path.rstrip("/") or "/v1"
        return (path if path != "/" else "") + "/chat/completions"

    def _preview(self, key):
        try:
            return (local_models_url if key == "models" else local_chat_url)(
                self._candidate_config())
        except (ValueError, TypeError):
            return "invalid route"

    def _candidate_config(self):
        return {**self.config, "local_endpoint_url": self.fields["url"].text.strip(),
                "local_models_path": self.fields["models"].text.strip(),
                "local_chat_path": self.fields["chat"].text.strip()}

    def commit(self):
        from urllib.parse import urlsplit
        candidate = self._candidate_config()
        try:
            parsed = urlsplit(candidate["local_endpoint_url"])
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("Enter a valid http:// or https:// server URL without credentials, query, or fragment.")
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("Port must be between 1 and 65535.")
            local_models_url(candidate)
            local_chat_url(candidate)
        except (ValueError, TypeError) as error:
            self.status = str(error)
            get_app().invalidate()
            return
        values = {"local_endpoint_url": candidate["local_endpoint_url"],
                  "local_models_path": candidate["local_models_path"],
                  "local_chat_path": candidate["local_chat_path"]}
        self.save(**values)
        self.config.update(values)
        self.status = "Connection settings saved. Review the route preview, then choose Done. Use Test connection from Local Models to verify it."
        get_app().invalidate()

    def close(self):
        self.on_close()

    def clear_key(self):
        return None
