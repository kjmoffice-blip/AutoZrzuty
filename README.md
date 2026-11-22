# AutoZrzuty 1.0.1

Program Python z polskim interfejsem tkinter: automatyczne zrzuty JPG z interwałem,
limitem, pauzą i wznowieniem; wybór monitora, regionu lub widocznego obszaru
aktywnego okna. Oferuje ręczny zrzut, skróty globalne, zasobnik, podgląd, eksport
sesji do PDF, pomijanie bezczynności i opcjonalną retencję własnych plików.
Ustawienia są zapisywane poza źródłami. Dane pozostają lokalnie; brak kont,
telemetrii i sieci podczas pracy. Retencja jest domyślnie wyłączona.

## Uruchomienie ze źródeł

Python 3.10+ z działającym tkinter. Na macOS użyj instalacji Python.org z Tcl/Tk.
Koła binarne przypiętych bibliotek wymagają zgodnego Pythona i systemu; wydanie
macOS zakłada minimum macOS 11. Nie testowano wszystkich starszych systemów.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r src/requirements.txt
python src/autozrzuty.py
python tools/check_core.py
```

`check_core.py` używa katalogów tymczasowych i syntetycznych obrazów: sprawdza
ustawienia, brak nadpisywania, PDF, retencję, blokadę instancji i logikę skrótów.
`check_gui.py`, `check_extended.py` oraz testy bundla wymagają uprawnień systemowych
i wykonują rzeczywiste zrzuty. Nie publikuj ich wynikowych obrazów.

Windows i Linux/X11 mają backendy w kodzie, ale nie były sprawdzane w tej
publikacji. Linux wymaga tkinter i sesji X11; Wayland nie jest wspierany w pełni.

## Odbudowa macOS Universal

```sh
python3 tools/build_macos.py
```

Wymaga macOS, Xcode Command Line Tools, internetu i Pythona uruchamiającego skrypt.
Pobiera przypięty instalator Python.org 3.13.16, sprawdza SHA-256 i rozpakowuje go
wyłącznie do `build-support/`. Instaluje zależności, łączy koła Pillow dla arm64 i
x86_64, buduje PyInstallerem `.app` i DMG. Nie zmienia systemowego Pythona.
Końcowa `.app` ma podpis ad hoc. Istniejący DMG nie jest nadpisywany.
`python3 tools/build_archive.py` pakuje czyste źródła; nie wymaga istniejącego DMG.

Licencje bibliotek są w `licenses/` i `THIRD_PARTY_LICENSES.txt`. Zachowano źródła
LGPL `pynput` i `pystray` w `source-libraries/`, aby wspierać ich modyfikację i
odbudowę. Każda zależność zachowuje własne warunki, również przy lokalnym pakowaniu.

## Uprawnienia na Macu

Włącz dla AutoZrzuty (lub Pythona/Terminala przy uruchamianiu źródeł) dostęp w **Ustawienia systemowe →
Prywatność i ochrona → Nagrywanie ekranu i dźwięku systemowego** (w niektórych
wersjach: Nagrywanie ekranu), zgodnie z [instrukcją Apple](https://support.apple.com/pl-pl/guide/mac-help/mchld6aa7d23/mac).
Program nie rejestruje dźwięku. Po zmianie uprawnień uruchom go ponownie.

Globalne skróty pynput mogą wymagać **Dostępności** oraz **Monitorowania wprowadzania**
dla AutoZrzuty (albo Pythona/Terminala przy uruchamianiu źródeł). [Dokumentacja pynput](https://pynput.readthedocs.io/en/latest/limitations.html)
opisuje te ograniczenia. Program nie zapisuje naciskanych klawiszy — obserwuje
wyłącznie skróty i czas ostatniej aktywności. Brak tych uprawnień nie blokuje
przycisków w oknie; na macOS/Windows bezczynność jest dodatkowo odczytywana przez
natywne API. Nie trzeba uruchamiać programu jako administrator/root.

Na Macach F8 może być klawiszem multimedialnym: użyj Fn+F8 albo zmień ustawienie
klawiszy funkcyjnych w systemie. Alternatywnie wybierz np. `<ctrl>+<alt>+s`.

## Korzystanie

1. Wybierz folder, jakość JPG, interwał oraz limit. Gotowe interwały: 5/10/30/60 s
   i 5 minut; limit: 30/50/100/200/500. Po wybraniu „Własny” aktywuje się pole obok.
   Własny interwał: 0,1–86400 s, liczba zrzutów: 1–1000000, jakość: 1–100.
2. Wybierz monitor lub tryb aktywnego okna. Domyślny jest monitor główny.
   „Odśwież” sprawdza aktualnie podłączone monitory.
3. Region zaznacz przez przeciągnięcie prostokąta na obrazie wybranego monitora,
   potem kliknij „Użyj regionu”. Esc anuluje. Współrzędne są przeliczane z obrazu
   na geometrię monitora, z uwzględnieniem ujemnego położenia i skali Retina.
4. **Start** rozpoczyna nową sesję i pierwszy JPG od razu. Następny zrzut następuje
   po interwale liczonym od zakończenia poprzedniego zapisu; program nie gromadzi
   zaległych zrzutów, gdy zapis trwa długo.
5. **Pauza / Wznów** zachowuje sesję i licznik. Wznowienie czeka pełny interwał.
   **Stop** zatrzymuje sesję i zachowuje pliki do eksportu. Operacja, która już
   rozpoczęła zapis, zostanie dokończona. Start po Stop tworzy nową sesję.
6. **Zrób teraz** zapisuje pojedynczy JPG, także przy pauzie lub przed Start.
   Wlicza się do limitu bieżącej sesji. Po osiągnięciu limitu nową sesję zaczyna Start.
   Ręczny zrzut po zmianie ustawień w stanie Stop rozpoczyna nową sesję.
7. Po limicie pojawia się komunikat w głównym oknie, okno jest przywracane,
   a program wysyła powiadomienie systemowe. System może je ukryć przez tryb
   skupienia lub ustawienia powiadomień; komunikat aplikacji pozostaje widoczny.
8. **Eksportuj sesję do PDF** zapisuje JPG w kolejności wykonania, po jednym
   na stronie, z zachowaniem proporcji. Eksport nie usuwa JPG. Jeśli zrzuty nadal
   działają, PDF obejmuje pliki dostępne w momencie zlecenia eksportu; do eksportu
   całej zakończonej sesji użyj go po Stop/limicie. Brakujący plik powoduje czytelny
   błąd, a poprzedni PDF pozostaje nienaruszony.
9. **Do zasobnika** ukrywa okno, pozostawiając program w pracy. Ikona aparatu
   ma menu pokazania okna, sterowania sesją, pojedynczego zrzutu i zakończenia.
   Zwykła minimalizacja również ukrywa do zasobnika, jeśli ikona jest dostępna.
   Gdy zasobnik nie działa, okno zostaje zminimalizowane do Docka/paska zadań.
   **Zakończ** zamyka program; macOS Command+Q również. Zamknięcie krzyżykiem
   domyślnie ukrywa okno; tę opcję można wyłączyć w głównym oknie.

F8 domyślnie: Start / Pauza / Wznów; Shift+F8: Stop. Oba skróty można zmieniać
(np. F9, `<ctrl>+<alt>+s`, `<ctrl>+<alt>+x`). Zmiany poprawnych ustawień zapisują
się automatycznie po 0,8 s oraz przed Start i zamknięciem. Nieprawidłowy skrót
pokazuje błąd; system może przechwycić skrót używany przez inne programy.

Ustawienia sesji są zablokowane w czasie działania i pauzy. Użyj Stop przed zmianą.
Na mniejszych ekranach ustawienia w głównym oknie przewija się pionowym paskiem.
Log pokazuje ostatnie 15 operacji. Mini podgląd przedstawia faktycznie zapisany zrzut.

## Bezczynność i automatyczne porządki

Obie funkcje są domyślnie wyłączone. Bezczynność oznacza brak ruchu myszy,
kliknięć, przewijania i wejścia klawiatury przez wybraną liczbę sekund. Pominięty
zrzut nie zwiększa licznika i nie zużywa limitu; po aktywności zrzuty wracają
przy następnym terminie. Ta reguła obejmuje też ręczne zrzuty.

Usuwanie starych JPG działa na początku nowej sesji, następnie najwyżej raz na
24 godziny w czasie sesji. Próg to dokładny wiek w dniach (doba = 86400 s).
Usuwane są **wyłącznie pliki zarejestrowane przez AutoZrzuty** w bazie
`.autozrzuty.sqlite3` w folderze docelowym. Cudze pliki, PDF, symlinki, zmienione
pliki i JPG z bieżącej sesji/eksportu są pomijane. Program nie usuwa całych folderów.
Porządków nie wykonuje się, gdy program jest wyłączony. Włączenie opcji jest zgodą
na usuwanie własnych wcześniejszych JPG spełniających ten warunek.

## Pliki i ustawienia

Pliki: `wybrany_folder/YYYY-MM-DD/screenshot_YYYYMMDD_HHMMSS_numer.jpg`.
Numer jest dopełniany zerami; jeśli nazwa jest zajęta, wybierany jest następny
numer — istniejący plik nie zostanie nadpisany. Podfolder daty jest wybierany
osobno dla każdego zrzutu, więc zmiana daty w nocy działa bez restartu.

`config.json` znajduje się poza źródłami programu:

- macOS: `~/Library/Application Support/AutoZrzuty/config.json`;
- Windows: `%APPDATA%\AutoZrzuty\config.json`;
- Linux: `$XDG_CONFIG_HOME/AutoZrzuty/config.json` lub `~/.config/AutoZrzuty/config.json`.

Własna lokalizacja: `python autozrzuty.py --config /ścieżka/config.json`.
Uszkodzony JSON powoduje powrót do domyślnych ustawień i wpis do logu.
Program uruchamia się z pełnym oknem konfiguracji. Jednoczesne uruchomienie
dwóch instancji z tym samym config.json jest blokowane. Lista plików sesji
nie jest przywracana po zamknięciu — JPG pozostają na dysku.


## Ograniczenia i weryfikacja

Aktywne okno jest widocznym prostokątem ekranu; zasłaniające okna mogą pojawić się
na obrazie. Chronione treści mogą być puste. Skróty zależą od uprawnień systemowych.
Bieżące wyniki publikowanych źródeł: `VERIFICATION.md`.

Historyczna kontrola wydania Developer ID z 26.10.2026: 14/14 prób ARM. Pełny test
GUI Rosetta przekroczył 55 sekund i nie został zaliczony; pomoc CLI Intel działała.
Nie testowano fizycznego Maca Intel, Windows, Linux, dwóch fizycznych monitorów,
skrótów z fizycznej klawiatury ani widoczności banera systemowego. Historyczne
wyniki wcześniejszych prób nie zastępują bieżącej kontroli źródeł.

## Publikacja i prawa

Repozytorium zawiera źródła i narzędzia deweloperskie. Nie zawiera instalatora,
certyfikatów, kluczy, prywatnych zrzutów ani nagrań. Kod aplikacji jest udostępniony na licencji **MIT** (`LICENSE`).
Informacje o prawach i zależnościach: `RIGHTS.md`.

Wcześniejsze wydanie binarne 1.0.1 było podpisane Developer ID i uzyskało
notaryzację Apple w październiku 2026. To informacja historyczna, która nie
obejmuje nowych lokalnych kompilacji z tego repozytorium. Lokalna kompilacja
nie jest automatycznie podpisana Developer ID ani notaryzowana. Publiczna
publikacja kodu 22.11.2026 nie zmienia funkcji, wyglądu ani wersji aplikacji.
