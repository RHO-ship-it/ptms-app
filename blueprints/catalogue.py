import io
import os
from datetime import datetime

from flask import Blueprint, render_template, request, flash, redirect, url_for, send_file
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, CampaignLaboratory
from werkzeug.utils import secure_filename
from sqlalchemy.orm import defer
from models import PortalDocument
from utils import log_action
from blueprints.auth import roles_required

catalogue_bp = Blueprint("catalogue", __name__, template_folder="../templates/catalogue")

# Campagnes réservables : en préparation ou en cours (pas clôturées ni archivées).
OPEN_STATUSES = ("draft", "active")

# Rubriques auxquelles l'équipe peut joindre des PDF.
DOC_KINDS = {"catalogue": "Catalogue général", "registration": "Registration sheets",
             "protocol": "Protocole statistique", "terms": "Conditions générales de vente"}


# Documents attendus dans la page « Documents » (fichier affiché, rubrique, titre).
EXPECTED_DOCS = [
    {"filename": "Catalogue_general_TTS_PT.pdf", "kind": "catalogue",
     "title": "Catalogue général"},
    {"filename": "Registration_sheet_Textile.pdf", "kind": "registration",
     "title": "Registration sheet – Textile"},
    {"filename": "Registration_sheet_Cuir.pdf", "kind": "registration",
     "title": "Registration sheet – Cuir"},
    {"filename": "Registration_sheet_Huile_olive.pdf", "kind": "registration",
     "title": "Registration sheet – Huile d'olive"},
    {"filename": "protocole_PT.pdf", "kind": "protocol",
     "title": "Proficiency Testing General Protocol (ITE 001)"},
    {"filename": "Condition_générale_de_vente_TTS_PT.pdf", "kind": "terms",
     "title": "Conditions générales de vente (FR)"},
    {"filename": "Terms_and_condition_of_sales_TTS_PT.pdf", "kind": "terms",
     "title": "Terms and conditions of sale (EN)"},
]

def seed_documents():
    """Charge en base les PDF présents dans documents/ (à la racine du dépôt) s'ils manquent.
    Nom attendu : le nom du fichier sans accent (ex. Condition_generale_de_vente_TTS_PT.pdf)."""
    folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "documents")
    for exp in EXPECTED_DOCS:
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
                                      size=len(data), data=data))
    db.session.commit()

def _documents(kind):
    """Documents d'une rubrique, sans charger leur contenu."""
    return (PortalDocument.query.options(defer(PortalDocument.data))
            .filter_by(kind=kind).order_by(PortalDocument.uploaded_at.desc()).all())


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
                           terms=_documents("terms"))


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
    """Page « Documents » : documents attendus du portail + autres PDF joints."""
    rows, used = [], set()
    for exp in EXPECTED_DOCS:
        doc = (PortalDocument.query.options(defer(PortalDocument.data))
               .filter_by(kind=exp["kind"], title=exp["title"]).first())
        if doc:
            used.add(doc.id)
        rows.append({**exp, "label": DOC_KINDS[exp["kind"]], "doc": doc})
    others = [d for kind in DOC_KINDS for d in _documents(kind) if d.id not in used]
    return render_template("catalogue/documents.html", rows=rows, others=others, kinds=DOC_KINDS)


@catalogue_bp.route("/documents/upload", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def upload_document():
    kind = request.form.get("kind")
    if kind not in DOC_KINDS:
        flash("Rubrique inconnue.", "danger")
        return redirect(url_for("catalogue.documents"))
    file = request.files.get("file")
    if file is None or not file.filename:
        flash("Choisissez un fichier PDF à joindre.", "warning")
        return redirect(url_for("catalogue.documents"))
    data = file.read()
    # Extension ET signature : un fichier renommé en .pdf est refusé.
    if not file.filename.lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
        flash("Le fichier doit être un PDF valide.", "danger")
        return redirect(url_for("catalogue.documents"))

    # Ligne « attendue » : le titre et le nom affiché sont imposés, l'ancien fichier est remplacé.
    slot = next((e for e in EXPECTED_DOCS
                 if e["filename"] == request.form.get("slot") and e["kind"] == kind), None)
    if slot:
        title, safe_name = slot["title"], slot["filename"]
        for old in PortalDocument.query.filter_by(kind=kind, title=title).all():
            db.session.delete(old)
    else:
        stem = os.path.splitext(file.filename)[0].strip()
        title = (request.form.get("title") or "").strip()[:200] or stem[:200] or "Document"
        safe_name = secure_filename(file.filename) or "document.pdf"
    doc = PortalDocument(kind=kind, title=title, filename=safe_name, size=len(data),
                         data=data, uploaded_by=current_user.id)
    db.session.add(doc)
    db.session.commit()
    log_action("create", "PortalDocument", doc.id, f"PDF « {title} » ajouté à : {DOC_KINDS[kind]}")
    flash(f"« {title} » est maintenant visible par les participants.", "success")
    return redirect(url_for("catalogue.documents"))


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
