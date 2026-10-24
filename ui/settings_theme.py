"""Original inline settings colors; the terminal background belongs to the user."""
from prompt_toolkit.styles import Style

SETTINGS_STYLE=Style.from_dict({
    'title':'fg:#38bdf8 bold','border':'fg:#3f3f46',
    'cursor':'fg:#38bdf8 bold','selected':'fg:#ffffff bg:#1e293b bold',
    'selected_hint':'fg:#38bdf8 bg:#1e293b bold','unselected':'fg:#94a3b8',
    'hint':'fg:#64748b','help':'fg:#52525b','status':'fg:#fbbf24',
    'input':'fg:#f8fafc','button.focused':'fg:#38bdf8 bold',
    'radio-selected':'fg:#38bdf8 bold','radio-checked':'fg:#34d399',
    'checkbox-checked':'fg:#34d399',
})
