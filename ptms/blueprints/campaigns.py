from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, Trial, CampaignLaboratory, ParticipantForm, Laboratory
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


@campaigns_bp.route("/<int:campaign_id>/publish-report", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def publish_report(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    if campaign.status != "closed":
        flash("La campagne doit être clôturée avant de publier le rapport.", "warning")
        return redirect(url_for("campaigns.view_campaign", campaign_id=campaign.id))
        
    campaign.report_published = True
    db.session.commit()
    log_action("publish", "Campaign", campaign.id, f"Publication du rapport pour {campaign.reference}")
    notify_role("participant", f"Le rapport final de la campagne {campaign.reference} est disponible !",
                link=url_for("participants.portal"))
    flash("Rapport final publié. Les participants ont été notifiés et peuvent voir leurs évaluations.", "success")
    return redirect(url_for("campaigns.view_campaign", campaign_id=campaign.id))


@campaigns_bp.route("/<int:campaign_id>/dashboard")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def campaign_dashboard(campaign_id):
    from datetime import date
    campaign = Campaign.query.get_or_404(campaign_id)
    
    # Get all forms for this campaign
    forms = ParticipantForm.query.join(Trial).filter(Trial.campaign_id == campaign_id).all()
    
    today = date.today()
    kpis = {
        "nb_labs": len(campaign.laboratories),
        "nb_trials": len(campaign.trials),
        "total_forms": len(forms),
        "submitted": sum(1 for f in forms if f.status != "pending"),
        "validated": sum(1 for f in forms if f.status == "validated"),
        "anomalies": sum(1 for f in forms if f.status == "anomaly"),
        "late": sum(1 for f in forms if f.status == "pending" and f.trial.due_date and f.trial.due_date < today),
        "pending": sum(1 for f in forms if f.status == "pending")
    }
    
    # Progression %
    kpis["progress_pct"] = round((kpis["submitted"] / kpis["total_forms"]) * 100) if kpis["total_forms"] > 0 else 0
    
    return render_template("campaigns/dashboard.html", campaign=campaign, kpis=kpis, forms=forms)


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


@campaigns_bp.route("/<int:campaign_id>/assign-labs", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def assign_labs(campaign_id):
    """Gérer quels laboratoires participent à quels essais dans cette campagne."""
    campaign = Campaign.query.get_or_404(campaign_id)
    all_labs = Laboratory.query.filter_by(active=True).order_by(Laboratory.code).all()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_lab":
            lab_id = request.form.get("lab_id", type=int)
            if not lab_id:
                flash("Veuillez sélectionner un laboratoire.", "warning")
                return redirect(url_for("campaigns.assign_labs", campaign_id=campaign_id))
            # Attach lab to campaign if not already
            existing_link = CampaignLaboratory.query.filter_by(
                campaign_id=campaign_id, laboratory_id=lab_id
            ).first()
            if not existing_link:
                db.session.add(CampaignLaboratory(campaign_id=campaign_id, laboratory_id=lab_id))
                db.session.flush()

                # Auto-créer les formulaires pour TOUS les essais de la campagne
                for trial in campaign.trials:
                    already = ParticipantForm.query.filter_by(
                        trial_id=trial.id, laboratory_id=lab_id
                    ).first()
                    if not already:
                        db.session.add(ParticipantForm(
                            trial_id=trial.id,
                            laboratory_id=lab_id,
                            status="pending"
                        ))
                db.session.commit()
                log_action("assign", "CampaignLaboratory", campaign_id,
                           f"Laboratoire {lab_id} ajouté à la campagne {campaign.reference} ({len(campaign.trials)} essais)")
                flash(f"Laboratoire ajouté avec {len(campaign.trials)} essai(s) pré-sélectionné(s). Décochez ceux qu'il ne doit pas réaliser.", "success")
            return redirect(url_for("campaigns.assign_labs", campaign_id=campaign_id))

        elif action == "save_trials":
            # For each lab in the campaign, sync ParticipantForms based on selected trials
            for cl in campaign.laboratories:
                lab_id = cl.laboratory_id
                selected_trial_ids = request.form.getlist(f"trials_{lab_id}", type=int)

                # Get all existing forms for this lab in this campaign
                existing_forms = (
                    ParticipantForm.query
                    .join(Trial)
                    .filter(Trial.campaign_id == campaign_id,
                            ParticipantForm.laboratory_id == lab_id)
                    .all()
                )
                existing_trial_ids = {f.trial_id for f in existing_forms}

                # Add missing forms
                for trial_id in selected_trial_ids:
                    if trial_id not in existing_trial_ids:
                        db.session.add(ParticipantForm(
                            trial_id=trial_id,
                            laboratory_id=lab_id,
                            status="pending"
                        ))

                # Remove deselected forms (only if not yet submitted)
                for f in existing_forms:
                    if f.trial_id not in selected_trial_ids:
                        if f.status == "pending" and not f.submission:
                            db.session.delete(f)

            db.session.commit()
            log_action("assign_trials", "Campaign", campaign_id,
                       f"Participations aux essais mises à jour pour {campaign.reference}")
            flash("Les participations aux essais ont été mises à jour avec succès.", "success")
            return redirect(url_for("campaigns.view_campaign", campaign_id=campaign_id))

        elif action == "remove_lab":
            lab_id = request.form.get("lab_id", type=int)
            cl = CampaignLaboratory.query.filter_by(
                campaign_id=campaign_id, laboratory_id=lab_id
            ).first()
            if cl:
                # Remove only pending forms for this lab in this campaign
                pending_forms = (
                    ParticipantForm.query
                    .join(Trial)
                    .filter(Trial.campaign_id == campaign_id,
                            ParticipantForm.laboratory_id == lab_id,
                            ParticipantForm.status == "pending")
                    .all()
                )
                for f in pending_forms:
                    if not f.submission:
                        db.session.delete(f)
                db.session.delete(cl)
                db.session.commit()
                flash("Laboratoire retiré de la campagne.", "info")
            return redirect(url_for("campaigns.assign_labs", campaign_id=campaign_id))

    # Build current assignment map: {lab_id: set of trial_ids}
    assignment_map = {}
    for cl in campaign.laboratories:
        lab_forms = (
            ParticipantForm.query
            .join(Trial)
            .filter(Trial.campaign_id == campaign_id,
                    ParticipantForm.laboratory_id == cl.laboratory_id)
            .all()
        )
        assignment_map[cl.laboratory_id] = [f.trial_id for f in lab_forms]

    # Labs not yet in campaign
    assigned_lab_ids = {cl.laboratory_id for cl in campaign.laboratories}
    unassigned_labs = [lab for lab in all_labs if lab.id not in assigned_lab_ids]

    return render_template(
        "campaigns/assign_labs.html",
        campaign=campaign,
        all_labs=all_labs,
        unassigned_labs=unassigned_labs,
        assignment_map=assignment_map,
    )
