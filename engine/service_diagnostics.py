"""Small read-only HTTP evidence before diagnosing local page/navigation failures."""
from pathlib import Path
from html.parser import HTMLParser
import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request


def requested(text):
    return bool(re.search(r'\b(?:server|website|site|page|pages|nav|navigation|links?)\b', text, re.I)
                and re.search(r"\b(?:cannot|can't|cant|csnt|unable|not working|not load|doesnt|doesn't|error|problem|broken|check|diagnose)\b", text, re.I))


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pages, self.assets = [], []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'a' and attrs.get('href'):
            self.pages.append(attrs['href'])
        if tag == 'script' and attrs.get('src'):
            self.assets.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') in ('stylesheet', 'modulepreload') and attrs.get('href'):
            self.assets.append(attrs['href'])


def diagnose(request, directory, tasks=()):
    if not requested(request):
        return None
    from engine.process_runner import local_url
    from engine.service_observation import _NoRedirect
    from engine.security import sensitive_path, redact
    from engine.cancellation import check
    root = Path(directory).resolve()
    explicit = re.findall(r'https?://[^\s`<>"\']+', request)
    urls = explicit or [task.get('readiness_url') for task in tasks
                       if task.get('readiness_url') and Path(task.get('cwd') or directory).resolve() == root]
    urls = list(dict.fromkeys(urls))
    if not urls:
        import json
        from engine.server_commands import configured_port
        try:
            package = root/'package.json'
            scripts = json.loads(package.read_text()).get('scripts', {}) if package.stat().st_size < 256000 else {}
            ports = {configured_port('npm run ' + name, scripts, {})
                     for name in ('dev', 'start', 'serve', 'preview') if name in scripts} - {None}
            if len(ports) == 1:
                urls = ['http://127.0.0.1:' + next(iter(ports)) + '/']
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if len(urls) != 1:
        return 'No unique current local service address was identified. Ask for its URL; do not invent a port or restart a server.'
    try:
        url = local_url(urls[0].rstrip('.,;'))
    except ValueError:
        return 'The supplied URL is outside local-service diagnostics. Ask for the intended local address.'
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https'):
        return 'Browser-page diagnosis needs the local HTTP(S) address, not just a TCP listener.'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    def fetch(address):
        check()
        try:
            with opener.open(address, timeout=2) as response:
                return response.status, response.headers.get('Content-Type', ''), response.read(1_000_001)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get('Content-Type', ''), b''
        except (OSError, ValueError):
            return None, 'unreachable', b''
    status, media, body = fetch(url)
    lines = [f'Local service diagnosis for {url}; project {root}.', f'GET {parsed.path or "/"}: {status or "unreachable"} {media}']
    if status != 200:
        return '\n'.join(lines + ['Diagnose this observed response before reading unrelated frontend source. No process was started or stopped.'])
    links = Links()
    links.feed(body.decode('utf-8', 'replace'))
    sources = [root/'src/App.jsx', root/'src/App.tsx']
    for folder in (root/'src/pages', root/'src/components'):
        if folder.is_dir():
            sources.extend(path for path in sorted(folder.iterdir())
                           if path.is_file() and re.search(r'index|home|nav|layout', path.stem, re.I))
    inspected = []
    for source in sources[:5]:
        try:
            if source.is_file() and not sensitive_path(source):
                with source.open(errors='replace') as handle:
                    text = handle.read(1_000_000)
                links.pages.extend(match[1] for match in re.findall(r'href\s*[:=]\s*(["\'])([^"\']+)\1', text))
                inspected.append(str(source.relative_to(root)))
        except OSError:
            pass
    if not links.pages:
        links.pages = [path.name for path in sorted(root.glob('*.html')) if path.name != 'index.html']
    def public_address(reference):
        target = urllib.parse.urlsplit(reference)
        if target.scheme or target.netloc or target.query or reference.startswith('#'):
            return None
        address = urllib.parse.urljoin(url, target.path)
        return address if urllib.parse.urlsplit(address).netloc == parsed.netloc else None
    for reference in list(dict.fromkeys(links.pages))[:10]:
        address = public_address(reference)
        if not address or Path(urllib.parse.urlsplit(address).path).suffix not in ('', '.html', '.htm'):
            continue
        code, kind, _ = fetch(address)
        lines.append(f'Page GET {urllib.parse.urlsplit(address).path}: {code or "unreachable"} {kind}')
    for reference in list(dict.fromkeys(links.assets))[:5]:
        # Cache-busting query strings are harmless for the local asset check.
        reference = reference.split('?', 1)[0]
        address = public_address(reference)
        if not address:
            continue
        code, kind, data = fetch(address)
        local = root / urllib.parse.unquote(urllib.parse.urlsplit(address).path.lstrip('/'))
        match = None
        try:
            if local.resolve().is_relative_to(root) and not sensitive_path(local) and local.is_file() and local.stat().st_size <= 1_000_000:
                match = hashlib.sha256(local.read_bytes()).digest() == hashlib.sha256(data).digest()
        except OSError:
            pass
        lines.append(f'Asset GET {urllib.parse.urlsplit(address).path}: {code or "unreachable"}; local bytes match={match}; {kind}')
    if inspected:
        lines.append('Navigation source candidates: ' + ', '.join(inspected))
    lines.append('HTTP success does not verify browser clicks or JavaScript execution. Use failing route/asset evidence first; if all pass, inspect the identified navigation source/browser errors instead of repeatedly reading whole documents. No process or project file was changed.')
    return redact('\n'.join(lines))
