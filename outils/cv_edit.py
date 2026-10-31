#!/usr/bin/env python3
"""
cv_edit.py — modifie un CV .docx du candidat SANS toucher à la mise en forme.

Principe : on ne régénère jamais le CV. On part d'un des 3 .docx de référence,
on remplace du texte / duplique ou supprime des puces directement dans
word/document.xml, puis on re-zippe et on vérifie que ça tient sur 1 page.

Usage :
  python3 cv_edit.py base.docx sortie.docx edits.json

edits.json = liste d'opérations, appliquées dans l'ordre :
  {"op": "replace",      "old": "texte exact", "new": "nouveau texte"}
  {"op": "add_bullet",   "after": "texte d'une puce existante", "text": "nouvelle puce"}
  {"op": "remove_para",  "contains": "texte unique du paragraphe à supprimer"}

Le script échoue (code 1) si un texte cherché est introuvable ou ambigu,
ou si le PDF généré dépasse 1 page.
"""
import json, re, shutil, subprocess, sys, tempfile, zipfile, os
from xml.sax.saxutils import escape


def load(docx):
    with zipfile.ZipFile(docx) as z:
        return {n: z.read(n) for n in z.namelist()}


def save(files, out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)


def para_bounds(x, i):
    ps = x.rfind("<w:p ", 0, i)
    ps2 = x.rfind("<w:p>", 0, i)
    ps = max(ps, ps2)
    pe = x.find("</w:p>", i) + len("</w:p>")
    return ps, pe


def unique(x, s):
    n = x.count(s)
    if n != 1:
        sys.exit(f"ERREUR : '{s[:60]}' trouvé {n} fois (doit être exactement 1)")
    return x.find(s)


_id = [0x0C000000]


def fresh_ids(p):
    _id[0] += 1
    h = f"{_id[0]:08X}"
    p = re.sub(r'w14:paraId="[0-9A-F]+"', f'w14:paraId="{h}"', p)
    return re.sub(r'w14:textId="[0-9A-F]+"', f'w14:textId="{h}"', p)


def apply(x, op):
    if op["op"] == "replace":
        old, new = escape(op["old"]), escape(op["new"])
        unique(x, old)
        return x.replace(old, new)
    if op["op"] == "add_bullet":
        anchor = escape(op["after"])
        i = unique(x, anchor)
        ps, pe = para_bounds(x, i)
        tpl = x[ps:pe]
        # la puce modèle = 1 seul run texte ; on remplace son contenu entier
        tpl = re.sub(r"(<w:t[^>]*>)[^<]*" + re.escape(anchor) + r"[^<]*(</w:t>)",
                     lambda m: m.group(1) + escape(op["text"]) + m.group(2), tpl, count=1)
        new_p = fresh_ids(tpl)
        return x[:pe] + new_p + x[pe:]
    if op["op"] == "replace_in_para":
        # Pour un texte présent plusieurs fois (ex. « Projet personnel ») : on vise le paragraphe
        # qui contient un texte unique (ex. le titre du projet), puis on remplace dans ce seul paragraphe.
        i = unique(x, escape(op["contains"]))
        ps, pe = para_bounds(x, i)
        para, old = x[ps:pe], escape(op["old"])
        if para.count(old) != 1:
            sys.exit(f"ERREUR : '{op['old'][:60]}' trouvé {para.count(old)} fois dans le paragraphe visé")
        return x[:ps] + para.replace(old, escape(op["new"])) + x[pe:]
    if op["op"] == "remove_para":
        i = unique(x, escape(op["contains"]))
        ps, pe = para_bounds(x, i)
        return x[:ps] + x[pe:]
    sys.exit(f"op inconnue : {op}")


def pages(docx):
    d = tempfile.mkdtemp()
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", d, docx],
                   check=True, capture_output=True)
    pdf = os.path.join(d, os.path.basename(docx).rsplit(".", 1)[0] + ".pdf")
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info).group(1)), pdf


if __name__ == "__main__":
    base, out, edits = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3], encoding="utf-8"))
    files = load(base)
    x = files["word/document.xml"].decode("utf-8")
    for op in edits:
        x = apply(x, op)
    files["word/document.xml"] = x.encode("utf-8")
    save(files, out)
    n, pdf = pages(out)
    final_pdf = out.rsplit(".", 1)[0] + ".pdf"
    shutil.copy(pdf, final_pdf)
    print(f"OK : {out} + {final_pdf} — {n} page(s)")
    if n != 1:
        sys.exit("ERREUR : le CV dépasse 1 page — raccourcir une puce ou en retirer une.")
