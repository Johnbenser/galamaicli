"""Shared terminal palette for settings and connection setup."""
from prompt_toolkit.styles import Style

SETTINGS_STYLE=Style.from_dict({
    '':'bg:#0b1120 fg:#cbd5e1',
    'frame.border':'fg:#475569', 'frame.label':'fg:#a78bfa bold',
    'title':'fg:#38bdf8 bold','input':'bg:#1e293b fg:#f8fafc',
    'status':'fg:#fbbf24','hint':'fg:#94a3b8','help':'fg:#94a3b8',
    'cursor':'fg:#34d399 bold','selected':'bg:#1e293b fg:#f8fafc bold',
    'selected_hint':'bg:#1e293b fg:#38bdf8','unselected':'fg:#cbd5e1',
    'button':'bg:#1e293b fg:#cbd5e1',
    'button.focused':'bg:#38bdf8 fg:#0b1120 bold',
    'radio-selected':'fg:#38bdf8 bold','radio-checked':'fg:#34d399',
    'checkbox-checked':'fg:#34d399',
})
