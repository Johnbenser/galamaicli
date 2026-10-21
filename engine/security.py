"""Local credential hygiene and a bounded, read-only project audit.

These checks are defense in depth, not a shell sandbox.
"""
import os
import re
import stat
import subprocess
import time
from pathlib import Path

_SECRET_NAME = re.compile(r'(?:API_?KEYS?|TOKEN|PASSWORD|SECRET|PRIVATE_KEY|ACCESS_KEY)', re.I)
_LITERAL = re.compile(r'(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b')


def redact(value):
    """Never include credential values in diagnostics or tool observations."""
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if _SECRET_NAME.search(str(k)) and isinstance(v, str)
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    for key, secret in os.environ.items():
        if _SECRET_NAME.search(key) and len(secret) >= 8:
            value = value.replace(secret, '[REDACTED]')
    value = _LITERAL.sub('[REDACTED]', value)
    value = re.sub(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                   '[REDACTED PRIVATE KEY]', value, flags=re.S)
    value = re.sub(r'(?i)(authorization\s*[:=]\s*["\']?(?:bearer|basic)\s+)[^\s"\']+', r'\1[REDACTED]', value)
    value = re.sub(r'(https?://)[^/\s:@]+:[^/\s@]+@', r'\1[REDACTED]@', value)
    return value


def public_environment(env):
    """Build/test commands do not inherit provider credentials by default."""
    return {name: value for name, value in env.items() if not _SECRET_NAME.search(name)}


def sensitive_path(path):
    path = Path(path).expanduser().resolve()
    name = path.name.strip().lower()
    return (name in {'.secrets', '.galam_keys', '.galam_keys.json', 'keys.json',
                     'id_rsa', 'id_ed25519', 'id_ecdsa', '.netrc', '.npmrc', '.pypirc'}
            or name == '.env' or name.startswith('.env.') and not name.endswith(('example', 'sample', 'template'))
            or '.aws' in path.parts and name in {'credentials', 'config'}
            or 'proc' in path.parts and name in {'environ', 'mem'})


def review_reason(tool, args):
    """Critical actions require review even in ALWAYS-PROCEED mode."""
    if tool in {'write_file', 'replace_file_content', 'make_directory'}:
        from config import AGENT_STATE
        root = Path(AGENT_STATE.get('working_directory') or os.getcwd()).resolve()
        path = Path(root, str(args.get('path', '.'))).expanduser().resolve()
        if root != path and root not in path.parents or '.git' in path.parts:
            return 'Change outside the current workspace or inside Git metadata'
    if tool != 'execute_bash':
        return ''
    command = str(args.get('command', ''))
    if re.search(r'(?i)(?:\.secrets\b|\.env\b|\.ssh\b|\.aws\b|keys\.json|\b(?:printenv|env|set|export)\s*(?:$|[;|])|\$(?:\{)?\w*(?:KEY|TOKEN|PASSWORD|SECRET))', command):
        return 'Credential or environment access'
    if re.search(r'(?i)(?:\b(?:rm|rmdir|shred|mkfs(?:\.\w+)?|dd|sudo|su|pkill|killall|fuser)\b|\bgit\s+(?:clean|reset|push)\b|\bchmod\b.*777|\b(?:curl|wget)\b.*\|.*\b(?:sh|bash)\b)', command):
        return 'Destructive, privileged or remote execution'
    return ''


def audit_project(directory):
    """Report names and risk categories only; never return matched secret text."""
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Audit directory does not exist')
    findings = []
    scanned = 0
    skipped = 0
    visited = 0
    started = time.monotonic()
    try:
        process = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, timeout=5)
        tracked = set(process.stdout.decode('utf-8', 'replace').split('\0')) if process.returncode == 0 else set()
    except (OSError, subprocess.TimeoutExpired):
        tracked = set()
    excluded = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'logs', 'conversations'}
    for parent, dirs, files in os.walk(root, followlinks=False):
        if time.monotonic() - started > 10:
            return '\n'.join(['Local security audit (partial; time limit reached).', *findings,
                              f'Scanned {scanned} files. No changes were made.'])
        dirs[:] = sorted(d for d in dirs if d not in excluded and not Path(parent, d).is_symlink())
        for name in sorted(files):
            visited += 1
            path = Path(parent, name)
            if path.is_symlink():
                skipped += 1
                continue
            if scanned >= 500 or visited > 1000 or len(findings) >= 80:
                return '\n'.join(['Local security audit (bounded; no changes).', *findings,
                    f'Scanned {scanned} files; scan limit reached. This is not a complete security assessment.'])
            relative = str(path.relative_to(root))
            try:
                info = path.stat()
                if sensitive_path(path):
                    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                        findings.append(f'Credential permissions: {relative} is accessible to other OS users (where filesystem permissions apply).')
                    if relative in tracked:
                        findings.append(f'Tracked credential file: {relative}; review and rotate exposed credentials.')
                    scanned += 1
                    continue
                if info.st_size > 200_000:
                    skipped += 1
                    continue
                text = path.read_text(encoding='utf-8')
                scanned += 1
                if _LITERAL.search(text):
                    findings.append(f'Possible embedded credential: {relative}; inspect locally, never paste the value.')
                if re.search(r'verify\s*=\s*False|_create_unverified_context\s*\(', text):
                    findings.append(f'TLS verification bypass: {relative}.')
                if re.search(r'(?:curl|wget)\s+[^\n]+\|\s*(?:ba)?sh\b', text):
                    findings.append(f'Remote script execution: {relative}; review the source before running.')
            except (OSError, UnicodeError):
                skipped += 1
    return '\n'.join(['Local security audit (read-only; no changes).', *findings,
        'No findings from these limited checks.' if not findings else 'Review findings locally; detection is heuristic.',
        f'Scanned {scanned} files; skipped {skipped}. Dependencies, running services and full Git history were not audited.'])
