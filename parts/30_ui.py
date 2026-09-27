

# ------------------------------------------------------------------------------------------
# Config and the Reader's credentials file (one JSON file, one section per app; this app's
# section is the phone's: "readers-scanner", so a file exported there sets this one up)
# ------------------------------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600)
    replace(tmp, CONFIG_FILE)


CREDENTIAL_KEYS = ("server", "folder", "username", "password")
CREDENTIALS_FORMAT = "readers-credentials"
# when this app's section is absent: another app's server and login (never its folder)
CREDENTIAL_FALLBACK = {"readers-notes": "Reader's Notes", "readers-recorder": "Reader's Recorder"}


def export_credentials(cfg, path):
    path = os.path.expanduser(path)
    data = {}
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except ValueError:
                raise ValueError(_("not a Reader's credentials file"))
        if not isinstance(data, dict) or data.get("format") != CREDENTIALS_FORMAT:
            raise ValueError(_("not a Reader's credentials file"))
    data.update({"format": CREDENTIALS_FORMAT, "version": 1})
    data[APP] = {k: cfg[k] for k in CREDENTIAL_KEYS if cfg.get(k) not in (None, "")}
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    replace(path + ".tmp", path)
    return path


def import_credentials(cfg, path):
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError:
            raise ValueError(_("not a Reader's credentials file"))
    if not isinstance(data, dict) or data.get("format") != CREDENTIALS_FORMAT:
        raise ValueError(_("not a Reader's credentials file"))
    section, keys, message = data.get(APP), CREDENTIAL_KEYS, _("credentials imported")
    if not isinstance(section, dict) or not section:
        other = next((n for n in CREDENTIAL_FALLBACK if isinstance(data.get(n), dict) and data[n].get("server")), None)
        if other is None:
            raise ValueError(_("this file holds nothing for %1", "Reader's Scanner"))
        section, keys = data[other], ("server", "username", "password")
        message = _("server and login taken from %1", CREDENTIAL_FALLBACK[other])
    for k in keys:
        if k in section:
            cfg[k] = section[k]
    return message


def credentials_cli(argv):
    for flag in ("--export-credentials", "--import-credentials"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                print(f"{flag} FILE", file=sys.stderr); sys.exit(2)
            cfg = load_config()
            try:
                if flag == "--export-credentials":
                    print(_("credentials exported to %1 — the file holds your passwords: keep it private", export_credentials(cfg, argv[i + 1])))
                else:
                    message = import_credentials(cfg, argv[i + 1]); save_config(cfg); print(message)
            except (OSError, ValueError) as e:
                print(str(e), file=sys.stderr); sys.exit(1)
            sys.exit(0)


# ------------------------------------------------------------------------------------------
# UI pieces
# ------------------------------------------------------------------------------------------

class Job(QtCore.QObject):
    """Runs one job off the UI thread; `note` carries what it says on the way."""
    done = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    note = QtCore.pyqtSignal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.note.emit))
        except Exception as e:
            self.failed.emit(str(e))


class Loader(QtCore.QObject):
    """Pictures made off the UI thread, a few at a time."""
    loaded = QtCore.pyqtSignal(object, QtGui.QImage)

    def __init__(self):
        super().__init__()
        self.pool = QtCore.QThreadPool()
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 2) - 1)))

    def load(self, key, fn):
        loader = self

        class Run(QtCore.QRunnable):
            def run(self):
                try:
                    img = fn()
                except Exception:
                    img = None
                loader.loaded.emit(key, img if img is not None else QtGui.QImage())
        self.pool.start(Run())


def qimage_of(img):
    img = img.convert("RGB")
    data = img.tobytes()
    return QtGui.QImage(data, img.width, img.height, img.width * 3, QtGui.QImage.Format_RGB888).copy()


