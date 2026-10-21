"""Recognize server executables, never server names inside inspected text."""
import os
import re
import shlex


def _tokens(command):
    # Preserve quote boundaries until shell operators have been recognized.
    lexer=shlex.shlex(str(command),posix=False,punctuation_chars=';&|()<>\n')
    lexer.whitespace=' \t\r';lexer.whitespace_split=True
    return list(lexer)


def _unquote(word):
    parsed=shlex.split(word)
    return parsed[0] if len(parsed)==1 else word


def _without_heredocs(command):
    pending=[];headers=[]
    for line in str(command).splitlines(keepends=True):
        if pending:
            delimiter,tabs=pending[0]
            if (line.lstrip('\t') if tabs else line).rstrip('\r\n')==delimiter:pending.pop(0)
            continue
        headers.append(line)
        if '<<' not in line:continue
        try:words=_tokens(line)
        except ValueError:continue
        for index,word in enumerate(words[:-1]):
            if word=='<<':
                delimiter=words[index+1];tabs=delimiter.startswith('-')
                pending.append((_unquote(delimiter[1:] if tabs else delimiter),tabs))
    return ''.join(headers)


def command_segments(command):
    try:tokens=_tokens(_without_heredocs(command))
    except ValueError:return []
    segments=[];current=[]
    for token in tokens:
        if token and all(char in ';&|()\n' for char in token):
            if current:segments.append(current);current=[]
        else:
            try:current.append(_unquote(token))
            except ValueError:return []
    if current:segments.append(current)
    return segments


def executable(segment):
    words=list(segment)
    while words:
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=',words[0]):words.pop(0)
        elif os.path.basename(words[0]) in ('env','nohup','command','exec'):
            words.pop(0)
            while words and words[0].startswith('-'):words.pop(0)
        else:break
    return words


def server_invocation(command):
    for segment in command_segments(command):
        words=executable(segment)
        if not words:continue
        name=os.path.basename(words[0]);args=words[1:]
        if any(arg in ('--help','--version','-h','-v') for arg in args):continue
        if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?',name):
            args=[arg for arg in args if arg in ('-m','-c') or not arg.startswith('-')]
            if args[:2]==['-m','http.server']:return segment,words
            if args and os.path.basename(args[0]) in ('server.py','app.py','main.py'):return segment,words
        elif name in ('npm','pnpm','yarn','bun'):
            script=args[1] if len(args)>1 and args[0]=='run' else args[0] if args else ''
            if script in ('dev','start','serve','preview'):return segment,words
        elif name=='node' and args and os.path.basename(args[0]) in tuple(stem+ext for stem in ('server','index','app','main') for ext in ('.js','.mjs','.cjs')):
            return segment,words
        elif name=='npx':
            nested=[arg for arg in args if arg not in ('--yes','-y','--no-install')]
            if nested and nested[0] in ('vite','next','astro','remix','live-server','http-server'):
                if len(nested)==1 or nested[1] not in ('build','lint','info','telemetry'):return segment,[nested[0],*nested[1:]]
        elif name in ('vite','next','astro','remix'):
            if not args or args[0] in ('dev','start','preview') or args[0].startswith('-'):return segment,words
        elif name in ('uvicorn','gunicorn','fastapi','live-server','http-server'):
            return segment,words
        elif name=='flask' and args and args[0]=='run':return segment,words
    return None


def _port(value):
    try:value=int(value)
    except (ValueError,TypeError):return None
    return str(value) if 1<=value<=65535 else None


def _explicit_port(words):
    for index,word in enumerate(words):
        if word in ('--port','-p') and index+1<len(words):return _port(words[index+1])
        if word.startswith('--port='):return _port(word.split('=',1)[1])
    if '-m' in words:
        index=words.index('-m')
        if words[index+1:index+2]==['http.server'] and len(words)>index+2:return _port(words[index+2])
    return None


def configured_port(command,scripts=None,environment=None,seen=()):
    found=server_invocation(command)
    if not found:return None
    segment,words=found
    explicit=_explicit_port(words)
    if explicit:return explicit
    environment=dict(environment or {})
    for word in segment:
        if not word.startswith('PORT='):continue
        value=word.split('=',1)[1]
        default=re.fullmatch(r'\$\{PORT:-(\d+)\}',value)
        environment['PORT']=(environment.get('PORT') or default[1]) if default else value
    if _port(environment.get('PORT')):return _port(environment['PORT'])
    name=os.path.basename(words[0]);args=words[1:]
    if name in ('npm','pnpm','yarn','bun'):
        script=args[1] if len(args)>1 and args[0]=='run' else args[0] if args else ''
        if script not in seen and len(seen)<8 and isinstance(scripts,dict):
            nested=configured_port(str(scripts.get(script,'')),scripts,environment,(*seen,script))
            if nested:return nested
    if '-m' in words and words[words.index('-m')+1:words.index('-m')+2]==['http.server']:return '8000'
    if name=='vite':return '5173'
    return None


def server_start_requested(request):
    # Plain questions and negated instructions must not become launch requests.
    from engine.action_results import request_intent
    def one_edit(a, b):
        if a == b:return True
        if len(a) == len(b):return sum(x != y for x,y in zip(a,b)) == 1
        if abs(len(a)-len(b)) != 1:return False
        short,long = (a,b) if len(a)<len(b) else (b,a)
        return any(long[:i]+long[i+1:] == short for i in range(len(long)))
    verbs = ('start','run','launch','restart')
    def normalize(match):
        word = match[1].lower()
        verb = next((v for v in verbs if one_edit(word,v)), word)
        return verb + match[2]
    # Conservative adjacent-verb correction, independent of a user's spelling/path.
    text=re.sub(r'\b(\w+)(\s+(?:(?:my|the|this|our|a|dev|development)\s+){0,3}(?:server|service)\b)',normalize,str(request),flags=re.I)
    if not request_intent(text)[0]:return False
    text=re.sub(r"\b(?:do not|don't|never)\s+(?:start|run|launch|restart|serve|bring\s+up)\b[^.;\n]*",'',text,flags=re.I)
    # A test-only clause is not startup, but must not hide an explicit launch
    # elsewhere in a compound request ("start the app, then run checks").
    text = re.sub(r'\b(?:run|execute)\s+(?:the\s+)?(?:tests?|lint|checks?)\b(?:(?!\b(?:and|then)\b)[^.;\n])*', '', text, flags=re.I)
    return bool(re.search(
        r'\b(?:start|run|launch|restart|bring\s+up)\b[^\n]{0,140}\b(?:server|service|web|website|site|app|preview)\b|'
        r'\b(?:start|run|launch|restart)\b[^\n]{0,140}\blocalhost\b|'
        r'\bserve\s+(?:my|the|this)\s+(?:app|site|project)\b',text,re.I))
