"""Retrieve small command references from live project metadata; never execute examples."""
from dataclasses import dataclass, field
from pathlib import Path
import json
import re
import shlex


@dataclass
class Assessment:
    allowed: bool = True
    reason: str = ''
    evidence: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)


def segments(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        groups, group = [], []
        for token in lexer:
            if token in (';', '&&', '||', '|', '&', '\n'):
                if group:
                    groups.append(group)
                    group = []
            else:
                group.append(token)
        if group:
            groups.append(group)
        return groups
    except ValueError:
        return []


def assess(command, directory, request=''):
    from engine.security import sensitive_path
    from engine.path_discovery import directory_request
    from engine.execution_policy import is_safe_read
    result = Assessment()
    simple = directory_request(request) if request else None
    if simple and not is_safe_read('execute_bash', {'command': command}):
        return Assessment(False, 'This request only navigates or lists a folder. Use cd/ls or ask_user to resolve its path.')
    cwd = Path(directory).resolve()
    # Opaque shell programs remain subject to the existing shell policy. Never
    # interpret their quoted contents as additional commands or verified facts.
    if any(char in command for char in ('$', '`', '\n', '<', '>', '|', '(', ')')):
        return result
    for position, words in enumerate(segments(command)):
        if not words:
            continue
        executable = words[0]
        if executable == 'cd':
            operands = [word for word in words[1:] if word != '--']
            if len(operands) != 1:
                continue
            raw = Path(operands[0]).expanduser()
            target = raw if raw.is_absolute() else cwd / raw
            if not target.is_dir() and not raw.is_absolute() and len(raw.parts) > 1:
                target = Path('/') / raw
            if not target.is_dir() and raw.is_absolute() and position == 0:
                target = cwd / str(raw).lstrip('/')
            if not target.is_dir():
                return Assessment(False, f'Directory is missing or ambiguous: {operands[0]}. Inspect or ask_user; do not create it for navigation.')
            cwd = target.resolve()
            result.evidence.append('Verified directory: ' + str(cwd))
        elif executable in ('npm', 'pnpm', 'yarn', 'bun'):
            # Commands changing their own working directory need their own
            # parser. Abstain rather than checking against the wrong manifest.
            if any(word in ('--prefix', '--cwd', '-C', '--dir', '-w', '--workspace', '--workspaces')
                   or word.startswith(('--prefix=', '--cwd=', '--workspace=')) for word in words[1:]):
                continue
            script = words[2] if len(words) > 2 and words[1] in ('run', 'run-script') else (
                words[1] if len(words) > 1 and words[1] in ('dev', 'start', 'serve', 'preview', 'build', 'test') else None)
            if not script:
                continue
            manifest = cwd / 'package.json'
            if not manifest.is_file():
                manifest = next((parent / 'package.json' for parent in cwd.parents
                                 if (parent / 'package.json').is_file()), manifest)
            if not manifest.is_file():
                return Assessment(False, f'package.json does not exist in {cwd} or its parents. Inspect the project before proposing a package script.')
            try:
                if sensitive_path(manifest) or manifest.stat().st_size > 256000:
                    continue
                data = json.loads(manifest.read_text())
                scripts = data.get('scripts', {})
                if not isinstance(scripts, dict):
                    continue
            except (OSError, ValueError, AttributeError):
                continue
            # npm start/test can use documented implicit behavior; let the
            # runtime diagnose it if no explicit matching script is present.
            implicit = words[1] in ('start', 'test') and executable == 'npm'
            if script not in scripts and not implicit:
                available = ', '.join(str(name) for name in scripts)[:360]
                return Assessment(False, f'{manifest} has no {script!r} script. Available: {available or "none"}.',
                                  [str(manifest)], [f'{executable} run {name}' for name in scripts
                                                   if re.fullmatch(r'[\w:-]+', str(name))][:3])
            if script in scripts:
                result.evidence.append(f'{manifest}: script {script!r} exists (its execution is still unverified).')
        elif re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', Path(executable).name):
            operands = words[1:]
            while operands and operands[0] in ('-u', '-B', '-E', '-s', '-S', '-I', '-O', '-OO', '-q', '-v'):
                operands = operands[1:]
            if operands and not operands[0].startswith('-') and operands[0].endswith('.py'):
                path = Path(operands[0])
                path = path if path.is_absolute() else cwd / path
                if not path.is_file():
                    return Assessment(False, f'Python entry file does not exist: {path}')
                result.evidence.append('Verified Python entry file: ' + str(path))
        elif Path(executable).name in ('node', 'nodejs'):
            if len(words) > 1 and not words[1].startswith('-') and words[1].endswith(('.js', '.cjs', '.mjs')):
                path = Path(words[1])
                path = path if path.is_absolute() else cwd / path
                if not path.is_file():
                    return Assessment(False, f'Node entry file does not exist: {path}')
                result.evidence.append('Verified Node entry file: ' + str(path))
    return result


def feedback(assessment):
    from engine.security import redact
    from engine.transparency import safe_display
    return redact(safe_display(assessment.reason + (' Suggested declared commands: ' + '; '.join(assessment.suggestions)
                                if assessment.suggestions else '')))