def read_scaled(path, width, rotation=0):
    """A picture file, decoded at about the width asked (fast for JPEG), upright."""
    r = QtGui.QImageReader(path)
    size = r.size()
    if rotation % 180:
        size = size.transposed()
    if size.isValid() and size.width() > width:
        s = r.size()
        f = width / size.width()
        r.setScaledSize(QtCore.QSize(max(1, round(s.width() * f)), max(1, round(s.height() * f))))
    img = r.read()
    if rotation % 360 and not img.isNull():
        img = img.transformed(QtGui.QTransform().rotate(rotation))
    return img


def clear(layout):
    """Empties a layout: its widgets disappear at once and are deleted at the next idle moment.
    They keep their parent until then: without one, a widget belongs to Python, which destroys
    it as soon as nothing names it — in the middle of its own click, when the click is what
    empties the layout (a crash on Windows)."""
    while layout.count():
        w = layout.takeAt(0).widget()
        if w is not None:
            w.hide()
            w.deleteLater()


def elide(label, text):
    """The text on one line of the label's width, « … » in the middle of what does not fit."""
    label.setToolTip(text)
    label.setText(label.fontMetrics().elidedText(text, QtCore.Qt.ElideRight, max(40, label.width())))


class Clickable(QtWidgets.QLabel):
    clicked = QtCore.pyqtSignal()

    def __init__(self, text="", name=None):
        super().__init__(text)
        if name:
            self.setObjectName(name)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()


