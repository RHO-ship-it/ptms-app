import io
import os
from datetime import datetime

from flask import Blueprint, render_template, request, flash, redirect, url_for, send_file
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, CampaignLaboratory
from werkzeug.utils import secure_filename
from sqlalchemy import func
from sqlalchemy.orm import defer
from models import PortalDocument
from utils import log_action
from blueprints.auth import roles_required

catalogue_bp = Blueprint("catalogue", __name__, template_folder="../templates/catalogue")

# Campagnes réservables : en préparation ou en cours (pas clôturées ni archivées).
OPEN_STATUSES = ("draft", "active")

# Rubriques auxquelles l'équipe peut joindre des PDF.
DOC_KINDS = {"catalogue": "Catalogue général", "registration": "Registration sheets",
             "instruction": "Instruction sheets",
             "protocol": "Protocole statistique", "terms": "Conditions générales de vente"}

# Rubriques classées par année : un jeu de documents par année.
YEARLY_KINDS = ("catalogue", "registration", "instruction")

# (rubrique, libellé, base du nom de fichier) : un exemplaire par année.
YEARLY_DOCS = [
    ("catalogue", "Catalogue général", "Catalogue_general_TTS_PT"),
    ("registration", "Registration sheet – Textile", "Registration_sheet_Textile"),
    ("registration", "Registration sheet – Cuir", "Registration_sheet_Cuir"),
    ("registration", "Registration sheet – Huile d'olive", "Registration_sheet_Huile_olive"),
    ("instruction", "Instruction sheet – Textile", "Instruction_sheet_Textile"),
    ("instruction", "Instruction sheet – Cuir", "Instruction_sheet_Cuir"),
    ("instruction", "Instruction sheet – Huile d'olive", "Instruction_sheet_Huile_olive"),
]

# Documents permanents (sans année).
FIXED_DOCS = [
    {"filename": "protocole_PT.pdf", "kind": "protocol", "year": None,
     "title": "Proficiency Testing General Protocol (ITE 001)"},
    {"filename": "Condition_générale_de_vente_TTS_PT.pdf", "kind": "terms", "year": None,
     "title": "Conditions générales de vente (FR)"},
    {"filename": "Terms_and_condition_of_sales_TTS_PT.pdf", "kind": "terms", "year": None,
     "title": "Terms and conditions of sale (EN)"},
]


def yearly_docs(year):
    """Documents attendus pour une année donnée (nom de fichier et titre portent l'année)."""
    return [{"filename": f"{base}_{year}.pdf", "kind": kind, "title": f"{label} {year}", "year": year}
            for kind, label, base in YEARLY_DOCS]


def _valid_year(value):
    return value if value and 2000 <= value <= 2100 else None


def _years(extra=None):
    """Années proposées : année en cours, suivante, celles déjà utilisées, et celle demandée."""
    current = datetime.utcnow().year
    years = {current, current + 1}
    years |= {y for (y,) in db.session.query(PortalDocument.year).filter(PortalDocument.year.isnot(None)).distinct()}
    if extra:
        years.add(extra)
    return sorted(years, reverse=True)


def seed_documents():
    """Charge en base les PDF présents dans documents/ (à la racine du dépôt) s'ils manquent.
    Nom attendu : le nom du fichier sans accent (ex. Catalogue_general_TTS_PT_2027.pdf)."""
    folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "documents")
    if not os.path.isdir(folder):
        return
    current = datetime.utcnow().year
    specs = list(FIXED_DOCS)
    for year in range(2024, current + 4):
        specs += yearly_docs(year)
    for exp in specs:
        path = os.path.join(folder, secure_filename(exp["filename"]))
        if not os.path.isfile(path):
            continue
        if PortalDocument.query.filter_by(kind=exp["kind"], title=exp["title"]).first():
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        if not data.startswith(b"%PDF-"):
            continue
        db.session.add(PortalDocument(kind=exp["kind"], title=exp["title"], filename=exp["filename"],
                                      size=len(data), data=data, year=exp["year"]))
    db.session.commit()


def _documents(kind):
    """Documents d'une rubrique (année la plus récente d'abord), sans charger leur contenu."""
    return (PortalDocument.query.options(defer(PortalDocument.data)).filter_by(kind=kind)
            .order_by(func.coalesce(PortalDocument.year, 0).desc(), PortalDocument.title.asc()).all())


