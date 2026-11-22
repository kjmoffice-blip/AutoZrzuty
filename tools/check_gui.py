"""Integracja prawdziwego GUI/MSS na macOS. Nie symuluje uprawnień systemowych.
Zapisuje lokalne artefakty w checks/gui; wymaga dostępu do WindowServer.
"""
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('autozrzuty', BASE/'src'/'autozrzuty.py')
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a
spec.loader.exec_module(a)
OUT = BASE/'checks'/'gui'
OUT.mkdir(parents=True, exist_ok=True)
results = []
root = a.tk.Tk()
ui = a.Application(root, OUT/'config.json')
started = time.monotonic()
failed = []

def record(name, value=True):
    results.append(dict(test=name, passed=bool(value), seconds=round(time.monotonic()-started, 2)))
    print(name, 'OK' if value else 'FAIL', flush=True)
    if not value:
        failed.append(name)


def step(delay, callback):
    def guarded():
        try:
            callback()
        except Exception as exc:
            print('FAIL',callback.__name__,repr(exc),flush=True)
            record(callback.__name__, False)
    root.after(delay, guarded)


def initial():
    from AppKit import NSApplication
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    root.lift()
    record('GUI: monitory', bool(ui.monitors))
    record('GUI: tray gotowy', ui.tray_ready)
    record('GUI: brak uprawnień pynput obsłużony', not ui.hooks.ready)
    print('window',root.winfo_rootx(),root.winfo_rooty(),root.winfo_width(),root.winfo_height(),flush=True)
    ui.mode.set('monitor')
    ui.folder.set(str(OUT/'captures'))
    ui.limit_choice.set('Własny')
    ui.limit_custom.set('3')
    ui.interval_choice.set('5 sekund')
    ui.close_to_tray.set(False)
    ui.start()


def snapshot():
    with a.capture_api() as sct:
        region = dict(left=root.winfo_rootx(), top=root.winfo_rooty(),
                      width=root.winfo_width(),height=root.winfo_height())
        shot = sct.grab(region)
        image = a.Image.frombytes('RGB', shot.size, shot.rgb)
        image.save(OUT/'window.png')
        image.close()
    record('GUI: pierwszy prawdziwy JPG',ui.count == 1)
    ui.pause()


def paused():
    record('GUI: pauza przez >5 sekund bez kolejnego JPG', ui.count == 1 and ui.state == 'paused')
    ui.manual()


def resume():
    record('GUI: ręczny JPG podczas pauzy',ui.count == 2 and ui.state == 'paused')
    ui.start()


def no_catchup():
    record('GUI: wznowienie nie nadrabia pominiętych zrzutów', ui.count == 2 and ui.state == 'running')


def completed():
    record('GUI: automatyczny limit dokładnie 3',ui.count == 3 and ui.state == 'completed')
    record('GUI: podgląd',ui.photo is not None)
    record('GUI: przyciski po limicie',str(ui.now_button['state']) == 'disabled' and str(ui.start_button['state']) == 'normal')
    a.filedialog.asksaveasfilename = lambda **kw: str(OUT/'session.pdf')
    ui.choose_pdf()


def exported():
    pdf = OUT/'session.pdf'
    record('GUI: PDF wszystkich 3 zrzutów',pdf.exists() and len(re.findall(rb'/Type\s*/Page\b',pdf.read_bytes())) == 3)
    cfg, error = a.load_settings(OUT/'config.json')
    record('GUI: config.json odczyt', error is None and cfg.limit == 3 and cfg.interval == 5)
    root.geometry('740x600')
    root.after(300, small_layout)


def small_layout():
    body = ui.scroller.nametowidget(ui.scroller.itemcget(1, 'window'))
    panels = body.winfo_children()
    record('GUI: dostępne opcje na małym ekranie', panels[1].grid_info()['row'] == 1)
    root.geometry('1080x860')
    ui.choose_region()


def select_region():
    picker = next(w for w in root.winfo_children() if isinstance(w,a.RegionPicker))
    ox,oy = picker.offset
    dw,dh = picker.draw_size
    picker.begin(SimpleNamespace(x=ox+dw//4,y=oy+dh//4))
    picker.drag(SimpleNamespace(x=ox+dw//2,y=oy+dh//2))
    picker.finish(SimpleNamespace(x=ox+dw//2,y=oy+dh//2))
    record('GUI: zaznaczenie regionu',bool(picker.area))
    picker.commit()
    ui.start()  # po osiągnięciu limitu nowa sesja


def region_check():
    record('GUI: rzeczywisty JPG regionu',ui.count == 1 and ui.settings.mode == 'region')
    path = ui.engine.files[0]
    with a.Image.open(path) as image:
        print('region image',image.size,'region rect',ui.region,flush=True)
        record('GUI: region mniejszy od ekranu',image.width < ui.monitors[0]['width'] and image.height < ui.monitors[0]['height'])
    ui.stop()


def active_start():
    ui.mode.set('active')
    ui.manual()


def active_check():
    record('GUI: rzeczywisty JPG aktywnego okna',ui.count == 1 and ui.settings.mode == 'active')
    print('active rect',a.active_window_rect(),flush=True)
    ui.minimize()
    record('GUI: ukrywanie do tray',root.state() == 'withdrawn')
    ui.show()
    record('GUI: przywrócenie z tray',root.state() == 'normal')
    ui.about = lambda: None  # modal nie jest częścią tej automatycznej próby
    (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print('LOG',list(ui.logs),flush=True)
    ui.quit()

step(1400, initial)
step(2800, snapshot)
step(9500, paused)
step(10700, resume)
step(13500, no_catchup)
step(17500, completed)
step(19200, exported)
step(20500, select_region)
step(22000, region_check)
step(23100, active_start)
step(24500, active_check)
root.after(32000, ui.quit)  # ograniczenie czasu próby
root.mainloop()
raise SystemExit(1 if failed else 0)
