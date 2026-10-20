"""Bounded, cached code-symbol context using the standard library on Termux."""

import ast
import fnmatch
import os
import re
import threading
import time
from pathlib import Path

_CACHE = {}
_LOCK = threading.RLock()
SKIP = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build",
        "logs", "conversations", "artifacts", "site-packages", "vendor"}
EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".rb", ".php"}
CODE_REQUEST = re.compile(r"\b(code|function|class|bug|debug|fix|edit|implement|refactor|module|import|test|api|repository|repo|workflow|pipeline)\b|\.(?:py|tsx?|jsx?|go|rs)\b", re.I)


def invalidate(path):
    real = os.path.realpath(path)
    with _LOCK:
        _CACHE.pop(real, None)


def _patterns(root):
    patterns = []
    for name in (".gitignore", ".galamignore"):
        try:
            patterns.extend(line.strip() for line in (root / name).read_text().splitlines()
                            if line.strip() and not line.startswith("#"))
        except OSError:
            pass
    return patterns


def _ignored(relative, patterns):
    ignored = False
    for entry in patterns:
        negate = entry.startswith("!")
        pattern = entry.lstrip("!").lstrip("/").rstrip("/")
        if not pattern:
            continue
        matches = (fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern + "/*")
                   or ("/" not in pattern and any(fnmatch.fnmatch(part, pattern) for part in relative.split("/"))))
        if matches:
            ignored = not negate
    return ignored


def _symbols(path):
    key = str(path.resolve())
    info = path.stat()
    if info.st_size > 256000:
        return []
    stamp = (info.st_mtime_ns, info.st_size)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    found = []
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    found.append((node.lineno, lines[node.lineno - 1].strip()[:180]))
        except SyntaxError:
            pass
    else:
        pattern = re.compile(r"\b(?:function|class|interface|type|func|fn|struct|def|trait)\s+\w|(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s*)?\(")
        found = [(n, line.strip()[:180]) for n, line in enumerate(lines, 1) if pattern.search(line)]
    found = sorted(found)[:100]
    with _LOCK:
        if len(_CACHE) > 1000:
            _CACHE.clear()
        _CACHE[key] = (stamp, found)
    return found


def repository_context(directory, query, char_budget=2400):
    if not CODE_REQUEST.search(query):
        return ""
    root = Path(directory).resolve()
    # Never recursively map a whole device as an accidental project root.
    if str(root) in ("/", "/sdcard", "/mnt/sdcard", "/storage", "/data"):
        return ""
    tokens = set(re.findall(r"[a-zA-Z_][\w.-]{2,}", query.lower()))
    patterns = _patterns(root)
    candidates = []
    started = time.monotonic()
    scanned = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        depth = len(Path(base).relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith(".")
                         and not os.path.islink(os.path.join(base, d))
                         and not _ignored(str((Path(base) / d).relative_to(root)), patterns)) if depth < 5 else []
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if path.suffix not in EXTENSIONS or name.startswith(".") or path.is_symlink() or _ignored(relative, patterns):
                continue
            scanned += 1
            score = sum(4 for token in tokens if token in relative.lower())
            candidates.append((score, relative, path))
        if scanned >= 300 or time.monotonic() - started > 0.15:
            break
    chunks = []
    remaining = max(0, char_budget - 100)
    for score, relative, path in sorted(candidates, key=lambda item: (-item[0], item[1]))[:24]:
        try:
            symbols = _symbols(path)
        except (OSError, ValueError):
            continue
        if not symbols:
            continue
        symbols.sort(key=lambda item: (-sum(token in item[1].lower() for token in tokens), item[0]))
        chosen = sorted(symbols[:5])
        block = relative + "\n" + "\n".join(f"  L{n}: {line}" for n, line in chosen) + "\n"
        if len(block) > remaining:
            continue
        chunks.append(block)
        remaining -= len(block)
        if remaining < 160:
            break
    return "CODE OUTLINE (bounded symbols; inspect exact files before editing):\n" + "".join(chunks) if chunks else ""
