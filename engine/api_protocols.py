"""Translate Responses wire objects to Galam's existing chat/tool contract."""
import copy
import json
import uuid
from urllib.parse import urlsplit


def uses_responses(endpoint,model):
    from storage.endpoints import ENDPOINT_REGISTRY
    connection=ENDPOINT_REGISTRY.get(endpoint,{}).get('models',{}).get(model,{}).get('custom_connection',{})
    return connection.get('api_format')=='responses'


def retain_reasoning(message,endpoint,model):
    if not uses_responses(endpoint,model):return False
    from storage.endpoints import ENDPOINT_REGISTRY
    connection=ENDPOINT_REGISTRY[endpoint]['models'][model]['custom_connection']
    origin=message.get('_galam_response_origin')
    return origin is None or list(origin)==[endpoint,model,connection['url']]


def prepare_payload(url,payload):
    kind=wire_format(url,payload.get('model',''))
    if kind=='ollama':return ollama_payload(payload)
    if kind!='responses':return payload
    if 'messages' not in payload:return payload
    result={key:copy.deepcopy(value) for key,value in payload.items()
            if key not in ('messages','max_tokens','max_completion_tokens','tools','tool_choice','chat_template_kwargs')}
    result['store']=False
    result['include']=['reasoning.encrypted_content']
    result['max_output_tokens']=payload.get('max_completion_tokens',payload.get('max_tokens',4096))
    items=[]
    for message in payload['messages']:
        role=message.get('role','user');content=message.get('content') or ''
        if role=='tool':
            items.append({'type':'function_call_output','call_id':message['tool_call_id'],'output':content})
            continue
        items.extend(copy.deepcopy(message.get('_galam_response_reasoning',[])))
        if content:items.append({'role':role,'content':content})
        for call in message.get('tool_calls',[]):
            function=call['function']
            items.append({'type':'function_call','call_id':call['id'],'name':function['name'],
                          'arguments':function.get('arguments','{}')})
    result['input']=items
    if payload.get('tools'):
        # Chat function definitions are nested; Responses function definitions are flat.
        result['tools']=[{'type':'function',**copy.deepcopy(tool['function']),'strict':False}
                         for tool in payload['tools'] if tool.get('type')=='function']
        choice=payload.get('tool_choice','auto')
        if isinstance(choice,dict):choice={'type':'function','name':choice['function']['name']}
        result['tool_choice']=choice
    return result


def output_parts(data):
    text=[];reasoning=[];tools=[];retained=[]
    for item in data.get('output',[]):
        kind=item.get('type')
        if kind=='message':
            for part in item.get('content',[]):
                if part.get('type')=='output_text':text.append(part.get('text',''))
                elif part.get('type')=='refusal':text.append(part.get('refusal',''))
        elif kind=='reasoning':
            retained.append(copy.deepcopy(item))
            reasoning.extend(part.get('text','') for part in item.get('summary',[]))
        elif kind=='function_call':
            tools.append({'id':item.get('call_id') or item.get('id'),'type':'function',
                          'function':{'name':item.get('name',''),'arguments':item.get('arguments','')}})
    return ''.join(text), '\n'.join(reasoning),tools,retained


def remember_reasoning(response,data):
    if response is None:return
    _,_,_,items=output_parts(data)
    current=getattr(response,'_galam_response_reasoning',[])
    if not isinstance(current,list):current=[]
    ids={item.get('id') for item in current}
    for item in items:
        if item.get('id') not in ids:current.append(item);ids.add(item.get('id'))
        else:
            for index,old in enumerate(current):
                if old.get('id')==item.get('id'):
                    current[index]={**old,**item};break
    response._galam_response_reasoning=current


