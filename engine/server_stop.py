"""Interpret only explicit stop requests; mentions and constraints are not actions."""
import re


def requested_stop_targets(request, tasks):
    """None delegates normal chat; [] means an explicit but unresolved target."""
    text = str(request).strip().lower().rstrip(' .!?')
    text = re.sub(r'^(?:(?:please|kindly)\s+|(?:can|could|would)\s+you\s+)+', '', text)
    match = re.fullmatch(r'(?:stop|kill|terminate|shut\s+down|close)\s+(.+?)(?:\s+please)?', text)
    if not match:
        return None
    target = re.sub(r'^(?:(?:the|my|this|current|running)\s+)+', '', match[1])
    if re.fullmatch(r'all\s+(?:(?:the|my|running|background)\s+)*(?:servers?|tasks?|processes|apps?|services?)', target):
        return [str(task['pid']) for task in tasks]
    specific = re.fullmatch(r'(?:(?:server|service|task)\s+(?:on|with)\s+)?(pid|task|port)\s*#?\s*(\d+)', target)
    if specific:
        key = {'pid':'pid', 'task':'id', 'port':'port'}[specific[1]]
        matches = [task for task in tasks if str(task.get(key)) == specific[2]]
    elif re.fullmatch(r'(?:background\s+)?(?:server|service|task|app|process)', target):
        matches = tasks
    elif re.fullmatch(r'(?:python|react|vite|next|node|npm)(?:\s+(?:server|service|app|process))?', target):
        name = target.split()[0]
        aliases = (('node', 'npm', 'vite', 'yarn', 'pnpm', 'bun') if name == 'react'
                   else (r'python(?:[0-9]+(?:\.[0-9]+)?)?',) if name == 'python' else (name,))
        matches = [task for task in tasks if any(re.search(r'\b'+alias+r'\b', str(task.get('full_cmd', '')).lower()) for alias in aliases)]
    else:
        return None
    # Without "all", never turn an ambiguous request into a global shutdown.
    return [str(matches[0]['pid'])] if len(matches) == 1 else []
