"""Testy bezpieczeństwa zapisów, retencji i eksportu; tylko w TemporaryDirectory."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import time
import unittest

SRC = Path(__file__).resolve().parents[1] / 'src' / 'autozrzuty.py'
spec = importlib.util.spec_from_file_location('autozrzuty', SRC)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class CoreChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_config_roundtrip_and_bad_input(self):
        cfg = app.Settings(folder=str(self.root), interval=0.1, limit=7, quality=1,
                           retention_enabled=True, retention_days=4, hotkey='<ctrl>+s')
        target = self.root/'config.json'
        app.atomic_text(target, json.dumps(app.asdict(cfg)))
        result, error = app.load_settings(target)
        self.assertIsNone(error)
        self.assertEqual(cfg, result)
        target.write_text('{broken json')
        result, error = app.load_settings(target)
        self.assertIsNotNone(error)
        for interval in (0, -1, float('nan'), float('inf')):
            cfg.interval = interval
            with self.assertRaises(ValueError):
                cfg.validate()

    def test_no_overwrite_pdf_and_failed_pdf_preserves_previous(self):
        store = app.CaptureStore(str(self.root))
        try:
            with app.Image.new('RGB', (300, 180), 'red') as image:
                first = store.save(image, 90, 1)
                original = first.read_bytes()
                second = store.save(image, 100, 1)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), original)
            self.assertRegex(first.name, app.FILE_RE)
            pdf = self.root/'session.pdf'
            app.export_pdf([first, second], pdf)
            content = pdf.read_bytes()
            self.assertTrue(content.startswith(b'%PDF-'))
            self.assertEqual(len(re.findall(rb'/Type\s*/Page\b', content)), 2)
            self.assertIn(b'/DCTDecode', content)  # obraz JPG, nie raster tekstu
            with self.assertRaises(FileNotFoundError):
                app.export_pdf([self.root/'missing.jpg'], pdf)
            self.assertEqual(content, pdf.read_bytes())
        finally:
            store.close()

    def test_retention_preserves_unowned_changed_symlink_and_current_files(self):
        store = app.CaptureStore(str(self.root))
        try:
            with app.Image.new('RGB', (30, 30), 'blue') as image:
                paths = [store.save(image, 90, i+1) for i in range(4)]
            untouched = paths[0].parent/'screenshot_20000101_010101_9999.jpg'
            untouched.write_bytes(b'user file')
            paths[1].write_bytes(b'modified by user')
            victim = self.root/'external.txt'
            victim.write_text('untouched')
            paths[2].unlink()
            paths[2].symlink_to(victim)
            store.db.execute('UPDATE captures SET created=?', (time.time()-40*86400,))
            store.db.commit()
            removed, skipped = store.cleanup(30, [paths[3]])
            self.assertEqual((removed, skipped), (1, 3))
            self.assertFalse(paths[0].exists())
            self.assertEqual(paths[1].read_bytes(), b'modified by user')
            self.assertTrue(paths[2].is_symlink())
            self.assertTrue(paths[3].exists())
            self.assertEqual(untouched.read_bytes(), b'user file')
            self.assertEqual(victim.read_text(), 'untouched')
        finally:
            store.close()

    def test_instance_lock_releases(self):
        lock = app.InstanceLock(self.root/'config.json')
        try:
            with self.assertRaises(RuntimeError):
                app.InstanceLock(self.root/'config.json')
        finally:
            lock.close()
        second = app.InstanceLock(self.root/'config.json')
        second.close()

    def test_hotkey_priority_and_repeat_without_os_input(self):
        hooks = app.InputHooks(queue.Queue(), app.Activity())
        class Keyboard:
            @staticmethod
            def canonical(key):
                return key
        hooks.keyboard = Keyboard()
        hooks.hotkeys = [({'shift', 'f8'}, 'stop'), ({'f8'}, 'toggle')]
        hooks.press('shift')
        hooks.press('f8')
        self.assertEqual(hooks.events.get_nowait(), ('hotkey', 'stop'))
        hooks.press('f8')
        self.assertTrue(hooks.events.empty())
        hooks.release('f8')
        hooks.release('shift')
        hooks.last_trigger = 0
        hooks.press('f8')
        self.assertEqual(hooks.events.get_nowait(), ('hotkey', 'toggle'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
