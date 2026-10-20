"""Full-screen resume selection, centering, text colors and saved preferences."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.data_structures import Size
from storage.conversations import save_history
from ui.conversation_picker import ConversationPicker,PALETTES


class ResumeDesignTests(unittest.TestCase):
    def test_centered_full_screen_color_selection_keeps_the_selected_conversation(self):
        async def exercise(root):
            save_history(root,'one',[{'role':'user','content':'CENTERED_CONVERSATION'},
                                    {'role':'assistant','content':'SAVED_RESPONSE'}])
            preference=root/'prefs'/'resume.json'
            with create_pipe_input() as pipe:
                picker=ConversationPicker(root,input=pipe,output=DummyOutput(),preferences_path=preference)
                task=asyncio.create_task(picker.app.run_async())
                try:
                    await asyncio.sleep(.06)
                    self.assertTrue(picker.app.full_screen)
                    screen=picker.app.renderer.last_rendered_screen
                    rows={y:''.join(row[x].char for x in sorted(row)) for y,row in screen.data_buffer.items()}
                    title_row=next(y for y,row in rows.items() if 'GALAM.AI' in row)
                    title=rows[title_row]
                    self.assertGreater(title_row,0)
                    self.assertGreater(title.index('GALAM.AI'),1)
                    self.assertIn('CENTERED_CONVERSATION','\n'.join(rows.values()))
                    old_style=picker.items()[0][0]
                    pipe.send_text('\x1b[C')
                    await asyncio.sleep(.06)
                    self.assertEqual(picker.palette[0],'Violet')
                    self.assertNotEqual(picker.items()[0][0],old_style)
                    self.assertEqual(json.loads(preference.read_text())['color'],'Violet')
                    pipe.send_text('\r')
                    self.assertEqual(await task,'one')
                    self.assertTrue((root/'one.json').exists())
                    restored=ConversationPicker(root,input=pipe,output=DummyOutput(),preferences_path=preference)
                    self.assertEqual(restored.palette[0],'Violet')
                finally:
                    if not task.done():picker.app.exit(result=None);await task
        with tempfile.TemporaryDirectory() as temp:asyncio.run(exercise(Path(temp)))

    def test_compact_terminal_and_color_wraparound(self):
        class SmallOutput(DummyOutput):
            def get_size(self):return Size(rows=20,columns=44)
        async def exercise(root):
            save_history(root,'one',[{'role':'user','content':'A SMALL CONVERSATION'}])
            with create_pipe_input() as pipe:
                picker=ConversationPicker(root,input=pipe,output=SmallOutput(),preferences_path=root/'prefs'/'resume.json')
                task=asyncio.create_task(picker.app.run_async())
                try:
                    await asyncio.sleep(.06)
                    screen=picker.app.renderer.last_rendered_screen
                    visible=''.join(c.char for row in screen.data_buffer.values() for c in row.values())
                    self.assertIn('GALAM.AI',visible)
                    self.assertIn('A SMALL CONVERSATION',visible)
                    picker.change_color(-1)
                    self.assertEqual(picker.palette[0],PALETTES[-1][0])
                    pipe.send_bytes(b'\x1b')
                    self.assertIsNone(await task)
                finally:
                    if not task.done():picker.app.exit(result=None);await task
        with tempfile.TemporaryDirectory() as temp:asyncio.run(exercise(Path(temp)))
