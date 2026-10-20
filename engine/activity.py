"""Local display labels derived from already supplied reasoning and tool progress."""
import re
from engine.transparency import safe_display, concise

TOOLS = {'view_file':'Reading','search_files':'Exploring','write_file':'Writing',
    'replace_file_content':'Editing','make_directory':'Creating','execute_bash':'Executing',
    'read_action_output':'Reading','undo_file_change':'Restoring','manage_tasks':'Managing'}
PATTERNS = [('Testing',r'\b(?:test\w*|verif\w*|validat\w*)\b'),
    ('Modifying',r'\b(?:modif\w*|refactor\w*|fixing)\b'),
    ('Editing',r'\b(?:edit\w*|replac\w*)\b'),
    ('Reading',r'\b(?:read\w*|inspect\w*)\b'),
    ('Researching',r'\b(?:research\w*|brows\w*|web search)\b'),
    ('Exploring',r'\b(?:explor\w*|search\w*|listing)\b'),
    ('Structuring',r'\b(?:structur\w*|plan\w*|outlin\w*|design\w*)\b'),
    ('Writing',r'\b(?:writ\w*|creat\w*|implement\w*)\b'),
    ('Reviewing',r'\b(?:review\w*|approval)\b'),
    ('Responding',r'\b(?:respond\w*|compos\w*)\b')]


def label(text):
    for tool, value in TOOLS.items():
        if tool in text:
            return value
    return next((value for value, pattern in PATTERNS if re.search(pattern,text,re.I)),'Thinking')


def clean(text):
    return re.sub(r'[*`#]+','',safe_display(text)).strip()


def reasoning_activity(text):
    lines = safe_display(text).splitlines()
    section = ''
    for line in reversed(lines):
        match = re.match(r'^\s*(?:#{1,6}\s+|\*\*|(?:Step\s+)?\d+[.)]\s+)(.+?)\s*$',line,re.I)
        if match:
            section=clean(match[1]);break
    summary = clean(concise(text))
    return label(section or summary), summary or section


def phase_activity(message):
    text = str(message)
    if 'approval' in text.lower():
        return 'Reviewing',clean(concise(text))
    if 'structured action' in text.lower():
        return 'Structuring','Receiving the complete action arguments.'
    if 'waiting for model' in text.lower() or 'receiving model output' in text.lower():
        return 'Connecting','Contacting the model for the next response.'
    if text.lower().startswith(('responding','continuing response')):
        return 'Responding','Composing the response.'
    return label(text),clean(concise(text))
