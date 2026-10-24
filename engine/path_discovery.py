"""Current-filesystem navigation facts. No conversation search or fuzzy selection."""
from pathlib import Path
import os
import re
import shlex
import time


def directory_request(text):
    text = re.sub(r'^\s*(?:(?:please|kindly)\s+)+', '', text, flags=re.I).strip().rstrip('.!')
    nav = re.fullmatch(r'(?:go|navigate|cd)\s+(?:to\s+)?(?:my\s+|the\s+)?(.+)', text, re.I)
    listing = re.fullmatch(r'(?:display|show|list)\s+(?:(?:me|my|the|all)\s+)*(?:folders?|directories|files?|contents?)(?:\s+(?:in|at|of)\s+(.+)|\s+(here|there))?', text, re.I)
    if not nav and not listing:
        return None
    target = (nav[1] if nav else listing[1] or '').strip().strip('`\'"')
    if re.search(r'\b(?:then|and)\b|[;&|\n]', target):
        return None
    return {'operation': 'navigate' if nav else 'list', 'target': target,
            'here': bool(listing and listing[2] == 'here')}


def candidates(target, directory, kind='directory', seconds=1.0, max_entries=1200):
    """Exact matches only, near the supplied parent or the current workspace."""
    from engine.security import sensitive_path
    if not str(target).strip():
        return []  # An unnamed request cannot be disambiguated from filesystem contents.
    raw = Path(os.path.expanduser(str(target).strip().strip('`\'"')))
    cwd = Path(directory).absolute()
    expected = raw if raw.is_absolute() else cwd / raw
    def suitable(path):
        try:
            return not sensitive_path(path) and (path.is_file() if kind == 'file' else path.is_dir())
        except OSError:
            return False
    if suitable(expected):
        return [str(expected.resolve())]
    # Recover a missing slash only if the root component and complete path exist.
    if not raw.is_absolute() and len(raw.parts) > 1:
        rooted = Path('/') / raw
        if suitable(rooted):
            return [str(rooted.resolve())]
    if raw.is_absolute() or len(raw.parts) > 1:
        return []  # Do not substitute another parent for an explicitly supplied path.
    roots = list(dict.fromkeys([cwd, cwd.parent]))
    found, visited = set(), set()
    deadline, inspected = time.monotonic() + seconds, 0
    for root in roots:
        try:
            with os.scandir(root) as entries:
                children = []
                for entry in entries:
                    inspected += 1
                    if inspected > max_entries or time.monotonic() > deadline:
                        return []  # Incomplete discovery cannot prove uniqueness.
                    path = Path(entry.path)
                    if entry.name == raw.name and suitable(path):
                        found.add(str(path.resolve()))
                    if (entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.')
                            and entry.name not in ('node_modules', 'venv', '__pycache__', 'Android')
                            and path.resolve() not in visited):
                        visited.add(path.resolve())
                        children.append(path)
            for child in children:
                path = child / raw.name
                if suitable(path):
                    found.add(str(path.resolve()))
        except OSError:
            continue
    return sorted(found)


def command_for(operation, path):
    return ('cd -- ' + shlex.quote(path) if operation == 'navigate'
            else 'ls -la -- ' + shlex.quote(path))


def listing_scope(request):
    text = re.sub(r'(?<![\w:])(?:/|~/)[^\s`"\'<>;,]+', ' PATH ', request)
    if (not re.search(r'\b(?:list|show|display)\b', text, re.I)
            or not re.search(r'\b(?:folders?|directories|files?|contents?)\b', text, re.I)
            or re.search(r'\b(?:create|make|write|edit|modify|delete|remove|build|install|start|stop|run|execute)\b', text, re.I)):
        return None
    files = bool(re.search(r'\bfiles?\b', text, re.I))
    folders = bool(re.search(r'\b(?:folders?|directories)\b', text, re.I))
    kind = 'f' if files and not folders else 'd' if folders and not files else None
    return {'recursive': bool(re.search(r'\b(?:sub[ -]?(?:folders?|directories)|recursive(?:ly)?|nested|all levels)\b', text, re.I)),
            'kind': kind}


def prepare_listing(request, command, directory):
    """Match simple model listings to the user's requested depth and real root."""
    scope = listing_scope(request)
    if not scope or any(mark in command for mark in (';', '|', '&', '$', '`', '\n', '>', '<')):
        return command
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if not words or words[0] != 'ls' or any(word in ('-d', '--directory') or word.startswith('-') and not word.startswith('--') and 'd' in word[1:] for word in words[1:]):
        return command
    targets = [word for word in words[1:] if not word.startswith('-')]
    if len(targets) > 1:
        return command
    root = Path(directory, targets[0] if targets else '.').expanduser().resolve()
    if not root.is_dir():
        return command
    explicit = re.findall(r'(?<![\w:])(?:/|~/)[^\s`"\'<>;,]+', request)
    if explicit and Path(explicit[-1].rstrip('.,!?')).expanduser().resolve() != root:
        return command
    if scope['recursive']:
        return 'find ' + shlex.quote(str(root)) + (' -type ' + scope['kind'] if scope['kind'] else '')
    if targets and Path(directory, targets[0]).is_symlink():
        return 'ls -la -- ' + shlex.quote(str(root))
    return command


def initial_action(request, directory, explicit_directory=False):
    plan = directory_request(request)
    if not plan:
        return None, None
    target = plan['target']
    if not target and (explicit_directory or plan['here']):
        paths = [str(Path(directory).resolve())]
    else:
        paths = candidates(target, directory)
    if len(paths) == 1:
        return plan, ('execute_bash', {'command': command_for(plan['operation'], paths[0])})
    question = ('Which folder should I use? Enter its path.' if not target else
                f'I could not identify one exact folder for {target!r}. Which path should I use?')
    return plan, ('ask_user', {'question': question, 'target': target,
                              'kind': 'directory', 'choices': '\n'.join(paths[:5])})
