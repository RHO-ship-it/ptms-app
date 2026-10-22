"""Feuille de saisie des résultats (type « submission sheet » Excel), configurable par l'admin.

Une campagne (pack) possède des feuilles (onglets). Chaque feuille a des colonnes (Result 1, Mean, Unit,
Method…) et des lignes (les essais de la campagne). L'admin ajoute / supprime lignes, colonnes et feuilles ;
le participant remplit les cellules jaunes comme dans le classeur Excel. À l'enregistrement, les valeurs
alimentent les « Submission » existantes, donc les statistiques (z-score, En…) restent inchangées.
"""
import json
import os
import re
import statistics
from datetime import datetime

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

from extensions import db
from models import (Campaign, CampaignLaboratory, Trial, ParticipantForm, Submission, SheetIdentification)
from utils import log_action, notify_role, compute_z_score, compute_en
from scoring import fixed_scores
from blueprints.auth import roles_required

sheets_bp = Blueprint("sheets", __name__, template_folder="../templates/sheets")

KINDS = {"quantitative": "Quantitatif (valeurs numériques)", "qualitative": "Qualitatif (notes / classes)",
         "solidite": "Solidité des couleurs (une note par colorant)",
         "sensory": "Analyse sensorielle (panel de dégustateurs)"}
COL_TYPES = {"number": "Nombre", "text": "Texte", "select": "Liste de choix", "date": "Date",
             "mean": "Moyenne (calculée)", "fixed": "Valeur de la ligne (non modifiable)"}
COL_ROLES = {"result": "Résultat", "final": "Résultat final", "dye": "Colorant (solidité)",
             "uncertainty": "Incertitude U(x)", "remarks": "Remarques", "info": "Information"}
FIXED_FROM = {"unit": "Unité", "method": "Standard / Méthode", "sample": "Code échantillon"}
GRADES = ["5", "4-5", "4", "3-4", "3", "2-3", "2", "1-2", "1"]
IDENT_FIELDS = [("country", "Country"), ("accreditation", "ISO 17025 Accreditation (Yes or No ?)"),
                ("submission_date", "Submission Date"), ("reception_date", "Sample Reception Date"),
                ("bottle_opening", "Bottle Opening Date"), ("samples_tested", "Number of Samples Tested"),
                ("storage", "Sample Storage Conditions"), ("storage_temp", "Storage Temperature (°C)"),
                ("remarks", "General Remarks")]
DEFAULT_IDENT = ["country", "accreditation", "submission_date", "reception_date", "samples_tested", "remarks"]
SENSORY_DEFAULT = {
    "attrs": [{"key": "fusty", "label": "Fusty", "group": "defect"}, {"key": "musty", "label": "Musty", "group": "defect"},
              {"key": "winey", "label": "Winey", "group": "defect"}, {"key": "rancid", "label": "Rancid", "group": "defect"},
              {"key": "frostbitten", "label": "Frostbitten", "group": "defect"}, {"key": "others", "label": "Others", "group": "defect"},
              {"key": "fruity", "label": "Fruity", "group": "positive"}, {"key": "bitter", "label": "Bitter", "group": "positive"},
              {"key": "pungent", "label": "Pungent", "group": "positive"}],
    "min": 8, "max": 12,
    "classes": ["Extra Virgin", "Virgin", "Lampante"],
    "fruity_types": ["Green", "Ripe", "Green & Ripe", "Not applicable"],
    "defect_types": ["Fusty", "Musty", "Winey", "Rancid", "Frostbitten", "Others", "None"],
    "reference": "COI/T.20/Doc. No 15 (except section 10.4) – Regulation (EU) 2022/2105",
}
SENSORY_NAME = "Sensory Analysis of virgin olive oil"
SENSORY_ROWS = [("Median of fruitiness", "quantitative"), ("Median of the dominant defect", "quantitative"),
                ("Category", "qualitative")]
# Feuilles proposées d'office pour une campagne « olive » (mots-clés cherchés dans le nom / paramètre des essais).
OLIVE_LAYOUT = [("sensory", "Sensory Analysis", "sensory", ["sensory"]),
                ("pesticides", "Pesticides", "pesticide", ["pesticide"]),
                ("fatty_acid_profile", "Fatty Acid Profile", "analysis_rounded", ["fatty"]),
                ("acidity", "Acidity", "analysis_basic", ["acidity"]),
                ("k232_k270", "K232-K270", "analysis_basic", ["k232", "k270", "uv absorb"])]
_PEST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sheet_data", "pesticides.txt")
IDENT_INPUT = {"submission_date": "date", "reception_date": "date", "bottle_opening": "date",
               "samples_tested": "number", "storage_temp": "number", "accreditation": "yesno"}


