"""Permanent per-response completion metadata. No model requests."""
import time


KEY='_galam_response_stats'


def summary(stats):
    duration=max(0,int(stats['elapsed']))
    minutes,seconds=divmod(duration,60)
    status=stats.get('status','cancelled' if stats.get('cancelled') else 'completed')
    label={'cancelled':'Cancelled after','failed':'Stopped after','incomplete':'Stopped after'}.get(status,'Worked for')
    clock=time.strftime('%I:%M:%S %p',time.localtime(stats.get('finished_at',0)))
    usage=stats.get('usage') or {}
    total=usage.get('total')
    if isinstance(total,int) and not isinstance(total,bool) and total>=0:
        amount=('~' if usage.get('source')=='estimated' else '')+f'{total:,}'
    else:amount='unavailable'
    return f'{label} {minutes}m {seconds:02d}s • {clock} • Total Token {amount}'


def footer(stats,indent='  '):
    # One logical status line; the terminal wraps it without clipping values.
    return indent+'\033[38;5;244m'+summary(stats)+'\033[0m'


def attach(messages,stats,previous_assistants=()):
    if not isinstance(messages,list):return
    target=next((message for message in reversed(messages) if message.get('role')=='assistant'
                 and message.get('content') and not message.get('_galam_tool_calls')
                 and id(message) not in previous_assistants),None)
    if target is None:
        target=next((message for message in reversed(messages) if message.get('role')=='user'
                     and not message.get('_galam_internal') and not message.get('_galam_action')),None)
    if target is not None:target[KEY]=stats