@catalogue_bp.route("/")
@login_required
@roles_required("participant")
def index():
    """Catalogue des essais d'aptitude : le participant consulte les campagnes ouvertes."""
    lab = current_user.laboratory
    enrolled_ids = set()
    if lab is not None:
        enrolled_ids = {cl.campaign_id for cl in CampaignLaboratory.query.filter_by(laboratory_id=lab.id)}

    items = []
    for campaign in (Campaign.query.filter(Campaign.status.in_(OPEN_STATUSES))
                     .order_by(Campaign.created_at.desc()).all()):
        items.append({"campaign": campaign, "state": "enrolled" if campaign.id in enrolled_ids else "open"})
    return render_template("catalogue/index.html", lab=lab, items=items,
                           documents=_documents("catalogue"), registrations=_documents("registration"),
                           instructions=_documents("instruction"), terms=_documents("terms"))


@catalogue_bp.route("/protocol")
@login_required
def protocol():
    """Protocole statistique : méthodes appliquées par PTMS."""
    return render_template("catalogue/protocol.html", documents=_documents("protocol"))


# ═════════════════════════════════════════════════════════════════════════════
#  DOCUMENTS PDF (catalogue et protocole) : joints par l'équipe, lus par tous
# ═════════════════════════════════════════════════════════════════════════════

@catalogue_bp.route("/documents")
@login_required
@roles_required("admin", "chef_technique")
def documents():
    """Page « Documents » : documents de l'année choisie, documents permanents, autres PDF."""
    year = _valid_year(request.args.get("year", type=int)) or datetime.utcnow().year
    years = _years(year)

    def build(specs):
        rows = []
        for exp in specs:
            doc = (PortalDocument.query.options(defer(PortalDocument.data))
                   .filter_by(kind=exp["kind"], title=exp["title"]).first())
            rows.append({**exp, "label": DOC_KINDS[exp["kind"]], "doc": doc})
        return rows

    known = {(e["kind"], e["title"]) for y in years for e in yearly_docs(y)}
    known |= {(e["kind"], e["title"]) for e in FIXED_DOCS}
    others = [d for kind in DOC_KINDS for d in _documents(kind) if (d.kind, d.title) not in known]
    return render_template("catalogue/documents.html", year=year, years=years, kinds=DOC_KINDS,
                           rows_year=build(yearly_docs(year)), rows_fixed=build(FIXED_DOCS), others=others)


@catalogue_bp.route("/documents/upload", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def upload_document():
    year = _valid_year(request.form.get("year", type=int))
    back = redirect(url_for("catalogue.documents", year=year))
    kind = request.form.get("kind")
    if kind not in DOC_KINDS:
        flash("Rubrique inconnue.", "danger")
        return back
    file = request.files.get("file")
    if file is None or not file.filename:
        flash("Choisissez un fichier PDF à joindre.", "warning")
        return back
    data = file.read()
    # Extension ET signature : un fichier renommé en .pdf est refusé.
    if not file.filename.lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
        flash("Le fichier doit être un PDF valide.", "danger")
        return back

    # Ligne « attendue » : titre, nom affiché et année sont imposés, l'ancien fichier est remplacé.
    candidates = (yearly_docs(year) if year else []) + FIXED_DOCS
    slot = next((e for e in candidates
                 if e["filename"] == request.form.get("slot") and e["kind"] == kind), None)
    if slot:
        title, safe_name, doc_year = slot["title"], slot["filename"], slot["year"]
        for old in PortalDocument.query.filter_by(kind=kind, title=title).all():
            db.session.delete(old)
    else:
        stem = os.path.splitext(file.filename)[0].strip()
        title = (request.form.get("title") or "").strip()[:200] or stem[:200] or "Document"
        safe_name = secure_filename(file.filename) or "document.pdf"
        doc_year = year if kind in YEARLY_KINDS else None
    doc = PortalDocument(kind=kind, title=title, filename=safe_name, size=len(data),
                         data=data, uploaded_by=current_user.id, year=doc_year)
    db.session.add(doc)
    db.session.commit()
    log_action("create", "PortalDocument", doc.id, f"PDF « {title} » ajouté à : {DOC_KINDS[kind]}")
    flash(f"« {title} » est maintenant visible par les participants.", "success")
    return back


@catalogue_bp.route("/documents/<int:doc_id>")
@login_required
def document(doc_id):
    """Ouvre (ou télécharge avec ?download=1) un PDF. Réservé aux utilisateurs connectés."""
    doc = PortalDocument.query.get_or_404(doc_id)
    response = send_file(io.BytesIO(doc.data), mimetype="application/pdf",
                         as_attachment=bool(request.args.get("download")), download_name=doc.filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@catalogue_bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def delete_document(doc_id):
    doc = PortalDocument.query.get_or_404(doc_id)
    title = doc.title
    db.session.delete(doc)
    db.session.commit()
    log_action("delete", "PortalDocument", doc_id, f"PDF « {title} » retiré")
    flash(f"« {title} » a été retiré.", "success")
    return redirect(url_for("catalogue.documents"))