# ───────────────────────── Structure de la feuille ─────────────────────────

def _c(key, label, type_="number", role="info", options=None, fixed=""):
    return {"key": key, "label": label, "type": type_, "role": role, "options": options or [], "fixed": fixed}


def _results(n, type_="number"):
    return [_c(f"r{i}", f"Result {i}", type_, "result") for i in range(1, n + 1)]


def _common_end():
    return [_c("method", "Standard / Method", "fixed", fixed="method"),
            _c("sample", "Sample Code", "fixed", fixed="sample"),
            _c("remarks", "Remarks", "text", "remarks")]


PRESETS = {
    "quant_mean": ("Quantitatif : Result 1 à 5 + moyenne (textile)", "quantitative", [],
                   lambda: _results(5) + [_c("mean", "Mean Value", "mean"), _c("unit", "Unit", "fixed", fixed="unit"),
                                          _c("u", "Expanded Uncertainty U(x) (%)", "number", "uncertainty")] + _common_end()),
    "quant_final": ("Quantitatif : Result 1 à 3 + résultat final (cuir)", "quantitative", [],
                    lambda: _results(3) + [_c("final", "Final Result", "number", "final"),
                                           _c("unit", "Unit", "fixed", fixed="unit"),
                                           _c("u", "Expanded Uncertainty U(x) (%)", "number", "uncertainty")] + _common_end()),
    "qual_grade": ("Qualitatif : Result 1 à 3 + classe finale (notes 1 à 5)", "qualitative", GRADES,
                   lambda: _results(3, "select") + [_c("final", "Final Class / Grade", "select", "final")] + _common_end()),
    "qual_4": ("Qualitatif : Result 1 à 4 + résultat final (cuir)", "qualitative", GRADES,
               lambda: _results(4, "select") + [_c("final", "Final Result", "select", "final")] + _common_end()),
    "solidity": ("Solidité des couleurs : un colorant par colonne (textile)", "solidite", GRADES,
                 lambda: [_c("couleur", "Colour change", "select", "dye"), _c("CA", "CA (Acetate)", "select", "dye"),
                          _c("CO", "CO (Cotton)", "select", "dye"), _c("PA", "PA (Polyamide)", "select", "dye"),
                          _c("PES", "PES (Polyester)", "select", "dye"), _c("PAN", "ACR (Acrylic)", "select", "dye"),
                          _c("WO", "WO (Wool)", "select", "dye")] + _common_end()),
    "analysis_basic": ("Analyse simple : méthode + résultat (olive : acidité, K232/K270)", "quantitative", [],
                       lambda: [_c("methodused", "Method", "text"), _c("result", "Result", "number", "result"),
                                _c("unit", "Unit", "fixed", fixed="unit"), _c("sample", "Sample Code", "fixed", fixed="sample"),
                                _c("remarks", "Remarks", "text", "remarks")]),
    "analysis_rounded": ("Analyse : résultat brut + arrondi + date (olive : acides gras)", "quantitative", [],
                         lambda: [_c("methodused", "Primary Method", "text"),
                                  _c("unrounded", "Unrounded Result", "number", "result"),
                                  _c("rounded", "Rounded Result", "number", "final"),
                                  _c("unit", "Unit", "fixed", fixed="unit"), _c("date", "Analysis Date", "date"),
                                  _c("sample", "Sample Code", "fixed", fixed="sample"),
                                  _c("remarks", "Comments", "text", "remarks")]),
    "sensory": ("Analyse sensorielle : grille des dégustateurs + paramètres globaux (olive)", "sensory", [], lambda: []),
    "pesticide": ("Pesticides : accréditation, état, résultat, technique (olive)", "quantitative", [],
                  lambda: [_c("accredited", "Accredited ?", "select", options=["Yes", "No"]),
                           _c("state", "State", "select", options=["Provide Result", "<LOQ", "Not Tested"]),
                           _c("result", "Result", "number", "result"), _c("unit", "Unit", "fixed", fixed="unit"),
                           _c("date", "Analysis Date", "date"),
                           _c("technique", "Analysis Technique", "select",
                              options=["GC-FID", "GC-ECD", "GC-MS", "GC-MS/MS", "LC-UV", "LC-Fluorescence", "LC-MS",
                                       "LC-MS/MS", "Other method (to be specified in comments)"]),
                           _c("sample", "Sample Code", "fixed", fixed="sample"),
                           _c("remarks", "Comments", "text", "remarks")]),
}


def _slug(text, fallback="x"):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:30] or fallback


