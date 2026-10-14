from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, Trial, CampaignLaboratory, ParticipantForm
from utils import log_action, notify_role
from blueprints.auth import roles_required

campaigns_bp = Blueprint("campaigns", __name__, template_folder="../templates/campaigns")


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


@campaigns_bp.route("/")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def list_campaigns():
    status = request.args.get("status", "")
    query = Campaign.query
    if status:
        query = query.filter_by(status=status)
    campaigns = query.order_by(Campaign.created_at.desc()).all()
    return render_template("campaigns/list.html", campaigns=campaigns, status=status)


@campaigns_bp.route("/new", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def new_campaign():
    if request.method == "POST":
        campaign = Campaign(
            reference=request.form["reference"].strip(),
            name=request.form["name"].strip(),
            description=request.form.get("description", "").strip(),
            status="draft",
            start_date=_parse_date(request.form.get("start_date")),
            end_date=_parse_date(request.form.get("end_date")),
            created_by=current_user.id,
        )
        db.session.add(campaign)
        db.session.commit()
        log_action("create", "Campaign", campaign.id, f"Création campagne {campaign.reference}")
        flash("Campagne créée avec succès.", "success")
        return redirect(url_for("campaigns.view_campaign", campaign_id=campaign.id))
    return render_template("campaigns/form.html", campaign=None)


@campaigns_bp.route("/<int:campaign_id>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def view_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    return render_template("campaigns/view.html", campaign=campaign)


@campaigns_bp.route("/<int:campaign_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def edit_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    if request.method == "POST":
        campaign.reference = request.form["reference"].strip()
        campaign.name = request.form["name"].strip()
        campaign.description = request.form.get("description", "").strip()
        campaign.start_date = _parse_date(request.form.get("start_date"))
        campaign.end_date = _parse_date(request.form.get("end_date"))
        db.session.commit()
        log_action("update", "Campaign", campaign.id, f"Modification campagne {campaign.reference}")
        flash("Campagne mise à jour.", "success")
        return redirect(url_for("campaigns.view_campaign", campaign_id=campaign.id))
    return render_template("campaigns/form.html", campaign=campaign)


@campaigns_bp.route("/<int:campaign_id>/activate", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def activate_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    campaign.status = "active"
    db.session.commit()
    log_action("activate", "Campaign", campaign.id, f"Activation campagne {campaign.reference}")
    notify_role("participant", f"La campagne {campaign.reference} est maintenant active.",
                link=url_for("participants.portal"))
    flash("Campagne activée. Les participants ont été notifiés.", "success")
    return redirect(url_for("campaigns.view_campaign", campaign_id=campaign.id))


@campaigns_bp.route("/<int:campaign_id>/close", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def close_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    campaign.status = "closed"
    db.session.commit()
    log_action("close", "Campaign", campaign.id, f"Clôture campagne {campaign.reference}")
    flash("Campagne clôturée.", "info")
    return redirect(url_for("campaigns.view_campaign", campaign_id=campaign.id))


@campaigns_bp.route("/<int:campaign_id>/archive", methods=["POST"])
@login_required
@roles_required("admin")
def archive_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    campaign.status = "archived"
    db.session.commit()
    log_action("archive", "Campaign", campaign.id, f"Archivage campagne {campaign.reference}")
    flash("Campagne archivée.", "info")
    return redirect(url_for("campaigns.list_campaigns"))


@campaigns_bp.route("/<int:campaign_id>/duplicate", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def duplicate_campaign(campaign_id):
    original = Campaign.query.get_or_404(campaign_id)
    new_ref = f"{original.reference}-COPIE"
    counter = 1
    while Campaign.query.filter_by(reference=new_ref).first():
        counter += 1
        new_ref = f"{original.reference}-COPIE{counter}"

    new_campaign = Campaign(
        reference=new_ref,
        name=f"{original.name} (copie)",
        description=original.description,
        status="draft",
        start_date=original.start_date,
        end_date=original.end_date,
        created_by=current_user.id,
    )
    db.session.add(new_campaign)
    db.session.commit()

    # Copier les laboratoires rattachés
    for cl in original.laboratories:
        db.session.add(CampaignLaboratory(campaign_id=new_campaign.id, laboratory_id=cl.laboratory_id))

    # Copier les essais (sans les formulaires/soumissions)
    for t in original.trials:
        db.session.add(Trial(
            campaign_id=new_campaign.id,
            name=t.name, parameter=t.parameter, unit=t.unit,
            method=t.method, target_value=t.target_value,
            target_sd=t.target_sd, due_date=t.due_date,
        ))
    db.session.commit()

    log_action("duplicate", "Campaign", new_campaign.id, f"Duplication de {original.reference} vers {new_ref}")
    flash(f"Campagne dupliquée sous la référence {new_ref}.", "success")
    return redirect(url_for("campaigns.view_campaign", campaign_id=new_campaign.id))


@campaigns_bp.route("/<int:campaign_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    ref = campaign.reference
    db.session.delete(campaign)
    db.session.commit()
    log_action("delete", "Campaign", campaign_id, f"Suppression campagne {ref}")
    flash("Campagne supprimée.", "info")
    return redirect(url_for("campaigns.list_campaigns"))
