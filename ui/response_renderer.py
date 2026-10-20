"""Deterministic terminal Markdown layouts; values and stored source stay unchanged."""
import re
import shutil
from prompt_toolkit.utils import get_cwidth
from engine.transparency import safe_display

ANSI=re.compile(r'\x1b\[[0-9;]*m')
RESET='\033[0m'
DIM='\033[38;5;240m'
TEXT='\033[38;5;253m'
CYAN='\033[1;38;5;81m'


def cells(text):return sum(get_cwidth(char) for char in ANSI.sub('',text))


def wrap_ansi(text,width,preserve_space=False):
    """Wrap by terminal cells; long paths/code are never clipped."""
    width=max(1,width)
    result=[];line=[];used=0
    for token in re.findall(r'\x1b\[[0-9;]*m|[^\x1b]',text):
        if token=='\n':
            result.append(''.join(value for value,_ in line)+RESET)
            line=[];used=0;continue
        size=0 if ANSI.fullmatch(token) else get_cwidth(token)
        if size and used+size>width and line:
            split=len(line)
            if not preserve_space:
                spaces=[n for n,(value,_) in enumerate(line) if value==' ']
                if spaces and spaces[-1]>0:split=spaces[-1]
            head=''.join(value for value,_ in line[:split])
            tail=line[split:]
            if not preserve_space:
                while tail and tail[0][0]==' ':tail.pop(0)
            result.append((head if preserve_space else head.rstrip())+RESET)
            codes=ANSI.findall(head)
            carry=codes[-1] if codes else ''
            line=([(carry,0)] if carry and carry!=RESET else [])+tail
            used=sum(count for _,count in line)
        line.append((token,size));used+=size
    if line or not result:result.append(''.join(value for value,_ in line)+RESET)
    return result


def row_cells(line):
    values=[];current='';code=False;escaped=False
    for char in line.strip():
        if escaped:
            current+=char if char=='|' else '\\'+char;escaped=False
        elif char=='\\':escaped=True
        elif char=='`':code=not code;current+=char
        elif char=='|' and not code:values.append(current.strip());current=''
        else:current+=char
    if escaped:current+='\\'
    values.append(current.strip())
    if values and values[0]=='':values.pop(0)
    if values and values[-1]=='':values.pop()
    return values


