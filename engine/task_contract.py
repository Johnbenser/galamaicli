"""Verify explicitly named filesystem outputs; never synthesize mutations from prose."""
import os
from pathlib import Path
import re

from engine.action_results import request_intent, requires_content

_TOKEN = re.compile(r'''`([^`]+)`|"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|([^\s,;]+|[,;])''')
_CREATE = {'create', 'make', 'write', 'touch', 'build', 'generate'}
_FILL = {'a', 'an', 'the', 'new', 'empty', 'blank', 'named', 'called', 'sibling', 'dedicated'}
_PREP = {'on', 'in', 'at', 'under', 'inside', 'to'}
_WORDS = _CREATE | _FILL | _PREP | {'file', 'files', 'text', 'folder', 'folders', 'directory', 'directories',
          'and', 'then', 'also', 'containing', 'with', 'content', 'contents', 'for', 'me', 'please', 'kindly',
          'complete', 'both', 'actions', 'it', 'save', 'as'}


class TaskContract:
    def __init__(self, request, directory):
        self.requirements = []
        self.complete_scope = False
        if not request_intent(request)[0]:
            return
        tokens = []
        boundaries = set()
        for match in _TOKEN.finditer(request):
            value = next(v for v in match.groups() if v is not None)
            quoted = match.group(4) is None
            if not quoted and value in (',', ';'):
                if tokens:
                    boundaries.add(len(tokens)-1)
                continue
            tokens.append((value if quoted else value.rstrip('.,;'), quoted))
            if not quoted and value.endswith(('.', ',', ';')):
                boundaries.add(len(tokens)-1)
        if not any(t.lower() in _CREATE for t, quoted in tokens if not quoted):
            return
        bases = []
        for i, (value, quoted) in enumerate(tokens):
            if i and tokens[i - 1][0].lower() in _PREP and value.startswith(('/', '~/')):
                path = Path(value).expanduser()
                if path.is_dir() or not path.suffix:
                    bases.append(path)
        base = bases[0] if bases and all(p == bases[0] for p in bases) else Path(directory)
        self.base = base.resolve()
        clauses, current = [], []
        negated = False
        def append_clause():
            nonlocal current, negated
            if not current:
                return
            prose = ' '.join(value for value, quoted in current if not quoted)
            negated = negated or bool(re.search(r"\b(?:do not|don't|never)\b", prose, re.I))
            clauses.append((current, negated))
            current = []
        for position, (value, quoted) in enumerate(tokens):
            if not quoted and value.lower() in ('and', 'then', 'also', 'but'):
                append_clause()
                if value.lower() == 'but':
                    negated = False
            else:
                current.append((value, quoted))
            if position in boundaries and current:
                append_clause()
                negated = False
        append_clause()
        known_values = {value for value, quoted in tokens if quoted or value.startswith(('/', '~/'))}
        for clause, negated in clauses:
            if negated:
                continue
            lower = [v.lower() for v, _ in clause]
            kind, target = None, None
            # Only the object of a creation verb (or an immediately coordinated
            # noun) declares an output. Later mentions such as "current directory
            # as ..." and "use file actions" describe context, not new paths.
            verb = next((i for i, (v, quoted) in enumerate(clause)
                         if not quoted and v.lower() in _CREATE), None)
            index = verb + 1 if verb is not None else 0
            while index < len(clause) and not clause[index][1] and lower[index] in _FILL | {'its', 'my'}:
                index += 1
            if index < len(clause) and verb is not None and Path(clause[index][0]).suffix:
                kind, target = 'file', clause[index][0]
            else:
                while index < len(clause) and lower[index] in {'html', 'html5', 'css', 'python', 'javascript', 'text'}:
                    index += 1
                if index < len(clause):
                    noun = lower[index]
                    if noun in ('folder', 'directory'):
                        kind = 'directory'
                    elif noun == 'file':
                        kind = 'file'
                    index += 1
            if not kind:
                continue
            if target is None:
                while index < len(clause) and lower[index] in _FILL:
                    index += 1
                if index < len(clause) and lower[index] == 'for' and index + 1 < len(clause) and clause[index + 1][1]:
                    index += 1
                if index < len(clause) and (clause[index][1] or any(word in ('named', 'called') for word in lower[:index]) or lower[index] not in _PREP | {'for', 'containing', 'with', 'content', 'about', 'that', 'which', 'structure', 'tree', 'not', 'it', 'them', 'this', 'these', 'those', 'one', 'ones'}):
                    target = clause[index][0]
            if target:
                known_values.add(target)
                path = Path(target).expanduser()
                path = path if path.is_absolute() else self.base / path
                path = path.resolve()
            else:
                path = None
            literal = None
            for i, value in enumerate(lower):
                if value in ('containing', 'with'):
                    j = i + 1
                    while j < len(clause) and lower[j] in ('text', 'content', 'contents'):
                        j += 1
                    if j < len(clause) and clause[j][1]:
                        literal = clause[j][0]
            self.requirements.append({'kind': kind, 'path': path, 'literal': literal,
                                      'nonempty': kind == 'file' and requires_content(request)})
        self.complete_scope = bool(self.requirements and all(r['path'] for r in self.requirements)
                                   and all(quoted or value in known_values or value.lower() in _WORDS for value, quoted in tokens))

    def _satisfied(self, requirement):
        path = requirement['path']
        if path is None:
            return False  # Unnamed outputs remain model-planned; never falsely fast-complete them.
        if requirement['kind'] == 'directory':
            return path.is_dir()
        if not path.is_file():
            return False
        try:
            content = path.read_text(encoding='utf-8')
            literal = requirement['literal']
            return (content in (literal, literal + '\n') if literal is not None
                    else bool(content.strip()) if requirement['nonempty'] else True)
        except (OSError, UnicodeError):
            return False

    def missing(self):
        return [f"{r['kind']}: {r['path']}" for r in self.requirements if r['path'] and not self._satisfied(r)]

    def guard(self, tool, args, directory):
        if tool not in ('write_file', 'replace_file_content', 'make_directory'):
            return None
        raw = Path(str(args.get('path', ''))).expanduser()
        path = (raw if raw.is_absolute() else Path(directory) / raw).resolve()
        for requirement in self.requirements:
            if requirement['path'] != path:
                continue
            if requirement['kind'] == 'directory' and tool != 'make_directory':
                return f"{path} was requested as a directory. Use make_directory; do not create a file at this path."
            if requirement['kind'] == 'file' and tool == 'make_directory':
                return f"{path} was requested as a file, not a directory."
            literal = requirement['literal']
            if requirement['kind'] == 'file' and literal is not None:
                if tool == 'write_file':
                    proposed = args.get('content', '')
                else:
                    try:
                        proposed = path.read_text().replace(args.get('target_content', ''), args.get('replacement_content', ''), 1)
                    except (OSError, UnicodeError):
                        return None
                if proposed not in (literal, literal + '\n'):
                    return f"The proposed change violates the user's explicit literal content for {path}. Preserve that requested content."
        return None

    def receipt(self, events, pending_checks):
        # Existing single-action receipts stay in completion.py. Compound receipts require full coverage.
        if not self.complete_scope or len(self.requirements) < 2 or pending_checks or not events:
            return None
        if not all(event['result'].success for event in events) or any(not self._satisfied(r) for r in self.requirements):
            return None
        return '\n'.join(f"Verified {r['kind']}: `{r['path']}`" for r in self.requirements)
