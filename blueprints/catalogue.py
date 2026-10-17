from datetime import datetime

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, CampaignLaboratory, ParticipantForm, EnrollmentRequest, User
from utils import notify, notify_role, log_action
from blueprints.auth import roles_required

catalogue_bp = Blueprint("catalogue", __name__, template_folder="../templates/catalogue")

# Campagnes réservables : en préparation ou en cours (pas clôturées ni archivées).
OPEN_STATUSES = ("draft", "active")


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
    return render_template("catalogue/index.html", lab=lab, items=items)


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
                    url_for("catalogue.requests"))
    flash("Votre demande de réservation a été envoyée. Vous serez notifié dès qu'elle sera traitée.", "success")
    return redirect(url_for("catalogue.index"))


@catalogue_bp.route("/requests")
@login_required
@roles_required("admin", "chef_technique")
def requests():
    """Demandes de réservation à traiter (personnel)."""
    pending = (EnrollmentRequest.query.filter_by(status="pending")
               .order_by(EnrollmentRequest.created_at.asc()).all())
    decided = (EnrollmentRequest.query.filter(EnrollmentRequest.status != "pending")
               .order_by(EnrollmentRequest.decided_at.desc()).limit(20).all())
    return render_template("catalogue/requests.html", pending=pending, decided=decided)


@catalogue_bp.route("/requests/<int:req_id>/<decision>", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def decide(req_id, decision):
    """Accepte ou refuse une demande. L'acceptation inscrit le laboratoire et crée
    ses formulaires pour tous les essais de la campagne (comme « Assigner des labos »)."""
    req = EnrollmentRequest.query.get_or_404(req_id)
    if decision not in ("approve", "reject"):
        flash("Décision inconnue.", "danger")
        return redirect(url_for("catalogue.requests"))
    if req.status != "pending":
        flash("Cette demande a déjà été traitée.", "info")
        return redirect(url_for("catalogue.requests"))

    campaign, lab = req.campaign, req.laboratory
    note = (request.form.get("note") or "").strip()[:300] or None
    if decision == "approve":
        if not CampaignLaboratory.query.filter_by(campaign_id=campaign.id, laboratory_id=lab.id).first():
            db.session.add(CampaignLaboratory(campaign_id=campaign.id, laboratory_id=lab.id))
            db.session.flush()
            for trial in campaign.trials:
                if not ParticipantForm.query.filter_by(trial_id=trial.id, laboratory_id=lab.id).first():
                    db.session.add(ParticipantForm(trial_id=trial.id, laboratory_id=lab.id, status="pending"))
        req.status = "approved"
        text = f"Votre réservation pour {campaign.reference} est acceptée : vos formulaires sont disponibles."
        link = url_for("participants.portal")
    else:
        req.status = "rejected"
        text = f"Votre réservation pour {campaign.reference} n'a pas pu être acceptée."
        if note:
            text += f" Motif : {note}"
        link = url_for("catalogue.index")
    req.decision_note, req.decided_at, req.decided_by = note, datetime.utcnow(), current_user.id
    db.session.commit()
    log_action(decision, "EnrollmentRequest", req.id, f"{campaign.reference} / {lab.code}")

    for user in User.query.filter_by(laboratory_id=lab.id, role="participant", active=True).all():
        notify(user.id, text[:300], link)
    flash("Demande acceptée : le laboratoire est inscrit." if decision == "approve" else "Demande refusée.", "success")
    return redirect(url_for("catalogue.requests"))


@catalogue_bp.route("/protocol")
@login_required
def protocol():
    """Protocole statistique : méthodes appliquées par PTMS."""
    return render_template("catalogue/protocol.html")
