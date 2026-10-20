"""Centered full-screen resume browser. Only local history and UI preferences."""
from datetime import datetime
import json
from pathlib import Path
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from storage.conversations import recent_conversations, delete_history

PALETTES = [('Cyan','#38bdf8','#102535'),('Violet','#c084fc','#251635'),
            ('Mint','#6ee7b7','#102c25'),('Amber','#fbbf24','#302712'),
            ('Rose','#fb7185','#321923')]


class ConversationPicker:
    def __init__(self, directory, *, input=None, output=None, preferences_path=None):
        self.directory = directory
        self.rows = recent_conversations(directory)
        self.index, self.color_index = 0, 0
        self.notice = ''
        self.preferences_path = Path(preferences_path) if preferences_path else Path.home()/'.galam'/'resume-ui.json'
        try:
            saved=json.loads(self.preferences_path.read_text()).get('color')
            self.color_index=next((n for n,palette in enumerate(PALETTES) if palette[0]==saved),0)
        except (OSError,ValueError,AttributeError):
            pass
        keys = KeyBindings()
        @keys.add('up')
        def up(event):self.index=max(0,self.index-1)
        @keys.add('down')
        def down(event):self.index=min(max(0,len(self.rows)-1),self.index+1)
        @keys.add('pageup')
        def pageup(event):self.index=max(0,self.index-5)
        @keys.add('pagedown')
        def pagedown(event):self.index=min(max(0,len(self.rows)-1),self.index+5)
        @keys.add('left')
        def left(event):self.change_color(-1)
        @keys.add('right')
        @keys.add('c')
        def right(event):self.change_color(1)
        @keys.add('enter')
        def enter(event):
            if self.rows:event.app.exit(result=self.rows[self.index]['id'])
        @keys.add('D')
        def delete(event):self.delete_selected()
        @keys.add('escape')
        @keys.add('c-c')
        def cancel(event):event.app.exit(result=None)
        control=FormattedTextControl(self.items,focusable=True,
            get_cursor_position=lambda:Point(x=0,y=self.index*2))
        self.list_window=Window(control,height=lambda:self.list_height(),dont_extend_height=True)
        preview=ConditionalContainer(Window(FormattedTextControl(self.preview),height=5,wrap_lines=True),
            filter=Condition(lambda:self.app.output.get_size().rows>=24))
        center=HSplit([
            Window(FormattedTextControl(self.heading),height=3),
            Window(FormattedTextControl(self.colors),height=2),
            Window(FormattedTextControl(self.separator),height=1),
            self.list_window,
            Window(FormattedTextControl(self.separator),height=1),
            preview,
            Window(FormattedTextControl(self.footer),height=3,wrap_lines=True),
        ],width=lambda:self.pane_width(),height=lambda:self.pane_height())
        centered=VSplit([Window(width=Dimension(weight=1)),center,Window(width=Dimension(weight=1))],height=lambda:self.pane_height())
        layout=HSplit([Window(height=Dimension(weight=1)),centered,Window(height=Dimension(weight=1))])
        self.app=Application(layout=Layout(layout,focused_element=control),key_bindings=keys,
            full_screen=True,input=input,output=output,
            style=Style.from_dict({'':'bg:#09090b fg:#d4d4d8'}))
        self.app.ttimeoutlen=.05

    def pane_width(self):return max(16,min(84,self.app.output.get_size().columns-6))

    def list_height(self):
        return min(12,max(2,self.app.output.get_size().rows-20),max(2,len(self.rows)*2))

    def pane_height(self):
        return 10+self.list_height()+(5 if self.app.output.get_size().rows>=24 else 0)

    def clipped(self,text,extra=4):
        value,cells='',0
        for char in str(text).replace('\n',' '):
            cells+=get_cwidth(char)
            if cells>self.pane_width()-extra-1:return value+'…'
            value+=char
        return value

    @property
    def palette(self):return PALETTES[self.color_index]

    def heading(self):
        return [(f'fg:{self.palette[1]} bold','  GALAM.AI  /  RESUME\n'),
            ('fg:#71717a',f'  {len(self.rows)} conversations · newest first\n'),
            ('fg:#a1a1aa','  ↑/↓ select · PgUp/PgDn scroll')]

    def colors(self):
        tokens=[('fg:#71717a','  ←/→ color  ')]
        for n,(name,color,_) in enumerate(PALETTES):
            tokens.append((f'fg:{color}'+(' bold' if n==self.color_index else ''),
                           '◆ ' if n==self.color_index else '● '))
        tokens.append((f'fg:{self.palette[1]}',' '+self.palette[0]))
        return tokens

    def separator(self):return [(f'fg:{self.palette[1]}','─'*self.pane_width())]

    def items(self):
        if not self.rows:return [('fg:#71717a','  No saved conversations. Esc to return.')]
        result=[]
        for n,row in enumerate(self.rows):
            selected=n==self.index
            color,bg=self.palette[1:]
            style=f'bg:{bg} fg:{color} bold' if selected else 'fg:#a1a1aa'
            detail=f'bg:{bg} fg:#a1a1aa' if selected else 'fg:#52525b'
            timestamp=datetime.fromtimestamp(row['updated']).strftime('%d %b %Y · %H:%M')
            result.append((style,(' ❯ ' if selected else '   ')+self.clipped(row['title'])+'\n'))
            result.append((detail,'   '+self.clipped(f"{timestamp} · {row['turns']} prompts · {row['id']}")+'\n'))
        return result

    def preview(self):
        if not self.rows:return ''
        return [(f'fg:{self.palette[1]} bold','  RECENT MESSAGES\n'),
                ('fg:#a1a1aa',self.rows[self.index]['preview'])]

    def footer(self):
        return [(f'fg:{self.palette[1]}','  Enter resume · Shift+D delete\n'),
            ('fg:#71717a','  Esc back · ←/→ or C color\n'),
            ('fg:#fbbf24','  '+self.clipped(self.notice or 'Shift+D deletes history permanently'))]

    def change_color(self,step):
        self.color_index=(self.color_index+step)%len(PALETTES)
        from engine.result_store import atomic_bytes
        try:
            self.preferences_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            atomic_bytes(self.preferences_path,json.dumps({'color':self.palette[0]}).encode())
        except OSError:
            self.notice='Color changed for this visit; preference could not be saved.'

    def delete_selected(self):
        if not self.rows:return
        row=self.rows[self.index]
        try:
            deleted=delete_history(self.directory,row['id'])
            self.rows.pop(self.index)
            self.index=min(self.index,max(0,len(self.rows)-1))
            self.notice=f"Permanently deleted {row['id']}." if deleted else 'Conversation was already removed.'
        except (OSError,ValueError) as error:
            self.notice=f'Could not delete selected conversation: {error}'

    def run(self):return self.app.run()


def choose_conversation(directory):return ConversationPicker(directory).run()
