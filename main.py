"""
Traceability GUI application (BSG H66 2 QW2)

Ten moduł zawiera aplikację okienkową PyQt5 do obsługi procesu
traceability (skanowanie kodów DMC, weryfikacja stacków, tworzenie
palet i eksport plików CSV). Plik definiuje dialogi pomocnicze oraz
klasę `TraceabilityApp` zarządzającą logiką i interfejsem.

Kluczowe elementy:
- `LoginDialog`, `SettingsDialog`, `UnassignedDialog`, `PalletDialog`, `StatsDialog` – dialogi GUI
- `TraceabilityApp` – główna klasa aplikacji z metodami:
    - `init_ui`, `init_login` – inicjalizacja UI i logowania
    - `on_dmc_enter`, `on_child_enter` – logika skanowania i weryfikacji
    - `get_matching_info`, `check_inspect` – pobieranie danych z intranetu
    - `start_new_pallet`, `_do_assign`, `sync_file` – operacje na paletach i plikach

Plik zawiera również flagę TEST_MODE, która pozwala na uruchomienie
aplikacji bez dostępu do serwerów intranetu (przydatne do testów).
"""

import sys
import re
import csv
import os
import json
from turtle import color
import requests
import shutil
import socket
import getpass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit,
    QVBoxLayout, QHBoxLayout, QMessageBox, QFrame,
    QPushButton, QDialog, QDialogButtonBox,
    QFormLayout, QFileDialog, QSpinBox, QListWidget,
    QTabWidget, QComboBox, QInputDialog, 
    QAbstractItemView, QShortcut, QTableWidget, QTableWidgetItem,
    QMainWindow, QAction, QToolBar, QSizePolicy
)
from PyQt5.QtGui import QFont, QPalette, QColor, QRegExpValidator, QKeySequence
from PyQt5.QtCore import Qt, QTimer, QRegExp, QSettings, QEvent


# ============ TRYB TESTOWY =============
HOSTNAME = socket.gethostname()
USERNAME = getpass.getuser()
TEST_MODE = False #(HOSTNAME == "N07WNB1559") or (USERNAME == "andrzej.florek")

def fake_get_matching_info(self, serno, line=436):
    # zawsze zwraca child_serno „TESTCHILD…”
    return {"child_serno": "1"}

def fake_check_inspect(self, serno, inspect, line, machine):
    # ⋅QW2_child_serno→ pusty (nie było skanu)
    if inspect == "QW2_child_serno":
        return []
    # ⋅Status EOL→ zawsze OK
    return [{"inspectdate": "2025-06-26 12:00:00", "judge": "0"}]
# ============ TRYB TESTOWY =============

# Wzorce
dmc_regex = re.compile(r'^\d+VIT\d{14}$')
badge_pattern = QRegExp(r'^[A-Z]-\d{4,5}$')

class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Zeskanuj kod badge")
        self.badge = None
        layout = QFormLayout(self)
        self.input_badge = QLineEdit()
        self.input_badge.setPlaceholderText("R-7015")
        self.input_badge.setValidator(QRegExpValidator(badge_pattern))
        layout.addRow("Badge:", self.input_badge)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        text = self.input_badge.text().strip()
        if not badge_pattern.exactMatch(text):
            QMessageBox.warning(self, "Błąd", "Niepoprawny format badge (R-7015).")
            return
        self.badge = text
        TraceabilityApp.update_user_menu
        super().accept()

class SettingsDialog(QDialog):
    def __init__(self, parent, local_dir, sync_dir, pallet_dir, current_counter):
        super().__init__(parent)
        self.setWindowTitle("Ustawienia")
        self.local_dir = local_dir
        self.sync_dir = sync_dir
        self.pallet_dir = pallet_dir

        # Local folder selection
        self.lbl_local = QLabel(self.local_dir)
        self.lbl_local.setWordWrap(True)
        btn_local = QPushButton("Wybierz folder lokalny")
        btn_local.clicked.connect(self.select_local)
        row_local = QHBoxLayout()
        row_local.addWidget(QLabel("Folder lokalny:"))
        row_local.addWidget(self.lbl_local)
        row_local.addWidget(btn_local)

        # Sync folder selection
        self.lbl_sync = QLabel(self.sync_dir)
        self.lbl_sync.setWordWrap(True)
        btn_sync = QPushButton("Wybierz folder traceability")
        btn_sync.clicked.connect(self.select_sync)
        row_sync = QHBoxLayout()
        row_sync.addWidget(QLabel("Folder traceability:"))
        row_sync.addWidget(self.lbl_sync)
        row_sync.addWidget(btn_sync)

        # Pallet folder selection
        self.lbl_pallet = QLabel(self.pallet_dir)
        self.lbl_pallet.setWordWrap(True)
        btn_pallet = QPushButton("Wybierz folder palet")
        btn_pallet.clicked.connect(self.select_pallet)
        row_pallet = QHBoxLayout()
        row_pallet.addWidget(QLabel("Folder palet:"))
        row_pallet.addWidget(self.lbl_pallet)
        row_pallet.addWidget(btn_pallet)

        self.spin_counter = QSpinBox()
        self.spin_counter.setRange(0, 72)             # zakres wg potrzeb
        self.spin_counter.setValue(current_counter)
        lbl_cnt = QLabel("Stan licznika:")
        row_counter = QHBoxLayout()
        row_counter.addWidget(lbl_cnt)
        row_counter.addWidget(self.spin_counter)

        # Dialog buttons
        dlg_buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dlg_buttons.accepted.connect(self.accept)
        dlg_buttons.rejected.connect(self.reject)

        # Główny layout
        main_layout = QVBoxLayout(self)
        main_layout.addLayout(row_local)
        main_layout.addLayout(row_sync)
        main_layout.addLayout(row_counter)
        main_layout.addLayout(row_pallet) 
        main_layout.addWidget(dlg_buttons)

    def select_local(self):
        d = QFileDialog.getExistingDirectory(self, "Wybierz folder lokalny", self.local_dir)
        if d:
            self.local_dir = d
            self.lbl_local.setText(d)

    def select_sync(self):
        d = QFileDialog.getExistingDirectory(self, "Wybierz folder traceability", self.sync_dir)
        if d:
            self.sync_dir = d
            self.lbl_sync.setText(d)

    def select_pallet(self):
        d = QFileDialog.getExistingDirectory(self, "Wybierz folder palet", self.pallet_dir)
        if d:
            self.pallet_dir = d
            self.lbl_pallet.setText(d)

    def accept(self):
        # zapisujemy wybraną przez użytkownika wartość licznika
        self.new_counter = self.spin_counter.value()
        super().accept()

