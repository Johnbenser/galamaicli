"""Per-request counters with provider usage kept separate from estimates."""
from contextvars import ContextVar
from engine.context_manager import estimate_tokens

_LEDGER = ContextVar('galam_usage_ledger', default=None)


def start_task(conversation_id=None):
    data = {'conversation_id':conversation_id,'requests':[]}
    _LEDGER.set(data)
    from config import AGENT_STATE
    AGENT_STATE['usage_task'] = data
    return data


def ledger():
    # Framework executors copy ContextVars across worker boundaries. Share the
    # current task ledger there, while independent /btw threads retain their own.
    data=_LEDGER.get()
    if data is None:
        data={'conversation_id':None,'requests':[]}
        _LEDGER.set(data)
    return data


def begin_request(endpoint,model,payload,delta=False):
    guard = ledger().get('_before_request')
    if guard:
        guard(payload)
    row = {'endpoint':endpoint,'model':model,'estimated_input_tokens':estimate_tokens(payload),
           'usage_source':'unavailable','_delta':delta}
    ledger()['requests'].append(row)
    return row


def attach(response,row):
    row['http_status'] = getattr(response,'status_code',getattr(response,'status',None))
    try:
        response._galam_usage_record = row
    except (AttributeError,TypeError):
        pass
    return response


def capture(response,data):
    row = getattr(response,'_galam_usage_record',None)
    if not isinstance(row,dict) or not isinstance(data,dict):
        return
    data = data.get('result',data)
    if not isinstance(data,dict):
        return
    delta = data.get('delta') if isinstance(data.get('delta'),dict) else {}
    nested = [data.get(name) for name in ('x_groq','response','message')]
    usage = (data.get('usageMetadata') or data.get('usage') or delta.get('usage') or
             next((value.get('usage') for value in nested if isinstance(value,dict) and value.get('usage')),None))
    if not isinstance(usage,dict) and ('prompt_eval_count' in data or 'eval_count' in data):
        usage={'input_tokens':data.get('prompt_eval_count'),'output_tokens':data.get('eval_count')}
    if not isinstance(usage,dict):
        return
    if 'tokens' in usage:
        usage = usage['tokens']
    if not isinstance(usage,dict):
        return
    def number(*values):
        return next((v for v in values if isinstance(v,int) and not isinstance(v,bool) and v>=0),None)
    input_count = number(usage.get('prompt_tokens'),usage.get('input_tokens'),usage.get('promptTokenCount'))
    output_count = number(usage.get('completion_tokens'),usage.get('output_tokens'),usage.get('candidatesTokenCount'))
    completion_details = usage.get('completion_tokens_details') if isinstance(usage.get('completion_tokens_details'),dict) else {}
    prompt_details = usage.get('prompt_tokens_details') if isinstance(usage.get('prompt_tokens_details'),dict) else {}
    reasoning = number(usage.get('thoughtsTokenCount'),completion_details.get('reasoning_tokens'))
    if 'candidatesTokenCount' in usage and reasoning is not None:
        output_count = (output_count or 0)+reasoning
    total = number(usage.get('total_tokens'),usage.get('totalTokenCount'))
    cached = number(usage.get('cachedContentTokenCount'),prompt_details.get('cached_tokens'))
    counts = {'input_tokens':input_count,'output_tokens':output_count,'total_tokens':total,
              'reasoning_tokens':reasoning,'cached_input_tokens':cached}
    if input_count is None and output_count is None and total is None:
        return
    if total is not None:row['_reported_total']=True
    if total is None and not row.get('_reported_total') and input_count is not None and output_count is not None:
        counts['total_tokens']=input_count+output_count
    for key,value in counts.items():
        if value is not None:
            row[key] = row.get(key,0)+value if row.get('_delta') else value
    if total is None and not row.get('_reported_total') and all(isinstance(row.get(key),int) for key in ('input_tokens','output_tokens')):
        row['total_tokens']=row['input_tokens']+row['output_tokens']
    row['usage_source']='provider'


def record_output(response, content='', reasoning='', tools=None):
    """Character counters only; estimates never pretend to be tokenizer counts."""
    import json
    row=getattr(response,'_galam_usage_record',None)
    if not isinstance(row,dict):return
    text=str(content or '')+str(reasoning or '')
    if tools:text+=json.dumps(tools,ensure_ascii=False)
    if not text:return
    ascii_count=sum(ord(char)<128 for char in text)
    row['_output_ascii']=row.get('_output_ascii',0)+ascii_count
    row['_output_other_bytes']=row.get('_output_other_bytes',0)+len(text.encode('utf-8',errors='replace'))-ascii_count
    row['estimated_output_tokens']=(row['_output_ascii']+2)//3+(row['_output_other_bytes']+1)//2


def totals(stats=None):
    stats=snapshot() if stats is None else stats
    rows=stats['requests']
    if not rows:return {'source':'local','input':0,'output':0,'total':0,'requests':0}
    def reported(row,key):
        value=row.get(key)
        return isinstance(value,int) and not isinstance(value,bool) and value>=0
    measured=all(reported(row,'total_tokens') for row in rows)
    def component(row,key,estimate):
        if reported(row,key):return row[key]
        # A total-only provider response cannot establish its component split.
        if reported(row,'total_tokens'):return None
        return row.get(estimate)
    def combine(values):
        return sum(values) if all(isinstance(value,int) for value in values) else None
    inputs=combine([component(row,'input_tokens','estimated_input_tokens') for row in rows])
    outputs=combine([component(row,'output_tokens','estimated_output_tokens') for row in rows])
    totals=[]
    for row in rows:
        if reported(row,'total_tokens'):totals.append(row['total_tokens'])
        else:
            parts=[component(row,'input_tokens','estimated_input_tokens'),component(row,'output_tokens','estimated_output_tokens')]
            totals.append(combine(parts))
    return {'source':'provider' if measured else 'estimated','input':inputs,'output':outputs,
            'total':combine(totals),'requests':len(rows)}


def snapshot(start=0):
    rows = [{k:v for k,v in r.items() if not k.startswith('_')} for r in ledger()['requests'][start:]]
    measured = [r for r in rows if r.get('usage_source')=='provider']
    return {'request_count':len(rows),'provider_counted_requests':len(measured),
            'provider_input_tokens':sum(r.get('input_tokens',0) for r in measured),
            'provider_output_tokens':sum(r.get('output_tokens',0) for r in measured),
            'provider_total_tokens':sum(r.get('total_tokens',0) for r in measured),
            'estimated_input_tokens_without_usage':sum(r['estimated_input_tokens'] for r in rows if r not in measured),
            'complete_provider_usage':len(measured)==len(rows) and all(all(k in r for k in ('input_tokens','output_tokens','total_tokens')) for r in measured),
            'requests':rows}
