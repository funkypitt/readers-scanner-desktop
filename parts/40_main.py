

def pdf_page(pdf, index, cache_dir, dpi=130):
    """Page `index` of a PDF as a picture, kept beside the document."""
    out = os.path.join(cache_dir, f"{index + 1}-{dpi}.jpg")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(pdf):
        try:
            made = pdf_pictures(pdf, cache_dir, f"page{index + 1}-{dpi}", dpi, index + 1, index + 1)
        except Exception:
            made = []
        if made:
            replace(made[0], out)
    return QtGui.QImage(out) if os.path.exists(out) else None


IMPORTABLE = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".pdf")
SYNC_MINUTES = 5


class Main(QtWidgets.QMainWindow):
    store_changed = QtCore.pyqtSignal()
    read_progress = QtCore.pyqtSignal()
    read_done = QtCore.pyqtSignal(str)

    def __init__(self, store=None):
        super().__init__()
        self.cfg = load_config()
        self.cfg.setdefault("source", "auto")
        self.cfg.setdefault("look", "original")
        self.cfg.setdefault("lang", default_lang())
        self.store = store or Store(os.path.join(DATA_DIR, "scans"))
        self.store.on_change = self.store_changed.emit          # from any thread: queued to the UI
        self.cfg.setdefault("best", True)
        self.reader = Reader(DATA_DIR)
        self.reader.prefer_best = bool(self.cfg["best"])
        self.naps2 = Naps2(DATA_DIR)
        self.loader = Loader()
        self.queue = ReadQueue(self.store, self.reader, on_done=self.read_done.emit, on_progress=self.read_progress.emit)
        self.threads = []
        self.place = FOLDERS          # the list: the folders, every document (None) or one folder
        self.current = None           # the document on the right
        self.session = None           # pages scanned, not filed yet
        self.session_dir = os.path.join(DATA_DIR, "session")
        self.scanning = False
        self.searching = False
        self.syncing = False
        self.sync_again = False
        self.downloads = {}           # document → percent
        self.show_text = False
        self.last_status = ""
        self.quitting = False
        self.setWindowTitle("reader's scanner")
        self.setAcceptDrops(True)
        self.resize(1180, 800)
        self.font_size = int(self.cfg.get("font_size", 13))
        self.dark = bool(self.cfg.get("dark", False))

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Left: where we are, find, the list, the three choices, « scan », the status line
        self.left = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(self.left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        self.place_bar = Clickable("", "placebar")
        self.place_bar.clicked.connect(self.to_folders)
        left.addWidget(self.place_bar)
        self.find = QtWidgets.QLineEdit()
        self.find.setObjectName("find")
        self.find.setPlaceholderText(_("find"))
        self.find.setToolTip(_("find in names and text") + " (Ctrl+F)")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self.refresh_list)
        self.find.installEventFilter(self)
        left.addWidget(self.find)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("rows")
        self.list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.delegate = RowDelegate(self.list)
        self.list.setItemDelegate(self.delegate)
        self.list.currentItemChanged.connect(self.list_moved)
        self.list.itemClicked.connect(self.item_clicked)
        self.list.itemActivated.connect(self.item_activated)
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.list_menu)
        left.addWidget(self.list, 1)
        left.addWidget(rule())
        choices = QtWidgets.QVBoxLayout()
        choices.setContentsMargins(22, 10, 22, 10)
        choices.setSpacing(4)
        self.choice = {}
        for key, pick in (("source", self.pick_source), ("look", self.pick_look), ("lang", self.pick_lang)):
            c = Clickable("", "choice")
            c.clicked.connect(pick)
            choices.addWidget(c)
            self.choice[key] = c
        left.addLayout(choices)
        self.scan_button = Clickable(_("scan"), "scan")
        self.scan_button.setToolTip("Ctrl+N")
        self.scan_button.clicked.connect(self.scan)
        left.addWidget(self.scan_button)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(22, 8, 16, 10)
        lines = QtWidgets.QVBoxLayout()
        lines.setSpacing(1)
        self.scanner_line = Clickable("", "dim")          # the scanner
        self.scanner_line.clicked.connect(self.setup)
        self.status = Clickable("", "dim")                # the sync
        self.status.clicked.connect(lambda: self.sync() if self.configured() else self.setup())
        lines.addWidget(self.scanner_line)
        lines.addWidget(self.status)
        bottom.addLayout(lines, 1)
        self.gear = Clickable("⚙", "gear")
        self.gear.setToolTip(_("settings (Ctrl+,)"))
        self.gear.clicked.connect(self.setup)
        bottom.addWidget(self.gear, 0)
        left.addLayout(bottom)
        outer.addWidget(self.left)
        outer.addWidget(rule(vertical=True))

        # Right: a message, a document, or the pages just scanned
        self.stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.message = Message()
        self.message.action.connect(self.message_action)
        self.stack.addWidget(self.message)

        self.doc_view = QtWidgets.QWidget()
        dv = QtWidgets.QVBoxLayout(self.doc_view)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(0)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(36, 12, 24, 4)
        self.head = QtWidgets.QLabel("")
        self.head.setObjectName("title")
        self.head.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        head.addWidget(self.head, 1)
        self.more = Clickable("⋯", "more")
        self.more.clicked.connect(self.show_menu)
        head.addWidget(self.more, 0)
        dv.addLayout(head)
        self.info = QtWidgets.QLabel("")
        self.info.setObjectName("dim")
        self.info.setContentsMargins(36, 0, 24, 12)
        self.info.setWordWrap(True)
        dv.addWidget(self.info)
        dv.addWidget(rule())
        self.body = QtWidgets.QStackedWidget()
        self.pages = Pages(self.loader)
        self.body.addWidget(self.pages)
        self.text = QtWidgets.QTextEdit()
        self.text.setReadOnly(True)
        self.text.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.text.setViewportMargins(36, 22, 36, 22)
        self.body.addWidget(self.text)
        dv.addWidget(self.body, 1)
        dv.addWidget(rule())
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(36, 12, 36, 14)
        foot.setSpacing(26)
        self.actions = {}
        for key, label, fn in (("open", _("open the PDF"), self.open_pdf), ("save", _("save a copy…"), self.save_copy),
                               ("copy", _("copy the text"), self.copy_text), ("text", _("text"), self.toggle_text)):
            c = Clickable(label, "action")
            c.clicked.connect(fn)
            foot.addWidget(c)
            self.actions[key] = c
        foot.addStretch(1)
        dv.addLayout(foot)
        self.stack.addWidget(self.doc_view)

        self.review = Review(self.loader)
        self.review.filed.connect(self.file_session)
        self.review.saved.connect(lambda: self.file_session(None, None))
        self.review.discarded.connect(self.discard_session)
        self.review.more.connect(self.scan)
        self.review.changed.connect(self.session_changed)
        self.review.keep_blank.connect(self.keep_blank)
        self.review.new_folder.connect(self.file_in_new_folder)
        self.review.pick_look.connect(self.pick_look)
        self.review.pick_lang.connect(self.pick_lang)
        self.stack.addWidget(self.review)

        self.refresh_timer = QtCore.QTimer(self, singleShot=True, interval=0, timeout=self.after_change)
        self.store_changed.connect(self.refresh_timer.start)
        self.read_progress.connect(self.refresh_timer.start)
        self.read_done.connect(self.after_read)
        self.periodic = QtCore.QTimer(self, interval=SYNC_MINUTES * 60 * 1000, timeout=self.sync)
        self.periodic.start()

        for keys, fn in (("Ctrl+N", self.scan), ("Ctrl+O", self.import_files), ("Ctrl+F", self.focus_find), ("Ctrl+T", self.toggle_theme),
                         ("F5", self.sync), ("Ctrl+R", self.sync), ("Ctrl+=", lambda: self.zoom(1)), ("Ctrl++", lambda: self.zoom(1)),
                         ("Ctrl+-", lambda: self.zoom(-1)), ("Ctrl+,", self.setup), ("Escape", self.escape), ("Ctrl+Q", self.close),
                         ("Delete", self.delete_selected), ("F2", self.rename_current)):
            QtWidgets.QShortcut(QtGui.QKeySequence(keys), self, fn)

        self.apply_style()
        self.show_choices()
        self.restore_session()
        self.refresh_list()
        if self.session:
            self.show_review()
        else:
            last = self.cfg.get("last_doc")
            if last and self.store.get(last):
                self.open_doc(last)
            else:
                self.welcome()
        self.update_status()
        if self.configured():
            QtCore.QTimer.singleShot(0, self.sync)
        if self.naps2.cmd and not self.cfg.get("device"):
            QtCore.QTimer.singleShot(0, self.find_scanner)

    def configured(self):
        return bool(self.cfg.get("server"))

    # ---- look ------------------------------------------------------------------------

    def colours(self):
        return ("#000000", "#ffffff") if self.dark else ("#ffffff", "#000000")

    def apply_style(self):
        bg, fg = self.colours()
        dim = "rgba(255,255,255,0.55)" if self.dark else "rgba(0,0,0,0.55)"
        rl = "rgba(255,255,255,0.25)" if self.dark else "rgba(0,0,0,0.25)"
        s = self.font_size
        family = {"serif": "serif", "mono": "monospace"}.get(self.cfg.get("font"), "sans-serif")
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {bg}; color: {fg}; font-family: "{family}"; font-size: {s}pt; font-weight: 300; }}
            QLabel#dim {{ color: {dim}; }}
            QLabel#title {{ font-size: {s + 3}pt; }}
            QLabel#big {{ font-size: {s + 9}pt; }}
            QLabel#more {{ font-size: {s + 5}pt; padding: 0 6px; }}
            QLabel#gear {{ color: {dim}; font-size: {s + 3}pt; padding: 0 2px 0 8px; }}
            QCheckBox {{ spacing: 8px; }}
            QLabel#choice {{ color: {dim}; padding: 2px 0; }}
            QLabel#choice:hover, QLabel#action:hover, QLabel#dimlink:hover, QLabel#tool:hover {{ color: {fg}; }}
            QLabel#dimlink {{ color: {dim}; }}
            QLabel#action {{ font-size: {s + 1}pt; }}
            QLabel#tool {{ color: {dim}; font-size: {s + 2}pt; padding: 0 2px; }}
            QLabel#addtile {{ color: {dim}; border: 1px dashed {dim}; font-size: {s + 3}pt; }}
            QLabel#addtile:hover {{ color: {fg}; border: 1px dashed {fg}; }}
            QLabel#scan {{ background: {fg}; color: {bg}; font-size: {s + 7}pt; padding: 26px 22px; }}
            QLabel#scanning {{ background: {bg}; color: {fg}; font-size: {s + 7}pt; padding: 25px 21px; border: 1px solid {fg}; }}
            QLabel#placebar {{ color: {dim}; padding: 12px 22px; border-bottom: 1px solid {rl}; }}
            QFrame#sep {{ background: {rl}; }}
            QLineEdit#find {{ border: none; border-bottom: 1px solid {rl}; padding: 14px 22px; }}
            QLineEdit#name {{ border: none; border-bottom: 1px solid {rl}; padding: 8px 0; font-size: {s + 3}pt; }}
            QListWidget#rows {{ background: {bg}; border: none; outline: none; padding: 6px 0; }}
            QTextEdit {{ background: {bg}; color: {fg}; border: none; font-size: {s + 2}pt; selection-background-color: {fg}; selection-color: {bg}; }}
            QScrollArea {{ border: none; }}
            QScrollBar:vertical {{ background: {bg}; width: 6px; }} QScrollBar::handle:vertical {{ background: {rl}; min-height: 24px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }} QScrollBar::add-page, QScrollBar::sub-page {{ background: {bg}; }}
            QMenu {{ background: {bg}; color: {fg}; border: 1px solid {rl}; padding: 4px 0; }}
            QMenu::item {{ padding: 6px 22px; }} QMenu::item:selected {{ background: {fg}; color: {bg}; }}
            QMenu::separator {{ height: 1px; background: {rl}; margin: 4px 0; }}
            QDialog QLineEdit, QComboBox {{ background: {bg}; color: {fg}; border: 1px solid {rl}; padding: 6px; }}
            QComboBox QAbstractItemView {{ background: {bg}; color: {fg}; selection-background-color: {fg}; selection-color: {bg}; }}
            QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {fg}; padding: 6px 18px; }}
            QPushButton:default {{ background: {fg}; color: {bg}; }}
            QPushButton#quiet {{ border: none; color: {dim}; padding: 6px 4px; text-align: left; }}
            QPushButton#chip {{ padding: 9px 18px; font-size: {s + 1}pt; }}
            QPushButton#chip:hover {{ background: {fg}; color: {bg}; }}
            QPushButton#chipnew {{ padding: 9px 18px; font-size: {s + 1}pt; border: 1px dashed {dim}; color: {dim}; }}
            QToolTip {{ background: {bg}; color: {fg}; border: 1px solid {rl}; }}
        """)
        self.delegate.fg, self.delegate.bg = QtGui.QColor(fg), QtGui.QColor(bg)
        big = QtGui.QFont(family)
        big.setPointSize(s + 1)
        big.setWeight(QtGui.QFont.Light)
        small = QtGui.QFont(family)
        small.setPointSize(max(8, s - 2))
        small.setWeight(QtGui.QFont.Light)
        self.delegate.big, self.delegate.small = big, small
        self.left.setFixedWidth(max(300, s * 25))
        for l in (self.scanner_line, self.status):
            l.setFixedWidth(max(300, s * 25) - 22 - 16 - 34)
        self.list.doItemsLayout()
        self.list.viewport().update()

    def toggle_theme(self):
        self.dark = not self.dark
        self.cfg["dark"] = self.dark
        save_config(self.cfg)
        self.apply_style()
        self.redraw()

    def zoom(self, delta):
        self.font_size = max(9, min(24, self.font_size + delta))
        self.cfg["font_size"] = self.font_size
        save_config(self.cfg)
        self.apply_style()

    def redraw(self):
        if self.stack.currentWidget() is self.review and self.session:
            self.show_review()
        elif self.current:
            self.open_doc(self.current)

    # ---- the three choices -------------------------------------------------------------

    def show_choices(self):
        self.choice["source"].setText(_("from") + ": " + source_name(self.cfg["source"]) + " ▾")
        self.choice["look"].setText(_("look") + ": " + look_name(self.cfg["look"]) + " ▾")
        self.choice["lang"].setText(_("text") + ": " + LANG_NAMES.get(self.cfg["lang"], self.cfg["lang"]) + " ▾")
        self.choice["source"].setToolTip(_("automatic: the feeder when it holds paper, the glass otherwise"))
        self.choice["look"].setToolTip(_("as scanned, or cleaned: white paper, grey, black and white"))
        self.choice["lang"].setToolTip(_("the language the text is read in"))

    def _pick(self, key, options, names, after=None):
        m = QtWidgets.QMenu(self)
        for o in options:
            a = m.addAction(("● " if self.cfg.get(key) == o else "○ ") + names(o))
            a.triggered.connect(lambda _c=False, v=o: self._picked(key, v, after))
        m.exec_(QtGui.QCursor.pos())

    def _picked(self, key, value, after):
        self.cfg[key] = value
        save_config(self.cfg)
        self.show_choices()
        if after:
            after(value)

    def pick_source(self):
        self._pick("source", SOURCES, source_name)

    def pick_look(self):
        self._pick("look", LOOKS, look_name, self.look_picked)

    def pick_lang(self):
        self._pick("lang", LANGS, lambda l: LANG_NAMES[l], self.lang_picked)

    def look_picked(self, look):
        if self.session:
            for p in self.session["pages"]:
                p["look"] = look
            self.session_changed()
            if self.stack.currentWidget() is self.review:
                self.show_review()

    def lang_picked(self, lang):
        if self.session and self.stack.currentWidget() is self.review:
            self.show_review()

    # ---- the list ----------------------------------------------------------------------

    def doc_title(self, d):
        return d.get("name") or when_label(d["created"])

    def doc_sub(self, d, with_folder):
        n = Store.page_count(d)
        parts = [when_label(d["created"])] if d.get("name") else []
        parts.append(_("1 page") if n == 1 else _("%1 pages", n))
        if with_folder and d.get("folder"):
            parts.append(d["folder"])
        state = self.doc_state(d)
        if state:
            parts.append(state)
        return " · ".join(parts)

    def doc_state(self, d):
        if d["id"] in self.queue.working:
            w = self.queue.working[d["id"]]
            return _("reading the text %1", w) if w else _("reading the text…")
        if d.get("ocr") == PENDING and not d.get("remote"):
            return _("text to be read")
        if d.get("ocr") == FAILED and not d.get("remote"):
            return _("text could not be read")
        if d["id"] in self.downloads:
            return _("downloading… %1 %", self.downloads[d["id"]])
        return None

    def refresh_list(self):
        q = self.find.text().strip()
        self.list.blockSignals(True)
        chosen = {i.data(QtCore.Qt.UserRole) for i in self.list.selectedItems()} - {None}
        self.list.clear()
        if self.session and self.session["pages"]:
            n = len(self.session["pages"])
            item = QtWidgets.QListWidgetItem(_("scan not filed yet"))
            item.setData(KIND, "session")
            item.setData(SUB, _("1 page") if n == 1 else _("%1 pages", n))
            self.list.addItem(item)
        if self.place == FOLDERS and not q:
            self.place_bar.hide()
            rows = [("all", _("all scans"), str(self.store.count()), None)]
            rows += [("folder", f, str(self.store.count(f)), f) for f in self.store.folder_names()]
            rows.append(("new", "+ " + _("new folder"), "", None))
            for kind, label, count, name in rows:
                item = QtWidgets.QListWidgetItem(label)
                item.setData(KIND, kind)
                item.setData(SUB, count)
                item.setData(NAME, name)
                self.list.addItem(item)
            self.list.blockSignals(False)
            return
        inside = self.place if self.place not in (None, FOLDERS) else None
        self.place_bar.setText("←  " + (inside or _("all scans")))
        self.place_bar.show()
        if q:
            rows = [(d, snippet) for d, snippet in self.store.search(q)]
        else:
            rows = [(d, None) for d in self.store.all(inside)]
        if not rows:
            item = QtWidgets.QListWidgetItem(_("nothing found") if q else _("no scans here yet"))
            item.setFlags(QtCore.Qt.NoItemFlags)
            self.list.addItem(item)
        for d, snippet in rows:
            item = QtWidgets.QListWidgetItem(self.doc_title(d))
            item.setData(QtCore.Qt.UserRole, d["id"])
            item.setData(SUB, snippet or self.doc_sub(d, inside is None))
            self.list.addItem(item)
            if d["id"] == self.current and self.stack.currentWidget() is self.doc_view and len(chosen) <= 1:
                self.list.setCurrentItem(item)
            elif d["id"] in chosen and len(chosen) > 1:
                item.setSelected(True)
        self.list.blockSignals(False)

    def choose_row(self, doc_id):
        """The list shows which document is open (when it is in the list)."""
        if len(self.list.selectedItems()) > 1:
            return
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        for i in range(self.list.count()):
            if self.list.item(i).data(QtCore.Qt.UserRole) == doc_id:
                self.list.setCurrentRow(i)
                break
        self.list.blockSignals(False)

    def to_folders(self):
        self.place = FOLDERS
        self.find.blockSignals(True); self.find.clear(); self.find.blockSignals(False)
        self.refresh_list()

    def list_moved(self, item, _prev):
        doc_id = item.data(QtCore.Qt.UserRole) if item else None
        if doc_id and len(self.list.selectedItems()) <= 1 and (doc_id != self.current or self.stack.currentWidget() is not self.doc_view):
            self.open_doc(doc_id)

    def item_clicked(self, item):
        kind = item.data(KIND) if item else None
        if kind == "all":
            self.place = None; self.refresh_list()
        elif kind == "folder":
            self.place = item.data(NAME); self.refresh_list()
        elif kind == "new":
            self.new_folder()
        elif kind == "session":
            self.show_review()
        elif item is not None and item.data(QtCore.Qt.UserRole) and len(self.list.selectedItems()) <= 1:
            if self.stack.currentWidget() is not self.doc_view or self.current != item.data(QtCore.Qt.UserRole):
                self.open_doc(item.data(QtCore.Qt.UserRole))

    def item_activated(self, item):
        if item is not None and item.data(QtCore.Qt.UserRole):
            self.open_pdf()           # double click, Enter: the PDF in the system's viewer
        else:
            self.item_clicked(item)

    def selected_docs(self):
        ids = [i.data(QtCore.Qt.UserRole) for i in self.list.selectedItems() if i.data(QtCore.Qt.UserRole)]
        return [d for d in (self.store.get(i) for i in ids) if d]

    def list_menu(self, pos):
        item = self.list.itemAt(pos)
        kind = item.data(KIND) if item else None
        m = QtWidgets.QMenu(self)
        if kind == "folder":
            name = item.data(NAME)
            m.addAction(_("rename…"), lambda: self.rename_folder(name))
            m.addAction(_("delete the folder"), lambda: self.delete_folder(name))
        elif kind in ("all", "new"):
            m.addAction("+ " + _("new folder"), self.new_folder)
        elif item is not None and item.data(QtCore.Qt.UserRole):
            docs = self.selected_docs()
            if item.data(QtCore.Qt.UserRole) not in [d["id"] for d in docs]:
                docs = [self.store.get(item.data(QtCore.Qt.UserRole))]
            self.doc_menu(m, [d for d in docs if d])
        else:
            m.addAction(_("scan"), self.scan)
            m.addAction(_("from files…"), self.import_files)
        m.exec_(self.list.mapToGlobal(pos))

    def new_folder(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "reader's scanner", _("name of the new folder"))
        made = self.store.add_folder(name) if ok and name.strip() else None
        if made:
            self.refresh_list()
            self.sync()
        return made

    def rename_folder(self, name):
        new, ok = QtWidgets.QInputDialog.getText(self, "reader's scanner", _("new name of the folder"), text=name)
        if ok and new.strip() and self.store.rename_folder(name, new):
            self.sync()

    def delete_folder(self, name):
        if QtWidgets.QMessageBox.question(self, "reader's scanner", _("Delete the folder “%1”? Its scans stay, in all scans.", name)) == QtWidgets.QMessageBox.Yes:
            self.store.delete_folder(name)
            if self.place == name:
                self.place = FOLDERS
            self.sync()

    def focus_find(self):
        self.find.setFocus()
        self.find.selectAll()

    def eventFilter(self, obj, e):
        if obj is self.find and e.type() == QtCore.QEvent.KeyPress:
            if e.key() == QtCore.Qt.Key_Escape:
                self.find.clear()
                return True
            if e.key() in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                for i in range(self.list.count()):
                    if self.list.item(i).data(QtCore.Qt.UserRole):
                        self.list.setCurrentRow(i)
                        self.list.setFocus()
                        break
                return True
        return super().eventFilter(obj, e)

    # ---- messages ----------------------------------------------------------------------

    def say(self, title, sub="", actions=()):
        self.message.say(title, sub, actions)
        self.stack.setCurrentWidget(self.message)

    def welcome(self):
        self.current = None
        if not self.naps2.cmd:
            self.naps2_page()
        elif self.store.count() == 0:
            self.say(_("put the pages on the scanner, press « scan »"),
                     _("In the feeder or on the glass: the scanner takes what it finds. The text is read on this computer, and the document becomes a PDF you can search."),
                     (("scan", _("scan")), ("import", _("from files…"))))
        else:
            self.say(_("scan, or choose a document"), "", (("scan", _("scan")),))

    def naps2_page(self):
        self.say(_("Reader's Scanner needs NAPS2"),
                 _("NAPS2 is the free program that talks to the scanner. It is installed separately, from naps2.com. Once it is there, « look again »; pictures and PDFs can be brought in from files meanwhile."),
                 (("naps2", _("get NAPS2")), ("again", _("look again")), ("import", _("from files…"))))

    def message_action(self, key):
        if key == "scan":
            self.scan()
        elif key == "import":
            self.import_files()
        elif key == "cancel":
            self.cancel_scan()
        elif key == "settings":
            self.setup()
        elif key == "naps2":
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(NAPS2_URL))
        elif key == "again":
            self.naps2 = Naps2(DATA_DIR)
            if self.naps2.cmd:
                self.welcome()
                if not self.cfg.get("device"):
                    self.find_scanner()
            else:
                self.naps2_page()
        elif key == "review":
            self.show_review()

    # ---- a document --------------------------------------------------------------------

    def open_doc(self, doc_id):
        d = self.store.get(doc_id)
        if d is None:
            self.welcome()
            return
        self.current = doc_id
        self.cfg["last_doc"] = doc_id
        self.stack.setCurrentWidget(self.doc_view)
        self.choose_row(doc_id)
        self.show_doc_head(d)
        frame = "#777777"
        n = Store.page_count(d)
        if d.get("remote"):
            pdf = self.store.pdf_file(doc_id)
            if self.store.has_pdf(doc_id):
                cache = os.path.join(self.store.dir(doc_id), "render")
                self.pages.show_pages([(lambda i=i: pdf_page(pdf, i, cache)) for i in range(n)], frame)
            else:
                self.pages.show_pages([None] * n, frame)
                self.download(d)
        else:
            sources = []
            for p in d["pages"]:
                shown = self.store.page_file(doc_id, p["id"])
                if os.path.exists(shown):
                    sources.append(lambda f=shown: read_scaled(f, 1100))
                else:
                    sources.append(lambda f=self.store.src_file(doc_id, p["id"]), r=p.get("rotation", 0): read_scaled(f, 1100, r))
            self.pages.show_pages(sources, frame)
        self.shown = (doc_id, d.get("rev", 0), d.get("remote") and self.store.has_pdf(doc_id), d.get("ocr"))
        self.show_body(d)

    def show_doc_head(self, d):
        self.head.setText(self.doc_title(d))
        n = Store.page_count(d)
        parts = [when_label(d["created"]), _("1 page") if n == 1 else _("%1 pages", n)]
        if d.get("folder"):
            parts.append(d["folder"])
        parts.append(LANG_NAMES.get(d.get("lang"), d.get("lang") or ""))
        if reader_name(d.get("readBy")):
            parts.append(reader_name(d.get("readBy")))
        if d.get("remote"):
            parts.append(_("scanned elsewhere"))
        state = self.doc_state(d)
        if state:
            parts.append(state)
        if d["id"] in self.queue.errors and d.get("ocr") == FAILED:
            parts.append(self.queue.errors[d["id"]])
        if d.get("remote") and not self.store.has_pdf(d["id"]) and d["id"] not in self.downloads:
            parts.append(getattr(self, "download_error", {}).get(d["id"]) or "")
        self.info.setText(" · ".join(p for p in parts if p))

    def show_body(self, d):
        text = [t for t in self.store.text(d["id"])]
        self.actions["text"].setText(_("pages") if self.show_text else _("text"))
        if self.show_text:
            if any(t.strip() for t in text):
                out = []
                for i, t in enumerate(text):
                    if len(text) > 1:
                        out.append(f"— {i + 1} —")
                    out.append(reflow(t))
                self.text.setPlainText("\n\n".join(out))
            else:
                self.text.setPlainText(_("No text was found on these pages.") if d.get("ocr") == DONE or d.get("remote") else _("The text has not been read yet."))
            self.body.setCurrentWidget(self.text)
        else:
            self.body.setCurrentWidget(self.pages)

    def toggle_text(self):
        self.show_text = not self.show_text
        d = self.store.get(self.current) if self.current else None
        if d:
            self.show_body(d)

    def download(self, d):
        """The PDF of a document from elsewhere, the first time it is needed."""
        doc_id = d["id"]
        if doc_id in self.downloads:
            return
        if not self.configured():
            self.download_error = dict(getattr(self, "download_error", {}), **{doc_id: _("its PDF needs the WebDAV folder")})
            self.show_doc_head(d)
            return
        self.downloads[doc_id] = 0
        cfg = dict(self.cfg)

        def done(path):
            self.downloads.pop(doc_id, None)
            if not path:
                self.download_error = dict(getattr(self, "download_error", {}), **{doc_id: _("the PDF is not on the server (any more)")})
            self.after_change()

        def failed(message):
            self.downloads.pop(doc_id, None)
            self.download_error = dict(getattr(self, "download_error", {}), **{doc_id: message})
            self.after_change()

        def note(pc):
            self.downloads[doc_id] = pc
            if self.current == doc_id:
                now = self.store.get(doc_id)
                now and self.show_doc_head(now)

        self.run(lambda say: fetch_pdf(self.store, cfg, d, say), done, failed, note)

    def the_pdf(self, d, then):
        """Calls then(path) with the document's PDF, downloading or making it first if needed."""
        doc_id = d["id"]
        if self.store.has_pdf(doc_id):
            then(self.store.pdf_file(doc_id))
        elif d.get("remote"):
            if not self.configured():
                return
            cfg = dict(self.cfg)
            self.run(lambda say: fetch_pdf(self.store, cfg, d, None), lambda p: (self.after_change(), p and then(p)), lambda m: None)
        else:
            pages = [f for f in (self.store.page_file(doc_id, p["id"]) for p in d["pages"]) if os.path.exists(f)]
            if len(pages) == len(d["pages"]) and pages:
                self.run(lambda say: plain_pdf(pages, os.path.join(self.store.dir(doc_id), "ocr-plain.pdf")), lambda p: p and then(p), lambda m: None)

    def named_copy(self, d, pdf):
        folder = os.path.join(DATA_DIR, "share")
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if time.time() - os.path.getmtime(p) > 86400:
                remove(p)
        out = os.path.join(folder, file_name_of(d))
        shutil.copyfile(pdf, out)
        return out

    def open_pdf(self, docs=None):
        for d in (docs or ([self.store.get(self.current)] if self.current else [])):
            if d:
                self.the_pdf(d, lambda p, d=d: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.named_copy(d, p))))

    def documents_dir(self):
        last = self.cfg.get("save_dir")
        if last and os.path.isdir(last):
            return last
        return QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation) or os.path.expanduser("~")

    def save_copy(self, docs=None):
        docs = [d for d in (docs or ([self.store.get(self.current)] if self.current else [])) if d]
        if not docs:
            return
        if len(docs) == 1:
            path, _f = QtWidgets.QFileDialog.getSaveFileName(self, _("save a copy…"), os.path.join(self.documents_dir(), file_name_of(docs[0])), "PDF (*.pdf)")
            if not path:
                return
            self.cfg["save_dir"] = os.path.dirname(path)
            self.the_pdf(docs[0], lambda p: shutil.copyfile(p, path))
        else:
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, _("save the copies in…"), self.documents_dir())
            if not folder:
                return
            self.cfg["save_dir"] = folder
            for d in docs:
                self.the_pdf(d, lambda p, d=d: shutil.copyfile(p, os.path.join(folder, file_name_of(d))))

    def save_images(self, docs):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, _("save the pages as pictures in…"), self.documents_dir())
        if not folder:
            return
        self.cfg["save_dir"] = folder

        def pictures(d, pdf=None):
            base = file_name_of(d, "")[:-1]
            n = Store.page_count(d)
            for i in range(n):
                out = os.path.join(folder, f"{base}.jpg" if n == 1 else f"{base} - {i + 1}.jpg")
                if d.get("remote"):
                    made = pdf_pictures(pdf, os.path.join(self.store.dir(d["id"]), "render"), f"copy{i + 1}", 200, i + 1, i + 1, quality=90)
                    made and move(made[0], out)
                else:
                    src = self.store.page_file(d["id"], d["pages"][i]["id"])
                    if os.path.exists(src):
                        shutil.copyfile(src, out)
                    else:
                        render_page(self.store.src_file(d["id"], d["pages"][i]["id"]), out, d["pages"][i].get("rotation", 0), d["pages"][i].get("look", "original"))

        for d in docs:
            if d.get("remote"):
                self.the_pdf(d, lambda p, d=d: self.run(lambda say: pictures(d, p), lambda r: None, lambda m: None))
            else:
                self.run(lambda say, d=d: pictures(d), lambda r: None, lambda m: None)

    def text_of(self, docs):
        out = []
        for d in docs:
            body = "\n\n".join(reflow(t) for t in self.store.text(d["id"]) if t.strip())
            out.append(body if len(docs) == 1 else title_of(d) + "\n\n" + body)
        return "\n\n\n".join(out).strip()

    def copy_text(self, docs=None):
        docs = [d for d in (docs or ([self.store.get(self.current)] if self.current else [])) if d]
        QtWidgets.QApplication.clipboard().setText(self.text_of(docs))
        self.flash(_("text copied"))

    def flash(self, text):
        elide(self.status, text)
        QtCore.QTimer.singleShot(2500, self.update_status)

    def doc_menu(self, m, docs):
        one = docs[0] if len(docs) == 1 else None
        m.addAction(_("open the PDF"), lambda: self.open_pdf(docs))
        m.addAction(_("save a copy…"), lambda: self.save_copy(docs))
        m.addAction(_("save the pages as pictures…"), lambda: self.save_images(docs))
        m.addAction(_("copy the text"), lambda: self.copy_text(docs))
        m.addSeparator()
        if one:
            m.addAction(_("rename…") + "\tF2", lambda: self.rename_doc(one))
        sub = m.addMenu(_("move to"))
        here = one.get("folder", "") if one else None
        for label, target in [(_("no folder (all scans only)"), "")] + [(f, f) for f in self.store.folder_names()]:
            a = sub.addAction(("● " if target == here else "○ ") + label)
            a.triggered.connect(lambda _c=False, t=target: self.move_docs(docs, t))
        sub.addSeparator()
        sub.addAction("+ " + _("new folder") + "…", lambda: self.move_docs(docs, self.new_folder()))
        if one and not one.get("remote"):
            m.addAction(_("edit the pages"), lambda: self.edit_pages(one))
            m.addAction(_("add pages from the scanner"), lambda: self.edit_pages(one, scan=True))
            again = m.addMenu(_("read the text again in"))
            for l in LANGS:
                a = again.addAction(("● " if l == one.get("lang") else "○ ") + LANG_NAMES[l])
                a.triggered.connect(lambda _c=False, l=l: self.read_again(one, l))
        m.addSeparator()
        m.addAction(_("delete") + "\tDel", lambda: self.delete_docs(docs))

    def show_menu(self):
        d = self.store.get(self.current) if self.current else None
        if d is None:
            return
        m = QtWidgets.QMenu(self)
        self.doc_menu(m, [d])
        m.addSeparator()
        m.addAction(_("from files…") + "\tCtrl+O", self.import_files)
        if self.configured():
            m.addAction(_("sync now") + "\tF5", self.sync)
        m.addAction(_("black on white") if self.dark else _("white on black"), self.toggle_theme)
        m.addAction(_("settings"), self.setup)
        m.exec_(self.more.mapToGlobal(QtCore.QPoint(self.more.width() - m.sizeHint().width(), self.more.height())))

    def rename_current(self):
        d = self.store.get(self.current) if self.current and self.stack.currentWidget() is self.doc_view else None
        if d:
            self.rename_doc(d)

    def rename_doc(self, d):
        name, ok = QtWidgets.QInputDialog.getText(self, "reader's scanner", _("name (empty: the first words of the text)"), text=d.get("name") or "")
        if not ok:
            return
        name = name.strip()
        if not name and not d.get("remote"):
            text = self.store.text(d["id"])
            self.store.update(d["id"], name=first_words(next((t for t in text if t.strip()), "")), named=False, modified=now_ms())
        else:
            self.store.rename(d["id"], name)
        self.sync()

    def move_docs(self, docs, folder):
        if folder is None:
            return
        for d in docs:
            self.store.move(d["id"], folder)
        self.sync()

    def delete_selected(self):
        if self.list.hasFocus() or self.stack.currentWidget() is self.doc_view:
            docs = self.selected_docs() or ([self.store.get(self.current)] if self.current and self.stack.currentWidget() is self.doc_view else [])
            self.delete_docs([d for d in docs if d])

    def delete_docs(self, docs):
        if not docs:
            return
        q = _("Delete “%1”?", self.doc_title(docs[0])) if len(docs) == 1 else _("Delete these %1 documents?", len(docs))
        if QtWidgets.QMessageBox.question(self, "reader's scanner", q) != QtWidgets.QMessageBox.Yes:
            return
        for d in docs:
            if d["id"] == self.current:
                self.current = None
            self.store.delete(d["id"])
        if self.current is None:
            self.welcome()
        self.sync()

    def read_again(self, d, lang):
        self.store.read_again(d["id"], lang)
        self.queue.enqueue(d["id"])

    # ---- scanning ----------------------------------------------------------------------

    def find_scanner(self):
        if self.searching or not self.naps2.cmd:
            return
        self.searching = True
        self.update_status()

        def found(devices):
            self.searching = False
            routes = pick_routes(devices, None)
            if routes:
                self.cfg["device"] = {"key": routes[0]["key"], "name": routes[0]["name"], "routes": routes}
                save_config(self.cfg)
            self.update_status()

        self.run(lambda say: self.naps2.devices(), found, lambda m: found([]))

    def scan(self):
        if self.scanning:
            self.cancel_scan()
            return
        if not self.naps2.cmd:
            self.naps2 = Naps2(DATA_DIR)
            if not self.naps2.cmd:
                self.naps2_page()
                return
        self.scanning = True
        self.trouble = ""
        self.scan_button.setText(_("cancel"))
        self.scan_button.setObjectName("scanning")
        self.scan_button.setStyle(self.scan_button.style())
        self.say(_("scanning…"), source_name(self.cfg["source"]) if self.cfg["source"] != "auto" else "", (("cancel", _("cancel")),))
        cfg = dict(self.cfg)
        out = os.path.join(DATA_DIR, "incoming")

        def work(say):
            r = scan_pages(self.naps2, cfg, cfg["source"], out, on_page=lambda n: say(("page", n)), on_state=lambda s: say(("state", s)))
            if r.get("files"):
                say(("state", "upright"))
                r["turn"] = upright_rotations(r["files"], self.reader)
            return r

        self.run(work, self.scanned, lambda m: self.scanned({"files": [], "error": "unknown", "detail": m}), self.scan_note)

    def scan_note(self, note):
        if not self.scanning:
            return
        kind, value = note
        if kind == "page":
            self.message.title.setText(_("page %1", value))
        elif value == "upright":
            self.message.sub.setText(_("setting the pages upright…")); self.message.sub.setVisible(True)
        elif value == "waiting":
            self.message.sub.setText(_("the scanner is getting ready…")); self.message.sub.setVisible(True)
        elif value == "searching":
            self.message.title.setText(_("scanning…"))
            self.message.sub.setText(_("looking for the scanner…")); self.message.sub.setVisible(True)
        else:
            self.message.title.setText(_("scanning…"))
            self.message.sub.setText({"feeder": _("from the feeder"), "glass": _("from the glass"), "duplex": _("both sides")}.get(value, "")); self.message.sub.setVisible(True)

    def cancel_scan(self):
        if self.scanning:
            self.naps2.cancel()

    def scan_over(self):
        self.scanning = False
        self.scan_button.setText(_("scan"))
        self.scan_button.setObjectName("scan")
        self.scan_button.setStyle(self.scan_button.style())

    def scanned(self, result):
        self.scan_over()
        if result.get("device"):
            self.cfg["device"] = result["device"]
            save_config(self.cfg)
            self.update_status()
        if result.get("error"):
            self.scan_failed(result["error"], result.get("detail", ""))
            return
        self.add_pages(result["files"], result.get("blank", []), result.get("turn"))

    def scan_failed(self, code, detail):
        if code == "nonaps2":
            self.naps2_page()
            return
        has = bool(self.session and self.session["pages"])
        if code == "cancelled":
            if has:
                self.show_review()
            elif self.current and self.store.get(self.current):
                self.open_doc(self.current)
            else:
                self.welcome()
            return
        hints = {"empty": _("Put the pages in the feeder, or choose « glass »."),
                 "nodevice": _("Switch the scanner on and check its cable; then scan again."),
                 "notfound": _("Switch the scanner on and check its cable; then scan again."),
                 "offline": _("Switch the scanner on and check its cable; then scan again.")}
        actions = [("scan", _("scan again"))]
        if has:
            actions.append(("review", _("back to the pages")))
        actions.append(("import", _("from files…")))
        self.say(error_text(code, detail), hints.get(code, detail if detail and detail != error_text(code, detail) else ""), actions)

    # ---- the pages just scanned ----------------------------------------------------------

    def session_file(self):
        return os.path.join(self.session_dir, "session.json")

    def save_session(self):
        if self.session is None:
            shutil.rmtree(self.session_dir, ignore_errors=True)
            return
        os.makedirs(self.session_dir, exist_ok=True)
        with open(self.session_file() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.session, f)
        replace(self.session_file() + ".tmp", self.session_file())

    def restore_session(self):
        """Pages scanned and not filed when the app was closed are still there."""
        try:
            with open(self.session_file(), encoding="utf-8") as f:
                s = json.load(f)
            s["pages"] = [p for p in s["pages"] if os.path.exists(p["src"])]
            s["blank"] = [p for p in s.get("blank", []) if os.path.exists(p["src"])]
            if s["pages"] and (not s.get("doc") or self.store.get(s["doc"])):
                self.session = s
        except (OSError, ValueError, KeyError):
            pass
        if self.session is None:
            shutil.rmtree(self.session_dir, ignore_errors=True)

    def new_session(self, doc=None):
        shutil.rmtree(self.session_dir, ignore_errors=True)
        os.makedirs(self.session_dir, exist_ok=True)
        self.session = {"doc": doc, "pages": [], "blank": [], "created": now_ms()}

    def add_pages(self, files, blank=(), turn=None):
        if self.session is None:
            self.new_session()
        os.makedirs(self.session_dir, exist_ok=True)
        look = self.session["pages"][0].get("look") if self.session["pages"] else self.cfg["look"]
        for f in list(files) + list(blank):
            pid = new_id()[:8]
            dst = os.path.join(self.session_dir, pid + ".jpg")
            move(f, dst)
            page = {"id": pid, "src": dst, "rotation": (turn or {}).get(f, 0), "look": look}
            (self.session["blank"] if f in blank else self.session["pages"]).append(page)
        self.save_session()
        self.show_review()
        self.refresh_list()

    def show_review(self):
        s = self.session
        if s is None:
            self.welcome()
            return
        look = s["pages"][0].get("look", "original") if s["pages"] else self.cfg["look"]
        lang = self.cfg["lang"]
        self.review.show_session(s["pages"], s["blank"], bool(s.get("doc")), look, lang, "#777777")
        default = self.place if self.place not in (None, FOLDERS) else (self.cfg.get("last_folder") or "")
        self.review.show_folders(self.store.folder_names(), default)
        self.stack.setCurrentWidget(self.review)
        self.list.blockSignals(True); self.list.clearSelection(); self.list.setCurrentRow(-1); self.list.blockSignals(False)
        if s.get("doc"):
            self.review.save.setFocus()
        else:
            self.review.name.setFocus()

    def session_changed(self):
        if self.session is not None and not self.session["pages"] and not self.session["blank"]:
            self.discard_session(ask=False)
            return
        self.save_session()
        self.refresh_list()

    def keep_blank(self):
        if self.session:
            self.session["pages"] += self.session["blank"]
            self.session["blank"] = []
            self.save_session()
            self.show_review()

    def discard_session(self, ask=True):
        s = self.session
        if s is None:
            return
        n = len(s["pages"])
        if ask and n and not s.get("doc"):
            q = _("Discard this page?") if n == 1 else _("Discard these %1 pages?", n)
            if QtWidgets.QMessageBox.question(self, "reader's scanner", q) != QtWidgets.QMessageBox.Yes:
                return
        doc = s.get("doc")
        self.session = None
        self.save_session()
        self.refresh_list()
        if doc and self.store.get(doc):
            self.open_doc(doc)
        elif self.current and self.store.get(self.current):
            self.open_doc(self.current)
        else:
            self.welcome()

    def file_in_new_folder(self):
        made = self.new_folder()
        if made and self.session and self.session["pages"]:
            self.file_session(made, self.review.name.text().strip())
        elif made:
            self.show_review()

    def file_session(self, folder, name):
        """Files the pages: a new document in `folder`, named or to be named by its text; or the
        new pages of the document being edited. The text is read afterwards."""
        s = self.session
        if s is None or not s["pages"]:
            return
        old = self.store.get(s["doc"]) if s.get("doc") else None
        doc_id = old["id"] if old else new_id()
        os.makedirs(self.store.dir(doc_id), exist_ok=True)
        pages = []
        for p in s["pages"]:
            pid = new_id()[:8]
            move(p["src"], self.store.src_file(doc_id, pid))
            pages.append({"id": pid, "rotation": p.get("rotation", 0), "look": p.get("look", "original")})
        now = now_ms()
        if old:
            doc = dict(old, pages=pages, lang=self.cfg["lang"], ocr=PENDING, rev=old.get("rev", 0) + 1, modified=now)
            if not old.get("named"):
                doc["name"] = None
        else:
            doc = {"id": doc_id, "created": s.get("created") or now, "modified": now, "name": name or None, "named": bool(name),
                   "folder": folder or "", "lang": self.cfg["lang"], "pages": pages, "ocr": PENDING, "rev": 0, "readBy": "", "remote": False, "pageCount": 0}
            self.cfg["last_folder"] = folder or ""
            self.place = folder if folder else None
        self.session = None
        self.save_session()
        self.review.name.clear()
        self.store.put(doc)
        save_config(self.cfg)
        self.queue.enqueue(doc_id)
        self.show_text = False
        self.open_doc(doc_id)
        self.refresh_list()

    def edit_pages(self, d, scan=False):
        if self.session and self.session["pages"] and self.session.get("doc") != d["id"]:
            self.show_review()         # one thing at a time: the scan not filed yet comes first
            return
        self.new_session(doc=d["id"])
        for p in d["pages"]:
            pid = new_id()[:8]
            dst = os.path.join(self.session_dir, pid + ".jpg")
            shutil.copyfile(self.store.src_file(d["id"], p["id"]), dst)
            self.session["pages"].append({"id": pid, "src": dst, "rotation": p.get("rotation", 0), "look": p.get("look", "original")})
        self.save_session()
        self.show_review()
        self.refresh_list()
        if scan:
            self.scan()

    # ---- from files ----------------------------------------------------------------------

    def import_files(self, paths=None):
        if not paths:
            paths, _f = QtWidgets.QFileDialog.getOpenFileNames(self, _("from files…"), self.documents_dir(),
                                                               _("Pictures and PDFs") + " (" + " ".join("*" + e for e in IMPORTABLE) + ")")
        paths = [p for p in paths or [] if p.lower().endswith(IMPORTABLE)]
        if not paths:
            return
        out = os.path.join(DATA_DIR, "incoming")
        self.say(_("bringing the pages in…"))

        def work(say):
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(out)
            files = []
            for p in paths:
                if p.lower().endswith(".pdf"):
                    files += pdf_pictures(p, out, f"f{len(files):04d}", DPI)
                else:
                    dst = os.path.join(out, f"f{len(files):04d}.jpg")
                    import_image(p, dst)
                    files.append(dst)
            return files, upright_rotations(files, self.reader)

        def done(result):
            files, turn = result
            if files:
                self.add_pages(files, turn=turn)
            else:
                self.say(_("nothing could be read in these files"), "", (("import", _("from files…")),))

        self.run(work, done, lambda m: self.say(_("nothing could be read in these files"), m, (("import", _("from files…")),)))

    def dragEnterEvent(self, e):
        if any(u.toLocalFile().lower().endswith(IMPORTABLE) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.import_files([u.toLocalFile() for u in e.mimeData().urls()])

    def escape(self):
        if self.scanning:
            self.cancel_scan()
        elif self.find.text():
            self.find.clear()
        elif self.stack.currentWidget() is self.review:
            self.discard_session()
        elif self.place != FOLDERS:
            self.to_folders()

    # ---- changes, sync -------------------------------------------------------------------

    def after_change(self):
        """The store changed (the text was read, a sync brought the other side): redraw."""
        self.refresh_list()
        if self.stack.currentWidget() is self.doc_view and self.current:
            d = self.store.get(self.current)
            if d is None:
                self.welcome()
                return
            state = (d["id"], d.get("rev", 0), d.get("remote") and self.store.has_pdf(d["id"]), d.get("ocr"))
            if state != getattr(self, "shown", None):
                self.open_doc(d["id"])
            else:
                self.show_doc_head(d)
        elif self.stack.currentWidget() is self.review and self.session:
            self.review.show_folders(self.store.folder_names(), self.review.default_folder)

    def after_read(self, doc_id):
        self.after_change()
        self.sync()

    def run(self, fn, on_done, on_failed, on_note=None):
        thread = QtCore.QThread(self)
        job = Job(fn)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.done.connect(on_done)
        job.failed.connect(on_failed)
        if on_note:
            job.note.connect(on_note)
        job.done.connect(thread.quit)
        job.failed.connect(thread.quit)
        pair = (thread, job)      # kept alive until the thread ends: a collected job never runs
        thread.finished.connect(lambda: self.threads.remove(pair) if pair in self.threads else None)
        self.threads.append(pair)
        thread.start()

    def ensure_pdf(self, d):
        if self.store.has_pdf(d["id"]):
            return self.store.pdf_file(d["id"])
        pages = [self.store.page_file(d["id"], p["id"]) for p in d.get("pages", [])]
        if pages and all(os.path.exists(p) for p in pages):
            return plain_pdf(pages, self.store.pdf_file(d["id"]))
        return None

    def sync(self):
        if not self.configured():
            self.update_status()
            return
        if self.syncing:
            self.sync_again = True
            return
        self.syncing = True
        self.update_status()
        cfg = dict(self.cfg)
        self.run(lambda say: sync_run(self.store, cfg, self.ensure_pdf), self.synced, self.sync_failed)

    def synced(self, result):
        self.syncing = False
        up, down, deleted = result
        arrows = "".join(f" {n}{a}" for n, a in ((up, "↑"), (down, "↓"), (deleted, "−")) if n)
        self.last_status = _("synced %1", datetime.now().strftime("%H:%M")) + arrows
        self.update_status()
        if self.sync_again:
            self.sync_again = False
            self.sync()

    def sync_failed(self, message):
        self.syncing = False
        self.sync_again = False
        self.last_status = message
        self.update_status()

    def update_status(self):
        device = (self.cfg.get("device") or {}).get("name")
        if not self.naps2.cmd:
            scanner = _("NAPS2 is not installed")
        elif self.searching:
            scanner = _("looking for the scanner…")
        elif device:
            scanner = re.sub(r"\s+\([^()]*\)$", "", device)
        else:
            scanner = _("no scanner found yet")
        if getattr(self, "trouble", ""):
            sync = self.trouble
        elif self.syncing:
            sync = _("syncing…")
        elif not self.configured():
            sync = _("on this computer only")
        else:
            sync = self.last_status
        elide(self.scanner_line, scanner)
        elide(self.status, sync)
        if not self.configured():
            self.status.setToolTip(_("Ctrl+, to set up a WebDAV folder shared with the phone"))

    def setup(self):
        dlg = SettingsDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        v = dlg.values()
        moved = (v["server"].rstrip("/"), v["folder"]) != (self.cfg.get("server", "").rstrip("/"), self.cfg.get("folder", "Scans"))
        if moved and self.cfg.get("server"):
            self.store.forget_server()
        self.cfg.update(v)
        save_config(self.cfg)
        self.reader.prefer_best = bool(self.cfg.get("best", True))
        self.last_status = ""
        self.apply_style()
        self.refresh_list()
        self.update_status()
        self.sync()

    # ---- window ------------------------------------------------------------------------

    def closeEvent(self, e):
        """What was scanned is finished before leaving: the text read, the PDF sent."""
        if self.quitting:
            e.accept()
            return
        e.ignore()
        self.quitting = True
        if self.scanning:
            self.cancel_scan()
        save_config(self.cfg)
        self.hide()
        self.deadline = time.time() + 180
        self.leave_timer = QtCore.QTimer(self, interval=300, timeout=self.leave)
        self.leave_timer.start()
        self.leave_synced = False

    def leave(self):
        busy = not self.queue.idle() or self.syncing or any(t.isRunning() for t, _j in self.threads)
        if busy and time.time() < self.deadline:
            return
        if not self.leave_synced and self.configured() and time.time() < self.deadline:
            self.leave_synced = True
            self.sync()
            return
        self.leave_timer.stop()
        QtWidgets.QApplication.quit()


def _icon():
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for name in (APP + ".png", os.path.join("packaging", APP + ".png")):
        path = os.path.join(here, name)
        if os.path.exists(path):
            return QtGui.QIcon(path)
    return QtGui.QIcon.fromTheme(APP)


def self_test(report):
    """What a build must be able to do before it is given to anyone, without a scanner and
    without the network: `--self-test REPORT` writes what it found and leaves with 0 or 1."""
    import tempfile
    from PIL import ImageDraw, ImageFont
    lines, bad = [], []

    def check(label, ok, detail=""):
        lines.append(("ok    " if ok else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))
        ok or bad.append(label)

    tmp = tempfile.mkdtemp(prefix="rs-self-")
    try:
        lines.append(f"Reader's Scanner {VERSION} on {sys.platform}, frozen: {bool(getattr(sys, 'frozen', False))}")
        reader = Reader(tmp)
        reader.prefer_best = False                     # no network here: the models that came with the app
        exe = reader.exe()
        check("Tesseract is there", bool(exe), str(exe))
        folder, langs = reader.system()
        check("with its models for the orientation and for English", "osd" in langs and "eng" in langs, f"{folder}: {langs}")
        page = Image.new("RGB", (2480, 3508), "white")
        draw = ImageDraw.Draw(page)
        try:
            font = ImageFont.load_default(size=110)
        except TypeError:
            font = ImageFont.load_default()
        for i, words in enumerate(("Invoice number 2026", "Total amount 106.37", "Thank you for your order")):
            draw.text((260, 400 + i * 260), words, font=font, fill="black")
        try:                                           # a letter's worth of lines: the orientation needs them
            small = ImageFont.load_default(size=58)
        except TypeError:
            small = font
        for i in range(14):
            draw.text((260, 1300 + i * 130), "We thank you for your trust and remain at your disposal for any question.", font=small, fill="black")
        jpg = os.path.join(tmp, "page.jpg")
        page.save(jpg, "JPEG", quality=JPEG_QUALITY, dpi=(DPI, DPI))
        text, layers = [""], [[]]
        try:
            text, layers, _by = reader.read([jpg], "eng", os.path.join(tmp, "read"))
        except ReadError as e:
            check("a page is read", False, str(e))
        check("a page is read, figures included", "Invoice number 2026" in text[0] and "106.37" in text[0], text[0][:120].replace("\n", " / "))
        page.rotate(180).save(os.path.join(tmp, "down.jpg"), "JPEG", quality=JPEG_QUALITY, dpi=(DPI, DPI))
        turn = upright_rotations([os.path.join(tmp, "down.jpg")], reader)
        check("a page upside down is seen as such", list(turn.values()) == [180], str(turn))
        pdf = write_pdf([jpg], os.path.join(tmp, "doc.pdf"), "self-test", layers)
        check("the PDF is written", bool(pdf) and os.path.getsize(pdf) > 50_000)
        try:
            import pypdfium2
            with _pdfium_lock:
                doc = pypdfium2.PdfDocument(pdf)
                inside = doc[0].get_textpage().get_text_range()
                doc.close()
            check("its text can be found in it", "Invoice number 2026" in " ".join(inside.split()), inside[:80])
        except ImportError:
            lines.append("      (pypdfium2 is not here: the PDF's text is checked by the test suite with poppler)")
        try:
            made = pdf_pictures(pdf, os.path.join(tmp, "render"), "p", 100)
            check("and its pages shown", len(made) == 1 and Image.open(made[0]).size[0] in range(820, 835), str(made))
        except Exception as e:
            check("and its pages shown", False, str(e))
        naps2 = Naps2(tmp)
        lines.append(f"      NAPS2: {' '.join(naps2.cmd) + ' ' + str(naps2.version) if naps2.cmd else 'not installed on this computer'}")
        if naps2.cmd:
            found = naps2.devices()
            lines.append(f"      scanners: {[d['name'] for d in found] or 'none'}")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        w = Main()
        w.show()
        app.processEvents()
        check("the window opens", w.isVisible() and w.scan_button.text() == _("scan"))
        w.quitting = True
        w.close()
    except Exception as e:                             # whatever it is, it goes in the report
        import traceback
        check("no surprise", False, f"{e!r} {traceback.format_exc()[-600:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines.append("FAILED: " + ", ".join(bad) if bad else "all good")
    out = "\n".join(lines) + "\n"
    if report and report != "-":
        with open(report, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        sys.stdout.write(out)
        sys.stdout.flush()
    os._exit(1 if bad else 0)


def unexpected(kind, error, trace):
    """An error nobody caught: written down, said in the status line — and the app goes on.
    (Left alone, PyQt ends the whole program on the spot, scan in hand.)"""
    import traceback
    words = "".join(traceback.format_exception(kind, error, trace))
    sys.stderr.write(words)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} · {VERSION} · {sys.platform}\n{words}\n")
    except OSError:
        pass
    for w in QtWidgets.QApplication.topLevelWidgets() if QtWidgets.QApplication.instance() else ():
        if isinstance(w, Main):
            w.trouble = _("an error — see errors.log")
            w.status.setToolTip(os.path.join(DATA_DIR, "errors.log"))
            QtCore.QTimer.singleShot(0, w.update_status)


def main():
    sys.excepthook = unexpected
    threading.excepthook = lambda a: unexpected(a.exc_type, a.exc_value, a.exc_traceback)
    if "--self-test" in sys.argv:
        at = sys.argv.index("--self-test")
        self_test(sys.argv[at + 1] if len(sys.argv) > at + 1 else "-")
    credentials_cli(sys.argv)
    try:
        locale.setlocale(locale.LC_TIME, "")
    except locale.Error:
        pass
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("reader's scanner")
    app.setDesktopFileName(APP)
    app.setWindowIcon(_icon())
    app.setQuitOnLastWindowClosed(False)
    w = Main()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
