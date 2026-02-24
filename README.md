**QW2 Traceability — dokumentacja i instrukcja**

- **Cel:** Aplikacja okienkowa do wspomagania procesu traceability w linii produkcyjnej
	(skanowanie kodów DMC, walidacja stacków, agregacja sztuk na paletach, eksport CSV).
- **Repozytorium:** zawiera dwa główne moduły: `main.py` (GUI + logika) i `logger.py` (pomocniczy logger).

**Wymagania**
- Python 3.10+ (dla `zoneinfo` i typów użytych w kodzie)
- Zależności: `PyQt5`, `requests`

Instalacja (zalecane w wirtualnym środowisku):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt  # lub: pip install PyQt5 requests
```

Uruchamianie:
```powershell
python main.py
```

TRYB TESTOWY
- W pliku `main.py` jest flaga `TEST_MODE`. Gdy `TEST_MODE = True`, aplikacja omija wywołania sieciowe
	i używa funkcji `fake_get_matching_info` i `fake_check_inspect` do symulacji odpowiedzi — przydatne
	do uruchomienia aplikacji bez dostępu do intranetu.

Przegląd plików i funkcji
**Plik:** [main.py](main.py)
- Cel: główny interfejs i logika aplikacji.
- Stałe i konfiguracje:
	- `HOSTNAME`, `USERNAME`, `TEST_MODE` — detekcja środowiska i tryb testowy.
	- `dmc_regex`, `badge_pattern` — wzorce walidacji skanów.
- Funkcje testowe (tylko w TEST_MODE): `fake_get_matching_info`, `fake_check_inspect` — zwracają przykładowe dane.
- Klasy GUI:
	- `LoginDialog(QDialog)` — proste logowanie przez skan badge; walidacja formatu.
	- `SettingsDialog(QDialog)` — edycja katalogów (`local_dir`, `sync_dir`, `pallet_dir`) i licznika.
	- `UnassignedDialog(QDialog)` — przegląd i edycja nieprzypisanych sztuk w paletach; operacje dodaj/usuń/przenieś.
	- `PalletDialog(QDialog)` — dialog przypisania palety (kod palety i zmiana).
	- `StatsDialog(QDialog)` — pokazuje statystyki palet za ostatnie 7 dni.
- `TraceabilityApp(QMainWindow)` — główna klasa aplikacji (najważniejsze metody):
	- `__init__` — inicjalizacja ustawień (`QSettings`), wczytanie `unassigned.json`, inicjalizacja UI, logowanie.
	- `init_ui` — tworzy layout, toolbar, menu i widgety główne (pole do skanowania DMC etc.).
	- `init_login` — pokazuje `LoginDialog` i ustawia `self.badge`.
	- `on_dmc_enter` — wywoływane po skanowaniu DMC: waliduje format, pyta intranet o `child_serno`, przełącza UI na skan stacka.
	- `on_child_enter` — obsługuje logikę po skanie stacka: porównuje, sprawdza EOL, przygotowuje wpisy CSV, zapisuje i synchronizuje pliki.
	- `get_matching_info(serno, line=436)` — wykonuje GET do intranetu `/getMaching/` i zwraca JSON zasobu (może zwrócić None przy błędzie).
	- `check_inspect(serno, inspect, line, machine)` — GET do `/getInspect/` zwracający listę inspekcji lub None.
	- `sync_file(local_path)` — kopiuje wygenerowany CSV do katalogu `sync_dir`.
	- `start_new_pallet`, `_do_assign` — tworzenie/kończenie palety i zapis palet jako CSV w `pallet_dir`.
	- `count_pallets_for_current_shift`, `collect_stats` — liczniki i statystyki palet według zmian.
	- wiele metod pomocniczych: `_load_unassigned`, `_save_unassigned`, `generate_pallet_id`, `get_last_pallet_id`, `reset_counter`, `remove_last_piece`, `skip_stack_scan`, `_log_mismatch`.

Uwagi dotyczące działania (flow):
- Użytkownik skanuje kod DMC → `on_dmc_enter`:
	- Walidacja formatu DMC
	- Zapytanie do intranetu o `child_serno` → jeśli brak → komunikat o błędzie
	- Wyświetlenie `child_serno`, oczekiwanie na skan stacka
- Użytkownik skanuje stack (child_serno) → `on_child_enter`:
	- Jeżeli stack nie pasuje → proces oddania sztuki jakości (wymagany badge) i zapis pliku mismatch
	- Jeżeli stack pasuje → sprawdzenie EOL (Status) → decyzja OK/NOK
	- Zapis pliku CSV inspekcji do `local_dir` + wywołanie `sync_file`
	- Jeśli EOL OK → inkrementacja `good_counter` i dodanie do `unassigned[current_pallet]`
	- Jeżeli licznik osiągnie 72 → prompt przypisania palety (`_do_assign`) i utworzenie nowej palety

**Plik:** [logger.py](logger.py)
- Cel: prosty, odporny logger z buforowaniem na wypadek braku zasobu (np. udział sieciowy).
- Kluczowe klasy i funkcje:
	- `_BufferedSink` — buforuje linie dziennika gdy zapis bezpośredni nie powiedzie się; zapisuje do `root/logs/` z nazewnictwem dziennym.
	- `init_logging(base_dir, app_name, level=logging.INFO, extra_dir=None)` — inicjalizuje loggera; tworzy lokalne i (opcjonalnie) sieciowe sinki.
	- `log_event(name, level='info', **kwargs)` — główne API: zapisuje zdarzenia, specjalnie traktuje zdarzenia `key` (bufferowanie sekwencji klawiszy).
	- `flush_pending_events(reason)` — wymusza zapis zaległych zdarzeń (klucze i sinki).
	- `set_extra_log_dir(path)` — wskazuje dodatkowy katalog (np. sieciowy) i konfiguruje buforowane sinki.

Schemat działania (mermaid)
```mermaid
flowchart TD
	A[Start: uruchomienie aplikacji] --> B{TEST_MODE?}
	B -- Tak --> B1[Podstawowe wartości testowe]
	B -- Nie --> C[Wywołaj LoginDialog]
	C --> D[Użytkownik skanuje DMC]
	D --> E[on_dmc_enter: pobierz child_serno z intranetu]
	E --> F[Pokazanie child_serno i oczekiwanie na stack]
	F --> G[on_child_enter: walidacja stacka i check_inspect(EOL)]
	G --> H{EOL OK?}
	H -- Tak --> I[Zapis CSV, zwiększ licznik, dodaj do unassigned]
	H -- Nie --> J[Zapis mismatch, zgłoszenie do jakości]
	I --> K{good_counter >= 72?}
	K -- Tak --> L[Wywołaj _do_assign -> zapisz paletę CSV]
	K -- Nie --> M[Powrót do kroku D]
	J --> M