class Flow(QtWidgets.QLayout):
    """Widgets side by side, wrapping to the next line."""

    def __init__(self, parent=None, gap=10):
        super().__init__(parent)
        self.items, self.gap = [], gap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._lay(QtCore.QRect(0, 0, w, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._lay(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QtCore.QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _lay(self, rect, move):
        x, y, line = rect.x(), rect.y(), 0
        for it in self.items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self.gap, 0
            if move:
                it.setGeometry(QtCore.QRect(x, y, w, h))
            x += w + self.gap
            line = max(line, h)
        return y + line - rect.y()


KIND = QtCore.Qt.UserRole + 2      # a folder row: "all", "folder" or "new"
SUB = QtCore.Qt.UserRole + 1
NAME = QtCore.Qt.UserRole + 3
FOLDERS = "\x00folders"            # the place: the list of folders


class RowDelegate(QtWidgets.QStyledItemDelegate):
    """The list: folders (a small folder, the name, the count) or documents (the name, then a
    dim line). The chosen row is drawn inverted, like every selection in the Reader's apps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fg, self.bg = QtGui.QColor("#000"), QtGui.QColor("#fff")
        self.big, self.small = QtGui.QFont(), QtGui.QFont()

    def sizeHint(self, option, index):
        fb, fs = QtGui.QFontMetrics(self.big), QtGui.QFontMetrics(self.small)
        if index.data(KIND):
            return QtCore.QSize(100, fb.height() + 22)
        if index.data(QtCore.Qt.UserRole) is None:
            return QtCore.QSize(100, fs.height() * 3 + 24)
        return QtCore.QSize(100, fb.height() + fs.height() + 22)

    def paint(self, p, option, index):
        p.save()
        r = option.rect.adjusted(22, 10, -22, -10)
        sel = bool(option.state & QtWidgets.QStyle.State_Selected) and not index.data(KIND)
        fg, bg = (self.bg, self.fg) if sel else (self.fg, self.bg)
        p.fillRect(option.rect, bg)
        dim = QtGui.QColor(fg)
        dim.setAlphaF(0.6 if sel else 0.55)
        fb, fs = QtGui.QFontMetrics(self.big), QtGui.QFontMetrics(self.small)
        kind = index.data(KIND)
        if kind:
            gh = int(fb.height() * 0.62); gw = int(gh * 1.3)
            if kind == "session":      # pages waiting to be filed: a sheet
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                p.setPen(QtGui.QPen(fg, 1.6)); p.setBrush(fg)
                sheet = QtCore.QRectF(r.left() + gw * 0.2, r.top() + (fb.height() - gh * 1.25) / 2, gw * 0.62, gh * 1.25)
                p.drawRect(sheet)
            else:
                folder_glyph(p, QtCore.QRectF(r.left(), r.top() + (fb.height() - gh) // 2, gw, gh), dim if kind == "new" else fg, kind == "all", kind == "new")
            count = index.data(SUB) or ""
            cw = fs.horizontalAdvance(count) + 8 if count else 0
            p.setFont(self.big)
            p.setPen(dim if kind == "new" else fg)
            tr = QtCore.QRect(r.left() + gw + 16, r.top(), r.width() - gw - 16 - cw, fb.height())
            p.drawText(tr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, fb.elidedText(index.data(QtCore.Qt.DisplayRole), QtCore.Qt.ElideRight, tr.width()))
            if count:
                p.setFont(self.small); p.setPen(dim)
                p.drawText(QtCore.QRect(r.right() - cw, r.top(), cw, fb.height()), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, count)
        elif index.data(QtCore.Qt.UserRole) is None:      # the empty-list message
            p.setFont(self.small)
            p.setPen(dim)
            p.drawText(r, QtCore.Qt.TextWordWrap | QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, index.data(QtCore.Qt.DisplayRole))
        else:
            p.setFont(self.big)
            p.setPen(fg)
            p.drawText(QtCore.QRect(r.left(), r.top(), r.width(), fb.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       fb.elidedText(index.data(QtCore.Qt.DisplayRole), QtCore.Qt.ElideRight, r.width()))
            p.setFont(self.small)
            p.setPen(dim)
            p.drawText(QtCore.QRect(r.left(), r.top() + fb.height(), r.width(), fs.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       fs.elidedText(index.data(SUB) or "", QtCore.Qt.ElideRight, r.width()))
        p.restore()


def folder_glyph(p, rect, colour, filled, dashed):
    """A folder, drawn small in the text's colour: tab on the top left (as on the phone)."""
    w, h, x, y = rect.width(), rect.height(), rect.left(), rect.top()
    tab, tw, rr = h * 0.18, w * 0.42, max(1.5, h * 0.12)
    path = QtGui.QPainterPath()
    path.moveTo(x + rr, y); path.lineTo(x + tw - tab * 0.4, y); path.lineTo(x + tw + tab * 0.6, y + tab)
    path.lineTo(x + w - rr, y + tab); path.quadTo(x + w, y + tab, x + w, y + tab + rr)
    path.lineTo(x + w, y + h - rr); path.quadTo(x + w, y + h, x + w - rr, y + h)
    path.lineTo(x + rr, y + h); path.quadTo(x, y + h, x, y + h - rr)
    path.lineTo(x, y + rr); path.quadTo(x, y, x + rr, y); path.closeSubpath()
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    if filled:
        p.fillPath(path, colour)
    else:
        pen = QtGui.QPen(colour, 1.6)
        if dashed:
            pen.setStyle(QtCore.Qt.DashLine)
        p.setPen(pen); p.setBrush(QtCore.Qt.NoBrush); p.drawPath(path)


class Picture(QtWidgets.QWidget):
    """One page: the picture at the width it is given, a thin frame, an empty sheet until it is there."""

    def __init__(self, ratio=0.707, frame="#888"):
        super().__init__()
        self.image, self.ratio, self.frame = None, ratio, QtGui.QColor(frame)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_image(self, img):
        if img is not None and not img.isNull():
            self.image, self.ratio = img, img.width() / max(1, img.height())
        self.updateGeometry()
        self.update()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w / self.ratio)

    def sizeHint(self):
        w = self.width() or 400
        return QtCore.QSize(w, self.heightForWidth(w))

    def resizeEvent(self, e):
        self.setFixedHeight(self.heightForWidth(self.width()))

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        r = self.rect().adjusted(0, 0, -1, -1)
        if self.image is not None:
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.drawImage(QtCore.QRectF(r), self.image)
        p.setPen(QtGui.QPen(self.frame, 1))
        p.drawRect(r)


class Pages(QtWidgets.QScrollArea):
    """A document's pages, one under the other, on a column."""

    def __init__(self, loader):
        super().__init__()
        self.loader = loader
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.inner = QtWidgets.QWidget()
        self.box = QtWidgets.QVBoxLayout(self.inner)
        self.box.setContentsMargins(36, 22, 36, 22)
        self.box.setSpacing(18)
        self.box.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        self.setWidget(self.inner)
        self.pictures, self.token = [], 0
        loader.loaded.connect(self._loaded)

    def show_pages(self, sources, frame):
        """sources: one function per page, giving its picture."""
        self.token += 1
        clear(self.box)
        self.pictures = []
        for i, fn in enumerate(sources):
            pic = Picture(frame=frame)
            pic.setMaximumWidth(900)
            self.box.addWidget(pic)
            self.pictures.append(pic)
            if fn:
                self.loader.load(("page", self.token, i), fn)
        self.verticalScrollBar().setValue(0)

    def _loaded(self, key, img):
        if isinstance(key, tuple) and key[0] == "page" and key[1] == self.token and key[2] < len(self.pictures):
            self.pictures[key[2]].set_image(img)


class Message(QtWidgets.QWidget):
    """The right side when there is no document: a line, a dim one under it, and what can be done."""
    action = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(60, 40, 60, 40)
        box.addStretch(2)
        self.title = QtWidgets.QLabel("")
        self.title.setObjectName("big")
        self.title.setWordWrap(True)
        self.title.setAlignment(QtCore.Qt.AlignHCenter)
        box.addWidget(self.title)
        self.sub = QtWidgets.QLabel("")
        self.sub.setObjectName("dim")
        self.sub.setWordWrap(True)
        self.sub.setAlignment(QtCore.Qt.AlignHCenter)
        self.sub.setOpenExternalLinks(True)
        box.addSpacing(10)
        box.addWidget(self.sub)
        box.addSpacing(22)
        self.row = QtWidgets.QHBoxLayout()
        self.row.setSpacing(14)
        box.addLayout(self.row)
        box.addStretch(3)

    def say(self, title, sub="", actions=()):
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        clear(self.row)
        self.row.addStretch(1)
        for i, (key, label) in enumerate(actions):
            b = QtWidgets.QPushButton(label)
            b.setDefault(i == 0)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.action.emit(k))
            self.row.addWidget(b)
        self.row.addStretch(1)


class Tile(QtWidgets.QWidget):
    """A page in the review: the picture, and under it its number, « turn » and « ✕ »."""
    turn = QtCore.pyqtSignal(object)
    remove = QtCore.pyqtSignal(object)
    earlier = QtCore.pyqtSignal(object)
    later = QtCore.pyqtSignal(object)

    WIDTH = 176

    def __init__(self, page, number, frame):
        super().__init__()
        self.page = page
        self.setFixedWidth(self.WIDTH)
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        self.picture = Picture(frame=frame)
        self.picture.setFixedWidth(self.WIDTH)
        box.addWidget(self.picture)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        self.number = QtWidgets.QLabel(str(number))
        self.number.setObjectName("dim")
        row.addWidget(self.number)
        row.addStretch(1)
        for text, tip, sig in (("←", _("move earlier"), self.earlier), ("→", _("move later"), self.later), ("⟳", _("turn"), self.turn), ("✕", _("delete this page"), self.remove)):
            c = Clickable(text, "tool")
            c.setToolTip(tip)
            c.clicked.connect(lambda s=sig: s.emit(self.page))
            row.addWidget(c)
        box.addLayout(row)


class AddTile(Clickable):
    """« + page »: one more page from the scanner, a dashed sheet to click."""

    def __init__(self):
        super().__init__("+ " + _("page"), "addtile")
        self.setFixedSize(Tile.WIDTH, int(Tile.WIDTH / 0.707))
        self.setAlignment(QtCore.Qt.AlignCenter)


class Review(QtWidgets.QWidget):
    """After a scan: the pages (turn, delete, reorder, one more), the look and the language of
    the text, then a name if wanted and the folder — a click on a folder files the document."""
    filed = QtCore.pyqtSignal(str, str)      # folder, name
    saved = QtCore.pyqtSignal()              # the pages of an existing document
    discarded = QtCore.pyqtSignal()
    more = QtCore.pyqtSignal()
    changed = QtCore.pyqtSignal()
    keep_blank = QtCore.pyqtSignal()
    new_folder = QtCore.pyqtSignal()
    pick_look = QtCore.pyqtSignal()
    pick_lang = QtCore.pyqtSignal()

    def __init__(self, loader):
        super().__init__()
        self.loader = loader
        self.pages, self.tiles, self.token = [], {}, 0
        self.frame = "#888"
        loader.loaded.connect(self._loaded)
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(36, 12, 24, 12)
        head.setSpacing(18)
        self.count = QtWidgets.QLabel("")
        self.count.setObjectName("dim")
        head.addWidget(self.count)
        self.look = Clickable("", "choice")
        self.look.clicked.connect(self.pick_look.emit)
        head.addWidget(self.look)
        self.lang = Clickable("", "choice")
        self.lang.clicked.connect(self.pick_lang.emit)
        head.addWidget(self.lang)
        self.blank = Clickable("", "dimlink")
        self.blank.clicked.connect(self.keep_blank.emit)
        head.addWidget(self.blank)
        head.addStretch(1)
        self.discard = Clickable(_("discard"), "dimlink")
        self.discard.clicked.connect(self.discarded.emit)
        head.addWidget(self.discard)
        box.addLayout(head)
        box.addWidget(rule())
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.grid_host = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self.grid_host)
        outer.setContentsMargins(36, 22, 36, 22)
        self.grid = Flow(gap=22)
        outer.addLayout(self.grid)
        outer.addStretch(1)
        self.scroll.setWidget(self.grid_host)
        box.addWidget(self.scroll, 1)
        box.addWidget(rule())
        # filing: a name if wanted, then the folder
        self.filing = QtWidgets.QWidget()
        f = QtWidgets.QVBoxLayout(self.filing)
        f.setContentsMargins(36, 14, 36, 16)
        f.setSpacing(10)
        self.name = QtWidgets.QLineEdit()
        self.name.setObjectName("name")
        self.name.setPlaceholderText(_("name — optional: without one, the first words read on the page"))
        self.name.returnPressed.connect(self.file_default)
        f.addWidget(self.name)
        self.folders_host = QtWidgets.QWidget()
        self.folders = Flow(self.folders_host, gap=8)
        f.addWidget(self.folders_host)
        self.hint = QtWidgets.QLabel("")
        self.hint.setObjectName("dim")
        f.addWidget(self.hint)
        box.addWidget(self.filing)
        self.save_row = QtWidgets.QWidget()
        s = QtWidgets.QHBoxLayout(self.save_row)
        s.setContentsMargins(36, 14, 36, 16)
        s.addStretch(1)
        self.save = QtWidgets.QPushButton(_("save"))
        self.save.setDefault(True)
        self.save.clicked.connect(self.saved.emit)
        s.addWidget(self.save)
        box.addWidget(self.save_row)
        self.default_folder = ""
        self.editing = False

    # the pages ------------------------------------------------------------------------

    def show_session(self, pages, blank, editing, look, lang, frame):
        self.pages, self.editing, self.frame = pages, editing, frame
        self.filing.setVisible(not editing)
        self.save_row.setVisible(editing)
        self.look.setText(_("look") + ": " + look_name(look) + " ▾")
        self.lang.setText(_("text") + ": " + LANG_NAMES.get(lang, lang) + " ▾")
        self.blank.setVisible(bool(blank))
        n = len(blank)
        self.blank.setText((_("1 blank page left out") if n == 1 else _("%1 blank pages left out", n)) + " · " + (_("keep it") if n == 1 else _("keep them")))
        self.rebuild()

    def rebuild(self):
        self.token += 1
        clear(self.grid)
        self.tiles = {}
        for i, page in enumerate(self.pages):
            t = Tile(page, i + 1, self.frame)
            t.turn.connect(self._turn); t.remove.connect(self._remove)
            t.earlier.connect(lambda p: self._move(p, -1)); t.later.connect(lambda p: self._move(p, 1))
            self.grid.addWidget(t)
            self.tiles[page["id"]] = t
            self._load(page)
        add = AddTile()
        add.clicked.connect(self.more.emit)
        self.grid.addWidget(add)
        n = len(self.pages)
        self.count.setText(_("1 page") if n == 1 else _("%1 pages", n))
        self.save.setEnabled(n > 0)
        self.grid_host.updateGeometry()

    def _load(self, page):
        src, rotation, look = page["src"], page.get("rotation", 0), page.get("look", "original")
        key = ("tile", self.token, page["id"], rotation, look)
        if look == "original":
            self.loader.load(key, lambda: read_scaled(src, Tile.WIDTH * 2, rotation))
        else:
            self.loader.load(key, lambda: qimage_of(apply_look(open_upright(src, rotation, draft=(Tile.WIDTH * 3, Tile.WIDTH * 4)), look)))

    def _loaded(self, key, img):
        if isinstance(key, tuple) and key[0] == "tile" and key[1] == self.token:
            t = self.tiles.get(key[2])
            if t is not None and (t.page.get("rotation", 0), t.page.get("look", "original")) == (key[3], key[4]):
                t.picture.set_image(img)

    def _turn(self, page):
        page["rotation"] = (page.get("rotation", 0) + 90) % 360
        t = self.tiles.get(page["id"])
        if t:
            t.picture.ratio = 1 / t.picture.ratio
            t.picture.image = None
            t.picture.setFixedHeight(t.picture.heightForWidth(Tile.WIDTH))
        self._load(page)
        self.changed.emit()

    def _remove(self, page):
        self.pages.remove(page)
        self.rebuild()
        self.changed.emit()

    def _move(self, page, by):
        i = self.pages.index(page)
        j = max(0, min(len(self.pages) - 1, i + by))
        if i != j:
            self.pages.insert(j, self.pages.pop(i))
            self.rebuild()
            self.changed.emit()

    # the folders ----------------------------------------------------------------------

    def show_folders(self, names, default):
        self.default_folder = default if default in names else ""
        clear(self.folders)
        for label, folder in [(_("all scans"), "")] + [(n, n) for n in names]:
            b = QtWidgets.QPushButton(label)
            b.setObjectName("chip")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setDefault(folder == self.default_folder)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _c=False, f=folder: self.filed.emit(f, self.name.text().strip()))
            self.folders.addWidget(b)
        b = QtWidgets.QPushButton("+ " + _("new folder"))
        b.setObjectName("chipnew")
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setAutoDefault(False)
        b.clicked.connect(self.new_folder.emit)
        self.folders.addWidget(b)
        where = self.default_folder or _("all scans")
        self.hint.setText(_("a click on a folder files the document there · Enter: « %1 »", where))
        self.folders_host.updateGeometry()

    def file_default(self):
        if self.editing:
            self.saved.emit()
        elif self.pages:
            self.filed.emit(self.default_folder, self.name.text().strip())