def make_sheet(preset, title, key, match=None):
    label, kind, scale, builder = PRESETS[preset]
    sheet = {"key": key, "title": title, "kind": kind, "scale": list(scale), "cols": builder(), "match": list(match or [])}
    if kind == "sensory":
        sheet["sensory"] = json.loads(json.dumps(SENSORY_DEFAULT))
        sheet["match"] = sheet["match"] or ["sensory"]
    return sheet


def trial_label(trial):
    """Texte affiché dans la colonne « Proficiency Testing Properties » : le paramètre s'il existe, sinon le nom."""
    p = (trial.parameter or "").strip()
    return p if p and p.lower() != (trial.name or "").strip().lower() else trial.name


def _trial_text(trial):
    return f"{trial.name} {trial.parameter or ''}".lower()


def default_config(campaign):
    """Configuration de départ : feuilles olive (si l'on reconnaît les essais), puis une feuille par type d'essai."""
    sheets, used = [], set()
    for key, title, preset, words in OLIVE_LAYOUT:
        hit = [t for t in campaign.trials if any(w in _trial_text(t) for w in words)]
        if hit:
            sheets.append(make_sheet(preset, title, key, words))
            used.update(t.id for t in hit)
    titles = {"quantitative": ("quant_mean", "Quantitative Results"), "qualitative": ("qual_grade", "Qualitative Results"),
              "solidite": ("solidity", "Solidity Fastness Results")}
    seen = set()
    for t in campaign.trials:
        if t.id not in used and t.trial_type in titles and t.trial_type not in seen:
            seen.add(t.trial_type)
            preset, title = titles[t.trial_type]
            sheets.append(make_sheet(preset, title, t.trial_type))
    return {"ident": list(DEFAULT_IDENT), "sheets": sheets}


def load_config(campaign):
    try:
        cfg = json.loads(campaign.sheet_config) if campaign.sheet_config else None
    except ValueError:
        cfg = None
    if not cfg or "sheets" not in cfg:
        cfg = default_config(campaign)
    cfg.setdefault("ident", list(DEFAULT_IDENT))
    return cfg


def save_config(campaign, cfg):
    campaign.sheet_config = json.dumps(cfg, ensure_ascii=False)
    db.session.commit()


def sheet_key_of(cfg, trial):
    sheets = cfg["sheets"]
    if trial.sheet_key in [s["key"] for s in sheets]:
        return trial.sheet_key
    text = _trial_text(trial)
    for s in sheets:                                   # feuille reconnue par mot-clé (olive…)
        if any(w in text for w in s.get("match", [])):
            return s["key"]
    for s in sheets:                                   # sinon : première feuille du même type, sans mot-clé
        if s["kind"] == trial.trial_type and not s.get("match"):
            return s["key"]
    for s in sheets:
        if s["kind"] == trial.trial_type:
            return s["key"]
    return None


def trials_of(cfg, campaign, key):
    return [t for t in campaign.trials if sheet_key_of(cfg, t) == key]


def _split(text):
    return [x.strip() for x in re.split(r"[,\n;]", text or "") if x.strip()]


def trial_scale(trial):
    return _split(trial.scale)


# ───────────────────────── Administration de la feuille ─────────────────────────