class UnassignedDialog(QDialog):
    def __init__(self, parent, unassigned_dict):
        super().__init__(parent)
        self.setWindowTitle("Nieprzypisane kody")
        self.resize(550, 400)
        self.unassigned = unassigned_dict  # dict: {pallet_id: [list]}
        self.list_widgets = []
        self.pallet_ids = list(self.unassigned.keys())

        main = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for idx, (pid, items) in enumerate(self.unassigned.items()):
            page = QWidget()
            lay = QVBoxLayout(page)
            lw = QListWidget()
            lw.setSelectionMode(QAbstractItemView.ExtendedSelection)
            for itm in items:
                lw.addItem(f"{itm['dmc']} → {itm['stack']}")
            lay.addWidget(lw)
            self.list_widgets.append(lw)

            # Przyciski Dodaj / Usuń / Przenieś
            btn_row = QHBoxLayout()
            btn_add = QPushButton("Dodaj")
            btn_rm  = QPushButton("Usuń")
            btn_mv  = QPushButton("Przenieś")
            btn_row.addWidget(btn_add)
            btn_row.addWidget(btn_rm)
            btn_row.addWidget(btn_mv)
            lay.addLayout(btn_row)
            btn_add.clicked.connect(lambda _, i=idx: self._add_item(i))
            btn_rm .clicked.connect(lambda _, i=idx: self._remove_item(i))
            btn_mv .clicked.connect(lambda _, i=idx: self._move_item(i))

            self.tabs.addTab(page, f"Paleta {pid} ({len(items)})")
        main.addWidget(self.tabs)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Ok).setText("Przypisz paletę")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        main.addWidget(btns)

    def selected_pallet_id(self):
        return self.pallet_ids[self.tabs.currentIndex()]

    def selected_items(self):
        idx = self.tabs.currentIndex()
        lw = self.list_widgets[idx]
        sel = lw.selectedIndexes()
        return [self.unassigned[self.pallet_ids[idx]][i.row()] for i in sel]

    def selected_chunk(self):
        idx = self.tabs.currentIndex()
        return self.unassigned[self.pallet_ids[idx]]

    def _add_item(self, idx):
        dmc, ok1 = QInputDialog.getText(self, "Dodaj", "Kod DMC klienta:")
        if not ok1 or not dmc: return
        stack, ok2 = QInputDialog.getText(self, "Dodaj", "Kod stacka:")
        if not ok2 or not stack: return
        itm = {"dmc": dmc.strip(), "stack": stack.strip()}
        self.unassigned[self.pallet_ids[idx]].append(itm)
        self._refresh_tab(idx)

    def _remove_item(self, idx):
        lw = self.list_widgets[idx]
        rows = sorted({i.row() for i in lw.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            self.unassigned[self.pallet_ids[idx]].pop(row)
        self._refresh_tab(idx)

    def _move_item(self, idx):
        lw = self.list_widgets[idx]
        rows = sorted({i.row() for i in lw.selectedIndexes()}, reverse=True)
        if not rows:
            return

        # Pytanie o docelową paletę (lista istniejących, poza aktualną)
        choices = [pid for i, pid in enumerate(self.pallet_ids) if i != idx]
        if not choices:
            QMessageBox.information(self, "Brak palety", "Nie ma innej palety do przeniesienia.")
            return

        dest_pid, ok = QInputDialog.getItem(
            self, "Przenieś do palety",
            "Wybierz docelową paletę:",
            choices,
            0, False
        )
        if not ok or not dest_pid:
            return

        # Przenosimy wybrane pozycje
        for row in rows:
            itm = self.unassigned[self.pallet_ids[idx]].pop(row)
            self.unassigned[dest_pid].append(itm)

        # Odśwież obie zakładki
        dest_idx = self.pallet_ids.index(dest_pid)
        self._refresh_tab(idx)
        self._refresh_tab(dest_idx)

    def _refresh_tab(self, idx):
        tab = self.tabs.widget(idx)
        lw = tab.findChild(QListWidget)
        lw.clear()
        for itm in self.unassigned[self.pallet_ids[idx]]:
            lw.addItem(f"{itm['dmc']} → {itm['stack']}")
        self.tabs.setTabText(
            idx,
            f"Paleta {self.pallet_ids[idx]} ({len(self.unassigned[self.pallet_ids[idx]])})"
        )

class PalletDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Przypisz paletę")
        layout = QFormLayout(self)

        # skan palety
        self.input_pallet = QLineEdit()
        self.input_pallet.setPlaceholderText("Kod palety")
        layout.addRow("Paleta:", self.input_pallet)

        # zmiana: edytowalny combo z podpowiedziami
        self.combo_shift = QComboBox()
        self.combo_shift.setEditable(True)
        # lista najczęstszych opcji
        for opt in ("A1", "A2", "B1", "B2", "C1", "C2", "D1", "D2"):
            self.combo_shift.addItem(opt)
        self.combo_shift.setCurrentText("")  # domyślnie puste
        layout.addRow("Zmiana:", self.combo_shift)

        # przyciski OK/Anuluj
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        pal = self.input_pallet.text().strip()
        zm = self.combo_shift.currentText().strip()
        if not pal:
            QMessageBox.warning(self, "Błąd", "Podaj kod palety.")
            return
        if not zm:
            QMessageBox.warning(self, "Błąd", "Podaj zmianę (np. C1, A2).")
            return
        self.pallet_code = pal
        self.shift = zm
        super().accept()

class StatsDialog(QDialog):
    def __init__(self, parent, stats):
        super().__init__(parent)
        self.setWindowTitle("Statystyki palet - ostatnie 7 dni")
        self.resize(450, 300)
        layout = QVBoxLayout(self)
        tbl = QTableWidget()
        tbl.setColumnCount(3)
        tbl.setHorizontalHeaderLabels(["Data", "Dzienna (6-18)", "Nocna (18-6)"])
        tbl.setRowCount(len(stats))
        for i, (date, val) in enumerate(sorted(stats.items())):
            tbl.setItem(i, 0, QTableWidgetItem(date))
            tbl.setItem(i, 1, QTableWidgetItem(str(val["dzienna"])))
            tbl.setItem(i, 2, QTableWidgetItem(str(val["nocna"])))
        tbl.resizeColumnsToContents()
        layout.addWidget(tbl)

class TraceabilityApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # Ustawiamy, żeby widget odbierał klawisze nawet jeśli focus jest gdzie indziej:
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        QApplication.instance().installEventFilter(self)
        # ustawienia persistent
        self.settings = QSettings("NMAP", "BSG H66 2 QW2 Traceability App")
        self.local_dir = self.settings.value("local_dir", os.getcwd())
        
        counter_json = os.path.join(self.local_dir, "counter.json")
        if os.path.exists(counter_json):
            try:
                with open(counter_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    good_counter = int(data.get("good_counter", 0))
                self.settings.setValue("good_counter", good_counter)
                os.remove(counter_json)
            except Exception as e:
                good_counter = 0
        else:
            good_counter = int(self.settings.value("good_counter", 0))
        self.good_counter = good_counter

        self.sync_dir = self.settings.value("sync_dir", os.getcwd())
        self.pallet_dir = self.settings.value("pallet_dir", os.path.join(self.local_dir, "palety"))
        self.unassigned_file = os.path.join(self.local_dir, "unassigned.json")
        # ładujemy listę: lista słowników {"dmc":…, "stack":…}
        self.unassigned = self._load_unassigned()
        if not isinstance(self.unassigned, dict):
            # migracja starego formatu (lista) do jednej palety
            self.unassigned = {self.generate_pallet_id(): self.unassigned}
        self.current_pallet_id = self.get_last_pallet_id()
        self.badge = None
        self.last_activity = datetime.now()
        self.skip_flag = False
        self.toolbar_scale = float(self.settings.value("toolbar_scale", 1.0))
        self.init_ui()
        self.set_toolbar_scale(self.toolbar_scale)  # ustaw skalę po inicjalizacji UI
        self.counter_label.setText(f"Sztuki: {self.good_counter}/72")
        self.init_login()  # <-- logowanie przed pokazaniem okna
        if self.badge:  # tylko jeśli login się udał
            self.showMaximized()
            self.show()
            self.raise_()
            self.activateWindow()
            self.start_inactivity_timer()

    def keyPressEvent(self, event):
        print(f"[DEBUG] keyPressEvent: {event.key()}")  # zobaczysz w konsoli numery klawiszy
        if event.key() == Qt.Key_B and self.btn_skip.isVisible():
            print("[DEBUG] przechwytuję B, wywołuję skip")
            self.skip_stack_scan()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        # kiedy wciśnięto SPACJĘ i widoczny jest przycisk skip → wywołaj skip_stack_scan
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space and self.btn_skip.isVisible():
            self.skip_stack_scan()
            return True    # nie przekazujemy dalej
        return super().eventFilter(obj, event)

    def init_login(self):
        if TEST_MODE:
            # tryb testowy: zawsze zalogowany, bez dialogu
            self.badge = "R-7015"
            self.update_user_menu()  # odśwież menu po zalogowaniu
            return
        dlg = LoginDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.badge = dlg.badge
            self.update_user_menu()  # odśwież menu po zalogowaniu
        else:
            sys.exit()

    def count_pallets_for_current_shift(self):
        """Zwraca liczbę palet utworzonych na bieżącej zmianie."""
        now = datetime.now()
        today = now.date()
        hour = now.hour

        # Wyznacz zakres czasu dla zmiany
        if 6 <= hour < 18:
            # Dzienna: dziś 06:00–18:00
            shift_start = datetime.combine(today, datetime.strptime("06:00", "%H:%M").time())
            shift_end = datetime.combine(today, datetime.strptime("18:00", "%H:%M").time())
        else:
            # Nocna: wczoraj 18:00–dziś 06:00
            if hour < 6:
                shift_start = datetime.combine(today - timedelta(days=1), datetime.strptime("18:00", "%H:%M").time())
                shift_end = datetime.combine(today, datetime.strptime("06:00", "%H:%M").time())
            else:
                shift_start = datetime.combine(today, datetime.strptime("18:00", "%H:%M").time())
                shift_end = datetime.combine(today + timedelta(days=1), datetime.strptime("06:00", "%H:%M").time())

        count = 0
        try:
            for fname in os.listdir(self.pallet_dir):
                if not fname.endswith(".csv"):
                    continue
                try:
                    parts = fname.split("_")
                    datestamp = parts[0]  # '2024-06-28'
                    hour_min = parts[1]   # '10-11'
                    hour_f = int(hour_min.split("-")[0])
                    min_f = int(hour_min.split("-")[1])
                    paleta_datetime = datetime.strptime(f"{datestamp} {hour_f:02d}:{min_f:02d}", "%Y-%m-%d %H:%M")
                    if shift_start <= paleta_datetime < shift_end:
                        count += 1
                except Exception:
                    continue
        except Exception:
            pass
        return count

    def update_counter_labels(self):
        """Aktualizuje oba liczniki: palet na zmianie i sztuk."""
        pallets = self.count_pallets_for_current_shift()
        self.pallets_label.setText(f"Palety: {pallets}")
        self.counter_label.setText(f"Sztuki: {self.good_counter}/72")

    def init_ui(self):
        self.setWindowTitle("BSG H66 2 QW2 Traceability App")
        font_header = QFont("Arial", 14, QFont.Bold)
        font_big = QFont("Arial", 16, QFont.Bold)

        # --- widgety ---
        self.pallets_label = QLabel()
        self.pallets_label.setFont(QFont("Arial", 12, QFont.Bold))
        self.counter_label = QLabel()
        self.counter_label.setFont(QFont("Arial", 12, QFont.Bold))
        self.update_counter_labels()

        # Przyciski do toolbara
        self.btn_remove = QPushButton("-")
        self.btn_remove.clicked.connect(self.remove_last_piece)

        self.btn_reset = QPushButton("Reset")
        self.btn_reset.clicked.connect(self.reset_counter)

        self.btn_new_pallet = QPushButton("Nowa paleta")
        self.btn_new_pallet.clicked.connect(self.start_new_pallet)

        self.btn_unassigned = QPushButton("Nieprzypisane")
        self.btn_unassigned.clicked.connect(self.show_unassigned)

        # --- toolbar ---
        self.toolbar = QToolBar("Główny pasek")
        self.toolbar.setMovable(True)
        self.toolbar.addWidget(self.counter_label)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(self.btn_remove)
        self.toolbar.addWidget(self.btn_reset)

        # Rozciągacz – przesuwa kolejne widgety do prawej (dynamicznie)
        self.toolbar_spacer = QWidget()
        self.toolbar_spacer.setMinimumSize(16, 8)
        self.toolbar_spacer.setMaximumSize(32, 16)
        self.toolbar_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.toolbar.addWidget(self.toolbar_spacer)

        self.toolbar.addWidget(self.pallets_label)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(self.btn_new_pallet)
        self.toolbar.addWidget(self.btn_unassigned)
        self.toolBarAreaChanged = False
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)
        self.addToolBarBreak(Qt.TopToolBarArea)
        #self.toolbar.dockLocationChanged.connect(self.on_toolbar_location_changed)
        self.on_toolbar_location_changed(self.toolBarArea(self.toolbar))  # ustaw na start

        # --- menu bar ---
        menubar = self.menuBar()
        menu = menubar.addMenu("Menu")

        action_stats = QAction("Statystyki", self)
        action_stats.triggered.connect(self.show_stats)
        action_settings = QAction("Ustawienia", self)
        action_settings.triggered.connect(self.open_settings)

        # Dodaj akcje do menu po prawej stronie
        menu.addAction(action_stats)
        menu.addAction(action_settings)
        menubar.setCornerWidget(QWidget(), Qt.TopLeftCorner)  # aby menu było po prawej

        # --- pasek narzędzi ---
        toolbar_menu = menubar.addMenu("Pasek")

        action_toolbar_bigger = QAction("Powiększ pasek", self)
        action_toolbar_bigger.triggered.connect(self.increase_toolbar_scale)
        toolbar_menu.addAction(action_toolbar_bigger)

        action_toolbar_smaller = QAction("Pomniejsz pasek", self)
        action_toolbar_smaller.triggered.connect(self.decrease_toolbar_scale)
        toolbar_menu.addAction(action_toolbar_smaller)

        # --- menu użytkownika ---
        self.user_menu = menubar.addMenu("Użytkownik")
        self.badge_action = QAction(f"Zalogowany: {self.badge or '-'}", self)
        self.badge_action.setEnabled(False)
        self.user_menu.addAction(self.badge_action)

        self.action_logout = QAction("Wyloguj", self)
        self.action_logout.triggered.connect(self.logout)
        self.user_menu.addAction(self.action_logout)

        # --- reszta widgetów ---
        self.instruction = QLabel("1) Zeskanuj kod DMC klienta:")
        self.instruction.setFont(font_header)
        self.instruction.setAlignment(Qt.AlignCenter)

        self.input_dmc = QLineEdit()
        self.input_dmc.setFont(font_big)
        self.input_dmc.setAlignment(Qt.AlignCenter)
        self.input_dmc.returnPressed.connect(self.on_dmc_enter)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)

        self.child_label = QLabel("")
        self.child_label.setFont(font_big)
        self.child_label.setAlignment(Qt.AlignCenter)
        self.child_label.hide()

        self.label_gauges = QLabel("")
        self.label_gauges.setFont(font_big)
        self.label_gauges.setAlignment(Qt.AlignCenter)
        self.label_gauges.setWordWrap(False)
        self.label_gauges.hide()

        self.hidden_scan = QLineEdit()
        self.hidden_scan.returnPressed.connect(self.on_child_enter)
        self.hidden_scan.setEchoMode(QLineEdit.Password)
        self.hidden_scan.setFixedSize(1,1)

        self.btn_skip = QPushButton("Pomiń skan (B)")
        self.btn_skip.setFixedSize(120,30)
        self.btn_skip.clicked.connect(self.skip_stack_scan)
        self.btn_skip.hide()

        self.shortcut_skip = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.shortcut_skip.setContext(Qt.ApplicationShortcut)
        self.shortcut_skip.activated.connect(self.skip_stack_scan)

        # --- układ główny ---
        layout = QVBoxLayout()
        layout.setSpacing(20)  # Jednakowy odstęp między wszystkimi elementami
        layout.setContentsMargins(20, 20, 20, 20)  # Marginesy wokół całego layoutu
        
        # Dodaj rozciągacz na górze aby wyśrodkować pionowo
        layout.addStretch(1)
        
        layout.addWidget(self.instruction)
        layout.addWidget(self.input_dmc)
        layout.addWidget(sep)
        layout.addWidget(self.child_label)
        layout.addWidget(self.label_gauges)
        layout.addWidget(self.hidden_scan)
        layout.addWidget(self.btn_skip)
        
        # Dodaj rozciągacz na dole aby wyśrodkować pionowo
        layout.addStretch(1)

        central_widget = QWidget()
        central_widget.setLayout(layout)

        # Dodaj kontener wyśrodkowujący
        container = QWidget()
        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)  # Brak marginesów w kontenerze poziomym
        hbox.addStretch(1)              # rozciągacz z lewej
        hbox.addWidget(central_widget)  # Twój główny widget
        hbox.addStretch(1)              # rozciągacz z prawej
        container.setLayout(hbox)

        self.setCentralWidget(container)
        self.input_dmc.setFocus()

    def show_stats(self):
        try:
            stats = self.collect_stats()
            dlg = StatsDialog(self, stats)
            dlg.exec_()
        except Exception as e:
            self.statusBar().showMessage(f"Błąd statystyk: {e}", 10000)  # 10 sekund

    def collect_stats(self):
        from collections import defaultdict
        stats = defaultdict(lambda: {"dzienna": 0, "nocna": 0})  # klucz: data (YYYY-MM-DD)
        today = datetime.now().date()
        try:
            for fname in os.listdir(self.pallet_dir):
                if not fname.endswith(".csv"):
                    continue
                # przykładowa nazwa: "2024-06-28_10-11_PAL123_A1.csv"
                try:
                    parts = fname.split("_")
                    datestamp = parts[0]  # '2024-06-28'
                    hour = int(parts[1].split("-")[0])  # '10' z '10-11'
                    minute = int(parts[1].split("-")[1])  # '11' z '10-11'
                    paleta_datetime = datetime.strptime(f"{datestamp} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
                    paleta_date = paleta_datetime.date()
                    paleta_time = paleta_datetime.time()
                    if paleta_time >= datetime.strptime("06:00", "%H:%M").time() and paleta_time < datetime.strptime("18:00", "%H:%M").time():
                        key = paleta_date.strftime("%Y-%m-%d")
                        stats[key]["dzienna"] += 1
                    else:
                        if paleta_time < datetime.strptime("06:00", "%H:%M").time():
                            key = (paleta_date - timedelta(days=1)).strftime("%Y-%m-%d")
                        else:
                            key = paleta_date.strftime("%Y-%m-%d")
                        stats[key]["nocna"] += 1
                except Exception:
                    continue
        except Exception as e:
            self.statusBar().showMessage(f"Nie można odczytać palet: {e}", 10000)
            raise RuntimeError(f"Nie można odczytać palet: {e}")

        last_7_days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6,-1,-1)]
        stats_7 = {day: stats.get(day, {"dzienna":0, "nocna":0}) for day in last_7_days}
        return stats_7

    def remove_last_piece(self):
        if self.good_counter == 0:
            QMessageBox.information(self, "Brak sztuk", "Licznik wynosi już 0.")
            return
        reply = QMessageBox.question(
            self, "Potwierdzenie",
            "Odjąć 1 sztukę z licznika?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.No:
            return
        self.good_counter -= 1
        self.update_counter_labels()
        self.settings.setValue("good_counter", self.good_counter)
        # pytanie o usunięcie z palety (unassigned)
        chunk = self.unassigned.get(self.current_pallet_id)
        if self.current_pallet_id and chunk:
            reply2 = QMessageBox.question(
                self, "Usunąć z palety?",
                "Usunąć ostatni wpis z palety (unassigned)?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply2 == QMessageBox.Yes:
                if chunk:
                    chunk.pop()
                    self._save_unassigned()

    def _load_unassigned(self):
        try:
            with open(self.unassigned_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):  # stara wersja
                    return {self.generate_pallet_id(): data}
                return data
        except FileNotFoundError:
            return {}
        except Exception as e:
            QMessageBox.warning(self, "Błąd odczytu", f"unassigned.json: {e}")
            self.statusBar().showMessage(f"unassigned.json: {e}: {e}", 10000)
            return {}

    def _save_unassigned(self):
        try:
            os.makedirs(self.local_dir, exist_ok=True)
            with open(self.unassigned_file, 'w', encoding='utf-8') as f:
                json.dump(self.unassigned, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Błąd zapisu", f"unassigned.json: {e}")
            self.statusBar().showMessage(f"unassigned.json: {e}", 10000)

    def show_unassigned(self):
        if not self.unassigned:
            QMessageBox.information(self, "Brak", "Brak nieprzypisanych kodów.")
            return

        dlg = UnassignedDialog(self, self.unassigned)
        result = dlg.exec_()
        if result == QDialog.Accepted:
            # 1) Pobieramy całą aktywną paletę
            pallet_id = dlg.selected_pallet_id()
            to_assign = dlg.selected_chunk()
            if to_assign:
                assigned = self._do_assign(to_assign, pid=pallet_id)
                if assigned and pallet_id in self.unassigned:
                    del self.unassigned[pallet_id]
                    self._save_unassigned()
                    self.statusBar().showMessage(f"Przypisano paletę {pallet_id}", 30000)
        else:
            # nawet jeśli dialog zamknięto bez przypisania, zapisujemy zmiany (np. dodane/usunięte kody)
            self._save_unassigned()

    def generate_pallet_id(self):
        """Zwraca nowy ID palety w formacie YYYYMMDD_001 lub prosty liczbowy."""
        today = datetime.now().strftime("%Y%m%d")
        # Szukaj palet dzisiaj
        existing = [pid for pid in self.unassigned.keys() if pid.startswith(today)]
        counter = len(existing) + 1
        return f"{today}_{counter:03d}"

    def get_last_pallet_id(self):
        """Zwraca ostatni (największy) ID palety lub tworzy pierwszy."""
        if not self.unassigned:
            return self.generate_pallet_id()
        # Zwraca ostatni utworzony (alfabetycznie po kluczu)
        return sorted(self.unassigned.keys())[-1]

    def start_new_pallet(self):
        """Ręcznie rozpocznij nową pustą paletę z potwierdzeniem i możliwością przypisania obecnej."""
        chunk = self.unassigned.get(self.current_pallet_id)
        if self.current_pallet_id and chunk:
            reply = QMessageBox.question(
                self,
                "Nowa paleta",
                "Czy na pewno rozpocząć nową paletę?\n"
                "Aktualna zostanie zakończona i przypisana do palety.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
            # Przypisz aktualną paletę jak po 72 sztukach
            if chunk:
                self._do_assign(chunk, pid=self.current_pallet_id)
                del self.unassigned[self.current_pallet_id]
                self._save_unassigned()
        else:
            reply = QMessageBox.question(
                self,
                "Nowa paleta",
                "Czy na pewno rozpocznij nową paletę?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        self.current_pallet_id = self.generate_pallet_id()
        self.unassigned[self.current_pallet_id] = []
        self._save_unassigned()
        self.good_counter = 0
        self.update_counter_labels()
        self.settings.setValue("good_counter", self.good_counter)

    def start_inactivity_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_inactivity)
        self.timer.start(60000)

    def check_inactivity(self):
        if datetime.now() - self.last_activity > timedelta(minutes=30):
            QMessageBox.information(self, "Wylogowanie", "Brak aktywności. Wylogowano.")
            self.logout()

    def record_activity(self):
        self.last_activity = datetime.now()

    def logout(self):
        self.badge = None
        self.timer.stop()
        self.init_login()
        if self.badge:  # jeśli login się udał, uruchom timer ponownie
            self.record_activity()
            self.timer.start(60000)
        else:
            sys.exit()  # jeśli anulowano login, zamknij aplikację

    def update_user_menu(self):
        # Zaktualizuj tekst badge w menu użytkownika bez usuwania i dodawania menu
        if hasattr(self, 'badge_action'):
            self.badge_action.setText(f"Zalogowany: {self.badge or '-'}")
    
    def reset_counter(self):
        reply = QMessageBox.question(
            self, "Potwierdzenie resetu",
            "Czy na pewno zresetować licznik?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.No:
            return
        self.good_counter = 0
        # self.counter_label.setText(f"Sztuki: {self.good_counter}/72")
        self.update_counter_labels()
        self.settings.setValue("good_counter", self.good_counter)

        self.current_pallet_id = self.generate_pallet_id()
        self.unassigned[self.current_pallet_id] = []
        self._save_unassigned()

    def open_settings(self):
        dlg = SettingsDialog(
            self,
            self.local_dir, self.sync_dir, self.pallet_dir,
            self.good_counter
        )
        if dlg.exec_() == QDialog.Accepted:
            # katalogi
            self.local_dir  = dlg.local_dir
            self.sync_dir   = dlg.sync_dir
            self.pallet_dir = dlg.pallet_dir                   # <<< nowość

            self.settings.setValue("local_dir",  self.local_dir)
            self.settings.setValue("sync_dir",   self.sync_dir)
            self.settings.setValue("pallet_dir", self.pallet_dir)  # <<< nowość

            # stan licznika…
            self.good_counter = dlg.new_counter
            self.settings.setValue("good_counter", self.good_counter)
            self.settings.setValue("good_counter", self.good_counter)
            # self.counter_label.setText(f"Sztuki: {self.good_counter}/72")
            self.update_counter_labels()

    def get_matching_info(self, serno, line=436):
        self.record_activity()
        try:
            resp = requests.get(
                "http://intranet/Traceability2/getMaching/",
                params={"line": line, "machine": "", "serno_out": serno}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            QMessageBox.critical(self, f"Błąd pobierania danych z intranetu dla {serno}", f"{e}")
            return None
        if not isinstance(data, dict):
            QMessageBox.critical(self, f"Błąd danych", f"Brak danych o {serno} w intranecie.")
        return data

    def check_inspect(self, serno, inspect, line, machine):
        self.record_activity()
        try:
            resp = requests.get(
                "http://intranet/Traceability2/getInspect/",
                params={"line": line, "machine": machine, "inspect": inspect, "serno": serno}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            QMessageBox.critical(self, f"Błąd pobierania danych z intranetu dla {serno}", f"{e}")
            return None
        
        if isinstance(data, list) and data:
            return data
        return None

    def on_dmc_enter(self):
        self.record_activity()
        if not self.badge:
            QMessageBox.warning(self, "Brak badge", "Zaloguj się przed skanowaniem.")
            return
        code = self.input_dmc.text().strip()
        if not dmc_regex.match(code):
            QMessageBox.warning(self, "Błąd", "Niepoprawny format DMC.")
            self.input_dmc.clear()
            return
        existing = self.check_inspect(
            self.input_dmc.text().strip(),
            "QW2_child_serno",
            436,
            3661
        )
        if existing:
            QMessageBox.information(
                self, "Status QW2",
                "Sztuka była już sprawdzona na QW2."
            )
        self.child_label.hide()
        self.label_gauges.hide()
        self.statusBar().showMessage("Szukanie...", 10000)
        QApplication.processEvents()
        try:
            info = self.get_matching_info(code)
        except Exception as e:
            QMessageBox.critical(self, "Błąd pobierania", str(e))
            self.statusBar().showMessage(f"Błąd pobierania: {str(e)}", 10000)
            return
        child = info.get("child_serno")
        if not child:
            QMessageBox.warning(self, "Brak danych", f"Nie znaleziono child_serno dla {code}")
            return
        self.dmc_code, self.child_serno = code, child
        self.child_label.setText(child)
        self.child_label.show()
        self.instruction.setText("2) Zeskanuj kod stacka (child_serno):")
        self.statusBar().showMessage("Oczekuję...", 10000)
        self.input_dmc.setDisabled(True)
        self.hidden_scan.setFocus()
        self.btn_skip.show()

    def skip_stack_scan(self):
        # działa tylko, gdy przycisk jest widoczny
        if not self.btn_skip.isVisible():
            return
        self.skip_flag = True
        # wywołujemy tę samą logikę, ale z flagą skip
        self.on_child_enter()

    def on_child_enter(self):
        self.record_activity()

        skip = getattr(self, "skip_flag", False)
        if skip:
            scan = self.child_serno
        else:
            scan = self.hidden_scan.text().strip()

        existing = self.check_inspect(
            self.dmc_code,
            "QW2_child_serno",
            436,
            3661
        )
        if existing:
            QMessageBox.information(
                self, "Status QW2",
                "Sztuka była już sprawdzona na QW2."
            )

        insps = []

        if scan != self.child_serno:
            #badge_pattern = QRegExp(r'^[A-Z]-\d{4,5}$')
            QMessageBox.information(
                self, "❌ Uwaga! Kod stacka NIEPRAWIDŁOWY! ❌",
                "Sztuka powinna zostać przekazana jakości w celu re-testu na EOL.")
            # Optymalizacja: dialog tworzony raz, tylko reset tekstu w pętli
            dlg = QInputDialog(self)
            dlg.setWindowTitle("Potwierdzenie oddania sztuki jakości")
            dlg.setLabelText("Podaj numer badge:")
            dlg.setInputMode(QInputDialog.TextInput)
            dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
            dlg.resize(325, 120)
            edit = dlg.findChild(QLineEdit)
            if edit is not None:
                edit.setPlaceholderText("R-7015")
            while True:
                if edit is not None:
                    edit.clear()
                ok = dlg.exec_() == QDialog.Accepted
                badge = dlg.textValue().strip() if ok else ""
                if not ok:
                    continue
                if badge_pattern.exactMatch(badge):
                    break
                else:
                    QMessageBox.warning(self, "Błąd", "Niepoprawny format badge (R-7015). Podaj poprawny numer badge.")
            self._log_mismatch(badge)
            self.label_gauges.setText(
                f"<span style='font-size:48pt; color:red; font-weight:bold'>❌Stack NIEPRAWIDŁOWY ❌</span>"
            )
            self.label_gauges.setPalette(QPalette())
            self.label_gauges.show()
            self.input_dmc.setEnabled(True)
            self.input_dmc.clear()
            self.input_dmc.setFocus()
            self.instruction.setText("1) Zeskanuj kod DMC klienta:")
            return
        else:
            insps.append(("STACK", "OK", False))

        eol_list = self.check_inspect(self.child_serno, "Status", 436, 3504)
        if not eol_list:
            missing = True
            eol_ok = False
            insps.append(("EOL", False, True))
        else:
            latest = max(
                eol_list,
                key=lambda x: datetime.strptime(x["inspectdate"], "%Y-%m-%d %H:%M:%S")
            )
            missing = False
            eol_ok = (latest.get("judge") == "1")
        insps.append(("EOL", eol_ok, False))

        # Podsumowanie
        if any(missing for _, _, missing in insps):
            summary_text = "⚠️ BRAK DANYCH ⚠️"
            color = "orange"
        elif (not eol_ok) or (any(not ok for _, ok, missing in insps if not missing)):
            summary_text = "❌ NOK ❌"
            color = "red"
        else:
            summary_text = "✅ OK ✅"
            color = "green"

        # Duży status na środku
        self.label_gauges.setText(
            f"<span style='font-size:48pt; color:{color}; font-weight:bold'>{summary_text}</span>"
        )
        self.label_gauges.setPalette(QPalette())
        self.label_gauges.show()

        # Szczegóły do statusbara
        details = []
        for code_i, ok, missing_flag in insps:
            if missing_flag:
                details.append(f"{code_i}: BRAK DANYCH ⚠️")
            elif ok:
                details.append(f"{code_i}: OK ✅")
            else:
                details.append(f"{code_i}: NOK ❌")
        self.statusBar().showMessage(" | ".join(details), 15000)

        # gauge_ok = any(ok for _, ok, _ in insps)
        # gauge_status = 'OK' if gauge_ok else 'NOK'
        # gauge_judge = '1' if gauge_ok else '0'

        # Save CSV
        ts = datetime.now(ZoneInfo("Europe/Warsaw"))
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        fn = ts.strftime("%Y%m%d%H%M") + f"_{self.dmc_code}.csv"
        date_dir = os.path.join(self.local_dir,
                                 ts.strftime("%Y"), ts.strftime("%m"), ts.strftime("%Y-%m-%d"))
        try:
            os.makedirs(date_dir, exist_ok=True)
            path = os.path.join(date_dir, fn)
            rows = []
            rows.append(["INSPECT","",ts_str,"436","ZI01-0010-0920","3661","ZI01-0010-0920-0380","139596023","505-455-99-99","1",self.dmc_code,"QW2_WpcRfid",self.badge,'-','-','0'])
            rows.append(["INSPECT","",ts_str,"436","ZI01-0010-0920","3504","ZI01-0010-0920-0380","139596023","505-455-00-00","1",self.dmc_code,"QW2_EOL_Status", 'OK' if eol_ok else 'NOK','1' if eol_ok else '0','-','0'])
            if missing:
                rows.append(["INSPECT","",ts_str,"436","ZI01-0010-0920","3504","ZI01-0010-0920-0380","139596023","505-455-00-00","1",self.dmc_code,"QW2_EOL_Missing_data",'NOK','0','-','0'])
            if skip:
                rows.append([
                    "INSPECT","",ts_str,
                    "436","ZI01-0010-0920","3661","ZI01-0010-0920-0380",
                    "139596023","505-455-99-99","1",
                    self.dmc_code,"QW2_child_serno",
                    "BRAK","2","-","0"
                ])
            else:
                rows.append([
                    "INSPECT","",ts_str,
                    "436","ZI01-0010-0920","3661","ZI01-0010-0920-0380",
                    "139596023","505-455-99-99","1",
                    self.dmc_code,"QW2_child_serno",
                    "OK","1","-","0"
                ])        
            # Gauge row with dynamic status
            # rows.append(["INSPECT","",ts_str,"436","ZI01-0010-0920","3661","ZI01-0010-0920-0380","139596023","505-455-99-99","1",self.dmc_code,"QW2_Gauge",gauge_status,gauge_judge,'-','0'])
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')  # <-- używamy średnika
                writer.writerows(rows)
            self.sync_file(path)
        except Exception as e:
            QMessageBox.warning(self, "Błąd zapisu pliku", f"Nie udało się zapisać pliku CSV: {e}")
            self.statusBar().showMessage(f"Błąd zapisu pliku: {e}", 10000)
            return

        # Przy CMM override sztuka zaliczana tylko jeśli EOL OK
        if eol_ok:
            self.good_counter += 1
            self.update_counter_labels()
            self.settings.setValue("good_counter", self.good_counter)
            self.unassigned.setdefault(self.current_pallet_id, []).append({
                "dmc": self.dmc_code,
                "stack": self.child_serno
            })
            self._save_unassigned()

# Pobierz aktualną listę dla palety
        current_pallet = self.unassigned.get(self.current_pallet_id, [])
        if self.good_counter >= 72:
            if not current_pallet:
                QMessageBox.warning(self, "Błąd", "Nie można przypisać pustej palety – brak nieprzypisanych sztuk.")
                # Reset liczników i utwórz nową paletę, ale nie przypisuj pustej
                self.good_counter = 0
                self.update_counter_labels()
                self.settings.setValue("good_counter", self.good_counter)
                self.current_pallet_id = self.generate_pallet_id()
                self.unassigned[self.current_pallet_id] = []
                self._save_unassigned()
                return
            reply = QMessageBox.question(
                self, "Pełna paleta",
                "Osiągnięto 72 sztuki. Przypisać paletę teraz?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self._do_assign(current_pallet, pid=self.current_pallet_id)
                if self.current_pallet_id in self.unassigned:
                    del self.unassigned[self.current_pallet_id]
                    self._save_unassigned()
            self.good_counter = 0
            self.update_counter_labels()
            self.settings.setValue("good_counter", self.good_counter)
            self.current_pallet_id = self.generate_pallet_id()
            self.unassigned[self.current_pallet_id] = []
            self._save_unassigned()


        # wyłączamy tryb skip i chowamy przycisk
        self.btn_skip.hide()
        if skip:
            if hasattr(self, "skip_flag"): del self.skip_flag

        # Reset UI
        self.input_dmc.setEnabled(True)
        self.input_dmc.clear()
        self.input_dmc.setFocus()
        self.instruction.setText("1) Zeskanuj kod DMC klienta:")

    def _log_mismatch(self, approver):
        ts = datetime.now(ZoneInfo("Europe/Warsaw"))
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        fn = ts.strftime("%Y%m%d%H%M") + f"_{self.dmc_code}.csv"
        path = os.path.join(self.local_dir, fn)
        row = ["INSPECT","",ts_str,"436","ZI01-0010-0920","3661","ZI01-0010-0920-0380","139596023","505-455-99-99","1",self.dmc_code,"QW2_WpcRfid",self.badge,'0','-','0']
        row_child = ["INSPECT","",ts_str,"436","ZI01-0010-0920","3661","ZI01-0010-0920-0380","139596023","505-455-99-99","1",self.dmc_code,"QW2_child_serno",'NOK','0','-','0']
        row_badge = ["INSPECT","",ts_str,"436","ZI01-0010-0920","3661","ZI01-0010-0920-0380","139596023","505-455-99-99","1",self.dmc_code,"QW2_Approver",approver,'0','-','0']
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(row)
                writer.writerow(row_child)
                writer.writerow(row_badge)
        except Exception as e:
            QMessageBox.warning(self, "Błąd zapisu", f"Nie udało się zapisać pliku niezgodności: {e}")
            self.statusBar().showMessage(f"Nie udało się zapisać pliku niezgodności: {e}", 10000)

    def sync_file(self, local_path):
        try:
            # Upewniamy się, że katalog sync_dir istnieje
            os.makedirs(self.sync_dir, exist_ok=True)
            # Kopiujemy plik bezpośrednio do self.sync_dir
            dest = os.path.join(self.sync_dir, os.path.basename(local_path))
            shutil.copy(local_path, dest)
        except Exception as e:
            QMessageBox.warning(self, "Sync error", f"Nie udało się zsynchronizować: {e}")
            self.statusBar().showMessage(f"Nie udało się zsynchronizować: {e}", 60000)

    def _do_assign(self, items_to_assign, pid=None):
        dlg = PalletDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            paleta, zmiana = dlg.pallet_code, dlg.shift
            ts = datetime.now(ZoneInfo("Europe/Warsaw"))
            fname = ts.strftime("%Y-%m-%d_%H-%M") + f"_{paleta}_{zmiana}.csv"
            dest_folder = self.pallet_dir
            try:
                os.makedirs(dest_folder, exist_ok=True)
                path = os.path.join(dest_folder, fname)
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Kod Vitesco", "Stack", "Paleta", "Zmiana"])
                    for itm in items_to_assign:
                        writer.writerow([itm["dmc"], itm["stack"], paleta, zmiana])
                return True
            except Exception as e:
                QMessageBox.warning(self, "Błąd zapisu", f"Nie udało się zapisać pliku palety: {e}")
                self.statusBar().showMessage(f"Błąd zapisu pliku palety: {e}", 10000)
        return False

    def closeEvent(self, event):
        try:
            self.settings.setValue("good_counter", self.good_counter)
        except Exception as e:
            QMessageBox.warning(self, "Błąd zapisu", f"Nie udało się zapisać licznika: {e}")
            self.statusBar().showMessage(f"Nie udało się zapisać licznika: {e}", 10000)
        super().closeEvent(event)

    def set_toolbar_scale(self, scale=1.0):
        """Ustawia skalowanie tekstu i przycisków w toolbarze."""
        base_font_size = 12
        base_btn_height = 30
        base_btn_width = 100

        font = QFont("Arial", int(base_font_size * scale), QFont.Bold)
        self.counter_label.setFont(font)
        self.pallets_label.setFont(font)

        for btn in [self.btn_remove, self.btn_reset, self.btn_new_pallet, self.btn_unassigned]:
            btn.setFont(font)
            btn.setMinimumHeight(int(base_btn_height * scale))
            btn.setMinimumWidth(int(base_btn_width * scale * 0.7))  # szerokość proporcjonalna

        self.toolbar_scale = scale
        self.settings.setValue("toolbar_scale", self.toolbar_scale)

    def increase_toolbar_scale(self):
        self.toolbar_scale = min(self.toolbar_scale + 0.1, 2.0)
        self.set_toolbar_scale(self.toolbar_scale)

    def decrease_toolbar_scale(self):
        self.toolbar_scale = max(self.toolbar_scale - 0.1, 0.7)
        self.set_toolbar_scale(self.toolbar_scale)

    def on_toolbar_location_changed(self, area):
        # area: Qt.ToolBarArea
        if area in (Qt.LeftToolBarArea, Qt.RightToolBarArea):
            # pionowo: tylko lekki odstęp
            self.toolbar_spacer.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
            self.toolbar_spacer.setMinimumSize(16, 16)
            self.toolbar_spacer.setMaximumSize(32, 32)
        else:
            # poziomo: rozciągacz do prawej
            self.toolbar_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            self.toolbar_spacer.setMinimumSize(16, 8)
            self.toolbar_spacer.setMaximumSize(16777215, 32)
        self.toolbar_spacer.update()

    def event(self, event):
        if event.type() == QEvent.ToolBarChange:
            self.on_toolbar_location_changed(self.toolBarArea(self.toolbar))
        return super().event(event)

if __name__ == "__main__":
    # ============ TRYB TESTOWY =============
    if TEST_MODE:
        TraceabilityApp.get_matching_info = fake_get_matching_info
        TraceabilityApp.check_inspect    = fake_check_inspect
    # ============ TRYB TESTOWY =============
    app = QApplication(sys.argv)
    win = TraceabilityApp()
    win.show()
    sys.exit(app.exec_())