def table(headers,separator,rows,width):
    from engine.tool_parser import _inline_format
    count=len(headers)
    plain=lambda value:ANSI.sub('',_inline_format(value))
    sizes=[max(cells(plain(row[n])) for row in [headers]+rows) for n in range(count)]
    available=width-(3*count+1)
    if available<count*5:
        out=[]
        for n,row in enumerate(rows,1):
            if n>1:out.append(DIM+'─'*width+RESET)
            for title,value in zip(headers,row):
                out.extend(wrap_ansi(CYAN+plain(title)+RESET+': '+TEXT+_inline_format(value)+RESET,width))
        return out
    widths=[min(size,max(5,available//count)) for size in sizes]
    while sum(widths)<available:
        candidates=[n for n in range(count) if widths[n]<sizes[n]]
        if not candidates:break
        chosen=max(candidates,key=lambda n:sizes[n]-widths[n]);widths[chosen]+=1
    border=lambda left,mid,right:DIM+left+mid.join('─'*(size+2) for size in widths)+right+RESET
    out=[border('┌','┬','┐')]
    for row_index,row in enumerate([headers]+rows):
        chunks=[wrap_ansi((CYAN if row_index==0 else TEXT)+_inline_format(value)+RESET,widths[n]) for n,value in enumerate(row)]
        for line_index in range(max(len(parts) for parts in chunks)):
            line=DIM+'│'+RESET
            for n,parts in enumerate(chunks):
                value=parts[line_index] if line_index<len(parts) else ''
                padding=max(0,widths[n]-cells(value))
                numeric=bool(re.fullmatch(r'[-+]?\d[\d,.%]*',plain(row[n])))
                if row_index and (separator[n].endswith(':') or numeric):value=' '*padding+value
                else:value+=' '*padding
                line+=' '+value+' '+DIM+'│'+RESET
            out.append(line)
        if row_index==0:out.append(border('├','┼','┤'))
    out.append(border('└','┴','┘'))
    return out


def code_block(source,language,width):
    gutter=len(str(max(1,len(source))))
    inner=max(4,width-gutter-5)
    title=' '+(language or 'code')+' '
    out=[DIM+'╭─'+title+'─'*max(0,width-cells(title)-3)+'╮'+RESET]
    for number,line in enumerate(source or [''],1):
        color='\033[38;5;222m'
        if language.lower() in ('diff','patch'):
            if line.startswith('+') and not line.startswith('+++'):color='\033[38;5;120m'
            elif line.startswith('-') and not line.startswith('---'):color='\033[38;5;203m'
        for part_index,part in enumerate(wrap_ansi(color+line+RESET,inner,True)):
            label=str(number).rjust(gutter) if part_index==0 else ' '*gutter
            out.append(DIM+'│ '+label+' '+RESET+part+' '*max(0,inner-cells(part))+DIM+' │'+RESET)
    out.append(DIM+'╰'+'─'*max(1,width-2)+'╯'+RESET)
    return out


def render(text,indent='  '):
    from engine.tool_parser import _inline_format,clean_response_text
    text=clean_response_text(safe_display(text))
    if not text:return ''
    width=max(16,shutil.get_terminal_size((44,24)).columns-cells(indent)-1)
    lines=text.splitlines();out=[];index=0;section=''
    while index<len(lines):
        line=lines[index];stripped=line.strip()
        if not stripped:
            if out and out[-1]!='':out.append('')
            index+=1;continue
        fence=re.match(r'^\s*(```|~~~)(.*)',line)
        if fence:
            language=fence[2].strip();source=[];index+=1
            while index<len(lines) and not lines[index].strip().startswith(fence[1]):
                source.append(lines[index]);index+=1
            index+=1;out.extend(code_block(source,language,width));continue
        if index+1<len(lines) and '|' in line:
            heads=row_cells(line);separators=row_cells(lines[index+1])
            if len(heads)==len(separators) and separators and all(re.fullmatch(r':?-{3,}:?',cell) for cell in separators):
                index+=2;rows=[]
                while index<len(lines) and '|' in lines[index] and lines[index].strip():
                    parts=row_cells(lines[index])
                    if len(parts)!=len(heads):break
                    rows.append(parts);index+=1
                out.extend(table(heads,separators,rows,width));continue
        heading=re.match(r'^\s*#{1,6}\s+(.+)',line)
        bold_heading=re.fullmatch(r'\*\*(.+?)\*\*',stripped)
        if heading or bold_heading:
            section=(heading or bold_heading)[1]
            if out and out[-1]!='':out.append('')
            out.extend(wrap_ansi(CYAN+_inline_format(section)+RESET,width))
            out.append(DIM+'─'*min(width,max(8,cells(ANSI.sub('',_inline_format(section)))))+RESET)
            index+=1;continue
        if re.fullmatch(r'(?:-{3,}|\*{3,}|_{3,})',stripped):
            out.append(DIM+'─'*width+RESET);index+=1;continue
        bullet=re.match(r'^(\s*)[-*+]\s+(.+)',line)
        if bullet:
            group=[];depth=len(bullet[1])
            while index<len(lines):
                match=re.match(r'^(\s*)[-*+]\s+(.+)',lines[index])
                if not match or len(match[1])!=depth:break
                group.append(match[2]);index+=1
            # Sort only explicit filename lists; numbered instructions and logs keep order.
            if re.search(r'\b(?:files?|folders?|directories)\b',section,re.I) and all(
                re.fullmatch(r'[^?<>]+\.[A-Za-z0-9]{1,10}/?',ANSI.sub('',_inline_format(item))) for item in group):
                group.sort(key=lambda item:ANSI.sub('',_inline_format(item)).casefold())
            for item in group:
                pad=' '*min(depth,8)
                chunks=wrap_ansi(TEXT+_inline_format(item)+RESET,max(8,width-len(pad)-2))
                out.append(pad+'\033[38;5;120m•'+RESET+' '+chunks[0])
                out.extend(pad+'  '+chunk for chunk in chunks[1:])
            continue
        numbered=re.match(r'^(\s*)(\d+)[.)]\s+(.+)',line)
        if numbered:
            prefix=numbered[1]+numbered[2]+'. '
            chunks=wrap_ansi(TEXT+_inline_format(numbered[3])+RESET,max(8,width-len(prefix)))
            out.append('\033[38;5;220m'+prefix+RESET+chunks[0])
            out.extend(' '*len(prefix)+chunk for chunk in chunks[1:])
            index+=1;continue
        if stripped.startswith('> '):
            out.extend(DIM+'│ '+RESET+chunk for chunk in wrap_ansi(TEXT+_inline_format(stripped[2:])+RESET,width-2))
        else:
            color=TEXT
            if re.match(r'^(?:Error|Failed|Failure|Incomplete)\b',stripped,re.I):color='\033[1;38;5;203m'
            elif re.match(r'^(?:Success|Completed|Done)[.!:]?$',stripped,re.I):color='\033[1;38;5;120m'
            elif stripped.startswith(('Note:','Warning:')):color='\033[38;5;222m'
            out.extend(wrap_ansi(color+_inline_format(line)+RESET,width))
            if stripped.endswith(':'):section=stripped
        index+=1
    while out and not out[-1]:out.pop()
    return '\n'.join(indent+line if line else '' for line in out)
