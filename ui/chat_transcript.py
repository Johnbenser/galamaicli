"""Shared live/history message presentation; rendering never runs model actions."""
import shutil
from engine.transparency import safe_display
from engine.tool_parser import clean_response_text,render_markdown_response
from ui.response_renderer import wrap_ansi


def assistant_text(content,success=False):
    body=render_markdown_response(clean_response_text(str(content)),indent='  ')
    status='  \033[1;32mSuccess.\033[0m\n\n' if success else ''
    return '\n  \033[1;38;5;75m✦ Galam.ai\033[0m\n\n'+status+body+'\n\n'


def user_text(content):
    width=max(10,shutil.get_terminal_size((44,24)).columns-1)
    lines=[]
    for line in safe_display(str(content)).splitlines() or ['']:
        lines.extend(wrap_ansi(line,max(8,width-2),preserve_space=True))
    body='\n'.join(('\033[38;5;141m> \033[0m' if index==0 else '  ')+line for index,line in enumerate(lines))
    return '\n\033[38;5;238m'+'─'*width+'\033[0m\n'+body+'\n\n'
