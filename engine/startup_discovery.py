"""Read-only project metadata for a startup decision, never an automatic launcher."""
import json
from pathlib import Path
import re
import tempfile
import os
from engine.security import redact, sensitive_path
from engine.result_store import archive_context


def discover(request, current_directory):
    from engine.server_commands import server_start_requested
    if not server_start_requested(request):
        return None
    matches=re.findall(r'''["']([^"']*(?:/|\\)[^"']*)["']|(?<![\w:/])((?:/(?!/)|~/)[^\s`?,;]+)''',request)
    paths=[a or b for a,b in matches if (a or b).startswith(('/','~/'))]
    target=Path(current_directory)
    if paths:
        candidate=Path(paths[-1]).expanduser()
        if not candidate.is_dir():
            candidate=Path(paths[-1].rstrip('.!')).expanduser()
        if not candidate.is_dir():
            return {'directory':None,'content':f'Explicit project directory does not exist: {candidate}. Resolve this before launching.'}
        target=candidate
    target=target.resolve()
    if not target.is_dir():
        return None
    content=f'Project directory: {target}\nRuntime temporary directory: {tempfile.gettempdir()} (do not assume /tmp is writable).\n'
    package=target/'package.json'
    result={'directory':str(target)}
    try:
        # Metadata only. Exact source inspection is still required before edits.
        if not sensitive_path(package) and package.stat().st_size <= 256000:
            parsed=json.loads(package.read_text())
            scripts=parsed.get('scripts',{})
            package_data={'packageManager':parsed.get('packageManager'),'scripts':scripts}
            original=redact(json.dumps(package_data,ensure_ascii=False))
            reference=archive_context(original)
            common={k:v for k,v in scripts.items() if k in ('dev','start','serve','preview','build')}
            content+=f'Manifest: {package}; original action {reference}\n'
            content+=redact(json.dumps({'packageManager':parsed.get('packageManager'),'scripts':common,'available_scripts':list(scripts)},ensure_ascii=False))+'\n'
            from engine.server_commands import configured_port
            manager = str(parsed.get('packageManager') or 'npm').split('@',1)[0]
            if manager not in ('npm','pnpm','yarn','bun'):
                manager = 'npm'
            ports = {configured_port(f'{manager} run {name}',scripts,{'PORT':os.environ.get('PORT','')})
                     for name in common if name != 'build'} - {None}
            if ports:
                content += ('Observed configured local address hints: ' + ', '.join(f'http://127.0.0.1:{port}/' for port in sorted(ports))
                            + '. Derived from project scripts/current PORT, not proof of readiness. Preserve these unless the user requests an override or an observed conflict requires another address.\n')
            result.update(package=original,source_id=reference,path=str(package))
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    names=sorted(p.name for p in target.iterdir() if p.is_file() and p.name in (
        'pyproject.toml','requirements.txt','README.md','Makefile','Procfile','Dockerfile',
        'server.py','app.py','main.py','manage.py','package-lock.json','pnpm-lock.yaml','yarn.lock','bun.lock','bun.lockb'))
    content+='Entrypoint/documentation candidates: '+', '.join(names)+'\n'
    content+=('Choose the actual project startup command using this evidence. Preserve its build/start chain; '
              'do not infer the serving architecture from a framework dependency. Inspect only unresolved prerequisites. '
              'Use execute_bash(background=true) for a service; explicit readiness_url is supported. '
              'This manifest summary is already observed: it is enough to propose reviewed startup. '
              'Do not reread unchanged metadata or inspect assistant runtime state without a specific unresolved prerequisite. '
              'For custom entrypoints inspect relevant instructions. This is metadata, not execution or edit inspection.')
    result['content']=content
    return result
