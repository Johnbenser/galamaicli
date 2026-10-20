"""Permanent per-response completion metadata. No model requests."""
import shutil
import textwrap
import time


KEY='_galam_response_stats'


def footer(stats,indent='  '):
    duration=max(0,int(stats['elapsed']))
    minutes,seconds=divmod(duration,60)
    status=stats.get('status','completed')
    label={'cancelled':'Cancelled after','failed':'Stopped after','incomplete':'Stopped after'}.get(status,'Worked for')
    clock=time.strftime('%H:%M:%S',time.localtime(stats['finished_at']))
    usage=stats['usage']
    number=lambda value:f'{value:,}' if isinstance(value,int) else 'unavailable'
    source={'provider':'provider reported','estimated':'estimated','local':'local; no API request'}[usage['source']]
    detail=f"Tokens: {number(usage['input'])} input + {number(usage['output'])} output = {number(usage['total'])} total ({source})"
    width=max(16,shutil.get_terminal_size((44,24)).columns-len(indent)-1)
    lines=[f'{label} {minutes}m {seconds:02d}s • {clock}']+textwrap.wrap(detail,width=width)
    return '\n'.join(indent+'\033[38;5;244m'+line+'\033[0m' for line in lines)


def attach(messages,stats,previous_assistants=()):
    if not isinstance(messages,list):return
    target=next((message for message in reversed(messages) if message.get('role')=='assistant'
                 and message.get('content') and not message.get('_galam_tool_calls')
                 and id(message) not in previous_assistants),None)
    if target is None:
        target=next((message for message in reversed(messages) if message.get('role')=='user'
                     and not message.get('_galam_internal') and not message.get('_galam_action')),None)
    if target is not None:target[KEY]=stats
