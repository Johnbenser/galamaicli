"""Atomic operational facts: never middle-truncate executable expressions."""
import json
import os
from pathlib import Path
import re
import shlex

from engine.security import redact


def package_evidence(tool, args, output, directory, goal, source_id):
    path = str(args.get('path', ''))
    if tool == 'execute_bash':
        command = str(args.get('command', ''))
        if not re.search(r'\b(?:cat|head|tail)\b[^;\n]*package\.json', command):
            return None
        try:
            path = next((word for word in shlex.split(command) if word.endswith('package.json')), 'package.json')
        except ValueError:
            return None
    elif tool != 'view_file' or Path(path).name != 'package.json':
        return None
    text = re.sub(r'(?m)^\s*\d+\s*[|:]\s?', '', output)
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{', text):
        try:
            package, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if not isinstance(package, dict) or not isinstance(package.get('scripts'), dict):
            continue
        scripts = {key: value for key, value in package['scripts'].items() if isinstance(value, str) and value.strip()}
        explicit = re.search(r'\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?([\w:-]+)', goal)
        preferred = explicit[1] if explicit and explicit[1] in scripts else None
        order = ('dev', 'start', 'serve', 'preview') if re.search(r'\bdev(?:elopment)?\b', goal, re.I) else ('start', 'dev', 'serve', 'preview')
        selected = preferred or next((key for key in order if key in scripts), None)
        if selected is None:
            return None
        source_path = Path(directory, path).expanduser().resolve()
        manager = str(package.get('packageManager', 'npm')).split('@', 1)[0]
        if manager not in ('npm', 'pnpm', 'yarn', 'bun'):
            manager = 'npm'
        return {'script': selected, 'command': redact(scripts[selected]),
                'invoke': manager + ' run ' + shlex.quote(selected), 'cwd': str(source_path.parent),
                'source_path': str(source_path), 'source_id': source_id,
                'selection': 'explicit user command' if preferred else 'candidate; choose using project instructions',
                'alternatives': {key:scripts[key] for key in ('dev','start','serve','preview') if key in scripts and key != selected}}
    return None


def legacy_launch(state):
    """Migrate already saved script facts instead of keeping a second formatter."""
    text = state.get('known', {}).get('package_scripts', '')
    if not isinstance(text, str):
        return None
    match = re.search(r'\{.*\}', text, re.S)
    if not match:
        return None
    try:
        scripts = json.loads(match[0])
    except ValueError:
        return None
    source = re.search(r'\[source ([a-f0-9]{32})\]', text)
    directory = state.get('directory', os.getcwd())
    return package_evidence('view_file', {'path': 'package.json'}, json.dumps({'scripts': scripts}),
                            directory, state.get('goal', ''), source[1] if source else 'legacy')


def render_evidence(state, limit):
    launch = state.get('launch') or legacy_launch(state)
    rows = []
    if state.get('attempted_launch'):
        rows.append('Actual attempted launch (preserve its chain unless correcting an observed error): ' + json.dumps(state['attempted_launch'],ensure_ascii=False))
    if launch:
        # Keep the selected fact whole. Old receipts must not displace its middle.
        rows.append('Observed package scripts — launch evidence: ' + json.dumps(launch, ensure_ascii=False))
    for fact in reversed(state.get('facts', [])[-3:]):
        if sum(len(row) + 1 for row in rows) + len(fact) <= limit:
            rows.append(fact)
    return '\n'.join(rows)
