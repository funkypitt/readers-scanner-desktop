#!/usr/bin/env python3
"""Reader's Scanner — documents from a real scanner (flatbed, feeder), read on this computer,
filed in plain folders as PDFs that can be searched, and shared with the phone through a WebDAV
folder (kDrive, Nextcloud…). NAPS2 (naps2.com, installed separately) talks to the scanner;
Tesseract reads the text. One file, PyQt5 + requests + Pillow + numpy. MIT licence."""

import base64
import json
import locale
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, date, timedelta
from urllib.parse import quote, unquote, urljoin, urlparse

import numpy as np
import requests
from PIL import Image
from PyQt5 import QtCore, QtGui, QtWidgets

APP = "readers-scanner"
VERSION = "1.0.0"
Image.MAX_IMAGE_PIXELS = 200_000_000      # an A3 page at 600 dpi is not an attack


def _app_dirs():
    """The settings folder and the documents' folder, one place per desktop."""
    elsewhere = os.environ.get("READERS_SCANNER_HOME")      # the tests' own place
    if elsewhere:
        return os.path.join(elsewhere, "config"), os.path.join(elsewhere, "data")
    if sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Readers Scanner")
        return base, base
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/" + APP)
        return base, base
    return (os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), APP),
            os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), APP))


def _steady(act, *args):
    """Windows refuses to move, replace or delete a file that anything still has open — a
    thumbnail being drawn, an antivirus looking at a new file. It is a matter of a moment: asked
    again for a few seconds before it is an error. Elsewhere an open file moves like any other."""
    for attempt in range(40 if sys.platform == "win32" else 1):
        try:
            return act(*args)
        except PermissionError:
            if attempt == (39 if sys.platform == "win32" else 0):
                raise
            time.sleep(0.1)


def move(src, dst):
    return _steady(shutil.move, src, dst)


def replace(src, dst):
    return _steady(os.replace, src, dst)


def remove(path):
    return _steady(os.remove, path)


def remove_tree(path):
    """A folder and what is in it; on Windows, asked again while a file of it is still open."""
    for _attempt in range(40 if sys.platform == "win32" else 1):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.1)


def quiet():
    """For every program started: on Windows, without this, a console window flashes each time."""
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def bundled(*path):
    """A file shipped inside the app (the Windows and macOS builds carry Tesseract); None elsewhere."""
    base = getattr(sys, "_MEIPASS", None)
    p = os.path.join(base, *path) if base else None
    return p if p and os.path.exists(p) else None


def system_locale():
    """"fr_CH": Qt knows it on every desktop; the environment often does not (Windows, an app
    opened from the Finder)."""
    return QtCore.QLocale.system().name() or "en_US"


