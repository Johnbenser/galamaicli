"""Separate full-screen connection editor; no stdout inside the settings renderer."""
import copy
from pathlib import Path
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.focus import focus_next,focus_previous
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit,VSplit,DynamicContainer,Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.scrollable_pane import ScrollablePane
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea,Label,Button,RadioList,Checkbox
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.layout.dimension import Dimension

from config import CONFIG,AGENT_STATE,save_settings
from storage.endpoints import ENDPOINT_REGISTRY
from storage.user_endpoints import read_profiles,save_profile,user_home,endpoint_choices,default_api_format,normalize_url,FORMATS


def busy():
    return bool(AGENT_STATE.get('task_active') or AGENT_STATE.get('active_message') or
                any(row.get('task')=='btw' for row in AGENT_STATE.get('active_subagents',[])))


class EndpointManager:
    def __init__(self):
        self.original=None;self.status='';self.page='form';self.picker=None
        choices=endpoint_choices()
        self.selected_endpoint=CONFIG.get('active_endpoint') if CONFIG.get('active_endpoint') in choices else choices[0]
        self.fields={name:TextArea(text=default,multiline=False,password=name=='key',
                                  style='class:input',height=1,history=DummyHistory())
                     for name,default in [('model',''),('url',''),
                       ('api_key_env',''),('key',''),('context_window','8192')]}
        self.protocol=RadioList(self.protocol_values())
        self.fields['model'].buffer.on_text_changed+=lambda buffer:self.update_default()
        self.fields['url'].buffer.on_text_changed+=self.url_changed
        self.activate=Checkbox('Use this model now',checked=True)
        self.key_bindings=KeyBindings()
        self.key_bindings.add('tab')(focus_next)
        self.key_bindings.add('s-tab')(focus_previous)
        self.key_bindings.add('c-s')(lambda event:self.save())
        self.key_bindings.add('escape',eager=True)(self.escape)
        self.key_bindings.add('c-c')(self.escape)
        self.body=DynamicContainer(self.container)
        self.app=Application(layout=Layout(HSplit([
            Window(FormattedTextControl('Galam.AI · Model connections'),height=1,style='class:title'),
            Window(height=1),self.body,
            Window(FormattedTextControl(lambda:self.status),height=Dimension(min=1,max=4),wrap_lines=True,style='class:status'),
            Window(FormattedTextControl('Tab/Shift+Tab move · Ctrl+S save · Esc back'),height=1,style='class:hint')
        ])),key_bindings=self.key_bindings,full_screen=True,mouse_support=False,
            style=Style.from_dict({'title':'fg:#38bdf8 bold','input':'bg:#1e293b fg:#f8fafc',
                'status':'fg:#fbbf24','hint':'fg:#94a3b8','button.focused':'bg:#38bdf8 fg:#0f172a',
                'radio-selected':'fg:#38bdf8 bold'}))
        self.app.ttimeoutlen=.1
        self.form=self.make_form()
        self.result=HSplit([Label('Connection saved'),Label(lambda:self.result_text),
                           VSplit([Button('Add another',handler=self.reset),Button('Back to settings',handler=self.finish)],padding=1)])
        if busy():
            self.status='Finish or cancel the current task before changing model connections.'
            self.page='blocked'
        else:self.app.layout.focus(self.endpoint_button)

    def protocol_values(self):
        kind=default_api_format(self.selected_endpoint,self.fields['model'].text)
        label={'chat_completions':'Chat Completions','responses':'Responses'}[kind]
        return [('default','Default: '+label),('chat_completions','Override: Chat Completions'),('responses','Override: Responses')]

    def update_default(self):
        self.protocol.values=self.protocol_values()

    def default_path(self):
        from urllib.parse import urlsplit
        kind=default_api_format(self.selected_endpoint,self.fields['model'].text)
        base=self.fields['url'].text or ENDPOINT_REGISTRY.get(self.selected_endpoint,{}).get('base_url','')
        try:path=urlsplit(normalize_url(base,kind)).path
        except ValueError:path='/v1/'+FORMATS[kind]
        return 'Default route: '+path

    def make_form(self):
        self.endpoint_button=Button('Choose endpoint',handler=self.pick_endpoint)
        rows=[VSplit([Button('Saved models',handler=self.pick_saved),self.endpoint_button],padding=1),
              Label(lambda:'Endpoint: '+self.selected_endpoint),Window(height=1)]
        labels={'model':'Model ID',
                'url':'Base URL or full API URL', 'api_key_env':'Key variable (NAME or $NAME; blank = auto)',
                'key':'API key (masked; blank keeps existing key)', 'context_window':'Context window in tokens'}
        for name in ('model','url'):
            rows.extend([Label(labels[name]),self.fields[name],Window(height=1)])
        rows.extend([Label('API format (keep Default unless needed)'),self.protocol,Label(self.default_path)])
        for name in ('api_key_env','key','context_window'):
            rows.extend([Label(labels[name]),self.fields[name],Window(height=1)])
        rows.extend([self.activate,Label('Keys: '+str(user_home()/'.secrets')),
                     VSplit([Button('Save',handler=self.save),Button('Cancel',handler=self.finish)],padding=1)])
        return ScrollablePane(HSplit(rows,padding=0),show_scrollbar=True)

    def url_changed(self,buffer):
        from urllib.parse import urlsplit
        try:path=urlsplit(buffer.text.strip()).path.rstrip('/')
        except ValueError:return
        kind='responses' if path.endswith(('/response','/responses')) else 'chat_completions' if path.endswith('/chat/completions') else None
        if kind:self.protocol.current_value='default' if kind==default_api_format(self.selected_endpoint,self.fields['model'].text) else kind

    def container(self):
        if self.page=='blocked':return HSplit([Label('Connection editor unavailable during active work.'),Button('Back',handler=self.finish)])
        if self.page=='result':return self.result
        if self.page=='picker':
            prompt=[Label('Endpoint number + Enter, or Tab to list'),self.choice_number] if self.picker_kind=='endpoint' else []
            return HSplit(prompt+[self.picker,Button('Use selection',handler=self.use_selection),Button('Back',handler=self.back_to_form)])
        # DynamicContainer is evaluated during Application initialization.
        return getattr(self,'form',Window())

    def finish(self):
        self.clear_key();self.app.exit()

    def clear_key(self):
        # TextArea has no persistent history. Clear undo/history as well as visible text.
        self.fields['key'].buffer.reset()

    def escape(self,event):
        if self.page=='picker':self.back_to_form()
        else:self.finish()

    def back_to_form(self):
        self.page='form';self.status='';self.app.layout.focus(self.endpoint_button);self.app.invalidate()

    def pick_saved(self):
        try:self.profiles=read_profiles()
        except (ValueError,OSError):
            self.status='Cannot read saved connections. Check ~/.galam/endpoints.json.';return
        values=[(-1,'+ New model')]+[(index,p['endpoint']+' / '+p['model']) for index,p in enumerate(self.profiles)]
        self.pick('saved',values)

    def pick_endpoint(self):
        self.pick('endpoint',[(name,f'{number}. {name}') for number,name in enumerate(endpoint_choices(),1)])

    def pick(self,kind,values):
        self.picker_kind=kind;self.picker=RadioList(values);self.page='picker';self.status=''
        if kind=='endpoint':
            self.choice_number=TextArea(multiline=False,history=DummyHistory(),height=1,style='class:input',accept_handler=self.select_number)
        self.app.layout.focus(self.choice_number if kind=='endpoint' else self.picker);self.app.invalidate()

    def select_number(self,buffer):
        text=buffer.text.strip()
        if not text.isdecimal() or len(text)>len(str(len(self.picker.values))) or not 1<=int(text)<=len(self.picker.values):
            self.status=f'Choose a number from 1 to {len(self.picker.values)}.';return True
        self.picker.current_value=self.picker.values[int(text)-1][0]
        self.use_selection()
        return True

    def use_selection(self):
        value=self.picker.current_value
        if self.picker_kind=='endpoint':
            changed=value!=self.selected_endpoint
            self.selected_endpoint=value
            if changed or not self.fields['url'].text:
                base=ENDPOINT_REGISTRY[value].get('base_url','')
                self.fields['url'].text=base if base.startswith(('https://','http://')) and '{' not in base else ''
            if changed:
                self.fields['api_key_env'].text=''
                self.clear_key()
                self.protocol.current_value='default'
            self.update_default()
        elif value==-1:self.reset();return
        else:
            profile=copy.deepcopy(self.profiles[value]);self.original=profile
            self.selected_endpoint=profile['endpoint']
            for name,field in self.fields.items():
                field.text='' if name=='key' else str(profile.get(name,''))
            self.update_default()
            self.protocol.current_value=profile.get('api_format_mode',profile['api_format'])
        self.back_to_form()

    def reset(self):
        self.original=None
        for name,field in self.fields.items():field.text={'context_window':'8192'}.get(name,'')
        self.clear_key();self.update_default();self.protocol.current_value='default';self.back_to_form()

    def save(self):
        if self.page!='form':return
        if busy():
            self.status='Finish or cancel the current task before saving.';return
        data={name:field.text for name,field in self.fields.items() if name!='key'}
        data['endpoint']=self.selected_endpoint
        data['api_format']=self.protocol.current_value
        try:
            profile=save_profile(data,self.fields['key'].text,original=self.original)
        except ValueError as error:
            self.status=str(error);self.app.invalidate();return
        except OSError:
            self.status='Save failed. Check home-directory permissions; no connection was applied.'
            self.app.invalidate();return
        self.clear_key();self.original=profile
        if self.activate.checked:
            save_settings(active_endpoint=profile['endpoint'],endpoint=profile['endpoint'],
                          active_model=profile['model'],model=profile['model'])
        self.result_text=(profile['endpoint']+' / '+profile['model']+'\n'+profile['url']+
                          '\nSaved in '+str(user_home()/'.galam'/'endpoints.json')+
                          '\nKey variable: '+profile['api_key_env']+
                          '\nApplied in Galam now. Keys entered here stay in ~/.secrets. Stored keys get a guarded shell source line.')
        self.status='Saved and selected.' if self.activate.checked else 'Saved. Select the model from settings when ready.'
        self.page='result';self.app.layout.focus(self.result);self.app.invalidate()

    def run(self):
        try:self.app.run()
        finally:self.clear_key()
