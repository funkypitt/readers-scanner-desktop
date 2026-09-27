#!/usr/bin/env python3
"""Test pages as a scanner would give them (A4, 300 dpi, a little noise): invented letters,
never a real document. make_pages.py OUT_DIR"""
import os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 2480, 3508
FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")      # DejaVu, free to pass on
BOLD = os.path.join(FONTS, "DejaVuSerif-Bold.ttf")
SERIF = os.path.join(FONTS, "DejaVuSerif.ttf")
rng = np.random.default_rng(7)


def paper(tone=244, shade=True):
    a = np.full((H, W, 3), tone, np.float32)
    if shade:      # the lamp falls off a little towards one edge
        a *= (1 - 0.06 * np.linspace(0, 1, W)[None, :, None])
    a += rng.normal(0, 2.2, (H, W, 1))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def page(title, lines, number=None):
    im = paper()
    d = ImageDraw.Draw(im)
    fb, f = ImageFont.truetype(BOLD, 110), ImageFont.truetype(SERIF, 58)
    if title:
        d.text((230, 260), title, font=fb, fill=(20, 20, 20))
    y = 520
    for l in lines:
        d.text((230, y), l, font=f, fill=(25, 25, 25))
        y += 96
    if number:
        d.text((W // 2 - 20, H - 230), str(number), font=f, fill=(25, 25, 25))
    return im


def save(im, path):
    im.save(path, "JPEG", quality=85, dpi=(300, 300))


out = sys.argv[1]
os.makedirs(out, exist_ok=True)
save(page("Facture d'électricité", ["Services industriels de Lausanne", "", "Madame, Monsieur,", "",
     "Nous vous remercions de votre confiance. Voici le", "décompte de votre consommation pour la période du", "1er juillet au 31 août 2026.", "",
     "Consommation totale : 412 kWh", "Montant hors taxes : 98,40 CHF", "TVA 8,1 % : 7,97 CHF", "Total à payer : 106,37 CHF"], 1), os.path.join(out, "facture-1.jpg"))
save(page(None, ["Conditions générales", "", "Le paiement est dû dans les trente jours. Pour toute", "question, notre service clients répond du lundi au", "vendredi, de 8 h à 17 h.", "",
     "Avec nos meilleures salutations."], 2), os.path.join(out, "facture-2.jpg"))
save(paper(), os.path.join(out, "blank.jpg"))
im = paper(); d = ImageDraw.Draw(im)          # show-through of the other side and two specks of dust: still blank
d.text((400, 900), "texte de l'autre face, vu par transparence", font=ImageFont.truetype(SERIF, 58), fill=(226, 226, 226))
d.ellipse((1200, 2000, 1203, 2003), fill=(60, 60, 60)); d.ellipse((700, 3000, 702, 3002), fill=(80, 80, 80))
save(im, os.path.join(out, "blank-showthrough.jpg"))
save(page(None, [], 7), os.path.join(out, "only-number.jpg"))            # a page number alone: not blank
save(page("Contrat de bail", ["Loyer mensuel : 1850 CHF", "Charges comprises", "", "Fait à Lausanne, le 3 septembre 2026"]), os.path.join(out, "contrat.jpg"))
save(page("Mietvertrag", ["Monatliche Miete: 1850 CHF", "Nebenkosten inbegriffen", "", "Zürich, den 3. September 2026"]), os.path.join(out, "vertrag.jpg"))
im = page("Note de frais", ["Train Lausanne – Berne : 64 CHF", "Repas : 28,50 CHF"]).rotate(180)   # fed upside down
save(im, os.path.join(out, "upside-down.jpg"))
print(sorted(os.listdir(out)))