@sheets_bp.route("/campaign/<int:campaign_id>/builder", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def builder(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    cfg = load_config(campaign)
    if request.method == "POST":
        action = request.form.get("action")
        key = request.form.get("key")
        sheet = next((s for s in cfg["sheets"] if s["key"] == key), None)
        try:
            msg = _builder_action(campaign, cfg, sheet, action)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sheets.builder", campaign_id=campaign.id))
        if msg:
            flash(msg, "success")
        return redirect(url_for("sheets.builder", campaign_id=campaign.id, _anchor=("sheet-" + key) if key else None))

    sheets = [{"sheet": s, "trials": trials_of(cfg, campaign, s["key"])} for s in cfg["sheets"]]
    unassigned = [t for t in campaign.trials if sheet_key_of(cfg, t) is None]
    return render_template("sheets/builder.html", campaign=campaign, cfg=cfg, sheets=sheets, unassigned=unassigned,
                           kinds=KINDS, col_types=COL_TYPES, col_roles=COL_ROLES, fixed_from=FIXED_FROM,
                           presets=PRESETS, ident_fields=IDENT_FIELDS, pesticides=_pesticide_list())


def _pesticide_list():
    try:
        with open(_PEST_FILE, encoding="utf-8") as fh:
            return [x.strip() for x in fh if x.strip()]
    except OSError:
        return []


def _new_trial(campaign, sheet, **kw):
    trial = Trial(campaign_id=campaign.id, sheet_key=sheet["key"], **kw)
    db.session.add(trial)
    db.session.flush()
    for cl in campaign.laboratories:
        db.session.add(ParticipantForm(trial_id=trial.id, laboratory_id=cl.laboratory_id, status="pending"))
    return trial


def _builder_action(campaign, cfg, sheet, action):
    f = request.form
    if action == "save_ident":
        cfg["ident"] = [k for k, _ in IDENT_FIELDS if f.get(f"ident_{k}")]
        save_config(campaign, cfg)
        return "Champs d'identification enregistrés."

    if action == "add_sheet":
        preset = f.get("preset")
        if preset not in PRESETS:
            raise ValueError("Choisissez un modèle de feuille.")
        title = (f.get("title") or "").strip() or PRESETS[preset][0].split(":")[0]
        key, n = _slug(title, "feuille"), 2
        while any(s["key"] == key for s in cfg["sheets"]):
            key, n = f"{_slug(title, 'feuille')}_{n}", n + 1
        new = make_sheet(preset, title, key)
        cfg["sheets"].append(new)
        if preset == "sensory" and not any(sheet_key_of(cfg, t) == key for t in campaign.trials):
            for pname, ptype in SENSORY_ROWS:     # les 3 résultats transmis pour évaluation
                _new_trial(campaign, new, name=SENSORY_NAME, parameter=pname, trial_type=ptype, method="COI")
            db.session.commit()
        save_config(campaign, cfg)
        return f"Feuille « {title} » ajoutée."

    if action == "assign":
        trial = Trial.query.filter_by(id=f.get("trial_id", type=int), campaign_id=campaign.id).first_or_404()
        target = next((s for s in cfg["sheets"] if s["key"] == f.get("sheet_key")), None)
        if not target:
            raise ValueError("Feuille inconnue.")
        trial.sheet_key = target["key"]
        db.session.commit()
        return f"« {trial.name} » rattaché à la feuille « {target['title']} »."

    if sheet is None:
        raise ValueError("Feuille inconnue.")

    if action == "delete_sheet":
        if trials_of(cfg, campaign, sheet["key"]):
            raise ValueError("Supprimez d'abord les lignes de cette feuille (ou rattachez-les à une autre feuille).")
        cfg["sheets"] = [s for s in cfg["sheets"] if s["key"] != sheet["key"]]
        save_config(campaign, cfg)
        return f"Feuille « {sheet['title']} » supprimée."

    if action == "save_sensory":
        sheet["title"] = (f.get("title") or "").strip() or sheet["title"]
        sx = sheet.setdefault("sensory", json.loads(json.dumps(SENSORY_DEFAULT)))
        attrs = []
        for line in (f.get("attrs") or "").splitlines():
            label, _, group = [p.strip() for p in line.partition("|")]
            if label:
                attrs.append({"key": _slug(label, "attr"), "label": label,
                              "group": "positive" if group.lower().startswith("pos") else "defect"})
        if attrs:
            sx["attrs"] = attrs
        sx["min"] = f.get("min", sx["min"], type=int)
        sx["max"] = min(max(f.get("max", sx["max"], type=int), 1), 30)
        for k in ("classes", "fruity_types", "defect_types"):
            lst = _split(f.get(k))
            if lst:
                sx[k] = lst
        sx["reference"] = (f.get("reference") or "").strip() or sx.get("reference", "")
        save_config(campaign, cfg)
        return f"Paramètres de l'analyse sensorielle « {sheet['title']} » enregistrés."

    if action == "bulk_add":
        lines = [x.strip() for x in (f.get("bulk_lines") or "").splitlines() if x.strip()]
        existing = {trial_label(t).lower() for t in trials_of(cfg, campaign, sheet["key"])}
        group = (f.get("bulk_name") or "").strip() or sheet["title"]
        ttype = sheet["kind"] if sheet["kind"] != "sensory" else "quantitative"
        n = 0
        for p in lines:
            if p.lower() in existing:
                continue
            existing.add(p.lower())
            _new_trial(campaign, sheet, name=group, parameter=p, trial_type=ttype,
                       unit=(f.get("bulk_unit") or "").strip(), method=(f.get("bulk_method") or "").strip(),
                       sample_reference=(f.get("bulk_sample") or "").strip())
            n += 1
        db.session.commit()
        return f"{n} ligne(s) ajoutée(s) à « {sheet['title']} »." if n else "Aucune nouvelle ligne (déjà présentes)."

    if action == "save_sheet":
        sheet["title"] = (f.get("title") or "").strip() or sheet["title"]
        sheet["scale"] = _split(f.get("scale"))
        cols, used = [], set()
        for i in range(f.get("ncols", 0, type=int)):
            label = (f.get(f"c{i}_label") or "").strip()
            if f.get(f"c{i}_del") or not label:
                continue
            type_, role = f.get(f"c{i}_type", "text"), f.get(f"c{i}_role", "info")
            if type_ not in COL_TYPES or role not in COL_ROLES:
                continue
            key = (f.get(f"c{i}_key") or "").strip() or _slug(label, f"col{i}")
            base, n = key, 2
            while key in used:
                key, n = f"{base}_{n}", n + 1
            used.add(key)
            fixed = f.get(f"c{i}_fixed", "") if type_ == "fixed" else ""
            if type_ == "fixed" and fixed not in FIXED_FROM:
                fixed = "unit"
            order = f.get(f"c{i}_order", type=float)
            cols.append((order if order is not None else float(i), {
                "key": key, "label": label, "type": type_, "role": role,
                "options": _split(f.get(f"c{i}_options")), "fixed": fixed}))
        sheet["cols"] = [c for _, c in sorted(cols, key=lambda x: x[0])]
        save_config(campaign, cfg)
        return f"Colonnes de « {sheet['title']} » enregistrées."

    if action == "save_rows":
        done, blocked = 0, []
        for i in range(f.get("nrows", 0, type=int)):
            name = (f.get(f"r{i}_name") or "").strip()
            tid = f.get(f"r{i}_id", type=int)
            if tid:
                trial = Trial.query.filter_by(id=tid, campaign_id=campaign.id).first()
                if not trial:
                    continue
                if f.get(f"r{i}_del"):
                    if any(fm.submission for fm in trial.forms):
                        blocked.append(trial.name)
                        continue
                    db.session.delete(trial)
                    done += 1
                    continue
                if name:
                    trial.name = name
                trial.parameter = (f.get(f"r{i}_param") or "").strip() or None
                trial.unit = (f.get(f"r{i}_unit") or "").strip()
                trial.method = (f.get(f"r{i}_method") or "").strip()
                trial.sample_reference = (f.get(f"r{i}_sample") or "").strip()
                trial.scale = ", ".join(_split(f.get(f"r{i}_scale"))) or None
                trial.sheet_key = sheet["key"]
            elif name:
                trial = Trial(campaign_id=campaign.id, name=name, trial_type=sheet["kind"] if sheet["kind"] != "sensory" else "quantitative",
                              parameter=(f.get(f"r{i}_param") or "").strip() or None,
                              unit=(f.get(f"r{i}_unit") or "").strip(), method=(f.get(f"r{i}_method") or "").strip(),
                              sample_reference=(f.get(f"r{i}_sample") or "").strip(),
                              scale=", ".join(_split(f.get(f"r{i}_scale"))) or None, sheet_key=sheet["key"])
                db.session.add(trial)
                db.session.flush()
                for cl in campaign.laboratories:
                    db.session.add(ParticipantForm(trial_id=trial.id, laboratory_id=cl.laboratory_id, status="pending"))
                done += 1
        db.session.commit()
        if blocked:
            flash("Non supprimé (des résultats existent déjà) : " + ", ".join(blocked), "warning")
        return f"Lignes de « {sheet['title']} » enregistrées." if done or not blocked else None
    raise ValueError("Action inconnue.")


# ───────────────────────── Saisie par le laboratoire ─────────────────────────

def _grade_to_float(text):
    t = text.strip().replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)", t)
    return (float(m.group(1)) + float(m.group(2))) / 2 if m else float(t)


def _num(text):
    return float(text.strip().replace(",", "."))


def _load_store(campaign, lab):
    row = SheetIdentification.query.filter_by(campaign_id=campaign.id, laboratory_id=lab.id).first() if lab else None
    try:
        data = json.loads(row.data) if row and row.data else {}
    except ValueError:
        data = {}
    data.setdefault("ident", {})
    data.setdefault("cells", {})
    return row, data


def _legacy_cells(trial, sheet, sub):
    """Reconstitue les cellules d'une soumission saisie avec l'ancien formulaire (un essai à la fois)."""
    cells, cols = {}, sheet["cols"]
    first = next((c for c in cols if c["role"] == "result"), None)
    final = next((c for c in cols if c["role"] == "final"), None)
    shown = sub.qualitative_result if sub.qualitative_result else (sub.value if sub.value is not None else "")
    if sheet["kind"] == "solidite":
        from utils import parse_solidite_data
        for k, v in parse_solidite_data(sub.solidite_data).items():
            cells[k] = "" if v is None else str(v)
    elif first:
        cells[first["key"]] = "" if shown is None else str(shown)
    if final and shown != "":
        cells[final["key"]] = str(shown)
    for c in cols:
        if c["role"] == "uncertainty" and sub.uncertainty is not None:
            cells[c["key"]] = str(sub.uncertainty)
        if c["role"] == "remarks" and sub.comments:
            cells[c["key"]] = sub.comments
    return cells


@sheets_bp.route("/campaign/<int:campaign_id>/entry", methods=["GET", "POST"])
@login_required
@roles_required("participant", "admin", "chef_technique")
def entry(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    cfg = load_config(campaign)
    preview = current_user.role != "participant"
    lab = None if preview else current_user.laboratory
    if not preview:
        if lab is None or not CampaignLaboratory.query.filter_by(campaign_id=campaign.id, laboratory_id=lab.id).first():
            flash("Votre laboratoire n'est pas inscrit à cette campagne.", "warning")
            return redirect(url_for("participants.portal"))
        for t in campaign.trials:   # lignes ajoutées après l'inscription
            if not ParticipantForm.query.filter_by(trial_id=t.id, laboratory_id=lab.id).first():
                db.session.add(ParticipantForm(trial_id=t.id, laboratory_id=lab.id, status="pending"))
        db.session.commit()

    row, store = _load_store(campaign, lab)
    forms = {f.trial_id: f for f in ParticipantForm.query.filter_by(laboratory_id=lab.id).all()} if lab else {}

    if request.method == "POST" and not preview:
        return _save_entry(campaign, cfg, lab, row, store, forms)

    sheets = []
    for s in cfg["sheets"]:
        rows = []
        for n, t in enumerate(trials_of(cfg, campaign, s["key"]), start=1):
            cells = store["cells"].get(str(t.id))
            if cells is None:
                sub = forms[t.id].submission if t.id in forms else None
                cells = _legacy_cells(t, s, sub) if sub else {}
            opts = {}
            for c in s["cols"]:
                if c["type"] == "select":
                    own = trial_scale(t) if c["role"] in ("result", "final", "dye") else []
                    opts[c["key"]] = own or c["options"] or s["scale"]
            fixed = {"unit": t.unit or "", "method": t.method or "", "sample": t.sample_reference or ""}
            rows.append({"n": n, "trial": t, "label": trial_label(t), "cells": cells, "opts": opts, "fixed": fixed,
                         "confirmed": bool(t.id in forms and forms[t.id].submission and forms[t.id].submission.confirmed)})
        sample = next((r["trial"].sample_reference for r in rows if r["trial"].sample_reference), "")
        sheets.append({"sheet": s, "rows": rows, "sample": sample,
                       "sval": store["cells"].get("sensory:" + s["key"], {}) if s["kind"] == "sensory" else {}})
    all_rows = [r for sh in sheets for r in sh["rows"]]
    confirmed_all = bool(all_rows) and all(r["confirmed"] for r in all_rows)
    ident_defaults = {"country": "", "accreditation": "Yes" if (lab and lab.iso17025_accredited) else ""}
    ident = {**ident_defaults, **{k: v for k, v in store["ident"].items() if v != ""}}
    scheme = "-".join(campaign.reference.split("-")[:2])[:5] if campaign.reference.startswith("TTS-") else campaign.reference
    return render_template("sheets/entry.html", campaign=campaign, cfg=cfg, sheets=sheets, preview=preview, lab=lab,
                           ident=ident, ident_fields=[(k, l) for k, l in IDENT_FIELDS if k in cfg["ident"]],
                           ident_input=IDENT_INPUT, scheme=scheme, confirmed_all=confirmed_all,
                           active_tab=request.args.get("tab", "instructions"))


def _save_entry(campaign, cfg, lab, row, store, forms):
    f = request.form
    store["ident"] = {k: (f.get(f"ident_{k}") or "").strip() for k, _ in IDENT_FIELDS if k in cfg["ident"]}
    reception = None
    try:
        reception = datetime.strptime(store["ident"].get("reception_date", ""), "%Y-%m-%d").date()
    except ValueError:
        pass
    accredited = store["ident"].get("accreditation") == "Yes"
    saved, errors = 0, []
    for s in cfg["sheets"]:
        if s["kind"] == "sensory":
            saved += _save_sensory(campaign, cfg, s, f, store, forms, errors, reception, accredited)
            continue
        for t in trials_of(cfg, campaign, s["key"]):
            vals = {c["key"]: (f.get(f"c_{t.id}_{c['key']}") or "").strip()
                    for c in s["cols"] if c["type"] not in ("mean", "fixed")}
            store["cells"][str(t.id)] = vals
            form = forms.get(t.id)
            if form is None:
                continue
            try:
                fields = _compute(t, s, vals)
            except ValueError as exc:
                errors.append(f"{t.name} : {exc}")
                continue
            if fields is None:
                continue
            _upsert(form, t, fields, vals, reception, accredited)
            saved += 1
    if row is None:
        row = SheetIdentification(campaign_id=campaign.id, laboratory_id=lab.id)
        db.session.add(row)
    row.data = json.dumps(store, ensure_ascii=False)
    db.session.commit()

    for err in errors[:5]:
        flash("Valeur non valide, ligne non enregistrée — " + err, "danger")
    if saved:
        log_action("submit", "Campaign", campaign.id, f"Feuille de saisie {campaign.reference} : {saved} ligne(s) par {current_user.username}")
        notify_role("chef_technique", f"Nouvelle soumission de {lab.code} pour {campaign.reference}",
                    link=url_for("participants.validation_center"))

    if f.get("action") == "confirm":
        n = 0
        for form in forms.values():
            if form.submission and form.trial.campaign_id == campaign.id and not form.submission.confirmed:
                form.submission.confirmed, form.submission.confirmed_at = True, datetime.utcnow()
                n += 1
        db.session.commit()
        flash(f"Soumission confirmée ({n} ligne(s))." if n
              else "Rien à confirmer : aucun résultat n'est enregistré.", "success" if n else "warning")
    elif saved and not errors:
        flash(f"Feuille enregistrée ({saved} ligne(s) avec résultat). Pensez à confirmer votre soumission.", "success")
    elif not saved and not errors:
        flash("Brouillon enregistré (aucun résultat renseigné pour l'instant).", "info")
    return redirect(url_for("sheets.entry", campaign_id=campaign.id, tab=f.get("active_tab", "instructions")))


def _compute(trial, sheet, vals):
    """Valeurs statistiques d'une ligne ; None si la ligne n'a aucun résultat ; ValueError si valeur invalide."""
    cols = sheet["cols"]
    results = [vals[c["key"]] for c in cols if c["role"] == "result" and vals.get(c["key"])]
    finals = [vals[c["key"]] for c in cols if c["role"] == "final" and vals.get(c["key"])]
    dyes = {c["key"]: vals.get(c["key"], "") for c in cols if c["role"] == "dye"}
    unc_txt = next((vals[c["key"]] for c in cols if c["role"] == "uncertainty" and vals.get(c["key"])), "")
    remarks = next((vals[c["key"]] for c in cols if c["role"] == "remarks" and vals.get(c["key"])), "")
    out = {"value": None, "replicate_2": None, "qualitative_result": None, "solidite_data": None,
           "uncertainty": None, "remarks": remarks, "conform": None, "anomaly": False, "z": None, "en": None}
    try:
        out["uncertainty"] = _num(unc_txt) if unc_txt else None
    except ValueError:
        raise ValueError("l'incertitude doit être un nombre")

    kind = sheet["kind"]
    if kind == "quantitative":
        try:
            nums = [_num(x) for x in results]
            final = _num(finals[0]) if finals else None
        except ValueError:
            raise ValueError("les résultats doivent être des nombres")
        value = final if final is not None else (sum(nums) / len(nums) if nums else None)
        if value is None:
            return None
        out["value"] = round(value, 10)
        out["replicate_2"] = nums[1] if len(nums) > 1 else None
        out["z"], out["anomaly"], out["en"] = fixed_scores(trial, value, out["uncertainty"])
    elif kind == "solidite":
        try:
            nums = {k: (_grade_to_float(v) if v else None) for k, v in dyes.items()}
        except ValueError:
            raise ValueError("les notes doivent être des nombres (ex. 4 ou 4-5)")
        present = [v for v in nums.values() if v is not None]
        if not present:
            return None
        out["solidite_data"] = json.dumps(nums)
        out["qualitative_result"] = f"{min(present):.1f}"
        if trial.expected_qualitative_result:
            try:
                out["conform"] = min(present) >= float(trial.expected_qualitative_result.replace(",", "."))
                out["anomaly"] = not out["conform"]
            except ValueError:
                pass
    else:
        value = finals[0] if finals else (results[0] if results else "")
        if not value:
            return None
        out["qualitative_result"] = value
        if trial.expected_qualitative_result:
            out["conform"] = value.strip().lower() == trial.expected_qualitative_result.strip().lower()
            out["anomaly"] = out["conform"] is False
    return out


def _upsert(form, trial, fields, vals, reception, accredited):
    sub = form.submission or Submission(form_id=form.id, submitted_by=current_user.id)
    if form.submission is None:
        db.session.add(sub)
    sub.value, sub.replicate_2 = fields["value"], fields["replicate_2"]
    sub.qualitative_result, sub.solidite_data = fields["qualitative_result"], fields["solidite_data"]
    sub.qualitative_conform = fields["conform"]
    sub.uncertainty, sub.comments = fields["uncertainty"], fields["remarks"]
    sub.method_used = trial.method
    sub.iso17025_accredited, sub.sample_reception_date = accredited, reception
    sub.extra_data = json.dumps({"cells": vals}, ensure_ascii=False)
    sub.z_score, sub.en_score, sub.anomaly_flag = fields["z"], fields["en"], fields["anomaly"]
    sub.submitted_at = datetime.utcnow()
    sub.validated, sub.confirmed, sub.confirmed_at = False, False, None
    form.status = "anomaly" if fields["anomaly"] else "submitted"


def sensory_stats(sx, grid):
    """Médianes par attribut, médiane fruitée, défaut dominant et catégorie suggérée (COI/T.15/NC n°3)."""
    meds = {}
    for a in sx["attrs"]:
        nums = []
        for row in grid:
            v = row.get(a["key"], "")
            if v != "":
                x = _num(v)
                if not 0 <= x <= 10:
                    raise ValueError("les intensités doivent être comprises entre 0 et 10")
                nums.append(x)
        meds[a["key"]] = round(statistics.median(nums), 1) if nums else None
    filled = sum(1 for row in grid if any(row.get(a["key"], "") != "" for a in sx["attrs"]))
    fruity_attr = next((a for a in sx["attrs"] if a["key"] == "fruity"), None) or next((a for a in sx["attrs"] if a["group"] == "positive"), None)
    return meds, filled, (meds.get(fruity_attr["key"]) if fruity_attr else None)


def _save_sensory(campaign, cfg, s, f, store, forms, errors, reception, accredited):
    sx = s.get("sensory") or SENSORY_DEFAULT
    k = s["key"]
    grid = []
    for i in range(1, sx["max"] + 1):
        row = {a["key"]: (f.get(f"sg_{k}_{i}_{a['key']}") or "").strip() for a in sx["attrs"]}
        row["descriptor"] = (f.get(f"sg_{k}_{i}_descriptor") or "").strip()
        grid.append(row)
    sval = {n: (f.get(f"sp_{k}_{n}") or "").strip() for n in
            ("panel", "leader", "recognised", "date", "comments", "classification", "fruity_type", "defect_type", "descriptor")}
    sval["grid"] = grid
    store["cells"]["sensory:" + k] = sval
    try:
        meds, filled, fruity = sensory_stats(sx, grid)
    except ValueError as exc:
        errors.append(f"{s['title']} : {exc}")
        return 0
    if filled == 0 or fruity is None:
        if filled:
            errors.append(f"{s['title']} : renseignez au moins l'intensité « Fruity »")
        return 0
    if filled < sx["min"]:
        flash(f"{s['title']} : {filled} dégustateur(s) saisi(s), le minimum est {sx['min']}.", "warning")
    defects = [a for a in sx["attrs"] if a["group"] == "defect"]
    chosen = next((a for a in defects if a["label"].lower() == sval["defect_type"].lower()), None)
    if chosen is not None:
        defect = meds.get(chosen["key"]) or 0.0
    else:
        defect = max([meds.get(a["key"]) or 0.0 for a in defects] or [0.0])
    if sval["classification"]:
        category = sval["classification"]
    else:
        category = "Extra Virgin" if (defect == 0 and fruity > 0) else ("Virgin" if (0 < defect <= 3.5 and fruity > 0) else "Lampante")
    n = 0
    for t in trials_of(cfg, campaign, k):
        form = forms.get(t.id)
        out = _sensory_output(t)
        if form is None or out is None:
            continue
        fields = {"value": None, "replicate_2": None, "qualitative_result": None, "solidite_data": None,
                  "uncertainty": None, "remarks": sval["comments"] or None, "conform": None, "anomaly": False, "z": None, "en": None}
        if out == "class":
            fields["qualitative_result"] = category
            if t.expected_qualitative_result:
                fields["conform"] = category.strip().lower() == t.expected_qualitative_result.strip().lower()
                fields["anomaly"] = fields["conform"] is False
        else:
            value = fruity if out == "fruity" else defect
            fields["value"] = value
            fields["z"], fields["anomaly"] = compute_z_score(value, t.target_value, t.target_sd)
        _upsert(form, t, fields, {"sensory": {"medians": meds, "tasters": filled, "category": category}}, reception, accredited)
        n += 1
    return n


def _sensory_output(trial):
    p = f"{trial.parameter or ''} {trial.name}".lower()
    if "fruit" in p:
        return "fruity"
    if "defect" in p:
        return "defect"
    if "categ" in p or "class" in p:
        return "class"
    return None
