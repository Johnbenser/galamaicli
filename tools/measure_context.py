"""Offline comparison of representative outgoing context; makes no API calls."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import AGENT_STATE
from routers.manager import build_enforced_messages
from engine.context_manager import estimate_tokens
from engine.tool_protocol import options


def measure():
    html = '<!DOCTYPE html>\n<html><head><title>Demo</title></head><body>\n<h1>Demo</h1><p>Version Alpha</p></body></html>\n'
    messages = []
    checkpoints = []
    def user(text):
        messages.append({'role':'user','content':text})
        checkpoints.append(copy.deepcopy(messages))
    def result(tool, args, output):
        index = str(len(messages))
        messages.append({'role':'assistant','content':'','_galam_tool_calls':[{
            'id':index,'type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]})
        messages.append({'role':'user','content':f'[Tool Observation for `{tool}`]:\n{output}\n\nStatus: Tool step executed successfully with exit status 0.',
            '_galam_call_id':index,'_galam_action':{'id':'a'*32,'status':'completed','exit_code':0,
                'tool':tool,'path':args.get('path'),'output_path':'/tmp/stored-action.txt'}})
        checkpoints.append(copy.deepcopy(messages))
    user('Create index.html as a complete HTML page with title and heading Demo and paragraph Version Alpha.')
    result('write_file',{'path':'index.html','content':html},
           "Successfully wrote index.html.\nChanges (old → new line):\n"+'\n'.join(f'+ {n} | {line}' for n,line in enumerate(html.splitlines(),1)))
    messages.append({'role':'assistant','content':'Created index.html.'})
    user('Read index.html, then modify only Version Alpha to Version Beta. Preserve all unrelated code.')
    result('view_file',{'path':'index.html'},'File: index.html\n'+'\n'.join(f'{n} | {line}' for n,line in enumerate(html.splitlines(),1)))
    result('replace_file_content',{'path':'index.html','target_content':'Version Alpha','replacement_content':'Version Beta'},
           "Successfully modified index.html.\nChanges (old → new line):\n- <p>Version Alpha</p>\n+ <p>Version Beta</p>")
    messages.append({'role':'assistant','content':'Updated the paragraph.'})
    user('Execute this exact terminal command using execute_bash: wc -c index.html. Report the actual byte count and exit status.')
    result('execute_bash',{'command':'wc -c index.html'},'121 index.html\n')
    report = {}
    with patch.dict(AGENT_STATE, {'working_directory':'/tmp/galam-context-fixture','app_ref':None}), \
         patch('engine.task_runner.workspace_snapshot',return_value='Current directory: /tmp/galam-context-fixture\nfile index.html'), \
         patch('engine.task_runner.GLOBAL_TASK_MANAGER.get_active_tasks',return_value=[]), \
         patch('engine.context_manager.repository_context',return_value=''):
        for endpoint,model in [('Groq','openai/gpt-oss-20b'),('Direct','gemini-3.5-flash')]:
            counts=[]
            for history in checkpoints:
                selected=build_enforced_messages(history,endpoint,model)
                tools=options(endpoint,model,history,{})
                counts.append(estimate_tokens(selected)+(estimate_tokens(tools) if tools else 0))
            chat=build_enforced_messages([{'role':'user','content':'What is a Python function?'}],endpoint,model)
            report[endpoint]={'estimated_input_per_request':counts,'estimated_input_total':sum(counts),
                              'estimated_chat_input':estimate_tokens(chat),'api_calls':0}
    return report


if __name__=='__main__':
    print(json.dumps(measure(),indent=2))
