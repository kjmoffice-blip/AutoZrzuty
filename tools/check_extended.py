"""Dodatkowe rzeczywiste próby MSS: krótki interwał, idle i błędny monitor.
Wymaga dostępu do WindowServer; w procesie blokuje sieć TCP/UDP przez sandbox-exec.
"""
import importlib.util
import json
from pathlib import Path
import queue
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('autozrzuty', BASE/'src'/'autozrzuty.py')
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name]=a
spec.loader.exec_module(a)
results=[]

def record(name, condition):
    print(name, 'OK' if condition else 'FAIL', flush=True)
    results.append({'test':name,'passed':bool(condition)})
    assert condition, name


def wait_state(engine, state, seconds=15):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if engine.state==state:
            return
        time.sleep(0.05)
    raise AssertionError((state,engine.state))


with tempfile.TemporaryDirectory(dir=BASE/'checks') as tmp:
    events=queue.Queue()
    activity=a.Activity()
    engine=a.Engine(events,activity)
    engine.start()
    try:
        with a.capture_api() as sct:
            monitors,primary=a.monitors_and_primary(sct)
        cfg=a.Settings(folder=tmp,interval=0.1,limit=5,monitor=monitors[primary])
        engine.command('start',cfg)
        wait_state(engine,'completed')
        record('MSS: interwał 0,1 s i dokładny limit',len(engine.files)==5)
        time.sleep(0.4)
        record('MSS: brak zapisu po limicie',len(engine.files)==5)
        files=list(engine.files)
        a.export_pdf(files,Path(tmp)/'real.pdf')
        record('MSS: PDF offline z prawdziwych JPG',(Path(tmp)/'real.pdf').stat().st_size>1000)
        engine.command('stop')
        wait_state(engine,'stopped')
        # Pynput nie ma Dostępności, ale natywny timer macOS nadal działa.
        idle=activity.seconds()
        record('macOS: natywny czas bezczynności bez Dostępności',idle>=0)
        cfg=a.Settings(folder=tmp,interval=0.1,limit=1,monitor=monitors[primary],
                       idle_enabled=True,idle_seconds=1)
        time.sleep(1.2)
        engine.command('start',cfg)
        wait_state(engine,'running')
        time.sleep(0.7)
        record('MSS: bezczynność pomija JPG i nie zwiększa limitu',len(engine.files)==0)
        engine.command('stop')
        wait_state(engine,'stopped')
        # Nieistniejący monitor — rzeczywisty błąd wyboru, bez mockowania MSS.
        bad=a.Settings(folder=tmp,interval=0.1,limit=1,
                       monitor=dict(left=123456,top=0,width=100,height=100))
        engine.command('start',bad)
        time.sleep(0.8)
        record('MSS: brak monitora zatrzymuje sesję',engine.state=='stopped' and not engine.files)
        emitted=[]
        while not events.empty():
            emitted.append(events.get())
        record('MSS: czytelny błąd odłączonego monitora',any(
            kind=='error' and 'monitor' in text for kind,text in emitted))
    finally:
        engine.command('quit')
        engine.join(timeout=10)
        record('Wątki: uporządkowane zamknięcie',not engine.is_alive())
(BASE/'checks'/'extended-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
