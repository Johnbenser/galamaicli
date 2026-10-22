"""Verify an existing local project's served bytes without adopting its process."""
from pathlib import Path
from html.parser import HTMLParser
from engine.cancellation import TaskCancelled
import re
import urllib.error
import urllib.parse
import urllib.request


def reuse_allowed(goal):
    from engine.conversation_constraints import prose_mask
    from engine.completion import explicit_report_command
    if explicit_report_command(str(goal)):
        return False
    text = prose_mask(str(goal))
    return not re.search(r'\b(?:restart|rebuild|build|compile|install|deploy|fresh|second|another)\b|\bnew\s+(?:server|instance)|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?\w+', text, re.I) and not re.search(r'`[^`]+`', str(goal))


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        reference = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' and attrs.get('rel') in ('stylesheet','modulepreload') else None
        if reference:
            self.references.append(reference)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def observe_existing(url, directory, entrypoints=()):
    """Return exact public-file matches; HTTP 200 alone is insufficient evidence."""
    from engine.process_runner import local_url
    from engine.security import sensitive_path
    url = local_url(url)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or parsed.path not in ('', '/', '/index.html'):
        return None
    root = Path(directory).resolve()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    limit = 4_000_000

    def read_public(path):
        path = path.resolve()
        if not path.is_relative_to(root) or sensitive_path(path) or not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_bytes()

    def fetch(address):
        if urllib.parse.urlsplit(address).netloc != parsed.netloc:
            return None
        from engine.cancellation import check
        check()
        with opener.open(address, timeout=2) as response:
            return response.read(limit+1) if response.status == 200 else None

    try:
        body = fetch(url)
        if not body or len(body) > limit:
            return None
        candidates = [root/'index.html'] + [root/name/'index.html' for name in ('dist','build','public','out')]
        candidates += [Path(path) for path in entrypoints if isinstance(path,(str,Path)) and path and Path(path).name == 'index.html']
        for path in dict.fromkeys(candidates):
            folder = path.parent
            content = read_public(path)
            if content != body:
                continue
            matched = [str(path.relative_to(root))]
            # Verify entrypoint scripts/styles locally, without another AI turn.
            assets = _Assets()
            assets.feed(body.decode('utf-8','replace'))
            for reference in dict.fromkeys(assets.references):
                target = urllib.parse.urlsplit(reference)
                if target.scheme or target.netloc or not target.path.lower().endswith(('.js','.css','.mjs')):
                    continue
                asset = folder/urllib.parse.unquote(target.path.lstrip('/'))
                expected = read_public(asset)
                if expected is None:
                    return None
                address = urllib.parse.urljoin(url, reference)
                if fetch(address) != expected:
                    return None
                matched.append(str(asset.relative_to(root)))
            return {'url':url, 'matched_files':matched, 'owned':False,
                    'existing_service':True, 'startup_satisfied':True, 'readiness':'ready'}
    except TaskCancelled:
        raise
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return None