def rule(vertical=False):
    r = QtWidgets.QFrame()
    r.setObjectName("sep")
    r.setFixedWidth(1) if vertical else r.setFixedHeight(1)
    return r


def look_name(look):
    return {"original": _("as scanned"), "clean": _("clean"), "grey": _("grey"), "bw": _("b & w")}.get(look, look)


def source_name(source):
    return {"auto": _("automatic"), "glass": _("glass"), "feeder": _("feeder"), "duplex": _("both sides")}.get(source, source)


def reader_name(key):
    return {"tesseract-fast": "Tesseract", "tesseract-best": _("Tesseract best"), "mlkit": "ML Kit (Google)"}.get(key)


class SettingsDialog(QtWidgets.QDialog):
    IMPORTED = 2

    def __init__(self, main):
        super().__init__(main)
        self.main, cfg = main, main.cfg
        self.cfg = cfg
        self.setWindowTitle("reader's scanner")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        intro = QtWidgets.QLabel(_("A WebDAV folder shares the scans with your phone and your other computers: the same server, folder and login as in Reader's Scanner on Android. kDrive: server https://ID.connect.kdrive.infomaniak.com (the ID is the number in the kDrive web address), your Infomaniak login, and an application password if two-factor authentication is on. Nextcloud and any WebDAV server work the same way."))
        intro.setObjectName("dim")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        outer.addLayout(form)
        self.server = QtWidgets.QLineEdit(cfg.get("server", ""))
        self.server.setPlaceholderText("https://123456.connect.kdrive.infomaniak.com")
        self.user = QtWidgets.QLineEdit(cfg.get("username", ""))
        self.password = QtWidgets.QLineEdit(cfg.get("password", ""))
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.folder = QtWidgets.QLineEdit(cfg.get("folder", "Scans"))
        form.addRow(_("server"), self.server)
        form.addRow(_("username"), self.user)
        form.addRow(_("password"), self.password)
        form.addRow(_("folder on the server"), self.folder)
        creds = QtWidgets.QHBoxLayout()
        for text, export in ((_("import credentials…"), False), (_("export credentials…"), True)):
            b = QtWidgets.QPushButton(text); b.setObjectName("quiet"); b.setAutoDefault(False)
            b.clicked.connect(lambda _c=False, x=export: self.credentials(x)); creds.addWidget(b)
        creds.addStretch(1)
        form.addRow("", creds)

        # the scanner
        self.scanner = QtWidgets.QComboBox()
        self.again = QtWidgets.QPushButton(_("look again")); self.again.setObjectName("quiet"); self.again.setAutoDefault(False)
        self.again.clicked.connect(self.look_again)
        row = QtWidgets.QHBoxLayout(); row.addWidget(self.scanner, 1); row.addWidget(self.again)
        form.addRow(_("scanner"), row)
        self.devices = None
        self.fill_scanners()
        naps = main.naps2
        self.naps = QtWidgets.QLabel(_("NAPS2 %1 is here, for the scanners that do not answer by themselves", naps.version) if naps.cmd else
                                     _("NAPS2 is not installed: only the scanners that do not answer by themselves (AirScan) need it.") + f' <a href="{NAPS2_URL}">naps2.com</a>')
        self.naps.setObjectName("dim"); self.naps.setOpenExternalLinks(True); self.naps.setWordWrap(True)
        form.addRow("", self.naps)

        self.format = QtWidgets.QComboBox()
        for key, label in (("auto", _("automatic (%1 here)", page_size_of({"format": "auto"}))), ("a", _("A series (A4)")), ("letter", "US Letter")):
            self.format.addItem(label, key)
        self.format.setCurrentIndex(max(0, self.format.findData(cfg.get("format", "auto"))))
        form.addRow(_("page format"), self.format)

        # reading
        self.best = QtWidgets.QCheckBox(_("the most accurate models (fetched once per language, 4 to 15 MB)"))
        self.best.setChecked(bool(cfg.get("best", True)))
        form.addRow(_("reading"), self.best)
        self.best_state = QtWidgets.QLabel(""); self.best_state.setObjectName("dim"); self.best_state.setWordWrap(True)
        form.addRow("", self.best_state)
        self.best_lang = cfg.get("lang") or default_lang()
        self.timer = QtCore.QTimer(self, interval=400, timeout=self.show_best)
        self.timer.start()
        self.show_best()

        self.font = QtWidgets.QComboBox()
        for key, label in (("sans", "sans-serif"), ("serif", "serif"), ("mono", "mono")):
            self.font.addItem(label, key)
        self.font.setCurrentIndex(max(0, self.font.findData(cfg.get("font", "sans"))))
        form.addRow(_("font"), self.font)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        cancel = QtWidgets.QPushButton(_("cancel")); cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton(_("save"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        outer.addLayout(row)
        self.message = QtWidgets.QLabel(""); self.message.setObjectName("dim"); self.message.setWordWrap(True); outer.addWidget(self.message)
        credits = QtWidgets.QLabel(f"reader's scanner {VERSION} · " + _("Pierre Gallaz · developed with Claude Code") + " · " + _("scanning by NAPS2, reading by Tesseract"))
        credits.setObjectName("dim"); credits.setWordWrap(True)
        outer.addWidget(credits)
        self.resize(720, 640)

    def fill_scanners(self):
        self.scanner.clear()
        current = self.cfg.get("device") or {}
        if self.devices is None:
            if current:
                self.scanner.addItem(current.get("name", "?"), current.get("key"))
            else:
                self.scanner.addItem(_("none found yet"), None)
            return
        keys = []
        for d in self.devices:
            if d["key"] not in keys:
                keys.append(d["key"])
                self.scanner.addItem(re.sub(r"\s+\([^()]*\)$", "", d["name"]), d["key"])
        if not keys:
            self.scanner.addItem(_("none found — is the scanner switched on?"), None)
        self.scanner.setCurrentIndex(max(0, self.scanner.findData(current.get("key"))))

    def look_again(self):
        self.again.setEnabled(False)
        self.again.setText(_("looking…"))
        self.main.run(lambda note: self.main.naps2.devices(), self.found, lambda m: self.found([]))

    def found(self, devices):
        self.devices = devices
        self.again.setEnabled(True)
        self.again.setText(_("look again"))
        self.fill_scanners()

    def show_best(self):
        r, lang = self.main.reader, self.best_lang
        name = LANG_NAMES.get(lang, lang)
        if not r.exe():
            self.best_state.setText(_("Tesseract is not installed: the pages are kept without their text"))
        elif lang in r.downloading:
            self.best_state.setText(_("%1: downloading the best model… %2 %", name, r.downloading[lang]))
        elif r.has_best(lang):
            self.best_state.setText(_("%1: the best model is here", name))
        elif lang in r.system()[1]:
            self.best_state.setText(_("%1: the standard model for now", name))
        else:
            self.best_state.setText(_("%1: its model will be fetched at the first reading", name))

    def credentials(self, export):
        title = _("export credentials…") if export else _("import credentials…")
        start = os.path.expanduser("~/readers-credentials.json")
        if export:
            path, _f = QtWidgets.QFileDialog.getSaveFileName(self, title, start, _("Reader's credentials (*.json)"), options=QtWidgets.QFileDialog.DontConfirmOverwrite)
        else:
            path, _f = QtWidgets.QFileDialog.getOpenFileName(self, title, os.path.dirname(start), _("Reader's credentials (*.json)"))
        if not path:
            return
        try:
            if export:
                self.message.setText(_("credentials exported to %1 — the file holds your passwords: keep it private", export_credentials(dict(self.cfg, **self.values()), path)))
            else:
                target = dict(self.cfg)
                self.message.setText(import_credentials(target, path))
                self.server.setText(target.get("server", "")); self.user.setText(target.get("username", ""))
                self.password.setText(target.get("password", "")); self.folder.setText(target.get("folder", "Scans") or "Scans")
        except (OSError, ValueError) as e:
            self.message.setText(str(e))

    def values(self):
        v = {"server": self.server.text().strip(), "username": self.user.text().strip(), "password": self.password.text(),
             "folder": self.folder.text().strip().strip("/") or "Scans", "font": self.font.currentData(), "format": self.format.currentData(),
             "best": self.best.isChecked()}
        key = self.scanner.currentData()
        if self.devices is not None and key:
            routes = pick_routes(self.devices, key)
            if routes:
                v["device"] = {"key": key, "name": routes[0]["name"], "routes": routes}
        return v
