import io
import os
from datetime import datetime

from flask import Blueprint, render_template, request, flash, redirect, url_for, send_file
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, CampaignLaboratory, ParticipantForm, EnrollmentRequest, User
from werkzeug.utils import secure_filename
from sqlalchemy.orm import defer
from models import PortalDocument
from utils import notify, notify_role, log_action
from blueprints.auth import roles_required

catalogue_bp = Blueprint("catalogue", __name__, template_folder="../templates/catalogue")

# Campagnes réservables : en préparation ou en cours (pas clôturées ni archivées).
OPEN_STATUSES = ("draft", "active")

# Rubriques auxquelles l'équipe peut joindre des PDF.
DOC_KINDS = {"catalogue": "Catalogue des essais d'aptitude", "protocol": "Protocole statistique",
             "terms": "Conditions générales de vente"}


# Documents attendus dans la page « Documents » (fichier affiché, rubrique, titre).
EXPECTED_DOCS = [
    {"filename": "protocole_PT.pdf", "kind": "protocol",
     "title": "Proficiency Testing General Protocol (ITE 001)"},
    {"filename": "Condition_générale_de_vente_TTS_PT.pdf", "kind": "terms",
     "title": "Conditions générales de vente (FR)"},
    {"filename": "Terms_and_condition_of_sales_TTS_PT.pdf", "kind": "terms",
     "title": "Terms and conditions of sale (EN)"},
]

def _documents(kind):
    """Documents d'une rubrique, sans charger leur contenu."""
    return (PortalDocument.query.options(defer(PortalDocument.data))
            .filter_by(kind=kind).order_by(PortalDocument.uploaded_at.desc()).all())


@catalogue_bp.route("/")
@login_required
@roles_required("participant")
def index():
    """Catalogue des essais d'aptitude : le participant voit ce qu'il peut réserver."""
    lab = current_user.laboratory
    enrolled_ids, requests_by_campaign = set(), {}
    if lab is not None:
        enrolled_ids = {cl.campaign_id for cl in CampaignLaboratory.query.filter_by(laboratory_id=lab.id)}
        for req in (EnrollmentRequest.query.filter_by(laboratory_id=lab.id)
                    .order_by(EnrollmentRequest.created_at.asc()).all()):
            requests_by_campaign[req.campaign_id] = req  # la plus récente l'emporte

    items = []
    for campaign in (Campaign.query.filter(Campaign.status.in_(OPEN_STATUSES))
                     .order_by(Campaign.created_at.desc()).all()):
        req = requests_by_campaign.get(campaign.id)
        if campaign.id in enrolled_ids:
            state = "enrolled"
        elif req is not None and req.status == "pending":
            state = "pending"
        else:
            state = "open"
        items.append({"campaign": campaign, "state": state, "rejected": bool(req and req.status == "rejected"),
                      "note": req.decision_note if req and req.status == "rejected" else None})
    return render_template("catalogue/index.html", lab=lab, items=items,
                           documents=_documents("catalogue"), terms=_documents("terms"))


@catalogue_bp.route("/<int:campaign_id>/book", methods=["POST"])
@login_required
@roles_required("participant")
def book(campaign_id):
    """Réserve un essai : crée une demande que l'équipe valide ensuite."""
    lab = current_user.laboratory
    campaign = Campaign.query.get_or_404(campaign_id)
    if lab is None:
        flash("Votre compte n'est rattaché à aucun laboratoire.", "warning")
        return redirect(url_for("catalogue.index"))
    if campaign.status not in OPEN_STATUSES:
        flash("Cette campagne n'est plus ouverte aux réservations.", "warning")
        return redirect(url_for("catalogue.index"))
    if CampaignLaboratory.query.filter_by(campaign_id=campaign.id, laboratory_id=lab.id).first():
        flash("Votre laboratoire participe déjà à cette campagne.", "info")
        return redirect(url_for("catalogue.index"))
    if EnrollmentRequest.query.filter_by(campaign_id=campaign.id, laboratory_id=lab.id, status="pending").first():
        flash("Une demande est déjà en attente pour cette campagne.", "info")
        return redirect(url_for("catalogue.index"))

    req = EnrollmentRequest(campaign_id=campaign.id, laboratory_id=lab.id, requested_by=current_user.id,
                            message=(request.form.get("message") or "").strip()[:300] or None)
    db.session.add(req)
    db.session.commit()
    log_action("create", "EnrollmentRequest", req.id, f"Réservation {campaign.reference} par {lab.code}")
    for role in ("admin", "chef_technique"):
        notify_role(role, f"Nouvelle demande de réservation : {lab.code} pour {campaign.reference}.",
                    url_for("dashboard.index"))
    flash("Votre demande de réservation a été envoyée. Vous serez notifié dès qu'elle sera traitée.", "success")
    return redirect(url_for("catalogue.index"))


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