```

Jak modyfikować kod
- Aby zmienić schemat zapisu CSV, zaktualizuj fragmenty w `on_child_enter` i `_log_mismatch` w `main.py`.
- Aby zmienić lokalizacje logów lub włączyć sync na udział sieciowy, użyj `logger.init_logging` i `logger.set_extra_log_dir`.
- Do testów bez intranetu ustaw `TEST_MODE = True` w górnej części `main.py`.

Najczęstsze problemy i debug
- Brak GUI po uruchomieniu: upewnij się, że `PyQt5` jest zainstalowane i uruchamiasz skrypt w środowisku z aktywną display (Windows: uruchom bez dodatkowych zmian).
- Błędy sieciowe przy `get_matching_info`/`check_inspect`: aplikacja pokazuje QMessageBox z treścią błędu; w trybie produkcyjnym należy zapewnić dostęp do intranetu lub włączyć tryb testowy.

Pliki do edycji:
- [main.py](main.py) — logika aplikacji i GUI
- [logger.py](logger.py) — pomocniczy logger z buforowaniem

Jeśli chcesz, mogę teraz:
- uruchomić szybki test w trybie `TEST_MODE`,
- dodać obszerne docstringi do poszczególnych metod w `main.py`,
- lub przygotować `requirements.txt` i prosty skrypt uruchamiający.

— koniec README —
