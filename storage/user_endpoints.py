"""Home-owned model connections. Keys are written only to the user's .secrets."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
from urllib.parse import urlsplit

from engine.result_store import atomic_bytes

FORMATS={'chat_completions':'chat/completions','responses':'responses','ollama':'api/chat'}


def endpoint_choices():
    from storage.endpoints import ENDPOINT_REGISTRY
    return sorted(ENDPOINT_REGISTRY,key=str.casefold)


def default_api_format(endpoint,model=''):
    """Local catalog preference; this never makes an inference request."""
    from collections import Counter
    from storage.endpoints import ENDPOINT_REGISTRY
    entry=ENDPOINT_REGISTRY.get(endpoint,{})
    models=entry.get('models',{})
    selected=models.get(model,{}).get('custom_connection',{}).get('api_format')
    if selected in FORMATS:return selected
    counts=Counter(info.get('custom_connection',{}).get('api_format',info.get('api_format','chat_completions'))
                   for info in models.values())
    counts={kind:count for kind,count in counts.items() if kind in FORMATS}
    return max(FORMATS,key=lambda kind:counts.get(kind,0))


def user_home():
    if os.environ.get('GALAM_HOME'):
        return Path(os.environ['GALAM_HOME']).expanduser().resolve()
    home=Path.home()
    termux=Path('/data/data/com.termux/files/home')
    if not (home/'.secrets').exists() and (termux/'.secrets').is_file():
        return termux
    return home


def profile_path():return user_home()/'.galam'/'endpoints.json'


def normalize_url(value,api_format):
    if api_format not in FORMATS:raise ValueError('Choose Chat Completions, Responses or Ollama Chat.')
    value=str(value).strip()
    if any(char.isspace() or ord(char)<32 for char in value):raise ValueError('URL cannot contain whitespace.')
    try:
        parsed=urlsplit(value)
        parsed.port  # Validate an explicitly supplied port without changing the URL.
    except ValueError:raise ValueError('Enter a valid HTTP(S) URL.') from None
    if parsed.scheme not in ('http','https') or not parsed.hostname:
        raise ValueError('URL must start with http:// or https://.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Keep credentials, queries and fragments out of the URL.')
    if parsed.path.rstrip('/').endswith(('/embeddings','/models','/messages')):
        raise ValueError('Enter a chat request URL, not models, embeddings or messages.')
    return value


def infer_api_format(url,endpoint='',model=''):
    path=urlsplit(str(url).strip()).path.rstrip('/')
    if path.endswith('/api/chat'):return 'ollama'
    if path.endswith(('/responses','/response')):return 'responses'
    if path.endswith('/chat/completions'):return 'chat_completions'
    return default_api_format(endpoint,model)


def suggested_url(endpoint):
    """Visible prefill only. Never use this to rewrite an entered URL."""
    from storage.endpoints import ENDPOINT_REGISTRY
    base=ENDPOINT_REGISTRY.get(endpoint,{}).get('base_url','')
    if not base.startswith(('http://','https://')) or '{' in base:return ''
    path=urlsplit(base).path.rstrip('/')
    if path.endswith(('/chat/completions','/responses','/api/chat')):return base
    return base.rstrip('/')+'/'+FORMATS[default_api_format(endpoint)]


def default_key_env(endpoint,url):
    slug=re.sub('[^A-Z0-9]+','_',str(endpoint).upper()).strip('_')[:36] or 'CUSTOM'
    host=urlsplit(url).netloc.lower()
    return 'GALAM_'+slug+'_'+hashlib.sha256((str(endpoint)+'\0'+host).encode()).hexdigest()[:8].upper()+'_API_KEY'


def validate_profile(data):
    endpoint=str(data.get('endpoint','')).strip()
    model=str(data.get('model','')).strip()
    if not endpoint or len(endpoint)>72 or any(not c.isprintable() for c in endpoint):
        raise ValueError('Endpoint name must contain 1–72 printable characters.')
    if not model or len(model)>240 or any(c.isspace() or not c.isprintable() for c in model):
        raise ValueError('Model ID must contain 1–240 characters without spaces.')
    api_format=data.get('api_format','default')
    mode=data.get('api_format_mode',api_format)
    if api_format=='default':api_format=infer_api_format(data.get('url',''),endpoint,model)
    if mode not in ('default',*FORMATS):raise ValueError('Choose Default, Chat Completions, Responses or Ollama Chat.')
    url=normalize_url(data.get('url',''),api_format)
    env=str(data.get('api_key_env','')).strip().removeprefix('$')
    if not env:
        from storage.endpoints import ENDPOINT_REGISTRY
        group=ENDPOINT_REGISTRY.get(endpoint,{})
        if urlsplit(group.get('base_url','')).hostname==urlsplit(url).hostname:
            env=group.get('api_key_env','')
        env=env or default_key_env(endpoint,url)
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}_API_(?:KEY|TOKEN)',env):
        raise ValueError('Key variable must be uppercase and end in _API_KEY or _API_TOKEN.')
    try:context=int(data.get('context_window',8192))
    except (TypeError,ValueError):raise ValueError('Context window must be an integer.') from None
    if not 2048<=context<=4000000:raise ValueError('Context window must be between 2,048 and 4,000,000 tokens.')
    return {'endpoint':endpoint,'model':model,'url':url,'api_format':api_format,'api_format_mode':mode,
            'api_key_env':env,'context_window':context}


def read_profiles():
    path=profile_path()
    if not path.exists():return []
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError('Custom endpoint store must be a regular file smaller than 1 MB.')
    try:
        data=json.loads(path.read_text())
        if not isinstance(data,dict) or not isinstance(data.get('models'),list):raise ValueError()
        return [validate_profile(row) for row in data['models']]
    except (ValueError,TypeError,AttributeError):
        raise ValueError('Custom endpoint store is invalid. Repair it before saving changes.') from None


def apply_profiles(registry,profiles):
    for profile in profiles:
        base=profile['url'].rsplit('/',1)[0]
        endpoint=registry.setdefault(profile['endpoint'],{'base_url':base,
                                                        'models':{},'user_defined':True})
        if endpoint.get('user_defined'):
            endpoint.setdefault('api_key_env',profile['api_key_env'])
        existing=endpoint.setdefault('models',{}).get(profile['model'],{})
        endpoint['models'][profile['model']]={**existing,'name':profile['model'],
            'display_name':profile['model'],'context_window':profile['context_window'],
            'supports_streaming':True,'custom_connection':copy.deepcopy(profile)}


def load_into(registry):
    try:
        profiles=read_profiles()
        apply_profiles(registry,profiles)
        # Saved model credentials take effect on restart even if an older value
        # remains exported in the parent shell. Never execute the secret file.
        path=user_home()/'.secrets'
        if path.is_file():
            text=path.read_text()
            for profile in profiles:
                value=_secret_value(text,profile['api_key_env'])
                if value:os.environ[profile['api_key_env']]=value
    except (ValueError,OSError):pass  # Built-in endpoints still work; editor reports malformed store.


def _regular_bytes(path):
    if path.is_symlink():raise ValueError('Refusing to replace a symlinked '+path.name+'.')
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError(path.name+' must be a regular file smaller than 1 MB.')
    return path.read_bytes()


def _secret_value(text,name):
    value=None
    for line in text.splitlines():
        match=re.match(r'^\s*(?:export\s+)?'+re.escape(name)+r'\s*=\s*(.*)$',line)
        if match:
            try:parts=shlex.split(match[1],comments=True)
            except ValueError:continue
            if len(parts)==1:value=parts[0]
    return value


def _updated_secret(text,name,key):
    replacement='export '+name+'='+shlex.quote(key)+'\n'
    lines=text.splitlines(keepends=True);found=False;result=[]
    for line in lines:
        if re.match(r'^\s*(?:export\s+)?'+re.escape(name)+r'\s*=',line):
            if not found:result.append(replacement);found=True
        else:result.append(line)
    if not found:
        if result and not result[-1].endswith(('\n','\r')):result[-1]+='\n'
        result.append(replacement)
    return ''.join(result)


def _rc_source(text):
    active='\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
    source=r'(?:~|\$HOME|\$\{HOME\}|'+re.escape(str(user_home()))+r')/\.secrets'
    if re.search(r'(?:^|&&|\|\||;)\s*(?:source|\.)\s+[\"\']?'+source+r'(?:[\"\']|\s|$)',active,re.M):return text
    return text+('' if not text or text.endswith('\n') else '\n')+'\n# Galam model credentials\n[ ! -f "$HOME/.secrets" ] || . "$HOME/.secrets"\n'


def save_profile(data,key='',*,original=None):
    """Serialize updates under one lock; rollback files before changing live state."""
    profile=validate_profile(data)
    if not isinstance(key,str) or any(not c.isprintable() for c in key):
        raise ValueError('API key must be a single line without control characters.')
    home=user_home();directory=home/'.galam'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.is_symlink():raise ValueError('Custom endpoint directory must not be a symlink.')
    lock_path=directory/'endpoints.lock'
    descriptor=os.open(lock_path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(descriptor,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        profiles=read_profiles()
        identity=(profile['endpoint'],profile['model'])
        previous=(original['endpoint'],original['model']) if original else None
        collision=next((p for p in profiles if (p['endpoint'],p['model'])==identity),None)
        from storage.endpoints import ENDPOINT_REGISTRY
        builtin=ENDPOINT_REGISTRY.get(profile['endpoint'],{}).get('models',{}).get(profile['model'],{})
        if (collision and previous!=identity) or (builtin and not builtin.get('custom_connection')):
            raise ValueError('That model already exists in this endpoint. Choose its saved profile to edit, or use another ID.')
        secrets=home/'.secrets';path=profile_path()
        old_secret=_regular_bytes(secrets)
        text=(old_secret or b'').decode('utf-8')
        effective_key=key or _secret_value(text,profile['api_key_env']) or os.environ.get(profile['api_key_env'])
        updates={}
        if key:
            updates[secrets]=_updated_secret(text,profile['api_key_env'],key).encode()
        shell=Path(os.environ.get('SHELL','bash')).name
        rc=home/('.zshrc' if shell=='zsh' else '.bashrc')
        old_rc=_regular_bytes(rc)
        if effective_key and (key or _secret_value(text,profile['api_key_env'])):
            updates[rc]=_rc_source((old_rc or b'').decode('utf-8')).encode()
        profiles=[p for p in profiles if (p['endpoint'],p['model']) not in {identity,previous}]
        profiles.append(profile)
        updates[path]=json.dumps({'version':1,'models':profiles},indent=2).encode()
        snapshots={p:(_regular_bytes(p),stat.S_IMODE(p.stat().st_mode) if p.exists() else 0o600) for p in updates}
        written=[]
        try:
            for p,content in updates.items():
                mode=snapshots[p][1] if p==rc and snapshots[p][0] is not None else 0o600
                atomic_bytes(p,content,mode);written.append(p)
        except OSError:
            for p in reversed(written):
                old,mode=snapshots[p]
                if old is None:p.unlink(missing_ok=True)
                else:atomic_bytes(p,old,mode)
            raise OSError('Could not save the connection; file changes were rolled back.') from None
        if effective_key:os.environ[profile['api_key_env']]=effective_key
        # Rename/move only a previously user-defined model, never a built-in entry.
        if previous and previous!=identity:
            endpoint=ENDPOINT_REGISTRY.get(previous[0],{})
            if endpoint.get('models',{}).get(previous[1],{}).get('custom_connection'):
                del endpoint['models'][previous[1]]
        apply_profiles(ENDPOINT_REGISTRY,[profile])
    return profile