def response_chunk(data,response=None):
    """Canonical deltas without duplicating done snapshots or re-executing tool calls."""
    kind=data.get('type','')
    if not kind.startswith('response.'):return None
    state=getattr(response,'_galam_responses_state',None) if response is not None else None
    if not isinstance(state,dict):
        state={'text':'','reasoning':'','tools':{}}
        if response is not None:response._galam_responses_state=state
    text='';reasoning='';deltas=[];finish=''
    if kind=='response.output_text.delta':text=data.get('delta','');state['text']+=text
    elif kind in ('response.reasoning_text.delta','response.reasoning_summary_text.delta'):
        reasoning=data.get('delta','');state['reasoning']+=reasoning
    elif kind=='response.refusal.delta':text=data.get('delta','');state['text']+=text
    elif kind in ('response.output_item.added','response.output_item.done','response.function_call_arguments.delta','response.function_call_arguments.done'):
        item=data.get('item',{})
        if item.get('type')=='reasoning':remember_reasoning(response,{'output':[item]})
        if item.get('type')=='function_call' or 'function_call_arguments' in kind:
            index=data.get('output_index',0)
            previous=state['tools'].setdefault(index,{'name':'','arguments':'','id':None})
            function={};identifier=item.get('call_id') or previous['id']
            if item.get('name') and not previous['name']:
                function['name']=item['name'];previous['name']=item['name']
            if kind.endswith('.delta'):
                arguments=data.get('delta','')
            else:
                full=data.get('arguments',item.get('arguments',''))
                arguments=full[len(previous['arguments']):] if full.startswith(previous['arguments']) else ''
            if arguments:function['arguments']=arguments;previous['arguments']+=arguments
            if identifier:previous['id']=identifier
            if function:
                delta={'index':index,'type':'function','function':function}
                if identifier:delta['id']=identifier
                deltas.append(delta)
    elif kind in ('response.completed','response.incomplete','response.failed'):
        result=data.get('response',{})
        remember_reasoning(response,result)
        if kind=='response.failed' or result.get('error'):
            raise ValueError('Response failed: '+str((result.get('error') or {}).get('message','Provider failed to complete this response.')))
        if kind=='response.incomplete' and (result.get('incomplete_details') or {}).get('reason')!='max_output_tokens':
            raise ValueError('Response incomplete: '+str((result.get('incomplete_details') or {}).get('reason','unknown reason')))
        finish='length' if kind=='response.incomplete' else 'stop'
        full_text,full_reasoning,tools,_=output_parts(result)
        # Some compatible services send only the final response event.
        if full_text.startswith(state['text']):text=full_text[len(state['text']):];state['text']=full_text
        if full_reasoning.startswith(state['reasoning']):reasoning=full_reasoning[len(state['reasoning']):];state['reasoning']=full_reasoning
        for call in tools:
            index=next((index for index,tool in state['tools'].items() if tool['id']==call['id']),None)
            if index is None:index=max(state['tools'],default=-1)+1
            previous=state['tools'].setdefault(index,{'name':'','arguments':'','id':call['id']})
            function={}
            if not previous['name']:function['name']=call['function']['name'];previous['name']=function['name']
            full=call['function']['arguments']
            if not full.startswith(previous['arguments']):raise ValueError('Provider returned inconsistent function arguments.')
            remaining=full[len(previous['arguments']):]
            if remaining:function['arguments']=remaining;previous['arguments']=full
            if function:deltas.append({'index':index,'id':call['id'],'type':'function','function':function})
        if tools and finish=='stop':finish='tool_calls'
    return text,reasoning,deltas,finish


def wire_format(url,model=''):
    from config import CONFIG
    from storage.endpoints import ENDPOINT_REGISTRY
    profile=ENDPOINT_REGISTRY.get(CONFIG.get('active_endpoint'),{}).get('models',{}).get(model,{}).get('custom_connection',{})
    if profile.get('url')==url:return profile['api_format']
    path=urlsplit(url).path.rstrip('/')
    if path.endswith('/api/chat'):return 'ollama'
    if path.endswith(('/response','/responses')):return 'responses'
    return 'chat_completions'


def ollama_payload(payload):
    result={key:copy.deepcopy(payload[key]) for key in ('model','stream','tools') if key in payload}
    options={}
    for source,target in (('max_tokens','num_predict'),('max_completion_tokens','num_predict'),
                          ('temperature','temperature'),('top_p','top_p'),('seed','seed'),('stop','stop')):
        if source in payload:options[target]=payload[source]
    if options:result['options']=options
    messages=[];names={}
    for original in payload.get('messages',[]):
        message={k:copy.deepcopy(v) for k,v in original.items() if k in ('role','content','tool_calls')}
        for call in message.get('tool_calls',[]):
            function=call['function'];names[call.get('id')]=function['name']
            arguments=function.get('arguments',{})
            if isinstance(arguments,str):arguments=json.loads(arguments or '{}')
            function['arguments']=arguments
            call.pop('id',None)
            call.pop('index',None)
        if message.get('role')=='tool':
            name=names.get(original.get('tool_call_id')) or original.get('name')
            if name:message['tool_name']=name
        messages.append(message)
    result['messages']=messages
    return result


def ollama_tools(message):
    tools=[]
    for index,call in enumerate(message.get('tool_calls',[]) or []):
        function=call.get('function',{})
        arguments=function.get('arguments',{})
        tools.append({'id':call.get('id') or 'ollama_'+uuid.uuid4().hex,'index':index,'type':'function',
                      'function':{'name':function.get('name',''),'arguments':arguments if isinstance(arguments,str) else json.dumps(arguments)}})
    return tools


def ollama_chunk(data,response=None):
    message=data.get('message',{})
    tools=ollama_tools(message)
    start=getattr(response,'_galam_ollama_tool_count',0) if response is not None else 0
    for index,tool in enumerate(tools,start):tool['index']=index
    if response is not None:response._galam_ollama_tool_count=start+len(tools)
    finish=('length' if data.get('done_reason')=='length' else 'stop') if data.get('done') else ''
    return message.get('content') or '',message.get('thinking') or '',tools,finish


def streaming_response(content_type):
    return any(kind in content_type.lower() for kind in ('text/event-stream','application/x-ndjson','application/ndjson'))


def stream_data(line):
    """SSE and native Ollama NDJSON share the cancellation-aware line reader."""
    if isinstance(line,bytes):line=line.decode('utf-8')
    line=line.strip()
    if line.startswith('data:'):return line[5:].strip()
    if line.startswith('{'):return line
    return None
