# Weryfikacja — 22.11.2026

Środowisko: macOS 26.7.1, Apple Silicon, Python 3.14.4, tkinter, osobne tymczasowe venv.

- Zainstalowano wszystkie przypięte zależności z `src/requirements.txt`.
- `python tools/check_core.py`: 5/5 testów zaliczone — ustawienia, priorytet skrótów,
  blokada instancji, brak nadpisywania JPG/PDF, retencja własnych plików i symlinki.
- Zweryfikowano składnię skryptów i archiwum źródeł, w tym zgodność bajtową plików.
- Kod aplikacji odpowiada źródłom 1.0.1 z jedną zmianą komunikatu o licencji:
  „Darmowy program do prywatnego użytku” zastąpiono informacją o MIT.
- Zachowano licencje i źródła zewnętrznych bibliotek LGPL. Zmieniono dokumentację
  i pakowanie źródeł, usuwając zależność od prywatnych raportów historycznych.

Nie powtarzano pełnej budowy PyInstaller/DMG ani rzeczywistych testów GUI,
przechwytywania ekranu, skrótów fizycznych, wielu monitorów i Rosetty.
Historycznie: wydanie Developer ID 26.10.2026 — 14/14 ARM; pełny test GUI Rosetta
przekroczył 55 s, więc niezaliczony; pomoc CLI Intel działała. Nie wykonywano nowej
notaryzacji ani testów Windows, Linux, fizycznego Maca Intel i wszystkich starszych
wersji macOS. Wersje zależności są zachowane z oryginalnego projektu.
