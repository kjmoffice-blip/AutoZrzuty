"""Kontrola uruchamiana wewnątrz zbudowanego .app, tylko przez --verify-build.
Nie nadaje uprawnień. Zapisuje rzeczywiste wyniki; brak uprawnień to ograniczenie,
a nie zaliczony test zrzutów. Nie jest częścią normalnego korzystania z aplikacji.
"""
import json
import platform
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace


def schedule(ui, output):
    import __main__ as app
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    root=ui.root
    ui.verify_mode=True
    results={'frozen':bool(getattr(sys,'frozen',False)), 'architecture':platform.machine(),
             'python':sys.version.split()[0], 'tk':app.tk.TkVersion,'checks':[],'limitations':[]}
    began=time.monotonic()

    def record(name, ok, detail=''):
        results['checks'].append({'test':name,'passed':bool(ok),'detail':str(detail),
                                  'seconds':round(time.monotonic()-began,2)})

    def step(ms, fn):
        def guarded():
            try:
                fn()
            except Exception as exc:
                record(fn.__name__,False,repr(exc))
                finish()
        root.after(ms,guarded)

    def finish():
        results['logs']=list(ui.logs)
        output.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
        ui.quit()

    def initial():
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        root.lift()
        record('GUI + konfiguracja', bool(ui.monitors))
        record('Zasobnik',ui.tray_ready)
        if not ui.hooks.ready:
            results['limitations'].append('Globalne skróty: brak Dostępności, sprawdzono komunikat.')
        ui.folder.set(str(output.parent/'captures'))
        ui.limit_choice.set('Własny')
        ui.limit_custom.set('3')
        ui.interval_choice.set('5 sekund')
        ui.mode.set('monitor')
        ui.close_to_tray.set(False)
        # Nie otwieraj monitów o nowe uprawnienia w automatycznym teście.
        try:
            app.screen_permission()
        except PermissionError as exc:
            results['limitations'].append(str(exc))
            image=app.Image.new('RGB',(80,50),'#24b898')
            store=app.CaptureStore(str(output.parent/'sample-data'))
            try:
                file=store.save(image,90,1)
                app.export_pdf([file],output.parent/'sample.pdf')
                record('Biblioteki JPG/PDF: próbka bez zrzutu ekranu',True)
            finally:
                store.close(); image.close()
            ui.notice.set(str(exc))
            finish()
            return
        ui.start()
        step(1800,pause)
        step(7600,manual)
        step(8700,resume)
        step(14400,complete)
        step(15700,exported)
        step(17000,region_select)
        step(18500,region_check)
        step(19400,active)
        step(21200,final)

    def pause():
        record('Pierwszy rzeczywisty JPG',ui.count==1)
        ui.pause()

    def manual():
        record('Pauza >5 s bez kolejnego zrzutu',ui.count==1 and ui.state=='paused')
        ui.manual()

    def resume():
        record('Ręczny JPG w pauzie',ui.count==2 and ui.state=='paused')
        ui.start()

    def complete():
        record('Dokładny limit 3 / interwał 5 s',ui.count==3 and ui.state=='completed')
        record('Podgląd rzeczywistego JPG',ui.photo is not None)
        app.filedialog.asksaveasfilename=lambda **kw:str(output.parent/'session.pdf')
        ui.choose_pdf()

    def exported():
        content=(output.parent/'session.pdf').read_bytes()
        record('PDF 3 strony',len(re.findall(rb'/Type\s*/Page\b',content))==3)
        ui.choose_region()

    def region_select():
        picker=next(w for w in root.winfo_children() if isinstance(w,app.RegionPicker))
        ox,oy=picker.offset
        dw,dh=picker.draw_size
        picker.begin(SimpleNamespace(x=ox+dw//4,y=oy+dh//4))
        picker.drag(SimpleNamespace(x=ox+dw//2,y=oy+dh//2))
        picker.finish(SimpleNamespace(x=ox+dw//2,y=oy+dh//2))
        picker.commit()
        ui.start()

    def region_check():
        record('JPG regionu',ui.count==1 and ui.settings.mode=='region')
        with app.Image.open(ui.engine.files[0]) as im:
            record('Rozmiar regionu',im.width<ui.monitors[0]['width'] and im.height<ui.monitors[0]['height'])
        ui.stop()

    def active():
        ui.mode.set('active')
        ui.manual()

    def final():
        record('JPG aktywnego okna',ui.count==1 and ui.settings.mode=='active')
        ui.minimize()
        record('Ukrywanie do tray',root.state()=='withdrawn')
        ui.show()
        record('Przywracanie z tray',root.state()=='normal')
        cfg,error=app.load_settings(ui.config_path)
        record('Odczyt config.json',error is None and cfg.limit==3)
        # Obraz wyłącznie własnego okna; prywatne zrzuty nie trafiają do dystrybucji.
        with app.capture_api() as sct:
            shot=sct.grab(dict(left=root.winfo_rootx(),top=root.winfo_rooty(),
                             width=root.winfo_width(),height=root.winfo_height()))
            image=app.Image.frombytes('RGB',shot.size,shot.rgb)
            image.save(output.parent/'window.png'); image.close()
        finish()

    step(1500,initial)
    root.after(35000,finish)