def said(raw):
    """What a program wrote, whatever the code page it wrote it in (Windows consoles have their own)."""
    for enc in ("utf-8", "oem" if sys.platform == "win32" else "latin-1", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", "replace")


CONFIG_DIR, DATA_DIR = _app_dirs()
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


# ------------------------------------------------------------------------------------------
# Six languages, the English text as the key: each line gives fr, de, es, pt, ru
# ------------------------------------------------------------------------------------------

_T = {
 "today": ("aujourd'hui", "heute", "hoy", "hoje", "сегодня"),
 "yesterday": ("hier", "gestern", "ayer", "ontem", "вчера"),
 "cannot reach the server": ("serveur injoignable", "Server nicht erreichbar", "no se puede alcanzar el servidor", "não é possível contactar o servidor", "сервер недоступен"),
 "wrong username or password": ("identifiant ou mot de passe incorrect", "Benutzername oder Passwort falsch", "usuario o contraseña incorrectos", "utilizador ou palavra-passe errados", "неверное имя пользователя или пароль"),
 "not a WebDAV folder at this address": ("pas de dossier WebDAV à cette adresse", "kein WebDAV-Ordner unter dieser Adresse", "no hay carpeta WebDAV en esta dirección", "não há pasta WebDAV neste endereço", "по этому адресу нет папки WebDAV"),
 "no reading model for %1 — it could not be downloaded": ("pas de modèle de lecture pour %1 — il n'a pas pu être téléchargé", "kein Lesemodell für %1 — es konnte nicht geladen werden", "no hay modelo de lectura para %1 — no se pudo descargar", "não há modelo de leitura para %1 — não foi possível descarregá-lo", "нет модели чтения для %1 — не удалось загрузить"),
 "Tesseract is not installed: the pages are kept without their text": ("Tesseract n'est pas installé : les pages sont gardées sans leur texte", "Tesseract ist nicht installiert: die Seiten bleiben ohne Text", "Tesseract no está instalado: las páginas se guardan sin su texto", "O Tesseract não está instalado: as páginas ficam sem texto", "Tesseract не установлен: страницы сохраняются без текста"),
 "the feeder is empty": ("le chargeur est vide", "der Einzug ist leer", "el alimentador está vacío", "o alimentador está vazio", "лоток подачи пуст"),
 "this scanner has no feeder": ("ce scanner n'a pas de chargeur", "dieser Scanner hat keinen Einzug", "este escáner no tiene alimentador", "este digitalizador não tem alimentador", "у этого сканера нет лотка подачи"),
 "this scanner cannot scan both sides": ("ce scanner ne scanne pas le recto verso", "dieser Scanner kann nicht beidseitig scannen", "este escáner no escanea a doble cara", "este digitalizador não digitaliza frente e verso", "этот сканер не сканирует обе стороны"),
 "the scanner is not answering — is it switched on?": ("le scanner ne répond pas — est-il allumé ?", "der Scanner antwortet nicht — ist er eingeschaltet?", "el escáner no responde — ¿está encendido?", "o digitalizador não responde — está ligado?", "сканер не отвечает — он включён?"),
 "the scanner is busy": ("le scanner est occupé", "der Scanner ist beschäftigt", "el escáner está ocupado", "o digitalizador está ocupado", "сканер занят"),
 "the scanner's cover is open": ("le capot du scanner est ouvert", "die Abdeckung des Scanners ist offen", "la tapa del escáner está abierta", "a tampa do digitalizador está aberta", "крышка сканера открыта"),
 "paper jam in the scanner": ("bourrage papier dans le scanner", "Papierstau im Scanner", "atasco de papel en el escáner", "papel encravado no digitalizador", "замятие бумаги в сканере"),
 "the scanner is warming up — try again in a moment": ("le scanner chauffe — réessayez dans un instant", "der Scanner wärmt auf — gleich noch einmal versuchen", "el escáner se está calentando — inténtelo en un momento", "o digitalizador está a aquecer — tente daqui a pouco", "сканер прогревается — попробуйте чуть позже"),
 "the connection to the scanner was interrupted": ("la liaison avec le scanner a été interrompue", "die Verbindung zum Scanner wurde unterbrochen", "la conexión con el escáner se interrumpió", "a ligação ao digitalizador foi interrompida", "связь со сканером прервалась"),
 "SANE is not installed (the scanner drivers)": ("SANE n'est pas installé (les pilotes de scanner)", "SANE ist nicht installiert (die Scannertreiber)", "SANE no está instalado (los controladores de escáner)", "O SANE não está instalado (os controladores de digitalizador)", "SANE не установлен (драйверы сканера)"),
 "scan cancelled": ("scan annulé", "Scan abgebrochen", "escaneo cancelado", "digitalização cancelada", "сканирование отменено"),
 "no scanner found — is it switched on?": ("aucun scanner trouvé — est-il allumé ?", "kein Scanner gefunden — ist er eingeschaltet?", "no se encontró ningún escáner — ¿está encendido?", "nenhum digitalizador encontrado — está ligado?", "сканер не найден — он включён?"),
 "the scan did not work": ("le scan n'a pas marché", "der Scan hat nicht geklappt", "el escaneo no funcionó", "a digitalização não funcionou", "сканирование не удалось"),
 "not a Reader's credentials file": ("ce n'est pas un fichier d'identifiants Reader's", "keine Reader's-Zugangsdatendatei", "no es un archivo de credenciales Reader's", "não é um ficheiro de credenciais Reader's", "это не файл учётных данных Reader's"),
 "credentials imported": ("identifiants importés", "Zugangsdaten importiert", "credenciales importadas", "credenciais importadas", "учётные данные импортированы"),
 "this file holds nothing for %1": ("ce fichier ne contient rien pour %1", "diese Datei enthält nichts für %1", "este archivo no contiene nada para %1", "este ficheiro não contém nada para %1", "в этом файле нет ничего для %1"),
 "server and login taken from %1": ("serveur et identifiants repris de %1", "Server und Anmeldung aus %1 übernommen", "servidor y usuario tomados de %1", "servidor e utilizador retirados de %1", "сервер и логин взяты из %1"),
 "credentials exported to %1 — the file holds your passwords: keep it private": ("identifiants exportés dans %1 — le fichier contient vos mots de passe : gardez-le privé", "Zugangsdaten nach %1 exportiert — die Datei enthält Ihre Passwörter: halten Sie sie privat", "credenciales exportadas a %1 — el archivo contiene sus contraseñas: manténgalo privado", "credenciais exportadas para %1 — o ficheiro contém as suas palavras-passe: mantenha-o privado", "учётные данные экспортированы в %1 — файл содержит ваши пароли: храните его в тайне"),
 "move earlier": ("avancer", "nach vorne", "mover antes", "mover para antes", "переместить раньше"),
 "move later": ("reculer", "nach hinten", "mover después", "mover para depois", "переместить позже"),
 "turn": ("tourner", "drehen", "girar", "rodar", "повернуть"),
 "delete this page": ("supprimer cette page", "diese Seite löschen", "eliminar esta página", "eliminar esta página", "удалить эту страницу"),
 "page": ("page", "Seite", "página", "página", "страница"),
 "discard": ("abandonner", "verwerfen", "descartar", "descartar", "отменить"),
 "name — optional: without one, the first words read on the page": ("nom — facultatif : sans nom, les premiers mots lus sur la page", "Name — freiwillig: ohne Namen die ersten gelesenen Wörter der Seite", "nombre — opcional: sin nombre, las primeras palabras leídas en la página", "nome — opcional: sem nome, as primeiras palavras lidas na página", "название — необязательно: без него — первые слова страницы"),
 "save": ("enregistrer", "speichern", "guardar", "guardar", "сохранить"),
 "look": ("aspect", "Aussehen", "aspecto", "aspeto", "вид"),
 "text": ("texte", "Text", "texto", "texto", "текст"),
 "1 blank page left out": ("1 page blanche écartée", "1 leere Seite ausgelassen", "1 página en blanco apartada", "1 página em branco posta de parte", "1 пустая страница пропущена"),
 "%1 blank pages left out": ("%1 pages blanches écartées", "%1 leere Seiten ausgelassen", "%1 páginas en blanco apartadas", "%1 páginas em branco postas de parte", "пустых страниц пропущено: %1"),
 "keep it": ("la garder", "behalten", "conservarla", "mantê-la", "оставить её"),
 "keep them": ("les garder", "behalten", "conservarlas", "mantê-las", "оставить их"),
 "1 page": ("1 page", "1 Seite", "1 página", "1 página", "1 страница"),
 "%1 pages": ("%1 pages", "%1 Seiten", "%1 páginas", "%1 páginas", "страниц: %1"),
 "all scans": ("tous les scans", "alle Scans", "todos los escaneos", "todas as digitalizações", "все сканы"),
 "new folder": ("nouveau dossier", "neuer Ordner", "nueva carpeta", "nova pasta", "новая папка"),
 "a click on a folder files the document there · Enter: « %1 »": ("un clic sur un dossier y range le document · Entrée : « %1 »", "ein Klick auf einen Ordner legt das Dokument dort ab · Eingabe: « %1 »", "un clic en una carpeta guarda allí el documento · Intro: « %1 »", "um clique numa pasta guarda lá o documento · Enter: « %1 »", "щелчок по папке кладёт документ в неё · Enter: « %1 »"),
 "as scanned": ("tel que scanné", "wie gescannt", "tal como se escaneó", "como digitalizado", "как отсканировано"),
 "clean": ("net", "sauber", "limpio", "limpo", "чисто"),
 "grey": ("gris", "grau", "gris", "cinzento", "серый"),
 "b & w": ("n & b", "s/w", "b/n", "p/b", "ч/б"),
 "automatic": ("automatique", "automatisch", "automático", "automático", "автоматически"),
 "glass": ("vitre", "Glas", "cristal", "vidro", "стекло"),
 "feeder": ("chargeur", "Einzug", "alimentador", "alimentador", "лоток подачи"),
 "both sides": ("recto verso", "beidseitig", "doble cara", "frente e verso", "обе стороны"),
 "Tesseract best": ("Tesseract meilleur", "Tesseract bestes", "Tesseract mejor", "Tesseract melhor", "Tesseract лучший"),
 "A WebDAV folder shares the scans with your phone and your other computers: the same server, folder and login as in Reader's Scanner on Android. kDrive: server https://ID.connect.kdrive.infomaniak.com (the ID is the number in the kDrive web address), your Infomaniak login, and an application password if two-factor authentication is on. Nextcloud and any WebDAV server work the same way.": (
    "Un dossier WebDAV partage les scans avec votre téléphone et vos autres ordinateurs : le même serveur, le même dossier et les mêmes identifiants que dans Reader's Scanner sur Android. kDrive : serveur https://ID.connect.kdrive.infomaniak.com (l'ID est le nombre dans l'adresse web de kDrive), votre identifiant Infomaniak et un mot de passe d'application si la double authentification est active. Nextcloud et tout serveur WebDAV fonctionnent de la même façon.",
    "Ein WebDAV-Ordner teilt die Scans mit Ihrem Telefon und Ihren anderen Computern: derselbe Server, Ordner und Login wie in Reader's Scanner auf Android. kDrive: Server https://ID.connect.kdrive.infomaniak.com (die ID ist die Zahl in der kDrive-Webadresse), Ihr Infomaniak-Login und bei Zwei-Faktor-Anmeldung ein App-Passwort. Nextcloud und jeder WebDAV-Server funktionieren genauso.",
    "Una carpeta WebDAV comparte los escaneos con su teléfono y sus otros ordenadores: el mismo servidor, carpeta y usuario que en Reader's Scanner en Android. kDrive: servidor https://ID.connect.kdrive.infomaniak.com (el ID es el número de la dirección web de kDrive), su usuario de Infomaniak y una contraseña de aplicación si tiene la verificación en dos pasos. Nextcloud y cualquier servidor WebDAV funcionan igual.",
    "Uma pasta WebDAV partilha as digitalizações com o seu telemóvel e os seus outros computadores: o mesmo servidor, pasta e utilizador que no Reader's Scanner no Android. kDrive: servidor https://ID.connect.kdrive.infomaniak.com (o ID é o número no endereço web do kDrive), o seu utilizador Infomaniak e uma palavra-passe de aplicação se tiver a verificação em dois passos. O Nextcloud e qualquer servidor WebDAV funcionam da mesma forma.",
    "Папка WebDAV делит сканы с телефоном и другими компьютерами: тот же сервер, папка и логин, что в Reader's Scanner на Android. kDrive: сервер https://ID.connect.kdrive.infomaniak.com (ID — число в веб-адресе kDrive), ваш логин Infomaniak и пароль приложения при двухфакторной аутентификации. Nextcloud и любой WebDAV-сервер работают так же."),
 "server": ("serveur", "Server", "servidor", "servidor", "сервер"),
 "username": ("identifiant", "Benutzername", "usuario", "utilizador", "имя пользователя"),
 "password": ("mot de passe", "Passwort", "contraseña", "palavra-passe", "пароль"),
 "folder on the server": ("dossier sur le serveur", "Ordner auf dem Server", "carpeta en el servidor", "pasta no servidor", "папка на сервере"),
 "import credentials…": ("importer les identifiants…", "Zugangsdaten importieren…", "importar credenciales…", "importar credenciais…", "импортировать учётные данные…"),
 "export credentials…": ("exporter les identifiants…", "Zugangsdaten exportieren…", "exportar credenciales…", "exportar credenciais…", "экспортировать учётные данные…"),
 "look again": ("chercher à nouveau", "noch einmal suchen", "buscar de nuevo", "procurar de novo", "искать снова"),
 "scanner": ("scanner", "Scanner", "escáner", "digitalizador", "сканер"),
 "automatic (%1 here)": ("automatique (%1 ici)", "automatisch (hier %1)", "automático (%1 aquí)", "automático (%1 aqui)", "автоматически (здесь %1)"),
 "A series (A4)": ("série A (A4)", "A-Reihe (A4)", "serie A (A4)", "série A (A4)", "серия A (A4)"),
 "page format": ("format des pages", "Seitenformat", "formato de página", "formato das páginas", "формат страниц"),
 "reading": ("lecture", "Lesen", "lectura", "leitura", "чтение"),
 "font": ("police", "Schrift", "fuente", "tipo de letra", "шрифт"),
 "cancel": ("annuler", "abbrechen", "cancelar", "cancelar", "отмена"),
 "Pierre Gallaz · developed with Claude Code": ("Pierre Gallaz · développé avec Claude Code", "Pierre Gallaz · entwickelt mit Claude Code", "Pierre Gallaz · desarrollado con Claude Code", "Pierre Gallaz · desenvolvido com Claude Code", "Pierre Gallaz · разработано с Claude Code"),
 "scanning by NAPS2, reading by Tesseract": ("scan par NAPS2, lecture par Tesseract", "Scannen mit NAPS2, Lesen mit Tesseract", "escaneo por NAPS2, lectura por Tesseract", "digitalização pelo NAPS2, leitura pelo Tesseract", "сканирование — NAPS2, чтение — Tesseract"),
 "none found yet": ("aucun trouvé pour l'instant", "noch keiner gefunden", "ninguno encontrado todavía", "nenhum encontrado ainda", "пока не найден"),
 "none found — is the scanner switched on?": ("aucun trouvé — le scanner est-il allumé ?", "keiner gefunden — ist der Scanner eingeschaltet?", "ninguno encontrado — ¿está encendido el escáner?", "nenhum encontrado — o digitalizador está ligado?", "не найден — сканер включён?"),
 "looking…": ("recherche…", "suche…", "buscando…", "a procurar…", "поиск…"),
 "%1: downloading the best model… %2 %": ("%1 : téléchargement du meilleur modèle… %2 %", "%1: bestes Modell wird geladen… %2 %", "%1: descargando el mejor modelo… %2 %", "%1: a descarregar o melhor modelo… %2 %", "%1: загрузка лучшей модели… %2 %"),
 "Reader's credentials (*.json)": ("Identifiants Reader's (*.json)", "Reader's-Zugangsdaten (*.json)", "Credenciales Reader's (*.json)", "Credenciais Reader's (*.json)", "Учётные данные Reader's (*.json)"),
 "find": ("chercher", "suchen", "buscar", "procurar", "найти"),
 "the most accurate models (fetched once per language, 4 to 15 MB)": ("les modèles les plus exacts (téléchargés une fois par langue, 4 à 15 Mo)", "die genauesten Modelle (einmal pro Sprache geladen, 4 bis 15 MB)", "los modelos más precisos (descargados una vez por idioma, 4 a 15 MB)", "os modelos mais exatos (descarregados uma vez por língua, 4 a 15 MB)", "самые точные модели (загружаются один раз для языка, 4–15 МБ)"),
 "%1: the best model is here": ("%1 : le meilleur modèle est là", "%1: das beste Modell ist da", "%1: el mejor modelo está aquí", "%1: o melhor modelo está cá", "%1: лучшая модель на месте"),
 "%1: the standard model for now": ("%1 : le modèle standard pour l'instant", "%1: vorerst das Standardmodell", "%1: el modelo estándar por ahora", "%1: o modelo padrão por agora", "%1: пока стандартная модель"),
 "%1: its model will be fetched at the first reading": ("%1 : son modèle sera téléchargé à la première lecture", "%1: sein Modell wird beim ersten Lesen geladen", "%1: su modelo se descargará en la primera lectura", "%1: o modelo será descarregado na primeira leitura", "%1: модель загрузится при первом чтении"),
 "find in names and text": ("chercher dans les noms et le texte", "in Namen und Text suchen", "buscar en nombres y texto", "procurar nos nomes e no texto", "поиск по названиям и тексту"),
 "scan": ("scanner", "scannen", "escanear", "digitalizar", "сканировать"),
 "settings (Ctrl+,)": ("réglages (Ctrl+,)", "Einstellungen (Strg+,)", "ajustes (Ctrl+,)", "definições (Ctrl+,)", "настройки (Ctrl+,)"),
 "open the PDF": ("ouvrir le PDF", "PDF öffnen", "abrir el PDF", "abrir o PDF", "открыть PDF"),
 "save a copy…": ("enregistrer une copie…", "Kopie speichern…", "guardar una copia…", "guardar uma cópia…", "сохранить копию…"),
 "copy the text": ("copier le texte", "Text kopieren", "copiar el texto", "copiar o texto", "копировать текст"),
 "from": ("depuis", "von", "desde", "de", "источник"),
 "automatic: the feeder when it holds paper, the glass otherwise": ("automatique : le chargeur s'il contient du papier, la vitre sinon", "automatisch: der Einzug, wenn Papier darin liegt, sonst das Glas", "automático: el alimentador si tiene papel, el cristal si no", "automático: o alimentador se tiver papel, o vidro se não", "автоматически: лоток, если в нём бумага, иначе стекло"),
 "as scanned, or cleaned: white paper, grey, black and white": ("tel que scanné, ou nettoyé : papier blanc, gris, noir et blanc", "wie gescannt oder gereinigt: weisses Papier, grau, schwarz-weiss", "tal como se escaneó, o limpiado: papel blanco, gris, blanco y negro", "como digitalizado, ou limpo: papel branco, cinzento, preto e branco", "как отсканировано или очищено: белая бумага, серый, чёрно-белый"),
 "the language the text is read in": ("la langue dans laquelle le texte est lu", "die Sprache, in der der Text gelesen wird", "el idioma en que se lee el texto", "a língua em que o texto é lido", "язык, на котором читается текст"),
 "reading the text %1": ("lecture du texte %1", "Text wird gelesen %1", "leyendo el texto %1", "a ler o texto %1", "чтение текста %1"),
 "reading the text…": ("lecture du texte…", "Text wird gelesen…", "leyendo el texto…", "a ler o texto…", "чтение текста…"),
 "text to be read": ("texte à lire", "Text wird noch gelesen", "texto por leer", "texto por ler", "текст ещё не прочитан"),
 "text could not be read": ("le texte n'a pas pu être lu", "Text konnte nicht gelesen werden", "no se pudo leer el texto", "não foi possível ler o texto", "текст не удалось прочитать"),
 "downloading… %1 %": ("téléchargement… %1 %", "wird geladen… %1 %", "descargando… %1 %", "a descarregar… %1 %", "загрузка… %1 %"),
 "scan not filed yet": ("scan pas encore rangé", "Scan noch nicht abgelegt", "escaneo aún sin guardar", "digitalização ainda por arrumar", "скан ещё не разложен"),
 "nothing found": ("rien trouvé", "nichts gefunden", "no se encontró nada", "nada encontrado", "ничего не найдено"),
 "no scans here yet": ("pas encore de scan ici", "hier noch keine Scans", "todavía no hay escaneos aquí", "ainda não há digitalizações aqui", "здесь пока нет сканов"),
 "rename…": ("renommer…", "umbenennen…", "cambiar el nombre…", "mudar o nome…", "переименовать…"),
 "delete the folder": ("supprimer le dossier", "Ordner löschen", "eliminar la carpeta", "eliminar a pasta", "удалить папку"),
 "from files…": ("depuis des fichiers…", "aus Dateien…", "desde archivos…", "de ficheiros…", "из файлов…"),
 "name of the new folder": ("nom du nouveau dossier", "Name des neuen Ordners", "nombre de la nueva carpeta", "nome da nova pasta", "название новой папки"),
 "new name of the folder": ("nouveau nom du dossier", "neuer Name des Ordners", "nuevo nombre de la carpeta", "novo nome da pasta", "новое название папки"),
 "Delete the folder “%1”? Its scans stay in all scans.": ("Supprimer le dossier « %1 » ? Ses scans restent dans tous les scans.", "Den Ordner „%1“ löschen? Seine Scans bleiben in „alle Scans“.", "¿Eliminar la carpeta «%1»? Sus escaneos siguen en todos los escaneos.", "Eliminar a pasta «%1»? As digitalizações continuam em todas as digitalizações.", "Удалить папку «%1»? Её сканы останутся во «всех сканах»."),
 "put the pages on the scanner, press « scan »": ("posez les pages sur le scanner, appuyez sur « scanner »", "Seiten auf den Scanner legen, « scannen » drücken", "ponga las páginas en el escáner, pulse « escanear »", "ponha as páginas no digitalizador, carregue em « digitalizar »", "положите страницы в сканер и нажмите « сканировать »"),
 "In the feeder or on the glass: the scanner takes what it finds. The text is read on this computer, and the document becomes a PDF you can search.": ("Dans le chargeur ou sur la vitre : le scanner prend ce qu'il trouve. Le texte est lu sur cet ordinateur, et le document devient un PDF dans lequel on peut chercher.", "Im Einzug oder auf dem Glas: der Scanner nimmt, was er findet. Der Text wird auf diesem Computer gelesen, und das Dokument wird ein durchsuchbares PDF.", "En el alimentador o sobre el cristal: el escáner toma lo que encuentra. El texto se lee en este ordenador, y el documento se convierte en un PDF en el que se puede buscar.", "No alimentador ou no vidro: o digitalizador pega no que encontra. O texto é lido neste computador, e o documento torna-se um PDF pesquisável.", "В лотке или на стекле: сканер берёт то, что найдёт. Текст читается на этом компьютере, документ становится PDF с поиском."),
 "scan, or choose a document": ("scannez, ou choisissez un document", "scannen oder ein Dokument wählen", "escanee o elija un documento", "digitalize ou escolha um documento", "сканируйте или выберите документ"),
 "get NAPS2": ("obtenir NAPS2", "NAPS2 holen", "obtener NAPS2", "obter o NAPS2", "получить NAPS2"),
 "scanned elsewhere": ("scanné ailleurs", "anderswo gescannt", "escaneado en otro lugar", "digitalizado noutro lado", "отсканировано в другом месте"),
 "pages": ("pages", "Seiten", "páginas", "páginas", "страницы"),
 "No text was found on these pages.": ("Aucun texte n'a été trouvé sur ces pages.", "Auf diesen Seiten wurde kein Text gefunden.", "No se encontró texto en estas páginas.", "Não foi encontrado texto nestas páginas.", "На этих страницах текст не найден."),
 "The text has not been read yet.": ("Le texte n'a pas encore été lu.", "Der Text wurde noch nicht gelesen.", "El texto todavía no se ha leído.", "O texto ainda não foi lido.", "Текст ещё не прочитан."),
 "its PDF needs the WebDAV folder": ("son PDF demande le dossier WebDAV", "sein PDF braucht den WebDAV-Ordner", "su PDF necesita la carpeta WebDAV", "o PDF precisa da pasta WebDAV", "для PDF нужна папка WebDAV"),
 "the PDF is not on the server (any more)": ("le PDF n'est pas (plus) sur le serveur", "das PDF ist nicht (mehr) auf dem Server", "el PDF ya no está en el servidor", "o PDF já não está no servidor", "PDF нет на сервере"),
 "save the copies in…": ("enregistrer les copies dans…", "Kopien speichern in…", "guardar las copias en…", "guardar as cópias em…", "сохранить копии в…"),
 "save the pages as pictures in…": ("enregistrer les pages en images dans…", "Seiten als Bilder speichern in…", "guardar las páginas como imágenes en…", "guardar as páginas como imagens em…", "сохранить страницы как изображения в…"),
 "text copied": ("texte copié", "Text kopiert", "texto copiado", "texto copiado", "текст скопирован"),
 "save the pages as pictures…": ("enregistrer les pages en images…", "Seiten als Bilder speichern…", "guardar las páginas como imágenes…", "guardar as páginas como imagens…", "сохранить страницы как изображения…"),
 "move to": ("déplacer vers", "verschieben nach", "mover a", "mover para", "переместить в"),
 "no folder (all scans only)": ("aucun dossier (tous les scans seulement)", "kein Ordner (nur alle Scans)", "sin carpeta (solo todos los escaneos)", "sem pasta (só todas as digitalizações)", "без папки (только «все сканы»)"),
 "edit the pages": ("modifier les pages", "Seiten bearbeiten", "editar las páginas", "editar as páginas", "изменить страницы"),
 "add pages from the scanner": ("ajouter des pages depuis le scanner", "Seiten vom Scanner hinzufügen", "añadir páginas desde el escáner", "juntar páginas do digitalizador", "добавить страницы со сканера"),
 "read the text again in": ("relire le texte en", "Text neu lesen auf", "volver a leer el texto en", "ler o texto de novo em", "прочитать текст заново на языке"),
 "delete": ("supprimer", "löschen", "eliminar", "eliminar", "удалить"),
 "sync now": ("synchroniser", "jetzt synchronisieren", "sincronizar ahora", "sincronizar agora", "синхронизировать"),
 "black on white": ("noir sur blanc", "schwarz auf weiss", "negro sobre blanco", "preto sobre branco", "чёрное на белом"),
 "white on black": ("blanc sur noir", "weiss auf schwarz", "blanco sobre negro", "branco sobre preto", "белое на чёрном"),
 "settings": ("réglages", "Einstellungen", "ajustes", "definições", "настройки"),
 "name (empty: the first words of the text)": ("nom (vide : les premiers mots du texte)", "Name (leer: die ersten Wörter des Textes)", "nombre (vacío: las primeras palabras del texto)", "nome (vazio: as primeiras palavras do texto)", "название (пусто — первые слова текста)"),
 "Delete “%1”?": ("Supprimer « %1 » ?", "„%1“ löschen?", "¿Eliminar «%1»?", "Eliminar «%1»?", "Удалить «%1»?"),
 "Delete these %1 documents?": ("Supprimer ces %1 documents ?", "Diese %1 Dokumente löschen?", "¿Eliminar estos %1 documentos?", "Eliminar estes %1 documentos?", "Удалить эти документы (%1)?"),
 "scanning…": ("scan en cours…", "scannt…", "escaneando…", "a digitalizar…", "сканирование…"),
 "page %1": ("page %1", "Seite %1", "página %1", "página %1", "страница %1"),
 "setting the pages upright…": ("remise à l'endroit des pages…", "Seiten werden aufgerichtet…", "poniendo las páginas derechas…", "a endireitar as páginas…", "поворот страниц…"),
 "looking for the scanner…": ("recherche du scanner…", "Scanner wird gesucht…", "buscando el escáner…", "a procurar o digitalizador…", "поиск сканера…"),
 "from the feeder": ("depuis le chargeur", "aus dem Einzug", "desde el alimentador", "do alimentador", "из лотка подачи"),
 "from the glass": ("depuis la vitre", "vom Glas", "desde el cristal", "do vidro", "со стекла"),
 "Put the pages in the feeder, or choose « glass ».": ("Mettez les pages dans le chargeur, ou choisissez « vitre ».", "Seiten in den Einzug legen oder « Glas » wählen.", "Ponga las páginas en el alimentador, o elija « cristal ».", "Ponha as páginas no alimentador, ou escolha « vidro ».", "Положите страницы в лоток или выберите « стекло »."),
 "Switch the scanner on and check its cable; then scan again.": ("Allumez le scanner et vérifiez son câble ; puis scannez à nouveau.", "Scanner einschalten und Kabel prüfen; dann noch einmal scannen.", "Encienda el escáner y revise su cable; luego escanee de nuevo.", "Ligue o digitalizador e verifique o cabo; depois digitalize de novo.", "Включите сканер и проверьте кабель; затем сканируйте снова."),
 "scan again": ("scanner à nouveau", "noch einmal scannen", "escanear de nuevo", "digitalizar de novo", "сканировать снова"),
 "back to the pages": ("retour aux pages", "zurück zu den Seiten", "volver a las páginas", "voltar às páginas", "назад к страницам"),
 "Discard this page?": ("Abandonner cette page ?", "Diese Seite verwerfen?", "¿Descartar esta página?", "Descartar esta página?", "Убрать эту страницу?"),
 "Discard these %1 pages?": ("Abandonner ces %1 pages ?", "Diese %1 Seiten verwerfen?", "¿Descartar estas %1 páginas?", "Descartar estas %1 páginas?", "Убрать эти страницы (%1)?"),
 "Pictures and PDFs": ("Images et PDF", "Bilder und PDFs", "Imágenes y PDF", "Imagens e PDF", "Изображения и PDF"),
 "bringing the pages in…": ("import des pages…", "Seiten werden geholt…", "trayendo las páginas…", "a trazer as páginas…", "импорт страниц…"),
 "poppler-utils is needed to read a PDF": ("poppler-utils est nécessaire pour lire un PDF", "poppler-utils wird gebraucht, um ein PDF zu lesen", "se necesita poppler-utils para leer un PDF", "é preciso o poppler-utils para ler um PDF", "для чтения PDF нужен poppler-utils"),
 "nothing could be read in these files": ("rien n'a pu être lu dans ces fichiers", "in diesen Dateien war nichts lesbar", "no se pudo leer nada en estos archivos", "não foi possível ler nada nestes ficheiros", "в этих файлах ничего не удалось прочитать"),
 "synced %1": ("synchronisé %1", "synchronisiert %1", "sincronizado %1", "sincronizado %1", "синхр. %1"),
 "no scanner found yet": ("pas encore de scanner", "noch kein Scanner gefunden", "aún sin escáner", "ainda sem digitalizador", "сканер пока не найден"),
 "the scanner is getting ready…": ("le scanner se prépare…", "der Scanner macht sich bereit…", "el escáner se prepara…", "o digitalizador prepara-se…", "сканер готовится…"),
 "an error — see errors.log": ("erreur — voir errors.log", "Fehler — siehe errors.log", "error — ver errors.log", "erro — ver errors.log", "ошибка — см. errors.log"),
 "no scanner found": ("aucun scanner trouvé", "kein scanner gefunden", "ningún escáner encontrado", "nenhum digitalizador encontrado", "сканер не найден"),
 "Most scanners made since 2015 (AirScan, Mopria) are found by themselves, on the network or by USB: is yours switched on? The others need NAPS2, a free program installed separately, from naps2.com. Pictures and PDFs can be brought in from files meanwhile.":
    ("La plupart des scanners fabriqués depuis 2015 (AirScan, Mopria) sont trouvés d'eux-mêmes, sur le réseau ou par USB : le vôtre est-il allumé ? Les autres ont besoin de NAPS2, un programme libre qui s'installe à part, depuis naps2.com. En attendant, des images et des PDF peuvent être repris depuis des fichiers.",
     "Die meisten scanner seit 2015 (AirScan, Mopria) werden von selbst gefunden, im netz oder über USB: ist ihrer eingeschaltet? Die anderen brauchen NAPS2, ein freies programm, das getrennt installiert wird, von naps2.com. Inzwischen lassen sich bilder und PDFs aus dateien übernehmen.",
     "La mayoría de los escáneres fabricados desde 2015 (AirScan, Mopria) se encuentran solos, en la red o por USB: ¿está encendido el suyo? Los demás necesitan NAPS2, un programa libre que se instala aparte, desde naps2.com. Mientras tanto se pueden traer imágenes y PDF desde archivos.",
     "A maior parte dos digitalizadores fabricados desde 2015 (AirScan, Mopria) é encontrada por si, na rede ou por USB: o seu está ligado? Os outros precisam do NAPS2, um programa livre instalado à parte, a partir de naps2.com. Entretanto podem trazer-se imagens e PDF de ficheiros.",
     "Большинство сканеров, выпущенных с 2015 года (AirScan, Mopria), находятся сами, в сети или по USB: ваш включён? Остальным нужен NAPS2 — свободная программа, которая устанавливается отдельно, с naps2.com. Пока можно взять изображения и PDF из файлов."),
 "NAPS2 %1 is here, for the scanners that do not answer by themselves": ("NAPS2 %1 est là, pour les scanners qui ne répondent pas d'eux-mêmes", "NAPS2 %1 ist da, für scanner, die nicht von selbst antworten", "NAPS2 %1 está aquí, para los escáneres que no responden solos", "NAPS2 %1 está cá, para os digitalizadores que não respondem por si", "NAPS2 %1 установлен — для сканеров, которые не отвечают сами"),
 "NAPS2 is not installed: only the scanners that do not answer by themselves (AirScan) need it.": ("NAPS2 n'est pas installé : seuls les scanners qui ne répondent pas d'eux-mêmes (AirScan) en ont besoin.", "NAPS2 ist nicht installiert: nur scanner, die nicht von selbst antworten (AirScan), brauchen es.", "NAPS2 no está instalado: solo lo necesitan los escáneres que no responden solos (AirScan).", "O NAPS2 não está instalado: só os digitalizadores que não respondem por si (AirScan) precisam dele.", "NAPS2 не установлен: он нужен только сканерам, которые не отвечают сами (AirScan)."),
 "two sheets went in together": ("deux feuilles sont passées ensemble", "zwei blätter wurden zusammen eingezogen", "dos hojas entraron juntas", "duas folhas entraram juntas", "два листа прошли вместе"),
 "syncing…": ("synchronisation…", "synchronisiert…", "sincronizando…", "a sincronizar…", "синхронизация…"),
 "on this computer only": ("sur cet ordinateur seulement", "nur auf diesem Computer", "solo en este ordenador", "só neste computador", "только на этом компьютере"),
 "Ctrl+, to set up a WebDAV folder shared with the phone": ("Ctrl+, pour configurer un dossier WebDAV partagé avec le téléphone", "Strg+, um einen mit dem Telefon geteilten WebDAV-Ordner einzurichten", "Ctrl+, para configurar una carpeta WebDAV compartida con el teléfono", "Ctrl+, para configurar uma pasta WebDAV partilhada com o telemóvel", "Ctrl+, — настроить папку WebDAV, общую с телефоном"),
}

_TR = {lang: {k: v[i] for k, v in _T.items()} for i, lang in enumerate(("fr", "de", "es", "pt", "ru"))}


def _lang():
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v:
            return v[:2].lower()
    return system_locale()[:2].lower()


_LANG = _lang()

def _(key, *args):
    s = _TR.get(_LANG, {}).get(key, key)
    for i, a in enumerate(args):
        s = s.replace("%" + str(i + 1), str(a))
    return s


# ------------------------------------------------------------------------------------------
# Names: the phone's rules, so both sides call a document and its file alike
# ------------------------------------------------------------------------------------------

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WORD = re.compile(r"[^\W_][^\W_'’.\-]*(?:['’.\-]+[^\W_]+)*")


def folder_name_of(name):
    """A folder name the server and the phone both accept; None when nothing is left."""
    n = re.sub(r"\s+", " ", _BAD.sub(" ", name)).strip().strip(".").strip()
    return n[:60] or None


def stamp_of(created_ms):
    return datetime.fromtimestamp(created_ms / 1000).strftime("%Y-%m-%d %Hh%M")


def title_of(doc):
    """"2026-09-24 11h32" followed by the name, when there is one."""
    return stamp_of(doc["created"]) + (" " + doc["name"] if doc.get("name") else "")


def file_name_of(doc, ext="pdf"):
    return re.sub(r"\s+", " ", _BAD.sub(" ", title_of(doc))).strip()[:120] + "." + ext


def _is_word(t):
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    return (letters >= 2 and letters * 10 >= len(t) * 6) or (digits >= 2 and letters == 0 and len(t) <= 10)


def first_words(text, most=5, most_chars=40):
    """The name a document gets from its text when the user gave none: the first few real
    words of the page, whole lines until there are three, debris lines skipped."""
    words, full = [], False
    for line in text.splitlines():
        tokens = [m.group(0).strip(".-'’") for m in _WORD.finditer(line)]
        tokens = [t for t in tokens if t]
        good = [t for t in tokens if _is_word(t)]
        if not good or len(good) * 2 < len(tokens):
            if words:
                break
            continue
        for t in good:
            if len(words) >= most or len(" ".join(words)) + len(t) + 1 > most_chars:
                full = True
                break
            words.append(t)
        if full or len(words) >= 3:
            break
    while len(words) > 1 and len(words[-1]) <= 3 and words[-1].islower():
        words.pop()
    return " ".join(words) or None


def fold(s):
    """Lower case, accents off: how search compares."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def reflow(text):
    """The text as read, the paper's line breaks taken out: a line as long as the page is wide
    goes on with the next one; short lines stay (addresses, amounts); blank lines still part
    paragraphs. The phone's rule."""
    widest = max((len(l.strip()) for l in text.splitlines()), default=0)
    out = []
    for para in re.split(r"\n\s*\n", text):
        lines = [l.strip() for l in para.splitlines() if l.strip()]
        buf = ""
        for i, line in enumerate(lines):
            buf += line
            if i == len(lines) - 1:
                break
            nxt = lines[i + 1]
            long = len(line) >= widest * 0.72
            full = long and (not line.endswith((":", ".")) or nxt[:1].islower())
            if full and line.endswith("-") and nxt[:1].islower():
                buf = buf[:-1]
            elif full:
                buf += " "
            else:
                buf += "\n"
        out.append(buf)
    return "\n\n".join(out).strip()


def new_id():
    return uuid.uuid4().hex[:12]


def now_ms():
    return int(time.time() * 1000)


def when_label(millis):
    d = datetime.fromtimestamp(millis / 1000)
    today = date.today()
    if d.date() == today:
        return _("today") + " " + d.strftime("%H:%M")
    if d.date() == today - timedelta(days=1):
        return _("yesterday") + " " + d.strftime("%H:%M")
    return f"{d.day} {d.strftime('%b' if d.year == today.year else '%b %Y')}".lower() + " " + d.strftime("%H:%M")


# ------------------------------------------------------------------------------------------
# Documents on disk. A document scanned here keeps its pages (the scan as it came, and the
# page as shown); one from another device has only its description and text, and its PDF once
# it has been opened.
#
#   index.json                 documents, folders, folders deleted here
#   docs/<id>/<page>.src.jpg   the scan            docs/<id>/<page>.jpg   the page as shown
#   docs/<id>/doc.pdf          the searchable PDF  docs/<id>/text.json    the text of each page
# ------------------------------------------------------------------------------------------

LOOKS = ("original", "clean", "grey", "bw")
PENDING, DONE, FAILED = "PENDING", "DONE", "FAILED"


class Store:
    """Thread-safe: reading the text, sync and downloads run off the UI thread. `on_change`
    is called after every change."""

    def __init__(self, root):
        self.root = root
        self.docs_dir = os.path.join(root, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)
        self.index_file = os.path.join(root, "index.json")
        self.lock = threading.RLock()
        self.on_change = None
        try:
            with open(self.index_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.docs = {d["id"]: d for d in data.get("docs", [])}
        self.folders = data.get("folders", [])          # {"name", "onServer"}
        self.gone_folders = data.get("goneFolders", [])
        self._texts = {}

    def _save(self):
        tmp = self.index_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"docs": list(self.docs.values()), "folders": self.folders, "goneFolders": self.gone_folders}, f, indent=1, ensure_ascii=False)
        replace(tmp, self.index_file)
        if self.on_change:
            self.on_change()

    # ---- files ------------------------------------------------------------------------

    def dir(self, doc_id):
        return os.path.join(self.docs_dir, doc_id)

    def src_file(self, doc_id, page_id):
        return os.path.join(self.dir(doc_id), page_id + ".src.jpg")

    def page_file(self, doc_id, page_id):
        return os.path.join(self.dir(doc_id), page_id + ".jpg")

    def pdf_file(self, doc_id):
        return os.path.join(self.dir(doc_id), "doc.pdf")

    def has_pdf(self, doc_id):
        p = self.pdf_file(doc_id)
        return os.path.exists(p) and os.path.getsize(p) > 0

    def text(self, doc_id):
        """The text read on each page (an empty list until it has been read)."""
        with self.lock:
            if doc_id not in self._texts:
                try:
                    with open(os.path.join(self.dir(doc_id), "text.json"), encoding="utf-8") as f:
                        self._texts[doc_id] = json.load(f)
                except (OSError, ValueError):
                    return []
            return list(self._texts[doc_id])

    def _set_text(self, doc_id, pages):
        os.makedirs(self.dir(doc_id), exist_ok=True)
        path = os.path.join(self.dir(doc_id), "text.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(pages, f, ensure_ascii=False)
        replace(path + ".tmp", path)
        self._texts[doc_id] = list(pages)

    # ---- reading ----------------------------------------------------------------------

    def get(self, doc_id):
        with self.lock:
            d = self.docs.get(doc_id)
            return json.loads(json.dumps(d)) if d else None

    def all(self, folder=None):
        """Newest first; folder None = every document."""
        with self.lock:
            docs = [json.loads(json.dumps(d)) for d in self.docs.values() if folder is None or d.get("folder", "") == folder]
        return sorted(docs, key=lambda d: -d["created"])

    def count(self, folder=None):
        with self.lock:
            return sum(1 for d in self.docs.values() if folder is None or d.get("folder", "") == folder)

    @staticmethod
    def page_count(doc):
        return doc.get("pageCount", 0) if doc.get("remote") else len(doc.get("pages", []))

    def pending(self):
        with self.lock:
            return [d["id"] for d in sorted(self.docs.values(), key=lambda d: d["created"]) if d.get("ocr") == PENDING and not d.get("remote")]

    def search(self, query):
        """Names and text, without case or accents; every word asked must be there, in any
        order: (document, snippet or None)."""
        q = fold(query.strip())
        words = q.split()
        if not words:
            return []
        out = []
        for d in self.all():
            text = "\n".join(self.text(d["id"]))
            folded = fold(text)
            title = fold(title_of(d))
            if not all(w in folded or w in title for w in words):
                continue
            at = folded.find(q)
            n = len(q)
            if at < 0:
                at, n = min(((folded.find(w), len(w)) for w in words if w in folded), default=(-1, 0))
            if at >= 0 and len(folded) == len(text):
                a, b = max(0, at - 40), min(len(text), at + n + 60)
                out.append((d, ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("…" if b < len(text) else "")))
            else:
                out.append((d, None))
        return out

    # ---- folders ----------------------------------------------------------------------

    def folder_names(self):
        with self.lock:
            return sorted((f["name"] for f in self.folders), key=str.lower)

    def folder_by_name(self, name):
        with self.lock:
            return next((f["name"] for f in self.folders if f["name"].lower() == name.lower()), None)

    def add_folder(self, name):
        """The name kept (made safe for a file system); the existing one if taken; None if empty."""
        with self.lock:
            n = folder_name_of(name)
            if not n:
                return None
            have = self.folder_by_name(n)
            if have:
                return have
            self.folders.append({"name": n, "onServer": False})
            self.gone_folders = [g for g in self.gone_folders if g != n]
            self._save()
            return n

    def rename_folder(self, old, name):
        with self.lock:
            n = folder_name_of(name)
            if not n or n == old or any(f["name"].lower() == n.lower() and f["name"] != old for f in self.folders):
                return None
            if any(f["name"] == old and f.get("onServer") for f in self.folders):
                self.gone_folders.append(old)
            self.folders = [f for f in self.folders if f["name"] != old] + [{"name": n, "onServer": False}]
            self.gone_folders = [g for g in self.gone_folders if g != n]
            now = now_ms()
            for d in self.docs.values():
                if d.get("folder", "") == old:
                    d.update(folder=n, modified=now)
            self._save()
            return n

    def delete_folder(self, name):
        """The folder goes; its documents stay, in « all scans »."""
        with self.lock:
            if any(f["name"] == name and f.get("onServer") for f in self.folders):
                self.gone_folders.append(name)
            self.folders = [f for f in self.folders if f["name"] != name]
            now = now_ms()
            for d in self.docs.values():
                if d.get("folder", "") == name:
                    d.update(folder="", modified=now)
            self._save()

    # folder side of the sync
    def folder_on_server(self, name):
        with self.lock:
            have = self.folder_by_name(name)
            if have is None:
                self.folders.append({"name": name, "onServer": True})
                have = name
            else:
                for f in self.folders:
                    if f["name"] == have:
                        f["onServer"] = True
            self._save()
            return have

    def folder_gone_there(self, name):
        with self.lock:
            self.folders = [f for f in self.folders if f["name"] != name]
            for d in self.docs.values():
                if d.get("folder", "") == name:
                    d["folder"] = ""
            self._save()

    def folder_removed_there(self, name):
        with self.lock:
            self.gone_folders = [g for g in self.gone_folders if g != name]
            self._save()

    def folders_state(self):
        with self.lock:
            return [dict(f) for f in self.folders], list(self.gone_folders)

    # ---- writing ----------------------------------------------------------------------

    def put(self, doc):
        """A new or changed document scanned here; its pages' files are already in its folder."""
        with self.lock:
            os.makedirs(self.dir(doc["id"]), exist_ok=True)
            if doc.get("ocr") == PENDING:
                # new pages: the old text and PDF no longer match them
                for name in ("doc.pdf", "text.json"):
                    try:
                        remove(os.path.join(self.dir(doc["id"]), name))
                    except OSError:
                        pass
                self._texts.pop(doc["id"], None)
                remove_tree(os.path.join(self.dir(doc["id"]), "render"))
            keep = {"doc.pdf", "text.json", "render"}
            for p in doc.get("pages", []):
                keep |= {p["id"] + ".jpg", p["id"] + ".src.jpg"}
            for name in os.listdir(self.dir(doc["id"])):
                if name not in keep and not name.startswith("ocr-"):
                    path = os.path.join(self.dir(doc["id"]), name)
                    remove_tree(path) if os.path.isdir(path) else remove(path)
            self.docs[doc["id"]] = doc
            self._save()

    def update(self, doc_id, **changes):
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None:
                return
            d.update(changes)
            self._save()

    def rename(self, doc_id, name):
        name = (name or "").strip()
        self.update(doc_id, name=name or None, named=bool(name), modified=now_ms())

    def move(self, doc_id, folder):
        self.update(doc_id, folder=folder or "", modified=now_ms())

    def delete(self, doc_id):
        with self.lock:
            self.docs.pop(doc_id, None)
            self._texts.pop(doc_id, None)
            remove_tree(self.dir(doc_id))
            self._save()

    def read_again(self, doc_id, lang=None):
        """Another language, or a better model: the text and the PDF are made again."""
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None or d.get("remote"):
                return
            for name in ("doc.pdf", "text.json"):
                try:
                    remove(os.path.join(self.dir(doc_id), name))
                except OSError:
                    pass
            self._texts.pop(doc_id, None)
            d.update(ocr=PENDING, lang=lang or d["lang"], rev=d.get("rev", 0) + 1, modified=now_ms())
            if not d.get("named"):
                d["name"] = None
            self._save()

    def ocr_done(self, doc_id, rev, pages, pdf, read_by):
        """Called once a revision of the document has been read."""
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None or d.get("rev", 0) != rev:
                return False
            self._set_text(doc_id, pages)
            if pdf:
                replace(pdf, self.pdf_file(doc_id))
            if not d.get("named"):
                d["name"] = first_words(next((p for p in pages if p.strip()), "")) or d.get("name")
            d.update(ocr=DONE, readBy=read_by)
            self._save()
            return True

    def ocr_failed(self, doc_id, rev, pdf=None):
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None or d.get("rev", 0) != rev:
                return
            if pdf:
                replace(pdf, self.pdf_file(doc_id))
            self._set_text(doc_id, [""] * len(d.get("pages", [])))
            d.update(ocr=FAILED, readBy="")
            self._save()

    def put_remote(self, doc, text, drop_pdf):
        """A document described by another device, new here or changed there."""
        with self.lock:
            os.makedirs(self.dir(doc["id"]), exist_ok=True)
            self._set_text(doc["id"], text)
            if drop_pdf:
                try:
                    remove(self.pdf_file(doc["id"]))
                except OSError:
                    pass
                remove_tree(os.path.join(self.dir(doc["id"]), "render"))
            self.docs[doc["id"]] = doc
            self._save()

    def forget_server(self):
        """Another server or folder: every document scanned here goes up again as new; the ones
        from elsewhere (they live on the old server) go."""
        with self.lock:
            for d in list(self.docs.values()):
                if d.get("remote"):
                    self.docs.pop(d["id"])
                    remove_tree(self.dir(d["id"]))
            for f in self.folders:
                f["onServer"] = False
            self.gone_folders = []
            try:
                remove(os.path.join(self.root, "sync.json"))
            except OSError:
                pass
            self._save()


# ------------------------------------------------------------------------------------------
# WebDAV
# ------------------------------------------------------------------------------------------

class WebDavError(Exception):
    pass


def encode_segment(s):
    return quote(s, safe="")


def folder_url(cfg):
    folder = (cfg.get("folder") or "Scans").strip().strip("/") or "Scans"
    return cfg.get("server", "").strip().rstrip("/") + "/" + "/".join(encode_segment(p) for p in folder.split("/")) + "/"


class WebDav:
    def __init__(self, username, password, timeout=30):
        self.s = requests.Session()
        self.s.auth = (username, password)
        self.s.headers["User-Agent"] = f"{APP}-desktop/{VERSION}"
        self.timeout = (min(15, timeout), timeout)

    def _req(self, method, url, body=None, depth=None, headers=None, content_type="text/plain; charset=utf-8", allow=(), stream=False):
        h = dict(headers or {})
        if depth is not None:
            h["Depth"] = str(depth)
        if body is not None:
            h["Content-Type"] = content_type
        try:
            r = self.s.request(method, url, data=body.encode("utf-8") if isinstance(body, str) else body, headers=h, timeout=self.timeout, stream=stream)
        except requests.RequestException:
            raise WebDavError(_("cannot reach the server"))
        if r.status_code == 401:
            raise WebDavError(_("wrong username or password"))
        if r.status_code >= 400 and r.status_code not in allow:
            raise WebDavError(f"{method}: HTTP {r.status_code}")
        return r

    @staticmethod
    def _etag(v):
        if not v:
            return None
        v = v.strip()
        if v.startswith("W/"):
            v = v[2:]
        return v.strip('"') or None

    _PROPS = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/><d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>'

    def list(self, url):
        """What is directly inside the folder (the folder itself excluded)."""
        r = self._req("PROPFIND", url, self._PROPS, depth=1, content_type="application/xml; charset=utf-8")
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            raise WebDavError(_("not a WebDAV folder at this address"))
        here = unquote(urlparse(url).path).rstrip("/")
        out = []
        for resp in root.iter("{DAV:}response"):
            href = resp.findtext("{DAV:}href")
            if not href:
                continue
            path = unquote(urlparse(urljoin(url, href.strip())).path).rstrip("/")
            if path == here:
                continue
            out.append({"name": path.rsplit("/", 1)[-1], "etag": self._etag(resp.findtext(".//{DAV:}getetag")),
                        "dir": resp.find(".//{DAV:}resourcetype/{DAV:}collection") is not None})
        return out

    def etag_of(self, url):
        r = self._req("PROPFIND", url, self._PROPS, depth=0, content_type="application/xml; charset=utf-8", allow=(404,))
        if r.status_code == 404:
            return None
        try:
            return self._etag(ET.fromstring(r.content).findtext(".//{DAV:}getetag"))
        except ET.ParseError:
            return None

    def get_text(self, url):
        r = self._req("GET", url, allow=(404,))
        return None if r.status_code == 404 else r.content.decode("utf-8", "replace")

    def put_text(self, url, text, content_type="application/json; charset=utf-8"):
        r = self._req("PUT", url, text, content_type=content_type)
        return self._etag(r.headers.get("ETag")) or self.etag_of(url)

    def put_file(self, url, path, content_type):
        with open(path, "rb") as f:
            r = self._req("PUT", url, f, content_type=content_type)
        return self._etag(r.headers.get("ETag")) or self.etag_of(url)

    def download(self, url, out, progress=None):
        """False when the server does not have it."""
        r = self._req("GET", url, allow=(404,), stream=True)
        if r.status_code == 404:
            return False
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(out + ".part", "wb") as f:
            for chunk in r.iter_content(64 * 1024):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done * 100 // total)
        replace(out + ".part", out)
        return True

    def move(self, src, dst):
        """Renames a file on the server without sending it again; never overwrites."""
        self._req("MOVE", src, headers={"Destination": dst, "Overwrite": "F"})

    def delete(self, url):
        self._req("DELETE", url, allow=(404,))

    def mkcol(self, url):
        self._req("MKCOL", url, allow=(405, 301))

    def exists(self, url):
        return self._req("PROPFIND", url, depth=0, allow=(404,)).status_code != 404

    def mkdirs(self, url):
        if self.exists(url):
            return
        u = urlparse(url)
        path = ""
        for seg in [s for s in u.path.strip("/").split("/") if s]:
            path += "/" + seg
            at = f"{u.scheme}://{u.netloc}{path}/"
            if not self.exists(at):
                self.mkcol(at)


# ------------------------------------------------------------------------------------------
# Sync: the protocol of readers-scanner/docs/SYNC.md, the same as the phone's sync/Sync.kt.
#
#   Scans/<folder>/<date> <name>.pdf     a folder = a subfolder, one level deep
#   Scans/<date> <name>.pdf              « all scans » only
#   Scans/.readers-scanner/<id>.json     one description per document
# ------------------------------------------------------------------------------------------

META_DIR = ".readers-scanner"
META_FORMAT = "readers-scanner"
_sync_lock = threading.Lock()


def meta_build(doc, pdf, text, pages):
    return json.dumps({"format": META_FORMAT, "version": 1, "id": doc["id"], "created": doc["created"], "modified": doc.get("modified", doc["created"]),
                       "name": doc.get("name") or None, "named": bool(doc.get("named")), "folder": doc.get("folder", ""), "lang": doc.get("lang", "eng"),
                       "pages": pages, "text": list(text), "pdf": pdf, "readBy": doc.get("readBy", ""), "ocr": doc.get("ocr", DONE)},
                      ensure_ascii=False, indent=1)


def meta_parse(s):
    try:
        o = json.loads(s)
        if not isinstance(o, dict) or o.get("format") != META_FORMAT:
            return None
        name = o.get("name")
        return {"id": o["id"], "created": int(o["created"]), "modified": int(o.get("modified", o["created"])),
                "name": name if isinstance(name, str) and name.strip() else None, "named": bool(o.get("named")),
                "folder": o.get("folder") or "", "lang": o.get("lang") or "eng", "pages": int(o.get("pages", 0)),
                "text": [t for t in o.get("text", []) if isinstance(t, str)], "pdf": o["pdf"], "readBy": o.get("readBy") or "",
                "ocr": o.get("ocr") if o.get("ocr") in (DONE, FAILED) else DONE}
    except (ValueError, KeyError, TypeError):
        return None


def _key(d):
    """What the description depends on here."""
    return f"{d.get('rev', 0)}|{d.get('ocr')}|{title_of(d)}|{d.get('folder', '')}|{d.get('lang')}|{bool(d.get('remote'))}|{bool(d.get('named'))}"


def _pdf_key(d):
    return f"{d.get('rev', 0)}|{d.get('ocr')}"


def _read_state(store):
    try:
        with open(os.path.join(store.root, "sync.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_state(store, state):
    path = os.path.join(store.root, "sync.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    replace(path + ".tmp", path)


def sync_run(store, cfg, ensure_pdf, timeout=60):
    """Two-way sync with the WebDAV folder. `ensure_pdf(doc)` gives the PDF of a document
    scanned here. Returns (sent, received, deleted)."""
    with _sync_lock:
        return _sync_run(store, cfg, ensure_pdf, timeout)


def _sync_run(store, cfg, ensure_pdf, timeout):
    dav = WebDav(cfg.get("username", ""), cfg.get("password", ""), timeout)
    root = folder_url(cfg)
    dav.mkdirs(root)

    def url(path):
        return root + "/".join(encode_segment(p) for p in path.split("/"))

    def dir_url(folder):
        return root + encode_segment(folder) + "/"

    meta_dir = root + encode_segment(META_DIR) + "/"

    def meta_url(doc_id):
        return meta_dir + encode_segment(doc_id + ".json")

    entries = dav.list(root)
    dirs = {e["name"] for e in entries if e["dir"] and not e["name"].startswith(".")}
    pdfs = {e["name"]: e for e in entries if not e["dir"] and e["name"].lower().endswith(".pdf")}
    for d in dirs:
        for e in dav.list(dir_url(d)):
            if not e["dir"] and e["name"].lower().endswith(".pdf"):
                pdfs[f"{d}/{e['name']}"] = e
    if any(e["dir"] and e["name"] == META_DIR for e in entries):
        metas = {e["name"][:-5]: e for e in dav.list(meta_dir) if not e["dir"] and e["name"].endswith(".json")}
    else:
        dav.mkcol(meta_dir)
        metas = {}
    state = _read_state(store)
    folders, gone = store.folders_state()
    gone = set(gone)
    up = down = deleted = 0

    # --- folders
    present = set(dirs)
    for d in dirs:
        if d not in gone:
            store.folder_on_server(d)
    for f in folders:
        if f["name"] in dirs:
            continue
        if f.get("onServer"):
            store.folder_gone_there(f["name"])
        else:
            dav.mkcol(dir_url(f["name"]))
            store.folder_on_server(f["name"])
            present.add(f["name"])

    def ensure_dir(path):
        folder = path.rpartition("/")[0]
        if folder and folder not in present:
            dav.mkcol(dir_url(folder))
            store.folder_on_server(folder)
            present.add(folder)

    taken = set()

    def upload(d, sent):
        nonlocal up
        folder = d.get("folder", "")
        base = file_name_of(d)

        def at(name):
            return f"{folder}/{name}" if folder else name

        name, i = base, 2
        path = at(name)
        # another document, or someone's file, already has that name
        while path != (sent or {}).get("path") and (path in taken or path in pdfs or any(k != d["id"] and s.get("path") == path for k, s in state.items())):
            name = f"{base[:-4]} ({i}).pdf"
            path = at(name)
            i += 1
        if not d.get("remote") and (sent is None or sent.get("pdfKey") != _pdf_key(d) or sent["path"] not in pdfs):
            pdf = ensure_pdf(d)
            if not pdf:
                return
            ensure_dir(path)
            etag = dav.put_file(url(path), pdf, "application/pdf")
            if sent is not None and sent["path"] != path and sent["path"] in pdfs:
                dav.delete(url(sent["path"]))
        elif sent is not None and sent["path"] != path and sent["path"] in pdfs:
            ensure_dir(path)
            dav.move(url(sent["path"]), url(path))
            etag = dav.etag_of(url(path))
        else:
            if sent is None:
                return              # a document from elsewhere never goes up as a new file
            path, etag = sent["path"], sent.get("etag")
        taken.add(path)
        meta_etag = dav.put_text(meta_url(d["id"]), meta_build(d, path, store.text(d["id"]), Store.page_count(d)))
        state[d["id"]] = {"path": path, "etag": etag, "key": _key(d), "pdfKey": _pdf_key(d), "metaEtag": meta_etag or "?"}
        _write_state(store, state)
        up += 1

    def folder_here(name):
        return (store.folder_by_name(name) or store.folder_on_server(name)) if name else ""

    # --- 1. documents described on the server
    for doc_id, mf in metas.items():
        local = store.get(doc_id)
        sent = state.get(doc_id)
        if local is None:
            if sent is not None and (sent.get("metaEtag") is None or sent.get("metaEtag") == mf["etag"]):
                # deleted here, unchanged there: deleted there too
                if sent["path"] in pdfs:
                    dav.delete(url(sent["path"]))
                dav.delete(meta_url(doc_id))
                state.pop(doc_id)
                _write_state(store, state)
                deleted += 1
                continue
            m = meta_parse(dav.get_text(meta_url(doc_id)) or "")
            if m is None:
                continue
            doc = {"id": m["id"], "created": m["created"], "modified": m["modified"], "name": m["name"], "named": m["named"],
                   "folder": folder_here(m["folder"]), "lang": m["lang"], "pages": [], "ocr": m["ocr"], "rev": 0, "readBy": m["readBy"],
                   "remote": True, "pageCount": m["pages"]}
            store.put_remote(doc, m["text"], drop_pdf=True)
            state[doc_id] = {"path": m["pdf"], "etag": (pdfs.get(m["pdf"]) or {}).get("etag"), "key": _key(doc), "pdfKey": _pdf_key(doc), "metaEtag": mf["etag"]}
            _write_state(store, state)
            down += 1
            continue
        changed_there = sent is None or (sent.get("metaEtag") is not None and sent.get("metaEtag") != mf["etag"])
        changed_here = sent is None or sent.get("key") != _key(local)
        if changed_there:
            m = meta_parse(dav.get_text(meta_url(doc_id)) or "")
            if m is None:
                continue
            if not changed_here or m["modified"] > local.get("modified", local["created"]):
                # theirs is the latest
                if local.get("remote"):
                    pdf_changed = sent is None or sent.get("etag") != (pdfs.get(m["pdf"]) or {}).get("etag")
                    local.update(name=m["name"], named=m["named"], folder=folder_here(m["folder"]), lang=m["lang"], ocr=m["ocr"],
                                 readBy=m["readBy"], pageCount=m["pages"], modified=m["modified"])
                    store.put_remote(local, m["text"], drop_pdf=pdf_changed)
                else:
                    store.update(doc_id, name=m["name"], named=m["named"], folder=folder_here(m["folder"]), modified=m["modified"])
                now = store.get(doc_id)
                state[doc_id] = {"path": m["pdf"], "etag": (pdfs.get(m["pdf"]) or {}).get("etag"), "key": _key(now), "pdfKey": _pdf_key(now), "metaEtag": mf["etag"]}
                _write_state(store, state)
                down += 1
                continue
            # ours is the latest; its file is where they left it
            sent = dict(sent or {}, path=m["pdf"], etag=(pdfs.get(m["pdf"]) or {}).get("etag"))
        if (changed_here or (sent or {}).get("metaEtag") is None) and local.get("ocr") != PENDING:
            upload(local, sent)
        elif sent:
            taken.add(sent["path"])

    # --- 2. documents here without a description there
    for d in sorted(store.all(), key=lambda d: d["created"]):
        if d["id"] in metas:
            continue
        sent = state.get(d["id"])
        if sent is not None and sent.get("metaEtag") is not None:
            # its description went: deleted there — here too, unless it changed here since
            if sent.get("key") == _key(d):
                store.delete(d["id"])
                state.pop(d["id"])
                _write_state(store, state)
                deleted += 1
                continue
        if d.get("remote") or d.get("ocr") == PENDING:
            continue
        upload(d, sent)

    # --- 3. deleted here before ever being described (sent by an old version)
    for doc_id, s in list(state.items()):
        if store.get(doc_id) is not None or doc_id in metas:
            continue
        there = pdfs.get(s["path"])
        if there is not None and (s.get("etag") is None or there["etag"] is None or there["etag"] == s["etag"]):
            dav.delete(url(s["path"]))
            deleted += 1
        state.pop(doc_id)
        _write_state(store, state)

    # --- 4. folders deleted or renamed here: removed there once empty
    for g in gone:
        if g in dirs and not any(not e["dir"] for e in dav.list(dir_url(g))):
            dav.delete(dir_url(g))
        store.folder_removed_there(g)
    return up, down, deleted


def fetch_pdf(store, cfg, doc, progress=None):
    """Brings down the PDF of a document from elsewhere. None when the server does not have it."""
    path = (_read_state(store).get(doc["id"]) or {}).get("path")
    if not path:
        return None
    os.makedirs(store.dir(doc["id"]), exist_ok=True)
    dav = WebDav(cfg.get("username", ""), cfg.get("password", ""), 120)
    out = store.pdf_file(doc["id"])
    ok = dav.download(folder_url(cfg) + "/".join(encode_segment(p) for p in path.split("/")), out, progress)
    return out if ok else None
# Written by tools/naps2_messages.py from NAPS2 v8.2.1 (46 languages): do not edit by hand.
# What NAPS2 says when a scan fails (lower case, no final stop) → our word for it.
NAPS2_WORDS = {
    'a apărut o eroare la driver-ul de scanare': 'driver',
    'a comunicação com o digitalizador foi interrompida': 'comm',
    'a comunicação com scanner foi interrompida': 'comm',
    'a fost întreruptă comunicarea cu dispozitivul de scanare': 'comm',
    'a kiválasztott lapolvasó foglalt': 'busy',
    'a kiválasztott lapolvasó inaktív': 'offline',
    'a kiválasztott lapolvasó nem található': 'notfound',
    'a kiválasztott lapolvasó nem támogatja a kétoldalas használatot. ha a lapolvasó feltehetőleg támogatja a kétoldalas használatot, próbáljon meg másik illesztőprogramot használni': 'noduplex',
    'a kiválasztott lapolvasó nem támogatja az lapadagoló használatát. ha a lapolvasója rendelkezik lapadagolóval, próbáljon meg másik illesztőprogramot használni': 'nofeeder',
    'a kommunikáció a lapolvasó eszközzel megszakadt': 'comm',
    'a lapolvasó fedele nyitva van': 'cover',
    'a lapolvasó felmelegedik': 'warming',
    'a lapolvasóban papírelakadás van': 'jam',
    'a munkavégző folyamat összeomlott': 'driver',
    'a sane illesztőprogram nem áll rendelkezésre. győződjön meg róla, hogy telepítette a szükséges csomagokat:': 'nosane',
    'a tampa do digitalizador está aberta': 'cover',
    'a tampa do scanner está aberta': 'cover',
    'an error occurred with the scanning driver': 'driver',
    'aparentemente, o digitalizador escolhido não permite digitalização em frente e verso. se é suposto que o digitalizador o permita, tente usar um controlador diferente': 'noduplex',
    'aparentemente, o digitalizador escolhido não possui um alimentador. se o seu digitalizador possuir um alimentador, tente usar um controlador diferente': 'nofeeder',
    'arbeidsprosessen krasja': 'driver',
    'arbejdsprocessen fejlede': 'driver',
    'arbetsprocessen kraschade': 'driver',
    'aucun périphérique sélectionné': 'notfound',
    'besleyicide sayfa yok': 'empty',
    'capacul scanerului este deschis': 'cover',
    'carta inceppata nello scanner': 'jam',
    'communicatie met het scanapparaat is onderbroken': 'comm',
    'communication with the scanning device was interrupted': 'comm',
    'có lỗi xảy ra với trình điều khiển máy quét': 'driver',
    'daar was n fout met die skandeer drywer': 'driver',
    'de geselecteerde scanner is bezig': 'busy',
    'de geselecteerde scanner is offline': 'offline',
    'de geselecteerde scanner ondersteunt geen dubbelzijdig scannen. als uw scanner dit hoort te ondersteunen, probeer dan een andere driver': 'noduplex',
    'de sane driver is niet beschikbaar. installeer de vereiste pakketten:': 'nosane',
    'de scanner warmt op': 'warming',
    'delovni proces se je zrušil': 'driver',
    'den valda skannern kan inte hittas': 'notfound',
    'den valda skannern stöder inte användning av duplex. försök med en annan drivrutin, om din skanner sägs stödja duplex': 'noduplex',
    'den valda skannern stöder inte användning av matare. försök med en annan drivrutin, om din skanner har en matare': 'nofeeder',
    'den valda skannern är offline': 'offline',
    'den valda skannern är upptagen': 'busy',
    'den valde skannaren er fråkopla': 'offline',
    'den valde skannaren er opptatt': 'busy',
    'den valde skannaren støtter ikkje bruk av matar. hvis skannaren din har ein matar, prøv å bruka ein annan drivar': 'nofeeder',
    'den valde skannaren støtter ikkje tosidig. hvis skannaren din skal støtta tosidig, prøv å bruka ein annan driver': 'noduplex',
    'den valde skannaren vart ikkje funnen': 'notfound',
    'den valgte scanner blev ikke fundet': 'notfound',
    'den valgte scanner er offline': 'offline',
    'den valgte scanner er optaget': 'busy',
    'den valgte scanner understøtter ikke at bruge en dokumentføder. hvis din scanner har en dokumentføder, skal du prøve at bruge en anden driver': 'nofeeder',
    'den valgte scanner understøtter ikke brugen af duplex. hvis din scanner burde understøtte duplex, kan du prøve at bruge en anden driver': 'noduplex',
    'der arbeitsprozess ist abgestürzt': 'driver',
    'der ausgewählte scanner unterstützt keine zuführung. wenn der scanner eine zuführung besitzt, probiere einen anderen treiber': 'nofeeder',
    'der ausgewählte scanner unterstützt keinen duplexscan. sollte der scanner einen duplexscan unterstützen, probieren sie einen anderen treiber aus': 'noduplex',
    'der er ingen ark i mataren': 'empty',
    'der er ingen billeder i føderen': 'empty',
    'der gewählte scanner ist ausgeschaltet (offline)': 'offline',
    'der gewählte scanner ist in benutzung': 'busy',
    'der gewählte scanner kann nicht gefunden werden': 'notfound',
    'der opstod en fejl med scanner driveren': 'driver',
    'der sane-treiber ist nicht verfügbar. stelle sicher, dass die erforderlichen pakete installiert sind:': 'nosane',
    'der scanner hat einen papierstau': 'jam',
    'der scanner wird vorgewärmt': 'warming',
    'det finns inga sidor i mataren': 'empty',
    'det oppstod en feil med skannerdriveren': 'driver',
    'deze scanner ondersteunt geen automatische documentinvoer. als uw scanner dit wel heeft, probeer dan een andere driver': 'nofeeder',
    'die kommunikation mit dem scan-gerät wurde unterbrochen': 'comm',
    'die scannerabdeckung ist offen': 'cover',
    'došlo je do greške sa driver-om za skener': 'driver',
    'došlo k chybě ovladače skeneru': 'driver',
    'drejtuesi sane nuk është i disponueshëm. sigurohuni të instaloni paketat e duhura:': 'nosane',
    'driverul sane nu este disponibil. asigurați-vă că instalați pachetele necesare:': 'nosane',
    'ein fehler ist mit dem scanner-treiber aufgetreten': 'driver',
    'el controlador sane no está disponible. asegúrese de instalar los paquetes necesarios:': 'nosane',
    'el escáner se está calentando': 'warming',
    'el escáner seleccionado está inactivo': 'offline',
    'el escáner seleccionado está ocupado': 'busy',
    'el escáner seleccionado no admite el uso de un alimentador. si el escáner tiene un alimentador, intente usar un controlador diferente': 'nofeeder',
    'el escáner seleccionado no admite el uso dúplex. si el escáner si admite dúplex, intente utilizar un controlador diferente': 'noduplex',
    'el escáner tiene un papel atascado': 'jam',
    'er is papier vastgelopen in de scanner': 'jam',
    'ett fel uppstod med skannerdrivrutinen': 'driver',
    'existe papel encravado no digitalizador': 'jam',
    'fout met het stuurprogramma van de scanner': 'driver',
    'galat ditemukan didalam driver piranti': 'driver',
    'geen apparaat geselecteerd': 'notfound',
    'geen bladsye is in die voerder': 'empty',
    "geen pagina's in de invoerlade": 'empty',
    'geen toestel gekies': 'notfound',
    'greška se pojavila kod drivera za skeniranje': 'driver',
    'het deksel van de scanner is open': 'cover',
    'het werkproces is gecrasht': 'driver',
    'hiba történt a lapolvasó illesztőprogrammal': 'driver',
    'hiçbir aygıt seçilmedi': 'notfound',
    'il coperchio dello scanner è aperto': 'cover',
    'il driver sane non è disponibile. assicurati di aver installato il pacchetto richiesto:': 'nosane',
    "il n'y a aucune page dans le dispositif d'alimentation": 'empty',
    'il processo di elaborazione si è bloccato': 'driver',
    'in der zuführung sind keine seiten': 'empty',
    'ingen enheder valgt': 'notfound',
    'ingen enhet er valgt': 'notfound',
    'ingen enhet vald': 'notfound',
    'ingen skannar vald': 'notfound',
    'izabrani skener je isključen': 'offline',
    'izabrani skener je zauzet': 'busy',
    'izabrani skener nema podršku za korištenje dupleksa - dvostranog skeniranja. ako skener treba da podržava dupleks, pokušajte s korištenjem drugog drajvera': 'noduplex',
    'izabrani skener nema podršku za korištenje ulagača papira. ako skener ima ulagač papira, pokušajte s korištenjem drugog drajvera': 'nofeeder',
    'izabrani skener se ne može pronaći': 'notfound',
    'izbran skener je izklopljen': 'offline',
    'izbran skener je zaseden': 'busy',
    'izbran skener ne podpira avtomatskega podajalnika. če skener vsebuje avtomatski podajalnik, poizkusite z drugim gonilnikom': 'nofeeder',
    'izbran skener ne podpira obojestranskega skeniranja. če bi naj vaš skener podpiral obojestransko skeniranje, poizkusite z drugim gonilnikom': 'noduplex',
    'izbranega skenerja ni bilo mogoče najti': 'notfound',
    'izvēlētais skeneris ir aizņemts': 'busy',
    'izvēlētais skeneris ir bezsaistē': 'offline',
    'izvēlētais skeneris neatbalsta dokumentu padevi. ja jūsu skenerim ir padeve, mēģiniet izmantot citu draiveri': 'nofeeder',
    'izvēlētais skeneris neatbalsta dupleksu. ja jūsu skenerim ir paredzēts duplekss, mēģiniet izmantot citu draiveri': 'noduplex',
    'izvēlēto skeneri nevar atrast': 'notfound',
    'kan de geselecteerde scanner niet vinden': 'notfound',
    'keine geräte ausgewählt': 'notfound',
    'không có thiết bị được lựa chọn': 'notfound',
    'không trang sau nằm trong máng': 'empty',
    'kommunikasie met die skandeertoestel is onderbreek': 'comm',
    'kommunikasjon med skannaren vart avbroten': 'comm',
    'kommunikation med scanningsenheden blev afbrudt': 'comm',
    'kommunikationen med skanningsenheten avbröts': 'comm',
    'komunikace se skenovacím zařízením byla přerušena': 'comm',
    'komunikacija s skenerjem je bila prekinjena': 'comm',
    'komunikacja z urządzeniem skanującym została przerwana': 'comm',
    'komunikasi dengan alat pindai terganggu': 'comm',
    'komunikimi me pajisjen e skanimit u ndërpre': 'comm',
    'komunikácia so skenovacím zariadením bola prerušená': 'comm',
    'kryt skenera je otvorený': 'cover',
    "l'escàner s'està escalfant": 'warming',
    "l'escàner seleccionat està apagat": 'offline',
    "l'escàner seleccionat està ocupat": 'busy',
    "l'escàner seleccionat no suporta alimentació des d'un alimentador. si el vostre escàner té alimentador de paper, seleccioneu un controlador diferent": 'nofeeder',
    "l'escàner seleccionat no suporta dúplex (escaneig per dues cares). si el vostre escàner suporta aquesta tecnologia, seleccioneu un controlador diferent": 'noduplex',
    "l'escàner té un embús de paper": 'jam',
    'la communication avec le périphérique de numérisation a été interrompue': 'comm',
    'la comunicazione con il dispositivo di scansione è stata interrotta': 'comm',
    "la tapa de l'escàner està oberta": 'cover',
    'la tapa del escáner está abierta': 'cover',
    'laitetta ei valittu': 'notfound',
    'le capot du scanner est ouvert': 'cover',
    "le pilote sane n'est pas disponible. merci d'installer les paquets nécessaires :": 'nosane',
    'le processus de travail a planté': 'driver',
    'le scanner a un bourrage papier': 'jam',
    'le scanner est en train de préchauffer': 'warming',
    'le scanner sélectionné est introuvable': 'notfound',
    'le scanner sélectionné est occupé': 'busy',
    'le scanner sélectionné est éteint': 'offline',
    "le scanner sélectionné ne semble pas disposer de dispositif d'alimentation. s'il en possède un, essayer d'utiliser un autre pilote": 'nofeeder',
    "le scanner sélectionné ne semble pas gérer pas le recto-verso (duplex). s'il est supposé le prendre en charge, essayer d'utiliser un autre pilote": 'noduplex',
    "lo scanner selezionato non supporta l'uso di un alimentatore automatico. se lo scanner ha un alimentatore automatico, prova a usare un driver differente": 'nofeeder',
    'lo scanner selezionato non supporta la scansione fronte retro. se lo scanner dovrebbe supportare la scansione fronte retro, prova a usare un driver differente': 'noduplex',
    'lo scanner selezionato non è stato trovato': 'notfound',
    'lo scanner selezionato è occupato': 'busy',
    'lo scanner selezionato è offline': 'offline',
    'lo scanner si sta riscaldando': 'warming',
    'mbulesa e skanerit është e hapur': 'cover',
    'máy quét chọn không hỗ trợ sử dụng hai mặt. nếu máy quét của bạn phải hỗ trợ hai mặt, hãy thử sử dụng một trình điều khiển khác nhau': 'noduplex',
    'máy quét chọn không hỗ trợ sử dụng một feeder. nếu máy quét của bạn không có một feeder, hãy thử sử dụng một trình điều khiển khác nhau': 'nofeeder',
    'máy quét chọn không thể được tìm thấy': 'notfound',
    'máy quét chọn đang bận': 'busy',
    'máy quét chọn đang ẩn': 'offline',
    'máy quét có kẹt giấy': 'jam',
    'máy quét đang ấm dần lên': 'warming',
    'naprava ni izbrana': 'notfound',
    'nav izvēlēta ierīce': 'notfound',
    'ndodhi një gabim me drejtuesin e skanimit': 'driver',
    'nebolo vybrané žiadne zariadenie': 'notfound',
    'nebylo vybráno žádné zařízení': 'notfound',
    'nema listova u uvlakaču': 'empty',
    'nema papira u ulagaču': 'empty',
    'nenhum dispositivo selecionado': 'notfound',
    'nenhum scanner escolhido': 'notfound',
    'nessun dispositivo selezionato': 'notfound',
    'nici o foaie in alimentator': 'empty',
    'nici un dispozitiv selectat': 'notfound',
    'nie ma stron w podajniku': 'empty',
    'nie można odnaleźć wybranego skanera': 'notfound',
    'nie wybrano urządzenia': 'notfound',
    'nije izabran uređaj': 'notfound',
    'nincs kiválasztott eszköz': 'notfound',
    'nincs lap a lapadagolóban': 'empty',
    'no device selected': 'notfound',
    'no ha seleccionado ningún dispositivo': 'notfound',
    "no hi ha cap pàgina a l'alimentador": 'empty',
    'no pages are in the feeder': 'empty',
    "no s'ha pogut trobar l'escàner seleccionat": 'notfound',
    "no s'ha seleccionat cap dispositiu": 'notfound',
    "no s'ha trobat el controlador sane. assegureu-vos d'instal·lar les dependències necessàries:": 'nosane',
    'no se ha encontrado el escáner seleccionado': 'notfound',
    "non ci sono fogli nell'alimentatore automatico": 'empty',
    'notika kļūda ar skenēšanas draiveri': 'driver',
    'nuk ka faqe te furnizuesi': 'empty',
    'nuk është zgjedhur asnjë pajisje': 'notfound',
    'não existem folhas no alimentador': 'empty',
    'nắp máy quét mở': 'cover',
    'o controlador sane não está disponível. certifique-se que instala os pacotes necessários:': 'nosane',
    'o digitalizador escolhido está desligado': 'offline',
    'o digitalizador escolhido está ocupado': 'busy',
    'o digitalizador escolhido não foi encontrado': 'notfound',
    'o digitalizador está a aquecer': 'warming',
    'o driver sane não está disponível. certifique-se de instalar os pacotes necessários:': 'nosane',
    'o processo de trabalho falhou': 'driver',
    'o processo de trabalho travou': 'driver',
    'o scanner escolhido está ocupado': 'busy',
    'o scanner escolhido está offline': 'offline',
    'o scanner escolhido não foi encontrado': 'notfound',
    'o scanner está aquecendo': 'warming',
    'o scanner selecionado não suporta o uso de alimentador. se o scanner tiver um alimentador, tente usar um driver diferente': 'nofeeder',
    'o scanner selecionado não suporta o uso duplex. se o scanner realmente suporta duplex, tente usar um driver diferente': 'noduplex',
    'o scanner tem um atolamento de papel': 'jam',
    'ocorreu algum erro com o driver do scanner': 'driver',
    'ocorreu um erro com o controlador de digitalização': 'driver',
    'ocurrió un error con el controlador del escáner': 'driver',
    'odabrani skener ne podržava dvostrano skeniranje. ako vaš skener nema duplekser, pokušajte da instalirate drugi drajver': 'noduplex',
    'odabrani skener ne podržava korišćenje uvlakača. ako vaš skener nema uvlakač, pokušajte da instalirate drugi drajver': 'nofeeder',
    'odabrani skener nije pronađen': 'notfound',
    'odabrani skener nije uključen': 'offline',
    'ovladač sane není dostupný. ověřte instalaci požadovaných balíčků:': 'nosane',
    'ovládač sane nie je k dispozícii. preverte požadované balíky pre inštaláciu:': 'nosane',
    'padavimo mechanizme nėra lapų': 'empty',
    'padevē nav lapu': 'empty',
    'papir je zaglavio u skeneru': 'jam',
    'papir v skenerju se je zagozdil': 'jam',
    'pasirinktas skeneris nepalaiko dvipusio skenavimo. jei jūsų skeneris turi dvipusio skenavimo galimybę, pabandykite naudoti kitą tvarkyklę': 'noduplex',
    'pasirinktas skeneris nepalaiko padavimo mechanizmo. jei jūsų skeneris tikrai turi padavimo mechanizmą, pabandykite naudoti kitą tvarkyklę': 'nofeeder',
    'pasirinktas skeneris neprisijungęs': 'offline',
    'pasirinktas skeneris nerastas': 'notfound',
    'pogreška s upravljačkim programom za skener': 'driver',
    'poklopac skenera je otvoren': 'cover',
    'pokrov skenerja je odprt': 'cover',
    'pokrywa skanera jest otwarta': 'cover',
    'pracovní proces spadl': 'driver',
    'pracovný proces spadol': 'driver',
    'prekinuta je komunikacija sa uređajem za skeniranje': 'comm',
    'pri gonilniku skenerja je prišlo do napake': 'driver',
    'pri ovládači skenovania sa vyskytla chyba': 'driver',
    'proces roboczy uległ awarii': 'driver',
    'procesi i punës u ndërpre': 'driver',
    'procesul worker s-a oprit neașteptat': 'driver',
    'pārtrūka sakari ar skenēšanas ierīci': 'comm',
    'radni proces je prekinut': 'driver',
    "s'ha interromput la comunicació amb el dispositiu d'escaneig": 'comm',
    "s'ha produït un error": 'driver',
    "s'ha produït un error amb el controlador de l'escàner": 'driver',
    'sane - ajuri ei ole käytettävissä. asenna tarvittava osat:': 'nosane',
    'sane drajver nije dostupan. pobrinite se da instalirate potrebne pakete:': 'nosane',
    'sane drivrutin är inte tillgänglig. kontroller och installera nödvändigt paket:': 'nosane',
    'sane gonilnik ni na voljo. preverite namestitev potrebnega dodatka:': 'nosane',
    'sane sürücüsü kullanılamıyor. gerekli paketlerin kuruluduğundan emin olun:': 'nosane',
    'sane 驱动不可用。请确认已安装所需的软件包:': 'nosane',
    'sane 드라이버를 사용할 수 없습니다. 다음 패키지가 설치 되어 있는지 확인해 주십시오:': 'nosane',
    'sane-drivaren er ikkje tilgjengeleg. installer den nødvendige pakken:': 'nosane',
    'sane-driveren er ikke tilgængelig. kontroller at du har installeret den påkrævede pakke:': 'nosane',
    'saneドライバが利用不可能です。必要なパッケージをインストールしてください。:': 'nosane',
    'scanerul are o foaie mototolită': 'jam',
    'scanerul se inițializează': 'warming',
    'scanerul selectat este deconectat': 'offline',
    'scanerul selectat este ocupat': 'busy',
    'scanerul selectat nu a fost găsit': 'notfound',
    'scanerul selectat nu suportă alimentarea automată. dacă scanerul are totuși alimentare automată, atunci încercați alt driver': 'nofeeder',
    'scanerul selectat nu suportă funcția duplex. dacă scanerul are totuși duplex, atunci încercați alt driver': 'noduplex',
    'scanneren har papirstop': 'jam',
    'scanneren varmer op': 'warming',
    'scannerens cover er åbent': 'cover',
    'se interrumpió la comunicación con el dispositivo de escaneo': 'comm',
    'sem papel no alimentador': 'empty',
    'seçilen tarayıcı bulunamadı': 'notfound',
    'seçilen tarayıcı meşgul': 'busy',
    'seçilen tarayıcı çevrim dışı': 'offline',
    'seçilen tarayıcı, besleyici kullanmayı desteklemiyor. eğer tarayıcınızın bir besleyicisi varsa başka bir sürücü kullanmayı deneyin': 'nofeeder',
    'seçilen tarayıcı, çift taraflı kullanımı desteklemiyor. eğer tarayıcınız çift taraflı kullanımı destekliyorsa başka bir sürücü kullanmayı deneyin': 'noduplex',
    'si è verificato un errore con il driver di scansione': 'driver',
    'sin hojas en el alimentador': 'empty',
    'skaner się rozgrzewa': 'warming',
    'skaneri i zgjedhur nuk e mbështet dupleksin. nëse skaneri supozohet se e pranon dupleksin, provoni të përdorni një drejtues tjetër': 'noduplex',
    'skaneri i zgjedhur nuk e mbështet përdorimin e furnizuesit. nëse skaneri yt nuk ka një furnizues, provo të përdorësh një drejtues të ndryshëm': 'nofeeder',
    'skaneri i zgjedhur nuk mund të gjendej': 'notfound',
    'skaneri i zgjedhur është i zënë': 'busy',
    'skaneri i zgjedhur është jashtë linje': 'offline',
    'skaneri ka një bllokim letre': 'jam',
    'skaneri po ngrohet': 'warming',
    'skannardekselet er opent': 'cover',
    'skannaren har papirstopp': 'jam',
    'skannaren varmer opp': 'warming',
    'skanner soojeneb': 'warming',
    'skanneri draiveril ilmnes tõrge': 'driver',
    'skanneri kaas on avatud': 'cover',
    'skanneri lämpiää': 'warming',
    'skanneril on paberiummistus': 'jam',
    'skannerin kansi on auki': 'cover',
    'skannerissa on paperitukos': 'jam',
    'skannerlocket är öppet': 'cover',
    'skannern har papperstrassel': 'jam',
    'skannern värmer upp': 'warming',
    'skener sa zahrieva': 'warming',
    'skener se ogreva': 'warming',
    'skener se rozehřívá': 'warming',
    'skener se zagrijava': 'warming',
    'skenera vāks ir atvērts': 'cover',
    'skenerio tvarkyklės klaida': 'driver',
    'skeneris uzsilst': 'warming',
    'skenerī ir iesprūdis papīrs': 'jam',
    'sterownik sane nie jest dostępny. upewnij się, że zainstalowałeś wymagane pakiety:': 'nosane',
    'syöttölaite on tyjä': 'empty',
    'sööturis ei ole ühtegi lehte': 'empty',
    'tarayıcı sürücüsü ile ilgili bir hata oluştu': 'driver',
    'tarayıcı ısınıyor': 'warming',
    'tarayıcıda kağıt sıkışması var': 'jam',
    'tarayıcının kapağı açık': 'cover',
    'tarayıcıyla olan iletişim kesintiye uğradı': 'comm',
    'the sane driver is not available. make sure to install the required packages:': 'nosane',
    'the scanner has a paper jam': 'jam',
    'the scanner is warming up': 'warming',
    "the scanner's cover is open": 'cover',
    'the selected scanner could not be found': 'notfound',
    'the selected scanner does not support using a feeder. if your scanner does have a feeder, try using a different driver': 'nofeeder',
    'the selected scanner does not support using duplex. if your scanner is supposed to support duplex, try using a different driver': 'noduplex',
    'the selected scanner is busy': 'busy',
    'the selected scanner is offline': 'offline',
    'the worker process crashed': 'driver',
    'työprosessi kaatui': 'driver',
    'ukjend feil med skanne-drivaren': 'driver',
    "une erreur est survenue avec le pilote d'acquisition": 'driver',
    'uređaj nije odabran': 'notfound',
    'v podajalniku ni listov': 'empty',
    'v podavači nejsou žádné papíry': 'empty',
    'v podávači nie sú žiadne listy': 'empty',
    'v skeneri sa zasekol papier': 'jam',
    'valittu skanneri ei tue monipuoleisuutta. jos skannerisi luultavasti tukee sitä, kokeile toista ajuria': 'noduplex',
    'valittu skanneri ei tue syöttölaitetta. jos skannerissasi on syöttölaite, kokeile toista ajuria': 'nofeeder',
    'valittu skanneri on offline-tilassa': 'offline',
    'valittu skanneri on varattu': 'busy',
    'valittua skanneria ei löydy': 'notfound',
    'valitud skanner ei toeta dupleksi kasutamist. kui teie skanner peaks dupleksit toetama, proovige kasutada teist draiverit': 'noduplex',
    'valitud skanner ei toeta sööturi kasutamist. kui teie skanneril on söötur, proovige kasutada teist draiverit': 'nofeeder',
    'valitud skanner on hõivatud': 'busy',
    'valitud skännerit ei leitud': 'notfound',
    've skeneru je zmuchlaný papír': 'jam',
    'virhe skannausajurissa': 'driver',
    'vybraný skener je offline': 'offline',
    'vybraný skener je vypnutý': 'offline',
    'vybraný skener je zaneprázdnený': 'busy',
    'vybraný skener je zaneprázdněn': 'busy',
    'vybraný skener nebyl nalezen': 'notfound',
    'vybraný skener nepodporuje použitie podávača. ak skener obsahuje podávač, skúste použiť iný ovládač': 'nofeeder',
    'vybraný skener nepodporuje použití oboustranný mód. pokud by měl podporovat oboustranný mód, zkuste použít jiný ovladač': 'noduplex',
    'vybraný skener nepodporuje použití podavače. pokud váš skener má podavač, zkuste použít jiný ovladač': 'nofeeder',
    'vybraný skener neskenuje obojstranne. ak sa v parametroch skenera uvádza, že ho podporuje, potom treba nájsť iný ovládač': 'noduplex',
    'vybraný skener sa nenašiel': 'notfound',
    'víko skeneru je otevřené': 'cover',
    'w skanerze zaciął się papier': 'jam',
    'wybrany skaner jest w trybie offline': 'offline',
    'wybrany skaner jest zajęty': 'busy',
    'wybrany skaner nie obsługuje druku obustronnego. jeśli twój skaner powinien wspierać druk obustronny, spróbuj użyć innego sterownika': 'noduplex',
    'wybrany skaner nie obsługuje podajnika. jeżeli twój skaner ma podajnik, spróbuj użyć innego sterownika': 'nofeeder',
    'wystąpił błąd w sterowniku skanowania': 'driver',
    'yhteys skannauslaitteen kanssa keskeytyi': 'comm',
    'çalışma işlemi çöktü': 'driver',
    'ükski seade pole valitud': 'notfound',
    'įrenginys nepasirinktas': 'notfound',
    'δεν επιλέχθηκε συσκευή': 'notfound',
    'δεν υπάρχουν σελίδες στον τροφοδότη': 'empty',
    'εμπλοκή χαρτιού στον σαρωτή': 'jam',
    'ο επιλεγμένος σαρωτής βρίσκεται εκτός σύνδεσης (offline)': 'offline',
    'ο επιλεγμένος σαρωτής δεν μπόρεσε να βρεθεί': 'notfound',
    'ο επιλεγμένος σαρωτής δεν υποστηρίζει λειτουργία διπλής όψης. εάν ο σαρωτής πράγματι διαθέτει λειτουργία διπλής όψης, προτείνεται η χρήση διαφορετικού οδηγού': 'noduplex',
    'ο επιλεγμένος σαρωτής δεν υποστηρίζει χρήση τροφοδότη. εάν ο σαρωτής πράγματι διαθέτει τροφοδότη, προτείνεται η χρήση διαφορετικού οδηγού': 'nofeeder',
    'ο επιλεγμένος σαρωτής είναι απασχολημένος': 'busy',
    'ο οδηγός sane δεν είναι διαθέσιμος. βεβαιωθείτε ότι έχετε εγκαταστήσει τα απαιτούμενα πακέτα:': 'nosane',
    'προθέρμανση σαρωτή': 'warming',
    'σφάλμα του οδηγού σάρωσης': 'driver',
    'το κάλυμμα του σαρωτή είναι ανοικτό': 'cover',
    'в податчике нет листов': 'empty',
    'в сканере застряла бумага': 'jam',
    'вибраний сканер вимкнено': 'offline',
    'вибраний сканер зайнятий': 'busy',
    'вибраний сканер не підтримує автоматичну подачу паперу. якщо ваш сканер все ж має автоматичну подачу паперу, спробуйте вибрати інший драйвер': 'nofeeder',
    'вибраний сканер не підтримує двостороннє сканування. якщо ваш сканер все ж підтримує двостороннє сканування, спробуйте вибрати інший драйвер': 'noduplex',
    'выбранный сканер занят': 'busy',
    'выбранный сканер не поддерживает дуплекс. если ваш сканер поддерживает двустраничное сканирование, попробуйте использовать другой драйвер': 'noduplex',
    'выбранный сканер не поддерживает использование автоподатчика. если ваш сканер имеет апд, попробуйте другой драйвер': 'nofeeder',
    'выбранный сканер отключён': 'offline',
    'відкрито кришку сканеру': 'cover',
    'дошло је до грешке са дрajвером за скенер': 'driver',
    'драйвер sane не доступний. перевірте, чи встановлено такі пакети:': 'nosane',
    'драйвер sane недоступен. убедитесь, что необходимые пакеты установлены:': 'nosane',
    'драйверът sane не е наличен. уверете се, че сте инсталирали необходимите пакети:': 'nosane',
    'заглављен папир у скенеру': 'jam',
    'заседнала хартия в скенера': 'jam',
    'збій робочого процесу': 'driver',
    'зминання паперу у сканері': 'jam',
    'избраният скенер е недостъпен': 'offline',
    'избраният скенер не е достъпен': 'notfound',
    'избраният скенер не поддържа автоматично подаване на листове. ако вашият скенер има устройство за автоматично подаване на листове, опитайте различен драйвър': 'nofeeder',
    'избраният скенер не поддържа двустранно сканиране. ако тази функция би трябвало да се поддържа, използвайте различен драйвер': 'noduplex',
    'капакът на скенера не е затворен': 'cover',
    'комуникацията със сканиращото устройство беше прекъсната': 'comm',
    'крышка сканера открыта': 'cover',
    'не вибрано пристрій': 'notfound',
    'не выбран сканер': 'notfound',
    'не е избрано устройство': 'notfound',
    'не знайдено вибраний сканер': 'notfound',
    'не найден выбранный сканер': 'notfound',
    'нема листова у увлакачу': 'empty',
    'няма листoве в устройството за подаване': 'empty',
    'обмін інформацією зі сканером було перервано': 'comm',
    'одабрани скенер не подржава двострано скенирање. ако ваш скенер нема дуплексер, покушајте да инсталирате други драјвер': 'noduplex',
    'одабрани скенер не подржава коришћење увлакача. ако ваш скенер нема увлакач, покушајте да инсталирате други драјвер': 'nofeeder',
    'одабрани скенер није пронађен': 'notfound',
    'одабрани скенер није укључен': 'offline',
    'ошибка драйвера сканирования': 'driver',
    'подготовка на скенера': 'warming',
    'поклопац скенера је отворен': 'cover',
    'помилка драйвера сканування': 'driver',
    'проблем с драйвъра на скенера': 'driver',
    'прогрівання сканеру': 'warming',
    'работният процес се срина': 'driver',
    'рабочий процесс завершился сбоем': 'driver',
    'разогрев сканера': 'warming',
    'связь с устройством сканирования была прервана': 'comm',
    'скенер се загрева': 'warming',
    'у пристрої відсутній папір': 'empty',
    'уређај није одабран': 'notfound',
    'אין דפים במזין המסמכים': 'empty',
    'אירעה שגיאה במנהל ההתקן של הסורק': 'driver',
    'המכסה של הסורק פתוח': 'cover',
    'הסורק הנבחר עסוק': 'busy',
    'הסורק מתחמם': 'warming',
    'הסורק שנבחר איננו מקוון': 'offline',
    'הסורק שנבחר איננו תומך במזין מסמכים. אם לסורק יש מזין מסמכים, כדאי לנסות להשתמש במנהל התקן אחר': 'nofeeder',
    'הסורק שנבחר לא נמצא': 'notfound',
    'הסורק שנבחר לא תומך במצב דופלקס (סריקה דו־צדדית). אם הסורק שלך אמור לתמוך בדופלקס, כדאי לנסות להשתמש במנהל התקן אחר': 'noduplex',
    'התקשורת מול מכשיר הסריקה נקטעה': 'comm',
    'לא נבחר התקן': 'notfound',
    'מנהל התקן ה־sane לא זמין. נא לוודא שהתקנת את החבילות הנדרשות:': 'nosane',
    'נתקע דף בסורק': 'jam',
    'תהליך הרקע קרס': 'driver',
    'اسکنر انتخاب شده استفاده از اسکن دورو را پشتیبانی نمی\u200cکند. اگر اسکنر پشتیبانی از اسکن دورو را پیشنهاد می\u200cدهد، تلاش کنید از درایور دیگری استفاده کنید': 'noduplex',
    'اسکنر انتخاب شده استفاده از خوراک\u200cدهنده را پشتیبانی نمی\u200cکند. اگر اسکنر شما خوراک\u200cدهنده دارد تلاش کنید از درایور دیگری استفاده کنید': 'nofeeder',
    'اسکنر انتخاب شده یافت نشد': 'notfound',
    'اسکنر انتخابی خاموش است': 'offline',
    'اسکنر انتخابی مشغول است': 'busy',
    'اسکنر در حال گرم شدن است': 'warming',
    'الماسح الضوئي المحدد غير متصل': 'offline',
    'الماسح الضوئي المحدد لا يدعم استخدام الوضع المزدوج. إذا كان ماسحك الضوئي من المفترض أن يدعم الوضع المزدوج، حاول استخدام تعريف مختلف': 'noduplex',
    'الماسح الضوئي المحدد مشغول': 'busy',
    'الماسح الضوئي لديه ورق منحشر': 'jam',
    'الماسح الضوئي يقوم بالإحماء': 'warming',
    'تعذر العثور على الماسح الضوئي المحدد': 'notfound',
    'حدث خطأ مع تعريف المسح الضوئي': 'driver',
    'خطای در ارتباط با درایور اسکن رخ داد': 'driver',
    'درایور sane موجود نیست. مطمئن شوید بسته\u200cهای مورد نیاز را نصب کرده\u200cاید.:': 'nosane',
    'درب اسکنر باز است': 'cover',
    'غطاء الماسح الضوئي مفتوح': 'cover',
    'لا توجد صفحات في المغذي': 'empty',
    'لا يدعم الماسح الضوئي المحدد باستخدام علبة تغذية. إذا كان الماسح الضوئي الخاص يوجد به مغذي، حاول استخدام برنامج تشغيل مختلف': 'nofeeder',
    'لم يتم تحديد أي جهاز': 'notfound',
    'هیچ دستگاهی انتخاب نشده': 'notfound',
    'کاغذ داخل اسکنر گیر کرده است': 'jam',
    'کاغذی داخل خوراک\u200cدهنده نیست': 'empty',
    'कोई उपकरण चयनित नहीं।': 'notfound',
    'फीडर में कोई पेज नहीं है।': 'empty',
    'स्कैनिंग डिवाइस के साथ संचार बाधित हो गया।': 'comm',
    'स्कैनिंग ड्राइवर के साथ कोई त्रुटि उत्पन्न हुई': 'driver',
    'පරිලෝකකයෙහි උපක්\u200dරම ධාවකයෙහි දෝෂයක් පැනනැගිනි': 'driver',
    'スキャナドライバにエラーが発生しました': 'driver',
    'スキャナーがウォームアップ中です': 'warming',
    'スキャナーのカバーが開いています': 'cover',
    'スキャナーの紙詰まりです': 'jam',
    'デバイスが選択されていません': 'notfound',
    'フィーダーが空です': 'empty',
    '与扫描设备的通信中断': 'comm',
    '両面スキャンがこのスキャナではできません。スキャナに両面機能が備わっている場合は、別のドライバを試してみてください': 'noduplex',
    '尚未選擇裝置': 'notfound',
    '尝试扫描文件发生错误': 'driver',
    '工作进程崩溃': 'driver',
    '所选的扫描仪不支持使用输稿器。如果您的扫描仪确实有输稿器，请尝试使用不同的驱动程序': 'nofeeder',
    '所选的扫描仪不支持双面扫描。如果您的扫描仪支持双面扫描，请尝试使用不同的驱动程序': 'noduplex',
    '所選掃瞄器不支援使用送紙器。如果您的掃瞄器沒有送紙器，請試著使用不同的驅動程式': 'nofeeder',
    '所選的掃瞄器離線': 'offline',
    '扫描仪原稿盖已打开': 'cover',
    '扫描仪发生卡纸': 'jam',
    '扫描仪正在预热': 'warming',
    '找不到所選的掃瞄器': 'notfound',
    '掃瞄時發生錯誤': 'driver',
    '无法找到所选的扫描仪': 'notfound',
    '未选择设备': 'notfound',
    '沒有頁面在送紙器中': 'empty',
    '输稿器中未发现纸张': 'empty',
    '选定的扫描仪处于脱机状态': 'offline',
    '选定的扫描仪正在工作': 'busy',
    '選択されたスキャナはフィーダーを利用できません。もしスキャナにフィーダーがある場合は、別のドライバを試してみてください': 'nofeeder',
    '選択されたスキャナは使用中です': 'busy',
    '選択されたスキャナは接続されていません': 'offline',
    '選択されたスキャナーが見つかりません': 'notfound',
    '공급장치에 용지가 없습니다': 'empty',
    '선택된 스캐너가 사용 중 입니다': 'busy',
    '선택된 스캐너를 찾을 수 없습니다': 'notfound',
    '선택된 스캐너에서는 양면 기능을 사용할 수 없습니다. 만약 스캐너가 양면 기능을 지원한다면, 다른 드라이버를 선택 해 주세요': 'noduplex',
    '선택된 스캐너에서는 지급기를 사용할 수 없습니다. 만약 지급기가 있는 스캐너라면, 다른 드라이버를 선택 해 주세요': 'nofeeder',
    '선택된 스캐너의 연결이 끊겼습니다': 'offline',
    '선택된 장치가 없습니다': 'notfound',
    '스캐너 예열 중': 'warming',
    '스캐너 용지걸림': 'jam',
    '스캐너 커버 열림': 'cover',
    '스캔 장비와 연결이 끊어졌습니다': 'comm',
    '스캔 중 오류가 발생했습니다': 'driver',
    '작업 프로세스가 튕겼습니다': 'driver',
}




# ------------------------------------------------------------------------------------------
# Pages: the four looks (the phone's clean-up, on numpy), blank pages, the page as shown
# ------------------------------------------------------------------------------------------

DPI = 300
JPEG_QUALITY = 85


def _max3(a, r):
    """Maximum over a (2r+1)² neighbourhood."""
    p = np.pad(a, r, mode="edge")
    out = a.copy()
    h, w = a.shape
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            np.maximum(out, p[dy:dy + h, dx:dx + w], out=out)
    return out


def _mean3(a):
    p = np.pad(a, 1, mode="edge")
    h, w = a.shape
    return sum(p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)) / 9.0


def _background(lum):
    """The brightness the paper would have at each pixel without ink: the brightest value of
    blocks larger than a letter, widened and smoothed, spread back over every pixel. Floored at
    half the paper's usual brightness, so a photo on the page is not blown out to white."""
    h, w = lum.shape
    b = max(8, max(w, h) // 64)
    gh, gw = -(-h // b), -(-w // b)
    padded = np.pad(lum, ((0, gh * b - h), (0, gw * b - w)), mode="edge")
    grid = padded.reshape(gh, b, gw, b).max(axis=(1, 3)).astype(np.float32)
    grid = _mean3(_mean3(_max3(grid, 2)))
    paper = np.sort(grid, axis=None)[min(grid.size - 1, int(grid.size * 0.9))]
    grid = np.maximum(grid, max(40.0, paper * 0.5))
    return np.asarray(Image.fromarray(grid, mode="F").resize((w, h), Image.BILINEAR), dtype=np.float32)


def _levels(v):
    t = np.clip((v - 40.0) / 195.0, 0.0, 1.0)
    return np.clip((t * t * (3 - 2 * t) * 0.5 + t * 0.5) * 255.0, 0, 255).astype(np.uint8)


def apply_look(img, look):
    """clean: the paper turns white, shadows evened out, colours kept. grey: the same, in grey.
    bw: black ink on white. original: untouched."""
    if look not in ("clean", "grey", "bw"):
        return img
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    lum = (rgb[..., 0] * 77 + rgb[..., 1] * 150 + rgb[..., 2] * 29) / 256.0
    bg = _background(lum)
    if look == "clean":
        return Image.fromarray(_levels(rgb * (255.0 / bg)[..., None]), "RGB")
    flat = lum * 255.0 / bg
    if look == "grey":
        return Image.fromarray(_levels(flat), "L")
    flat = np.minimum(flat, 255.0)
    h, w = flat.shape
    r = max(6, max(w, h) // 80)
    integral = np.pad(flat.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(h) - r, 0, h)[:, None]; y1 = np.clip(np.arange(h) + r + 1, 0, h)[:, None]
    x0 = np.clip(np.arange(w) - r, 0, w)[None, :]; x1 = np.clip(np.arange(w) + r + 1, 0, w)[None, :]
    mean = (integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]) / ((y1 - y0) * (x1 - x0))
    return Image.fromarray(np.where((flat < mean * 0.86) | (flat < 90), 0, 255).astype(np.uint8), "L")


def is_blank(path):
    """A page with nothing on it (the back of a one-sided sheet in a two-sided scan): after
    evening out the paper, hardly any pixel is clearly darker than it. The edges are left out
    (shadows of the sheet), and single specks of dust do not count."""
    try:
        img = Image.open(path)
        img.draft("L", (img.width // 2, img.height // 2))
        img = img.convert("L")
        img.thumbnail((1200, 1200))
        lum = np.asarray(img, dtype=np.float32)
    except (OSError, ValueError):
        return False
    h, w = lum.shape
    my, mx = int(h * 0.04), int(w * 0.04)
    flat = (lum * 255.0 / _background(lum))[my:h - my, mx:w - mx]
    ink = _mean3(flat) < 165            # a speck of dust is averaged away, show-through is too pale
    # measured at this size: an empty sheet 0, a page number alone 50, a short letter 8000
    return int(ink.sum()) < 16


def open_upright(path, rotation=0, draft=None):
    img = Image.open(path)
    if draft:
        img.draft("RGB", draft)
    img = img.convert("RGB")
    if rotation % 360:
        img = img.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[rotation % 360])
    return img


def render_page(src, out, rotation, look):
    """The page as shown and as it goes into the PDF. An untouched scan is kept as it is."""
    if look == "original" and rotation % 360 == 0:
        if os.path.abspath(src) != os.path.abspath(out):
            shutil.copyfile(src, out)
        return
    img = apply_look(open_upright(src, rotation), look)
    img.save(out + ".tmp", "JPEG", quality=80 if look == "bw" else JPEG_QUALITY, dpi=(DPI, DPI))
    replace(out + ".tmp", out)


# Tesseract's glyphless font (tessdata/pdf.ttf, Apache 2.0): every character an empty glyph half
# an em wide, so any language's text can lie invisibly over the page.
_GLYPHLESS = base64.b64decode(
    "AAEAAAAKAIAAAwAgT1MvMlbeyJQAAAEoAAAAYGNtYXAACgA0AAABkAAAAB5nbHlmFSJBJAAAAbgAAAAYaGVhZAt48WUAAACsAAAANmhoZWEMAgQCAAAA5AAAACRobXR4BAAAAAAAAYgAAAAI"
    "bG9jYQAMAAAAAAGwAAAABm1heHAABAAFAAABCAAAACBuYW1l8usW2gAAAdAAAABLcG9zdAABAAEAAAIcAAAAIAABAAAAAQAAsJRxEF8PPPUEBwgAAAAAAM+a/G4AAAAA1MOn8gAAAAAEAAgAAAAA"
    "EAACAAAAAAAAAAEAAAgA//8AAAQAAAAAAAQAAAEAAAAAAAAAAAAAAAAAAAACAAEAAAACAAQAAQAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAwAAAZAABQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAUA"
    "AQABAAAAAAAAAAAAAAAAAAAAAAAAAAAAR09PRwBAAAAAAAAB//8AAAABAAGAAAAAAAAAAAAAAAAAAAABAAAAAAAABAAAAAAAAAIAAQAAAAAAFAADAAAAAAAUAAYACgAAAAAAAAAAAAAAAAAMAAAA"
    "AQAAAAAEAAgAAAMAADEhESEEAPwACAAAAAADACoAAAADAAAABQAWAAAAAQAAAAAABQALABYAAwABBAkABQAWAAAAVgBlAHIAcwBpAG8AbgAgADEALgAwVmVyc2lvbiAxLjAAAAEAAAAAAAAAAAAA"
    "AAAAAQAAAAAAAAAAAAAAAAAAAAA=")
_TO_UNICODE = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identify-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfrange
<0000> <FFFF> <0000>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


def page_inches(path, w, h):
    """The width in inches a page picture stands for: from its resolution when it says one that
    makes sense, otherwise as if its long side were A4's (or Letter's, for a page of that shape)."""
    try:
        dpi = float(Image.open(path).info.get("dpi", (0, 0))[0])
    except (OSError, ValueError, TypeError):
        dpi = 0
    if 70 <= dpi <= 1300 and max(w, h) / dpi <= 20:
        return w / dpi
    long_side = 11.0 if abs(min(w, h) / max(w, h) - 8.5 / 11) < 0.012 else 11.69
    return w * long_side / max(w, h)


def write_pdf(pages, out, title="", layers=None):
    """The document's PDF: each page picture as it is (the JPEG goes in untouched), and over it,
    invisible, the lines read on it (`layers`: per page, lines of words with their boxes in the
    picture's pixels), so the PDF can be searched and its text copied."""
    objs = []                  # bytes of each object, numbered from 1

    def add(body):
        objs.append(body if isinstance(body, bytes) else body.encode("latin-1"))
        return len(objs)

    def stream(data, extra=""):
        return f"<< {extra} /Length {len(data)} >>\nstream\n".encode("latin-1") + data + b"\nendstream"

    catalog, tree = add(b""), add(b"")
    info = add("<< /Title <FEFF" + title.encode("utf-16-be").hex().upper() + "> /Producer (Reader's Scanner) >>")
    font = None
    if layers and any(layers):
        file2 = add(stream(zlib.compress(_GLYPHLESS), f"/Filter /FlateDecode /Length1 {len(_GLYPHLESS)}"))
        descriptor = add(f"<< /Type /FontDescriptor /FontName /GlyphLessFont /FontFile2 {file2} 0 R /Ascent 1000 /CapHeight 1000 /Descent -1 /Flags 5 "
                         "/FontBBox [ 0 0 500 1000 ] /ItalicAngle 0 /StemV 80 >>")
        to_unicode = add(stream(_TO_UNICODE))
        gids = add(stream(zlib.compress(b"\x00\x01" * 65536), "/Filter /FlateDecode"))
        cid = add(f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /GlyphLessFont /CIDToGIDMap {gids} 0 R /DW 500 /FontDescriptor {descriptor} 0 R "
                  "/CIDSystemInfo << /Ordering (Identity) /Registry (Adobe) /Supplement 0 >> >>")
        font = add(f"<< /Type /Font /Subtype /Type0 /BaseFont /GlyphLessFont /DescendantFonts [ {cid} 0 R ] /Encoding /Identity-H /ToUnicode {to_unicode} 0 R >>")
    kids = []
    for i, path in enumerate(pages):
        img = Image.open(path)
        w, h = img.size
        if img.format == "JPEG" and img.mode in ("L", "RGB"):
            with open(path, "rb") as f:
                data = f.read()
            space = "/DeviceGray" if img.mode == "L" else "/DeviceRGB"
        else:                  # anything else becomes a JPEG on the way
            import io
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
            data, space = buf.getvalue(), "/DeviceRGB"
        k = page_inches(path, w, h) * 72.0 / w          # points per pixel
        pw, ph = w * k, h * k
        image = add(stream(data, f"/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {space} /BitsPerComponent 8 /Filter /DCTDecode"))
        ops = [f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q"]
        lines = (layers[i] if layers and i < len(layers) else None) or []
        if lines and font:
            ops.append("BT 3 Tr")
            for line in lines:
                top, bottom = min(wd[2] for wd in line), max(wd[4] for wd in line)
                size = max(3.0, (bottom - top) * k * 0.8)
                base = ph - (top + (bottom - top) * 0.8) * k
                for j, (text, left, _t, right, _b) in enumerate(line):
                    text += " "
                    units = len(text.encode("utf-16-be")) // 2
                    until = line[j + 1][1] if j + 1 < len(line) else right + size / k * 0.25
                    stretch = max(10.0, min(1000.0, 100.0 * max(1.0, until - left) * k / (units * 0.5 * size)))
                    ops.append(f"/F0 {size:.2f} Tf {stretch:.1f} Tz 1 0 0 1 {left * k:.2f} {base:.2f} Tm <{text.encode('utf-16-be').hex().upper()}> Tj")
            ops.append("ET")
        content = add(stream(zlib.compress("\n".join(ops).encode("latin-1")), "/Filter /FlateDecode"))
        fonts = f"/Font << /F0 {font} 0 R >> " if font else ""
        kids.append(add(f"<< /Type /Page /Parent {tree} 0 R /MediaBox [ 0 0 {pw:.2f} {ph:.2f} ] /Contents {content} 0 R "
                        f"/Resources << /XObject << /Im0 {image} 0 R >> {fonts}>> >>"))
    if not kids:
        return None
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    objs[tree - 1] = f"<< /Type /Pages /Count {len(kids)} /Kids [ {' '.join(f'{n} 0 R' for n in kids)} ] >>".encode()
    with open(out + ".tmp", "wb") as f:
        f.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for n, body in enumerate(objs, 1):
            offsets.append(f.tell())
            f.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
        xref = f.tell()
        f.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
        for o in offsets:
            f.write(f"{o:010d} 00000 n \n".encode())
        f.write(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    replace(out + ".tmp", out)
    return out


def plain_pdf(pages, out, title=""):
    """A PDF of the pages without text, when nothing can read them."""
    return write_pdf(pages, out, title)


def reading_copy(page, look, out):
    """The page as the reader wants it: grey, the paper evened out to white. Commas and dots get
    lost on a raw scan (measured: « TVA 8,1 % : 7,97 » read « TVA 81% :797 »); on this copy they
    are read. A page already cleaned is read as it is."""
    if look in ("clean", "grey", "bw"):
        return page
    apply_look(Image.open(page).convert("RGB"), "grey").save(out, "JPEG", quality=92, dpi=(DPI, DPI))
    return out


_pdfium_lock = threading.Lock()      # pdfium does one thing at a time


def pdf_pictures(pdf, out_dir, stem, dpi, first=1, last=None, quality=88):
    """The pages of a PDF as JPEG files in `out_dir`, in order (`first`…`last`, counted from 1).
    With pypdfium2 when it is installed (the Windows and macOS builds carry it), otherwise with
    poppler's pdftoppm."""
    os.makedirs(out_dir, exist_ok=True)
    try:
        import pypdfium2
    except ImportError:
        pypdfium2 = None
    if pypdfium2 is not None:
        files = []
        with _pdfium_lock:
            doc = pypdfium2.PdfDocument(pdf)
            try:
                for i in range(max(1, first) - 1, min(last or len(doc), len(doc))):
                    out = os.path.join(out_dir, f"{stem}-{i + 1:04d}.jpg")
                    doc[i].render(scale=dpi / 72.0).to_pil().convert("RGB").save(out, "JPEG", quality=quality, dpi=(dpi, dpi))
                    files.append(out)
            finally:
                doc.close()
        return files
    exe = shutil.which("pdftoppm")
    if not exe:
        raise RuntimeError(_("poppler-utils is needed to read a PDF"))
    for f in os.listdir(out_dir):
        if f.startswith(stem + "-"):
            remove(os.path.join(out_dir, f))
    cmd = [exe, "-r", str(dpi), "-jpeg", "-jpegopt", f"quality={quality}", "-f", str(max(1, first))] + (["-l", str(last)] if last else [])
    subprocess.run(cmd + [pdf, os.path.join(out_dir, stem)], capture_output=True, timeout=600, **quiet())
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith(stem + "-") and f.endswith(".jpg"))


def import_image(path, out):
    """Any picture as a page: upright (EXIF), JPEG."""
    from PIL import ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    dpi = img.info.get("dpi", (0, 0))[0] or max(72, round(max(img.size) / 11.69))
    img.save(out, "JPEG", quality=90, dpi=(dpi, dpi))


# ------------------------------------------------------------------------------------------
# Reading the text: Tesseract, on this computer. The system's models (the distribution's
# packages) read at once; the « best » ones, more accurate, are downloaded into the app's own
# folder — on request, or by themselves for a language the system does not have.
# ------------------------------------------------------------------------------------------

LANGS = ("eng", "fra", "deu", "ita", "spa", "por", "rus")
LANG_NAMES = {"eng": "English", "fra": "français", "deu": "Deutsch", "ita": "italiano", "spa": "español", "por": "português", "rus": "русский"}
MODEL_MB = {"eng": 15, "fra": 4, "deu": 9, "ita": 9, "spa": 14, "por": 8, "rus": 15}
BEST_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main/%s.traineddata"


def default_lang():
    return {"fr": "fra", "de": "deu", "it": "ita", "es": "spa", "pt": "por", "ru": "rus"}.get(_LANG, "eng")


class ReadError(Exception):
    pass


class Reader:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "tessdata")
        self._system = None
        self.downloading = {}          # language → percent
        self.prefer_best = True        # the most accurate model, fetched at the first reading in a language
        self.refused = {}              # language → when its download last failed

    @staticmethod
    def exe():
        """Tesseract: the one named by the environment, the one inside the app, the system's."""
        named = os.environ.get("READERS_SCANNER_TESSERACT")
        if named:
            return shutil.which(named)
        return bundled("tesseract", "tesseract.exe") or bundled("tesseract", "bin", "tesseract") or shutil.which("tesseract")

    @staticmethod
    def env():
        """The one inside the app is told where its own models are (orientation, English)."""
        if not os.environ.get("READERS_SCANNER_TESSERACT") and bundled("tesseract", "tessdata"):
            return dict(os.environ, TESSDATA_PREFIX=bundled("tesseract", "tessdata"))
        return dict(os.environ)

    def system(self):
        """(the system's tessdata folder, its languages)."""
        if self._system is None:
            folder, langs = None, []
            if self.exe():
                try:
                    out = subprocess.run([self.exe(), "--list-langs"], capture_output=True, text=True, timeout=20, env=self.env(), **quiet())
                    lines = (out.stdout + out.stderr).splitlines()
                    m = re.search(r'"([^"]+)"', lines[0]) if lines else None
                    folder = m.group(1) if m else None
                    langs = [l.strip() for l in lines[1:] if l.strip()]
                except (OSError, subprocess.SubprocessError):
                    pass
            self._system = (folder, langs)
        return self._system

    def has_best(self, lang):
        p = os.path.join(self.dir, lang + ".traineddata")
        return os.path.exists(p) and os.path.getsize(p) > 500_000

    def remove_best(self, lang):
        try:
            remove(os.path.join(self.dir, lang + ".traineddata"))
        except OSError:
            pass

    def _fetch(self, url, out, lang=None):
        r = requests.get(url, stream=True, timeout=(15, 60), headers={"User-Agent": f"{APP}-desktop/{VERSION}"})
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0) or MODEL_MB.get(lang, 10) * 1_000_000
        done = 0
        with open(out + ".part", "wb") as f:
            for chunk in r.iter_content(64 * 1024):
                f.write(chunk)
                done += len(chunk)
                if lang:
                    self.downloading[lang] = min(99, done * 100 // total)
        replace(out + ".part", out)

    def download(self, lang):
        """The best model of a language, into the app's folder. True when it is there."""
        if self.has_best(lang):
            return True
        if time.time() - self.refused.get(lang, 0) < 600:
            return False               # no network a moment ago: not at every document
        os.makedirs(self.dir, exist_ok=True)
        self.downloading[lang] = 0
        try:
            self._fetch(BEST_URL % lang, os.path.join(self.dir, lang + ".traineddata"), lang)
            return self.has_best(lang)
        except (OSError, requests.RequestException):
            self.refused[lang] = time.time()
            return False
        finally:
            self.downloading.pop(lang, None)

    def model_for(self, lang):
        """(tessdata folder or None for the system's, the reader's name)."""
        if self.prefer_best and (self.has_best(lang) or self.download(lang)):
            return self.dir, "tesseract-best"
        if lang in self.system()[1]:
            return None, "tesseract-fast"
        if self.has_best(lang) or self.download(lang):
            return self.dir, "tesseract-best"
        raise ReadError(_("no reading model for %1 — it could not be downloaded", LANG_NAMES.get(lang, lang)))

    def read(self, pages, lang, work, progress=None):
        """Reads the pictures (one per page). Returns (the text of each page, the lines of each
        page as lists of (word, left, top, right, bottom), the reader's name). `work`: a path
        to write beside."""
        if not self.exe():
            raise ReadError(_("Tesseract is not installed: the pages are kept without their text"))
        folder, read_by = self.model_for(lang)
        listing = work + ".list"
        with open(listing, "w", encoding="utf-8") as f:
            f.write("\n".join(pages) + "\n")
        cmd = [self.exe(), listing, work, "-l", lang, "--dpi", str(DPI)]
        if folder:
            cmd += ["--tessdata-dir", folder]
        cmd += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=1"]
        env = dict(self.env(), OMP_THREAD_LIMIT=str(max(1, min(4, os.cpu_count() or 1))))
        errors = []
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace", env=env, **quiet())
            for line in proc.stderr:
                m = re.match(r"Page (\d+)", line)
                if m and progress:
                    progress(min(len(pages), int(m.group(1))), len(pages))
                elif line.strip():
                    errors.append(line.strip())
            proc.wait()
        except OSError as e:
            raise ReadError(str(e))
        finally:
            try:
                remove(listing)
            except OSError:
                pass
        if proc.returncode != 0 or not os.path.exists(work + ".tsv"):
            raise ReadError(errors[-1] if errors else "tesseract")
        try:
            with open(work + ".txt", encoding="utf-8", errors="replace") as f:
                text = f.read().split("\f")
            remove(work + ".txt")
        except OSError:
            text = []
        text = [t.strip() for t in text[:len(pages)]]
        text += [""] * (len(pages) - len(text))
        layers = [[] for _p in pages]
        lines = {}
        with open(work + ".tsv", encoding="utf-8", errors="replace") as f:
            for row in f:
                c = row.rstrip("\n").split("\t", 11)
                if len(c) < 12 or c[0] != "5" or not c[11].strip():
                    continue
                try:
                    page, left, top, width, height = int(c[1]) - 1, int(c[6]), int(c[7]), int(c[8]), int(c[9])
                except ValueError:
                    continue
                if 0 <= page < len(pages):
                    key = (page, c[2], c[3], c[4])
                    if key not in lines:
                        lines[key] = []
                        layers[page].append(lines[key])
                    lines[key].append((c[11].strip(), left, top, left + width, top + height))
        remove(work + ".tsv")
        return text, layers, read_by


def upright_rotations(files, reader):
    """Sheets fed upside down or sideways: how far each page must be turned to read upright
    (Tesseract's orientation detection, on a few pages at a time). 0 when it is not sure, when
    the page has too little text, or when the orientation model is not there."""
    exe = reader.exe()
    if not exe or "osd" not in reader.system()[1]:
        return {}

    def one(path):
        try:
            out = subprocess.run([exe, path, "-", "--psm", "0", "-l", "osd", "--dpi", str(DPI)], capture_output=True, text=True, errors="replace", timeout=60,
                                 env=reader.env(), **quiet())
            turn = re.search(r"Rotate: (\d+)", out.stdout)
            sure = re.search(r"Orientation confidence: ([\d.]+)", out.stdout)
            if turn and sure and float(sure.group(1)) >= 2.5 and int(turn.group(1)) in (90, 180, 270):
                return int(turn.group(1))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return 0

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, min(4, os.cpu_count() or 1))) as pool:
        return {f: r for f, r in zip(files, pool.map(one, files)) if r}


class ReadQueue:
    """Reads filed documents one at a time, off the UI thread: makes the pages as shown, reads
    them, names the document after its first words. Documents still waiting when the app was
    closed are taken up again at the next start."""

    def __init__(self, store, reader, on_done=None, on_progress=None):
        self.store, self.reader = store, reader
        self.on_done, self.on_progress = on_done, on_progress
        self.q = queue.Queue()
        self.working = {}              # document → "2/5"
        self.errors = {}               # document → why it could not be read
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        for doc_id in store.pending():
            self.q.put(doc_id)

    def enqueue(self, doc_id):
        self.q.put(doc_id)

    def idle(self):
        return self.q.empty() and not self.working

    def _say(self, doc_id, text):
        if text is None:
            self.working.pop(doc_id, None)
        else:
            self.working[doc_id] = text
        if self.on_progress:
            self.on_progress()

    def _run(self):
        while True:
            doc_id = self.q.get()
            doc = self.store.get(doc_id)
            if doc is None or doc.get("ocr") != PENDING or doc.get("remote"):
                continue
            try:
                self._read(doc)
            except Exception as e:     # a page that cannot be opened, a disk that is full…
                self.errors[doc_id] = str(e)
                self.store.ocr_failed(doc_id, doc.get("rev", 0))
            self._say(doc_id, None)
            if self.on_done:
                self.on_done(doc_id)

    def _read(self, doc):
        doc_id, rev = doc["id"], doc.get("rev", 0)
        self._say(doc_id, "")
        pages = []
        for p in doc["pages"]:
            out = self.store.page_file(doc_id, p["id"])
            if not os.path.exists(out):
                render_page(self.store.src_file(doc_id, p["id"]), out, p.get("rotation", 0), p.get("look", "original"))
            pages.append(out)
        base = os.path.join(self.store.dir(doc_id), "ocr-out")
        copies = []
        try:
            for i, (p, f) in enumerate(zip(doc["pages"], pages)):
                copies.append(reading_copy(f, p.get("look", "original"), f"{base}-{i}.jpg"))
            text, layers, read_by = self.reader.read(copies, doc.get("lang", "eng"), base, lambda i, n: self._say(doc_id, f"{i}/{n}"))
            self.errors.pop(doc_id, None)
            named = doc if doc.get("named") else dict(doc, name=first_words(next((t for t in text if t.strip()), "")) or doc.get("name"))
            if not self.store.ocr_done(doc_id, rev, text, write_pdf(pages, base + ".pdf", title_of(named), layers), read_by):
                self.q.put(doc_id)     # its pages changed meanwhile: read again
        except ReadError as e:
            self.errors[doc_id] = str(e)
            self.store.ocr_failed(doc_id, rev, plain_pdf(pages, base + ".pdf", title_of(doc)))
        finally:
            for c in copies:
                if c not in pages:
                    try:
                        remove(c)
                    except OSError:
                        pass


# ------------------------------------------------------------------------------------------
# The scanner: NAPS2 does the acquisition (naps2.com, a separate program), through its
# console. It gets a settings folder of its own (NAPS2_TEST_DATA) holding one profile that
# names the device: a scan then starts at once, without NAPS2 looking for scanners again
# (10 s with SANE), and the user's own NAPS2 profiles are never touched.
# ------------------------------------------------------------------------------------------

NAPS2_URL = "https://www.naps2.com/download"
SOURCES = ("auto", "glass", "feeder", "duplex")
_ERRORS = (   # what NAPS2 says in English → our word for it; NAPS2_WORDS has the other languages
    ("No pages are in the feeder", "empty"), ("does not support using a feeder", "nofeeder"), ("does not support using duplex", "noduplex"),
    ("could not be found", "notfound"), ("scanner is offline", "offline"), ("scanner is busy", "busy"), ("cover is open", "cover"),
    ("paper jam", "jam"), ("warming up", "warming"), ("was interrupted", "comm"), ("SANE driver is not available", "nosane"),
    ("No device was specified", "notfound"), ("error occurred with the scanning driver", "driver"), ("unexpected error", "driver"),
    ("worker process crashed", "driver"),
)


def error_of(line):
    """Our word for what NAPS2 said on this line, or None. English where we can ask for it
    (Linux, macOS); on Windows NAPS2 speaks the system's language."""
    low = re.sub(r"\s+", " ", line).strip().lower()
    for needle, code in _ERRORS:
        if needle.lower() in low:
            return code
    if len(low) > 12:
        for sentence, code in NAPS2_WORDS.items():
            if sentence in low or (len(low) > 20 and sentence.startswith(low.rstrip(".。"))):
                return code
    return None


def error_in(raw):
    """(our word, NAPS2's words) for a line as NAPS2 wrote it, in bytes: on Windows nobody says
    which code page a program without a console writes in, so the likely ones are tried until
    the sentence is one NAPS2 has."""
    pages = ["utf-8"] + (["oem", "mbcs"] if sys.platform == "win32" else []) + ["cp850", "cp1252", "cp866", "cp1251", "cp852", "cp1250", "cp437",
                                                                                 "cp932", "cp936", "cp949", "cp950", "cp1253", "cp1254", "cp1255", "cp1256", "cp874"]
    for page in pages:
        try:
            line = raw.decode(page)
        except (UnicodeDecodeError, LookupError):
            continue
        code = error_of(line)
        if code:
            return code, line.strip()
    return None, ""


def error_text(code, detail=""):
    return {
        "empty": _("the feeder is empty"), "nofeeder": _("this scanner has no feeder"), "noduplex": _("this scanner cannot scan both sides"),
        "notfound": _("the scanner is not answering — is it switched on?"), "offline": _("the scanner is not answering — is it switched on?"),
        "busy": _("the scanner is busy"), "cover": _("the scanner's cover is open"), "jam": _("paper jam in the scanner"),
        "warming": _("the scanner is warming up — try again in a moment"), "comm": _("the connection to the scanner was interrupted"),
        "nosane": _("SANE is not installed (the scanner drivers)"), "cancelled": _("scan cancelled"),
        "nodevice": _("no scanner found — is it switched on?"), "nonaps2": _("no scanner found"),
        "multipick": _("two sheets went in together"),
    }.get(code) or (detail or _("the scan did not work"))


def _model_key(name):
    """The same scanner reached by several drivers gets the same key."""
    n = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", name.lower().replace("_", " "))
    n = re.sub(r"\b(hewlett[- ]?packard|hp|canon|epson|brother|fujitsu|ricoh|samsung|xerox|kodak|lexmark|kyocera)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n) or re.sub(r"[^a-z0-9]", "", name.lower())


# the scanner asked directly first (parts/19_escl.py), then NAPS2's ways to it: the driverless
# ones, which work without the maker's software. sane's own « escl » comes last of all: with a
# stack in the feeder it handed over one page (HP ScanJet Pro 4500 fn1, 2026-09-27).
_BACKEND_ORDER = ("direct", "airscan")
_BACKEND_LAST = ("escl",)


def backend_rank(backend):
    if backend in _BACKEND_ORDER:
        return _BACKEND_ORDER.index(backend)
    return len(_BACKEND_ORDER) + (2 if backend in _BACKEND_LAST else 1)


def link_of(way):
    """"usb" or "net": how this way reaches the scanner."""
    if way.get("link"):
        return way["link"]
    words = f"{way.get('id') or ''} {way.get('name') or ''}".lower()
    return "usb" if "(usb)" in words or "/usb/" in words or ":usb:" in words or "//localhost" in words or "libusb" in words else "net"


def way_order(way):
    """The app chooses, nobody is asked: a way that failed twice running goes last; then the
    better driver; then, of two ways by the same driver, the cable before the network."""
    return (way.get("misses", 0) >= 2, backend_rank(way["backend"]), link_of(way) != "usb")


class Naps2:
    def __init__(self, data_dir):
        self.data = os.path.join(data_dir, "naps2")
        self.cmd = self.find()
        self.version = self._version() if self.cmd else None
        if self.version is None:
            self.cmd = None            # there, but it does not run: as good as absent
        self.proc = None
        self._cancelled = False
        self.heard = []                # what NAPS2 wrote during the last scans, for whoever must understand one
        self.alive = 0                 # when the scanner last answered
        self.direct = None             # the scanner being asked directly, while it scans
        self.asked_all = False

    @staticmethod
    def find():
        env = os.environ.get("READERS_SCANNER_NAPS2")
        if env:
            return shlex.split(env)
        if sys.platform == "win32":
            for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles(x86)"),
                         os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs"), os.environ.get("LOCALAPPDATA")):
                p = os.path.join(base or "", "NAPS2", "NAPS2.Console.exe")
                if base and os.path.exists(p):
                    return [p]
            exe = shutil.which("NAPS2.Console.exe") or shutil.which("naps2.console")
            return [exe] if exe else None
        if sys.platform == "darwin":
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                p = os.path.join(base, "NAPS2.app", "Contents", "MacOS", "NAPS2")
                if os.path.exists(p):
                    return [p, "console"]
            return None
        exe = shutil.which("naps2")
        if exe:
            return [exe, "console"]
        if shutil.which("flatpak"):
            try:
                if subprocess.run(["flatpak", "info", "com.naps2.Naps2"], capture_output=True, timeout=10, **quiet()).returncode == 0:
                    return ["flatpak", "run", "--command=naps2", "com.naps2.Naps2", "console"]
            except (OSError, subprocess.SubprocessError):
                pass
        return None

    @property
    def flatpak(self):
        return bool(self.cmd) and self.cmd[0] == "flatpak"

    @property
    def drivers(self):
        """NAPS2's drivers for this desktop, the usual one first."""
        named = os.environ.get("READERS_SCANNER_DRIVER")
        if named:
            return tuple(named.split(","))
        return ("wia", "twain") if sys.platform == "win32" else ("apple", "escl") if sys.platform == "darwin" else ("sane",)

    @property
    def driver(self):
        return self.drivers[0]

    def _env(self):
        env = dict(os.environ, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8", LANGUAGE="en")
        if not self.flatpak:
            os.makedirs(self.data, exist_ok=True)
            env["NAPS2_TEST_DATA"] = self.data
        return env

    def _version(self):
        try:
            out = subprocess.run(self.cmd + ["--help"], capture_output=True, timeout=60, env=self._env(), **quiet())
            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", said(out.stdout + out.stderr).strip().splitlines()[0])
            return m.group(1) if m else "?"
        except (OSError, subprocess.SubprocessError, IndexError):
            return None

    def devices(self, every=True):
        """Every way to every scanner: {"id" (None when only NAPS2 knows it), "name", "backend",
        "key"}, and "url" for a scanner that can be asked directly. Those are found in a moment;
        NAPS2's ways take ten seconds and more: with `every` false they are only looked for when
        no scanner answers by itself."""
        named = os.environ.get("READERS_SCANNER_DIRECT")
        if named is not None:              # the tests' scanners, and no others
            direct = [w for w in (escl_probe(u.strip()) for u in named.split(",") if u.strip()) if w]
        else:
            direct = escl_find()
        direct.sort(key=way_order)
        self.asked_all = not (direct and not every) and bool(self.cmd)      # were NAPS2's ways looked for too?
        if not self.asked_all:
            return direct
        found = self._devices()
        if named is None:                  # sane's escl names the address of a network scanner: it can be asked directly too
            known = {d["url"] for d in direct}
            for d in found:
                url = (d.get("id") or "")[5:] if (d.get("id") or "").startswith("escl:http") else None
                if url and url.rstrip("/") not in known and "localhost" not in url:
                    way = escl_probe(url.rstrip("/"))
                    if way and way["uuid"] not in {x["uuid"] for x in direct if x["uuid"]}:
                        direct.append(way)
                        known.add(way["url"])
        return sorted(direct + found, key=way_order)

    def _devices(self):
        found, asked = [], False
        scanimage = os.environ.get("READERS_SCANNER_SCANIMAGE") or shutil.which("scanimage")
        if self.driver == "sane" and scanimage and not self.flatpak:
            try:
                out = subprocess.run(shlex.split(scanimage) + ["-f", "%d\t%v\t%m\t%t%n"], capture_output=True, text=True, errors="replace", timeout=60, **quiet())
                asked = out.returncode == 0        # SANE answered: NAPS2, which asks SANE too, would find no more
                for line in out.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 3 and parts[0]:
                        backend = parts[0].split(":")[0]
                        model = parts[2].replace("_", " ").strip()
                        vendor = parts[1].strip()
                        initials = "".join(t[0] for t in re.split(r"[\s-]+", vendor) if t).lower()
                        known = vendor in ("eSCL", "WSD", "") or model.lower().startswith((vendor.lower() + " ", initials + " "))
                        name = model if known else f"{vendor} {model}"
                        found.append({"id": parts[0], "name": name, "backend": backend, "key": _model_key(parts[2])})
            except (OSError, subprocess.SubprocessError):
                pass
        for driver in self.drivers if self.cmd and not found and not asked else ():
            try:
                try:                   # the second driver is a last resort: it is not waited for a whole minute
                    listed = subprocess.run(self.cmd + ["--listdevices", "--driver", driver], capture_output=True, env=self._env(),
                                            timeout=90 if driver == self.driver else 25, **quiet()).stdout
                except subprocess.TimeoutExpired as late:
                    listed = late.stdout or b""
                for line in said(listed).splitlines():
                    line = line.strip()
                    if not line or error_of(line) or any(w in line for w in ("not available", "could not", "error")):
                        continue
                    m = re.match(r"^(.*\S)\s+\(([^()]+)\)$", line)
                    inner = m.group(2) if m else ""
                    found.append({"id": inner if inner.startswith("escl:") else None, "name": line, "driver": driver,
                                  "backend": (inner.split(":")[0] if driver == "sane" else "") or driver, "key": _model_key(m.group(1) if m else line)})
            except (OSError, subprocess.SubprocessError):
                pass
            if found:
                break                  # the usual driver sees it: the others are not asked
        return sorted(found, key=way_order)

    def _profile(self, device, source, pagesize, deskew):
        os.makedirs(self.data, exist_ok=True)
        x = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ArrayOfScanProfile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <ScanProfile>
    <Version>2</Version>
    <Device><ID>{x(device["id"])}</ID><Name>{x(device["name"])}</Name></Device>
    <DriverName>{device.get("driver") or self.driver}</DriverName>
    <DisplayName>readers-scanner</DisplayName>
    <IsDefault>true</IsDefault>
    <BitDepth>C24Bit</BitDepth>
    <PageSize>{pagesize}</PageSize>
    <Resolution>Dpi{DPI}</Resolution>
    <PaperSource>{source.capitalize()}</PaperSource>
    <AutoDeskew>{"true" if deskew else "false"}</AutoDeskew>
    <Quality>{JPEG_QUALITY}</Quality>
  </ScanProfile>
</ArrayOfScanProfile>
"""
        with open(os.path.join(self.data, "profiles.xml"), "w", encoding="utf-8") as f:
            f.write(xml)

    def scan(self, device, source, pagesize, out_dir, on_page=None):
        """One scan from one source. Returns (page files, error code or None, NAPS2's words)."""
        if device.get("url"):
            self._cancelled = False
            self.direct = Escl(device["url"])
            try:
                self.heard = self.heard[-200:] + [f"--- {source} · direct · {datetime.now():%H:%M:%S}"]
                files, code, words = self.direct.scan(source, pagesize, out_dir, on_page)
                self.heard.append(f"{len(files)} page(s) {code or ''} {words}".strip())
                return ([], "cancelled", "") if self._cancelled else (files, code, words)
            finally:
                self.direct = None
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(out_dir):
            remove(os.path.join(out_dir, f))
        deskew = source != "glass"       # a feeder pulls sheets askew; on the glass, leave the page as laid
        out = os.path.join(out_dir, "p$(nnnn).jpg")
        if device.get("id") and not self.flatpak:
            self._profile(device, source, pagesize, deskew)
            cmd = self.cmd + ["-p", "readers-scanner"]
        else:
            shown = device["name"] if (device.get("driver") or self.driver) != "sane" else re.sub(r"\s+\([^()]*\)$", "", device["name"])
            cmd = self.cmd + ["--noprofile", "--driver", device.get("driver") or self.driver, "--device", shown,
                              "--source", source, "--dpi", str(DPI), "--bitdepth", "color", "--pagesize", pagesize.lower()] + (["--deskew"] if deskew else [])
        cmd += ["-o", out, "--jpegquality", str(JPEG_QUALITY), "-f", "-v"]
        code, words = None, ""
        self._cancelled = False
        try:
            # a group of its own: NAPS2 scans through a helper process, and « cancel » must reach both
            own = {"creationflags": 0x08000000 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self._env(), **own)
            self.heard = self.heard[-200:] + [f"--- {source} · {device.get('backend') or device.get('driver')} · {datetime.now():%H:%M:%S}"]
            for raw in self.proc.stdout:
                line = said(raw).strip()
                line and self.heard.append(line)
                m = re.match(r"Scanned page (\d+)", line)
                if m and on_page:
                    on_page(int(m.group(1)))
                if code is None:
                    code, words = error_in(raw)
            self.proc.wait()
        except OSError as e:
            return [], "driver", str(e)
        finally:
            self.proc = None
        if self._cancelled:
            self._cancelled = False
            return [], "cancelled", ""
        files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.lower().endswith(".jpg"))
        if files:
            return files, None, ""
        return [], code or "unknown", words

    def cancel(self):
        direct = self.direct
        if direct is not None:
            self._cancelled = True
            direct.cancel()
        p = self.proc
        if p is not None:
            self._cancelled = True
            try:
                if sys.platform == "win32":    # no signal to send there: NAPS2 and its helper are ended
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=20, **quiet())
                else:                          # asked first, then told, the helper with it
                    group = os.getpgid(p.pid)
                    os.killpg(group, signal.SIGINT)

                    def insist(how):
                        try:
                            p.poll() is None and os.killpg(group, how)
                        except OSError:
                            pass
                    threading.Timer(1.5, insist, (signal.SIGTERM,)).start()
                    threading.Timer(4, insist, (signal.SIGKILL,)).start()
            except (OSError, subprocess.SubprocessError):
                pass


def page_size_of(cfg):
    fmt = cfg.get("format", "auto")
    if fmt == "auto":
        country = (os.environ.get("LC_ALL") or os.environ.get("LC_PAPER") or os.environ.get("LANG") or "").split(".")[0][-2:].upper()
        if not country.isalpha() or len(country) != 2:
            country = system_locale()[-2:].upper()
        return "Letter" if country in ("US", "CA", "MX", "PH", "CL", "CO") else "A4"
    return "Letter" if fmt == "letter" else "A4"


def scan_pages(naps2, cfg, source, out_dir, on_page=None, on_state=None):
    """A scan with the smart defaults: « automatic » takes the feeder when it holds paper and the
    glass otherwise; when the scanner does not answer on one driver, the next one is tried, and
    the scanners are looked for again once (an address may have changed). Returns a dict:
    files, source (the one used), error (code) and detail, blank (pages left out), device."""
    routes = list((cfg.get("device") or {}).get("routes") or [])
    if not naps2.cmd:
        routes = [r for r in routes if r.get("url")]
    searched = 0                       # 1: the scanners that answer by themselves were looked for; 2: NAPS2's too
    if not routes:
        on_state and on_state("searching")
        found = naps2.devices(every=False)
        routes = pick_routes(found, None)
        searched = 2 if naps2.asked_all or not naps2.cmd else 1
        if not routes:
            return {"files": [], "error": "nodevice" if naps2.cmd else "nonaps2", "detail": ""}
    key = routes[0]["key"]
    pagesize = page_size_of(cfg)
    last = ("unknown", "")
    missed = []                        # the ways that did not answer during this scan
    for src in (("feeder", "glass") if source == "auto" else (source,)):
        tries = list(routes)
        while tries:
            route = tries.pop(0)
            on_state and on_state(src)
            for patience in range(5):      # just after a scan the scanner may still be busy: a moment, not an error
                files, err, said = naps2.scan(route, src, pagesize, out_dir, on_page)
                if files or err in ("empty", "busy", "warming", "nofeeder", "noduplex", "cover", "jam", "multipick"):
                    naps2.alive = time.time()
                # « offline » from a scanner that answered a minute ago is the same moment of absence
                # (asked directly, a scanner that does not answer is not there: the next way at once)
                moment = err in ("busy", "warming") or (err in ("offline", "comm") and patience == 0 and time.time() - naps2.alive < 90 and not route.get("url"))
                if not moment or patience == 4:
                    break
                on_state and on_state("waiting")
                time.sleep(2.5)
            if files:
                blank = []
                if src in ("feeder", "duplex"):      # the backs of one-sided sheets, a separator sheet
                    blank = [f for f in files if is_blank(f)]
                    if len(blank) == len(files):
                        blank = []             # a stack of empty sheets is what was asked for
                # the order of preference stays (the driverless airscan first: on the scanner this was
                # tried on, sane's own escl gave one page of a stack); a way that failed twice running
                # goes behind the others
                for r in routes:
                    r["misses"] = 0 if r is route else r.get("misses", 0) + (1 if r in missed else 0)
                routes = sorted(routes, key=way_order)
                return {"files": [f for f in files if f not in blank], "blank": blank, "source": src, "error": None,
                        "device": {"key": key, "name": route["name"], "routes": routes}}
            last = (err, said)
            if err in ("notfound", "offline", "comm", "driver", "unknown") and route not in missed:
                missed.append(route)
            if err == "cancelled":
                return {"files": [], "error": err, "detail": ""}
            if err in ("empty", "nofeeder", "noduplex", "unknown") and src != "glass":
                break                          # nothing in the feeder: the glass, when automatic
            if err in ("notfound", "offline", "comm", "driver", "unknown"):
                while not tries and searched < 2:
                    # looked for again, once: first those that answer by themselves (a moment), then every way
                    on_state and on_state("searching")
                    again = pick_routes(naps2.devices(every=searched == 1), key)
                    searched = 2 if naps2.asked_all or not naps2.cmd else searched + 1
                    tries = [r for r in again if r.get("id") not in {x.get("id") for x in routes} or r.get("id") is None] if again else []
                    for r in again:        # what is known of a way is kept
                        r["misses"] = next((x.get("misses", 0) for x in routes if x.get("id") == r.get("id") and r.get("id")), 0)
                    # every way looked for: what is not found any more is forgotten; after the quick
                    # look, NAPS2's ways, which it did not ask, stay
                    kept = [x for x in routes if not x.get("url") and x.get("id") not in {r.get("id") for r in again}] if searched == 1 else []
                    routes = (again + kept) if again else routes
                continue
            return {"files": [], "error": err, "detail": said, "source": src}
        else:
            if source == "auto" and src == "feeder" and last[0] in ("notfound", "offline", "comm", "driver"):
                break                          # nobody answers: the glass would not either
    return {"files": [], "error": last[0], "detail": last[1]}


def pick_routes(devices, key):
    """The ways to one scanner: the one asked for, or the first found."""
    if not devices:
        return []
    key = key if key and any(d["key"] == key for d in devices) else devices[0]["key"]
    return [d for d in devices if d["key"] == key]
# ------------------------------------------------------------------------------------------
# Scanners asked directly. Most scanners sold since about 2015 speak eSCL (« AirScan »,
# « Mopria »): HTTP and a little XML, over the network, or over USB through ipp-usb on Linux.
# Nothing to install, no driver; the scanner says itself whether its feeder is loaded, hands
# its pages over one by one, and names what goes wrong by a code instead of a sentence.
# ------------------------------------------------------------------------------------------

ESCL_NS = {"scan": "http://schemas.hp.com/imaging/escl/2011/05/03", "pwg": "http://www.pwg.org/schemas/2010/12/sm"}
_ADF_STATES = {   # what the scanner says of its feeder → our word for it
    "ScannerAdfEmpty": "empty", "ScannerAdfJam": "jam", "ScannerAdfMispick": "jam", "ScannerAdfMultipickDetected": "multipick",
    "ScannerAdfDoorOpen": "cover", "ScannerAdfHatchOpen": "cover", "ScannerAdfInputTrayFailed": "jam", "ScannerAdfInputTrayOverloaded": "jam",
    "ScannerAdfDuplexPageTooShort": "jam", "ScannerAdfDuplexPageTooLong": "jam",
}


class EsclError(Exception):
    def __init__(self, code, words=""):
        super().__init__(words or code)
        self.code, self.words = code, words


class Escl:
    """One scanner at one address ("http://192.168.1.120:8080", "http://localhost:60001")."""

    def __init__(self, url, timeout=8):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self.http.verify = False           # scanners sign their own certificates
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        self.http.headers["User-Agent"] = f"{APP}-desktop/{VERSION}"
        self.job = None
        self._cancelled = False
        self._caps = None

    # ---- what it is and how it is --------------------------------------------------------

    def _get(self, path, timeout=None):
        try:
            r = self.http.get(self.url + path, timeout=(min(3, timeout or self.timeout), timeout or self.timeout))
        except requests.RequestException as e:
            raise EsclError("offline", str(e))
        if r.status_code == 503:
            raise EsclError("busy", "503")
        if r.status_code != 200:
            raise EsclError("offline", f"{path}: HTTP {r.status_code}")
        return r

    @staticmethod
    def _xml(text):
        try:
            return ET.fromstring(text)
        except ET.ParseError as e:
            raise EsclError("driver", str(e))

    def caps(self):
        """{"name", "uuid", "version", "glass": {...} or None, "feeder": {...} or None}; a source:
        {"width", "height" (300ths of an inch), "dpi": [...], "modes": [...], "formats": [...], "duplex": bool}."""
        if self._caps is None:
            root = self._xml(self._get("/eSCL/ScannerCapabilities").content)

            def text(node, path):
                found = node.find(path, ESCL_NS) if node is not None else None
                return (found.text or "").strip() if found is not None else ""

            def source(caps, duplex=False):
                if caps is None:
                    return None
                dpi = sorted({int(x.text) for x in caps.iterfind(".//scan:DiscreteResolution/scan:XResolution", ESCL_NS) if (x.text or "").strip().isdigit()})
                ranges = caps.find(".//scan:ResolutionRange/scan:XResolutionRange", ESCL_NS)
                if not dpi and ranges is not None:
                    low, high = int(text(ranges, "scan:Min") or 75), int(text(ranges, "scan:Max") or 600)
                    dpi = [d for d in (75, 100, 150, 200, 300, 400, 600, 1200) if low <= d <= high]
                return {"width": int(text(caps, "scan:MaxWidth") or 2550), "height": int(text(caps, "scan:MaxHeight") or 3508), "dpi": dpi,
                        "modes": sorted({(x.text or "").strip() for x in caps.iterfind(".//scan:ColorMode", ESCL_NS)}),
                        "formats": sorted({(x.text or "").strip() for x in caps.iterfind(".//pwg:DocumentFormat", ESCL_NS)}
                                          | {(x.text or "").strip() for x in caps.iterfind(".//scan:DocumentFormatExt", ESCL_NS)}),
                        "duplex": duplex}

            adf = root.find("scan:Adf", ESCL_NS)
            two = adf.find("scan:AdfDuplexInputCaps", ESCL_NS) if adf is not None else None
            one = adf.find("scan:AdfSimplexInputCaps", ESCL_NS) if adf is not None else None
            options = {(x.text or "").strip() for x in adf.iterfind(".//scan:AdfOption", ESCL_NS)} if adf is not None else set()
            self._caps = {"name": text(root, "pwg:MakeAndModel") or "scanner", "uuid": text(root, "scan:UUID"), "version": text(root, "pwg:Version") or "2.0",
                          "glass": source(root.find("scan:Platen/scan:PlatenInputCaps", ESCL_NS)),
                          "feeder": source(one if one is not None else two, duplex=two is not None or "Duplex" in options),
                          "both": source(two, duplex=True), "knows_if_loaded": "DetectPaperLoaded" in options}
        return self._caps

    def status(self):
        """(state: "Idle", "Processing", "Stopped"…, feeder: "ScannerAdfLoaded", "ScannerAdfEmpty"… or "")."""
        root = self._xml(self._get("/eSCL/ScannerStatus").content)
        state, adf = root.find("pwg:State", ESCL_NS), root.find("scan:AdfState", ESCL_NS)
        return ((state.text or "").strip() if state is not None else "", (adf.text or "").strip() if adf is not None else "")

    def job_reasons(self, job):
        """Why a job ended, as the scanner tells it afterwards."""
        try:
            root = self._xml(self._get("/eSCL/ScannerStatus").content)
        except EsclError:
            return []
        for info in root.iterfind(".//scan:JobInfo", ESCL_NS):
            uri = info.find("pwg:JobUri", ESCL_NS)
            if uri is not None and (uri.text or "").strip().rstrip("/") == job.rstrip("/"):
                return [(x.text or "").strip() for x in info.iterfind(".//pwg:JobStateReason", ESCL_NS)]
        return []

    # ---- a scan ------------------------------------------------------------------------------

    def _settings(self, source, pagesize, dpi):
        caps = self.caps()
        box = caps["glass" if source == "glass" else "both" if source == "duplex" and caps.get("both") else "feeder"]
        if box is None:
            raise EsclError("nofeeder" if source != "glass" else "driver")
        if source == "duplex" and not caps["feeder"]["duplex"]:
            raise EsclError("noduplex")
        width, height = (2550, 3300) if pagesize == "Letter" else (2480, 3508)
        width, height = min(width, box["width"]), min(height, box["height"])
        dpi = dpi if dpi in box["dpi"] or not box["dpi"] else min(box["dpi"], key=lambda d: (abs(d - dpi), -d))
        mode = "RGB24" if "RGB24" in box["modes"] or not box["modes"] else box["modes"][0]
        ext = float(caps["version"]) >= 2.1 if re.fullmatch(r"\d+(\.\d+)?", caps["version"]) else False
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<scan:ScanSettings xmlns:scan="{ESCL_NS["scan"]}" xmlns:pwg="{ESCL_NS["pwg"]}">'
                f'<pwg:Version>{caps["version"]}</pwg:Version>'
                '<pwg:ScanRegions><pwg:ScanRegion><pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>'
                f'<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset><pwg:Width>{width}</pwg:Width><pwg:Height>{height}</pwg:Height>'
                '</pwg:ScanRegion></pwg:ScanRegions>'
                f'<pwg:InputSource>{"Platen" if source == "glass" else "Feeder"}</pwg:InputSource>'
                f'<scan:ColorMode>{mode}</scan:ColorMode>'
                '<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>'
                + ('<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>' if ext else '')
                + f'<scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>'
                + (f'<scan:Duplex>{"true" if source == "duplex" else "false"}</scan:Duplex>' if source != "glass" else '')
                + '</scan:ScanSettings>'), dpi

    def scan(self, source, pagesize, out_dir, on_page=None, dpi=None):
        """One scan from one source ("glass", "feeder", "duplex"). Returns (page files, error code
        or None, the scanner's words). The pages are written as they come."""
        dpi = dpi or DPI
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(out_dir):
            remove(os.path.join(out_dir, f))
        self._cancelled, self.job, files = False, None, []
        try:
            state, adf = self.status()     # is it there at all? a cable pulled out is known in a moment
            if source != "glass" and adf in _ADF_STATES:
                return [], _ADF_STATES[adf], adf
            body, dpi = self._settings(source, pagesize, dpi)
            for patience in range(8):
                try:
                    r = self.http.post(self.url + "/eSCL/ScanJobs", data=body.encode("utf-8"), headers={"Content-Type": "text/xml"}, timeout=(3, 30))
                except requests.RequestException as e:
                    return [], "offline", str(e)
                if r.status_code != 503 or self._cancelled:
                    break
                time.sleep(2)              # busy with the scan before
            if self._cancelled:
                return [], "cancelled", ""
            if r.status_code == 503:
                return [], "busy", "503"
            if r.status_code not in (200, 201) or not r.headers.get("Location"):
                state, adf = self.status()
                return [], _ADF_STATES.get(adf) or ("empty" if r.status_code == 409 and source != "glass" else "driver"), f"HTTP {r.status_code} {adf}".strip()
            self.job = urlparse(r.headers["Location"]).path.rstrip("/")     # the address it gives may be one only it knows
            quiet_tries = 0
            while not self._cancelled:
                try:
                    page = self.http.get(self.url + self.job + "/NextDocument", timeout=(10, 180))
                except requests.RequestException as e:
                    if files:
                        break
                    return [], "comm", str(e)
                if page.status_code == 200 and page.content:
                    out = os.path.join(out_dir, f"p{len(files) + 1:04d}.jpg")
                    with open(out, "wb") as f:
                        f.write(page.content)
                    self._stamp(out, dpi)
                    files.append(out)
                    quiet_tries = 0
                    on_page and on_page(len(files))
                    if source == "glass":
                        break
                elif page.status_code == 503 and quiet_tries < 30:
                    quiet_tries += 1       # the page is not ready yet
                    time.sleep(1)
                else:
                    break                  # 404: no more pages
            if self._cancelled:
                return [], "cancelled", ""
            if files:
                return files, None, ""
            reasons = self.job_reasons(self.job)
            state, adf = self.status()
            return [], _ADF_STATES.get(adf) or ("empty" if source != "glass" else "driver"), " ".join(reasons + [adf]).strip()
        except EsclError as e:
            return [], e.code, e.words
        finally:
            job, self.job = self.job, None
            if job and (self._cancelled or not files):
                try:
                    self.http.delete(self.url + job, timeout=5)
                except requests.RequestException:
                    pass

    @staticmethod
    def _stamp(path, dpi):
        """The resolution written in the file when the scanner left it out (the PDF's page size
        comes from it) — without touching the picture."""
        try:
            with open(path, "r+b") as f:
                head = f.read(20)
                if head[:4] == b"\xff\xd8\xff\xe0" and head[6:11] == b"JFIF\x00" and (head[13] == 0 or head[14:16] in (b"\x00\x00", b"\x00\x01")):
                    f.seek(13)
                    f.write(bytes([1]) + dpi.to_bytes(2, "big") + dpi.to_bytes(2, "big"))
        except OSError:
            pass

    def cancel(self):
        self._cancelled = True
        job = self.job
        if job:
            try:
                requests.delete(self.url + job, timeout=5, verify=False)
            except requests.RequestException:
                pass


def escl_probe(url, timeout=1.5):
    """The scanner at this address, as a way to it, or None."""
    try:
        e = Escl(url, timeout)
        caps = e.caps()
    except EsclError:
        return None
    # ipp-usb, which carries eSCL over the cable, listens on this computer under the name localhost
    return {"id": url, "url": url, "name": caps["name"], "backend": "direct", "key": _model_key(caps["name"]), "uuid": caps["uuid"],
            "link": "usb" if urlparse(url).hostname == "localhost" else "net",
            "feeder": caps["feeder"] is not None, "duplex": bool(caps["feeder"] and caps["feeder"]["duplex"])}


def escl_find(seconds=3.0):
    """The scanners that can be asked directly: over USB through ipp-usb (Linux: it listens on
    this computer, ports 60000 and up), and on the network (they announce themselves). A scanner
    plugged in and on the network is found twice: two ways to one scanner, the cable first."""
    found, seen = [], set()

    def add(url):
        way = escl_probe(url)
        if way and url not in seen:
            seen.add(url)
            found.append(way)

    if sys.platform.startswith("linux"):
        import socket
        for port in range(60000, 60016):
            try:
                socket.create_connection(("127.0.0.1", port), 0.15).close()
            except OSError:
                continue
            add(f"http://localhost:{port}")
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return found
    urls = []

    class Heard:
        def add_service(self, zc, kind, name):
            info = zc.get_service_info(kind, name, 1500)
            if info and info.parsed_addresses():
                address = next((a for a in info.parsed_addresses() if ":" not in a), info.parsed_addresses()[0])
                root = (info.properties.get(b"rs") or b"eSCL").decode("utf-8", "replace").strip("/")
                host = f"[{address}]" if ":" in address else address
                urls.append(("https" if kind.startswith("_uscans") else "http", host, info.port, root))

        update_service = remove_service = lambda self, *a: None

    try:
        zc = Zeroconf()
    except OSError:
        return found
    try:
        heard = Heard()
        browsers = [ServiceBrowser(zc, kind, heard) for kind in ("_uscan._tcp.local.", "_uscans._tcp.local.")]
        time.sleep(seconds)
        del browsers
    finally:
        zc.close()
    for scheme, host, port, root in sorted(set(urls)):      # http before https
        if root.lower() == "escl" and not host.startswith("127."):
            add(f"{scheme}://{host}:{port}")
    return found


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
        if not self.cfg.get("device"):
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
        if QtWidgets.QMessageBox.question(self, "reader's scanner", _("Delete the folder “%1”? Its scans stay in all scans.", name)) == QtWidgets.QMessageBox.Yes:
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
        if self.store.count() == 0:
            self.say(_("put the pages on the scanner, press « scan »"),
                     _("In the feeder or on the glass: the scanner takes what it finds. The text is read on this computer, and the document becomes a PDF you can search."),
                     (("scan", _("scan")), ("import", _("from files…"))))
        else:
            self.say(_("scan, or choose a document"), "", (("scan", _("scan")),))

    def naps2_page(self):
        """No scanner answers by itself and NAPS2, which knows the others, is not there."""
        self.say(_("no scanner found"),
                 _("Most scanners made since 2015 (AirScan, Mopria) are found by themselves, on the network or by USB: is yours switched on? "
                   "The others need NAPS2, a free program installed separately, from naps2.com. Pictures and PDFs can be brought in from files meanwhile."),
                 (("again", _("look again")), ("naps2", _("get NAPS2")), ("import", _("from files…"))))

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
            self.scan()
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
        if self.searching:
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

        self.run(lambda say: self.naps2.devices(every=False), found, lambda m: found([]))

    def scan(self):
        if self.scanning:
            self.cancel_scan()
            return
        if not self.naps2.cmd:
            self.naps2 = Naps2(DATA_DIR)   # installed meanwhile, perhaps
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
            remove_tree(self.session_dir)
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
            remove_tree(self.session_dir)

    def new_session(self, doc=None):
        remove_tree(self.session_dir)
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
            remove_tree(out)
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
        if self.searching:
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
        if getattr(sys, "frozen", False) or os.environ.get("READERS_SCANNER_NEEDS_ZEROCONF"):
            try:
                import zeroconf
                check("scanners on the network can be looked for", True, "zeroconf " + zeroconf.__version__)
            except ImportError as e:
                check("scanners on the network can be looked for", False, str(e))
        t0 = time.time()
        direct = escl_find(2.0)
        lines.append(f"      scanners that answer by themselves: {[(d['name'], d['link']) for d in direct] or 'none'} ({time.time() - t0:.1f} s)")
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
        remove_tree(tmp)
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
