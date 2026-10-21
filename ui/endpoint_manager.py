"""Separate full-screen connection editor; no stdout inside the settings renderer."""
import copy
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.focus import focus_next,focus_previous
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit,VSplit,DynamicContainer,Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.scrollable_pane import ScrollablePane
from prompt_toolkit.widgets import TextArea,Label,Button,RadioList,Checkbox,Frame,Box
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.layout.dimension import Dimension

from config import CONFIG,AGENT_STATE,save_settings
from storage.endpoints import ENDPOINT_REGISTRY
from storage.user_endpoints import read_profiles,save_profile,user_home,endpoint_choices,normalize_url,infer_api_format,validate_profile,suggested_url


def button_row(*buttons):
    """Keep controls reachable on phone terminals without shrinking their labels."""
    return DynamicContainer(lambda:HSplit(list(buttons)) if get_app().output.get_size().columns<48
                            else VSplit(list(buttons),padding=1))


def busy():
    return bool(AGENT_STATE.get('task_active') or AGENT_STATE.get('active_message') or
                any(row.get('task')=='btw' for row in AGENT_STATE.get('active_subagents',[])))


class EndpointManager:
    def __init__(self,on_close=None):
        self.on_close=on_close
        self.original=None;self.status='';self.page='form';self.picker=None;self.step=0;self.auto_url=''
        choices=endpoint_choices()
        self.selected_endpoint=CONFIG.get('active_endpoint') if CONFIG.get('active_endpoint') in choices else choices[0]
        self.fields={name:TextArea(text=default,multiline=False,password=name=='key',
                                  style='class:input',height=1,history=DummyHistory())
                     for name,default in [('model',''),('url',''),
                       ('api_key_env',''),('key',''),('context_window','8192')]}
        self.protocol=RadioList(self.protocol_values(),select_on_focus=True)
        self.fields['model'].buffer.on_text_changed+=lambda buffer:self.update_default()
        self.fields['url'].buffer.on_text_changed+=self.url_changed
        self.activate=Checkbox('Use this model now',checked=True)
        self.key_bindings=KeyBindings()
        self.key_bindings.add('tab')(focus_next)
        self.key_bindings.add('s-tab')(focus_previous)
        self.key_bindings.add('c-s')(lambda event:self.advance())
        self.key_bindings.add('escape',eager=True)(self.escape)
        self.key_bindings.add('c-c')(self.escape)
        self.body=DynamicContainer(self.container)
        from ui.settings_theme import SETTINGS_STYLE
        self.app=Application(layout=Layout(HSplit([
            Window(FormattedTextControl('  GALAM / SETTINGS / CONNECTIONS'),height=1,style='class:title'),
            Frame(Box(self.body,padding=1),title='Model connection'),
            Window(FormattedTextControl(lambda:self.status),height=Dimension(min=1,max=3),wrap_lines=True,style='class:status'),
            Window(FormattedTextControl('Tab: move · Ctrl+S: next/save · Esc: back'),height=2,wrap_lines=True,style='class:hint')
        ])),key_bindings=self.key_bindings,full_screen=True,mouse_support=True,style=SETTINGS_STYLE)
        self.root=self.app.layout.container
        self.app.ttimeoutlen=.1
        self.form=self.make_form()
        self.result=HSplit([ScrollablePane(HSplit([Label('Connection saved'),Label(lambda:self.result_text)])),
                           button_row(Button('Add another',handler=self.reset),Button('Settings',handler=self.finish))])
        if busy():
            self.status='Finish or cancel the current task before changing model connections.'
            self.page='blocked'
        else:self.app.layout.focus(self.endpoint_button)

    def protocol_values(self):
        return [('default','Default — use entered URL'),('chat_completions','Chat Completions'),
                ('responses','Responses'),('ollama','Ollama Chat')]

    def update_default(self):
        self.protocol.values=self.protocol_values()

    def default_path(self):
        kind=infer_api_format(self.fields['url'].text,self.selected_endpoint,self.fields['model'].text)
        return 'Default uses this URL unchanged. Payload: '+kind.replace('_',' ')

    def make_form(self):
        self.endpoint_button=Button('Choose endpoint',handler=self.pick_endpoint,width=20)
        self.review=TextArea(read_only=True,scrollbar=True,wrap_lines=True,style='class:hint',height=Dimension(min=3))
        self.sections=[
            HSplit([Label('01 / MODEL'),Label(lambda:'Endpoint group: '+self.selected_endpoint),
                    button_row(self.endpoint_button,Button('Saved models',handler=self.pick_saved,width=16)),
                    Window(height=1),Label('Model ID'),self.fields['model']]),
            HSplit([Label('02 / REQUEST'),Label('Complete request URL'),self.fields['url'],
                    Label('The URL you enter is the URL we send to.'),Window(height=1),
                    Label('API format (request body only)'),self.protocol,Label(self.default_path)]),
            HSplit([Label('03 / CREDENTIALS'),Label('Key variable (blank = auto)'),self.fields['api_key_env'],
                    Label('API key (masked; blank keeps existing)'),self.fields['key'],
                    Label('Context window in tokens'),self.fields['context_window'],self.activate,
                    Label('Keys stay in ~/.secrets')]),
            HSplit([Label('04 / REVIEW'),self.review])]
        self.section=DynamicContainer(lambda:self.sections[self.step])
        self.next_button=Button('Next / Save',handler=self.advance)
        return HSplit([ScrollablePane(self.section,show_scrollbar=True),Window(height=1),
                       button_row(Button('Back',handler=self.previous),self.next_button,Button('Cancel',handler=self.finish))])

    def review_text(self):
        kind=self.protocol.current_value
        if kind=='default':kind=infer_api_format(self.fields['url'].text,self.selected_endpoint,self.fields['model'].text)
        return ('Endpoint group: '+self.selected_endpoint+'\nModel: '+self.fields['model'].text+
                '\nRequest URL: '+self.fields['url'].text+'\nPayload: '+kind.replace('_',' ')+
                '\nKey reference: '+(self.fields['api_key_env'].text or '(automatic)')+
                '\nUse now: '+('Yes' if self.activate.checked else 'No')+
                '\n\nSave writes this connection to your home directory.')

    def focus_step(self):
        if self.step==3:self.review.text=self.review_text()
        target=[self.endpoint_button,self.fields['url'],self.fields['api_key_env'],self.review][self.step]
        self.app.layout.focus(target);self.app.invalidate()

    def previous(self):
        if self.step:self.step-=1;self.status='';self.focus_step()
        else:self.finish()

    def advance(self):
        if self.page!='form':return
        if self.step==0 and not self.fields['model'].text.strip():
            self.status='Enter the model ID first.';self.app.invalidate();return
        if self.step==1:
            try:normalize_url(self.fields['url'].text,'chat_completions')
            except ValueError as error:self.status=str(error);self.app.invalidate();return
        if self.step==2:
            try:validate_profile(self.profile_data())
            except ValueError as error:self.status=str(error);self.app.invalidate();return
        if self.step==3:self.save();return
        self.step+=1;self.status='';self.focus_step()

    def profile_data(self):
        data={name:field.text for name,field in self.fields.items() if name!='key'}
        return {**data,'endpoint':self.selected_endpoint,'api_format':self.protocol.current_value}

    def url_changed(self,buffer):
        # Choosing a format changes only the body; typing a URL never changes selection.
        if hasattr(self,'app'):self.app.invalidate()

    def container(self):
        if self.page=='blocked':return HSplit([Label('Connection editor unavailable during active work.'),Button('Back',handler=self.finish)])
        if self.page=='result':return self.result
        if self.page=='picker':return self.picker_view
        # DynamicContainer is evaluated during Application initialization.
        return getattr(self,'form',Window())

    def finish(self):
        self.clear_key()
        if self.on_close:self.on_close()
        else:self.app.exit()

    def clear_key(self):
        # TextArea has no persistent history. Clear undo/history as well as visible text.
        self.fields['key'].buffer.reset()

    def escape(self,event):
        if self.page=='picker':self.back_to_form()
        elif self.page=='form':self.previous()
        else:self.finish()

    def back_to_form(self):
        self.page='form';self.status='';self.focus_step()

    def pick_saved(self):
        try:self.profiles=read_profiles()
        except (ValueError,OSError):
            self.status='Cannot read saved connections. Check ~/.galam/endpoints.json.';return
        values=[(-1,'+ New model')]+[(index,p['endpoint']+' / '+p['model']) for index,p in enumerate(self.profiles)]
        self.pick('saved',values)

    def pick_endpoint(self):
        self.pick('endpoint',[(name,f'{number}. {name}') for number,name in enumerate(endpoint_choices(),1)])

    def pick(self,kind,values):
        self.picker_kind=kind;self.picker=RadioList(values,default=self.selected_endpoint if kind=='endpoint' else None,select_on_focus=True);self.page='picker';self.status=''
        if kind=='endpoint':
            self.choice_number=TextArea(multiline=False,history=DummyHistory(),height=1,style='class:input',accept_handler=self.select_number)
        prompt=[Label('Number + Enter, or Tab for list'),self.choice_number] if kind=='endpoint' else []
        self.picker_view=HSplit([ScrollablePane(HSplit(prompt+[self.picker]),show_scrollbar=True),
                                button_row(Button('Use selection',handler=self.use_selection,width=18),Button('Back',handler=self.back_to_form))])
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
            from urllib.parse import urlsplit
            previous_url=self.fields['url'].text
            self.selected_endpoint=value
            # Only an empty/untouched prefill follows the provider selection.
            if not previous_url or previous_url==self.auto_url:
                self.auto_url=suggested_url(value)
                self.fields['url'].text=self.auto_url
                if urlsplit(previous_url).hostname!=urlsplit(self.auto_url).hostname:
                    self.fields['api_key_env'].text='';self.clear_key()
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
        self.original=None;self.step=0;self.auto_url=''
        for name,field in self.fields.items():field.text={'context_window':'8192'}.get(name,'')
        self.clear_key();self.update_default();self.protocol.current_value='default';self.back_to_form()

    def save(self):
        if self.page!='form':return
        if busy():
            self.status='Finish or cancel the current task before saving.';return
        data=self.profile_data()
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
