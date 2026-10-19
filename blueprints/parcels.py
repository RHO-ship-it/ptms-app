"""Colis : un colis par laboratoire et par campagne (et non plus un suivi par paramètre).

Le colis porte le suivi logistique (transporteur, n° de suivi, QR code), la réception et la liste des
échantillons reçus, cochés essai par essai. Les formulaires (ParticipantForm) restent synchronisés avec le
colis afin que les écrans existants (suivi, rapports…) continuent de fonctionner.
"""
import json
import re
import secrets
from datetime import datetime

from flask import Blueprint, render_template, request, flash, redirect, url_for, abort
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, Trial, ParticipantForm, Parcel, Laboratory
from utils import log_action
from blueprints.auth import roles_required

parcels_bp = Blueprint("parcels", __name__, template_folder="../templates/parcels")


# ───────────────────────── Outils ─────────────────────────

def group_key(trial):
    """Un « essai » du colis = même nom d'essai et même échantillon (plusieurs paramètres partagent un échantillon)."""
    return re.sub(r"[^a-z0-9]+", "-", (trial.name or "").lower()).strip("-")[:40] + "|" + (trial.sample_reference or "")


def lab_forms(campaign_id, lab_id):
    return (ParticipantForm.query.join(Trial)
            .filter(Trial.campaign_id == campaign_id, ParticipantForm.laboratory_id == lab_id)
            .order_by(Trial.id).all())


def get_parcel(campaign_id, lab_id, create=True):
    """Colis du laboratoire pour la campagne ; créé à la demande à partir des anciennes données par paramètre."""
    parcel = Parcel.query.filter_by(campaign_id=campaign_id, laboratory_id=lab_id).first()
    if parcel or not create:
        return parcel
    forms = lab_forms(campaign_id, lab_id)
    if not forms:
        return None

    def pick(attr):
        return next((getattr(f, attr) for f in forms if getattr(f, attr)), None)

    received = [f for f in forms if f.received]
    parcel = Parcel(campaign_id=campaign_id, laboratory_id=lab_id, carrier=pick("carrier"),
                    tracking_number=pick("tracking_number"), dispatch_date=pick("dispatch_date"),
                    dispatch_confirmed=any(f.dispatch_confirmed for f in forms),
                    received=bool(received),
                    reception_date=min((f.reception_date for f in received if f.reception_date), default=None),
                    qr_token=pick("qr_token") or secrets.token_hex(20), items="{}")
    db.session.add(parcel)
    db.session.commit()
    return parcel


def mirror_to_forms(parcel):
    """Recopie l'état du colis sur tous ses formulaires (les écrans historiques lisent ces champs)."""
    for f in lab_forms(parcel.campaign_id, parcel.laboratory_id):
        f.carrier, f.tracking_number, f.dispatch_date = parcel.carrier, parcel.tracking_number, parcel.dispatch_date
        f.dispatch_confirmed = parcel.dispatch_confirmed
        f.received, f.reception_date = parcel.received, parcel.reception_date


def sync_from_form(form):
    """Un formulaire a été modifié par l'ancien écran de suivi : le colis reprend ses valeurs."""
    parcel = get_parcel(form.trial.campaign_id, form.laboratory_id)
    if parcel is None:
        return None
    parcel.carrier, parcel.tracking_number, parcel.dispatch_date = form.carrier, form.tracking_number, form.dispatch_date
    parcel.dispatch_confirmed = bool(form.dispatch_confirmed or parcel.dispatch_confirmed)
    if form.received and not parcel.received:
        parcel.received, parcel.reception_date = True, form.reception_date or datetime.utcnow()
    mirror_to_forms(parcel)
    db.session.commit()
    return parcel


def receive_parcel(parcel, who):
    parcel.received = True
    parcel.reception_date = datetime.utcnow()
    parcel.received_by = who
    mirror_to_forms(parcel)
    db.session.commit()
    log_action("confirm_reception", entity_type="Parcel", entity_id=parcel.id,
               details=f"Réception du colis {parcel.campaign.reference} / {parcel.laboratory.code} confirmée ({who})")


def find_parcel_by_token(token):
    parcel = Parcel.query.filter_by(qr_token=token).first()
    if parcel:
        return parcel
    form = ParticipantForm.query.filter_by(qr_token=token).first()      # anciennes étiquettes (une par paramètre)
    return get_parcel(form.trial.campaign_id, form.laboratory_id) if form else None


def trial_groups(forms):
    """Essais du colis (regroupés par essai + échantillon) avec l'avancement des résultats."""
    groups = {}
    for f in forms:
        t = f.trial
        g = groups.setdefault(group_key(t), {"key": group_key(t), "name": t.name, "sample": t.sample_reference or "",
                                              "sample_code": "", "forms": [], "first": t.id})
        g["forms"].append(f)
        if f.sample_code and not g["sample_code"]:
            g["sample_code"] = f.sample_code
    out = sorted(groups.values(), key=lambda g: g["first"])
    for g in out:
        subs = [f.submission for f in g["forms"] if f.submission]
        g["n"], g["n_sub"] = len(g["forms"]), len(subs)
        g["n_conf"] = sum(1 for s in subs if s.confirmed)
        g["n_val"] = sum(1 for f in g["forms"] if f.status == "validated")
    return out


def parcel_state(parcel, forms):
    subs = [f.submission for f in forms if f.submission]
    return {"shipped": bool(parcel.tracking_number or parcel.dispatch_confirmed), "received": parcel.received,
            "submitted": bool(subs), "confirmed": bool(subs) and all(s.confirmed for s in subs),
            "validated": bool(forms) and all(f.status == "validated" for f in forms)}


def _can_access(parcel):
    return current_user.is_staff() or current_user.laboratory_id == parcel.laboratory_id


# ───────────────────────── Côté laboratoire ─────────────────────────

@parcels_bp.route("/<int:parcel_id>/receive", methods=["POST"])
@login_required
def receive(parcel_id):
    parcel = Parcel.query.get_or_404(parcel_id)
    if not _can_access(parcel):
        abort(403)
    if not parcel.received:
        receive_parcel(parcel, current_user.full_name or current_user.username)
        flash(f"Réception du colis enregistrée le {parcel.reception_date.strftime('%d/%m/%Y à %H:%M')}. "
              "Cochez maintenant les échantillons reçus.", "success")
    return redirect(request.referrer or url_for("participants.portal", campaign_id=parcel.campaign_id))


@parcels_bp.route("/<int:parcel_id>/samples", methods=["POST"])
@login_required
def samples(parcel_id):
    parcel = Parcel.query.get_or_404(parcel_id)
    if not _can_access(parcel):
        abort(403)
    if not parcel.received:
        flash("Confirmez d'abord la réception du colis.", "warning")
        return redirect(url_for("participants.portal", campaign_id=parcel.campaign_id))
    groups = trial_groups(lab_forms(parcel.campaign_id, parcel.laboratory_id))
    valid = {g["key"] for g in groups}
    checked = {k for k in request.form.getlist("got") if k in valid}
    old = parcel.items_dict()
    now = datetime.utcnow().isoformat(timespec="minutes")
    parcel.items = json.dumps({k: {"received": True, "at": old.get(k, {}).get("at", now)} for k in checked})
    db.session.commit()
    log_action("check_samples", entity_type="Parcel", entity_id=parcel.id,
               details=f"{len(checked)}/{len(valid)} échantillon(s) cochés reçus par {current_user.username}")
    flash(f"Échantillons reçus enregistrés : {len(checked)} / {len(valid)}."
          + ("" if len(checked) == len(valid) else " Signalez à TTS tout échantillon manquant ou endommagé."),
          "success" if len(checked) == len(valid) else "warning")
    return redirect(url_for("participants.portal", campaign_id=parcel.campaign_id))


@parcels_bp.route("/<int:parcel_id>/label")
@login_required
def label(parcel_id):
    parcel = Parcel.query.get_or_404(parcel_id)
    if not _can_access(parcel):
        abort(403)
    if not parcel.qr_token:
        parcel.qr_token = secrets.token_hex(20)
        db.session.commit()
    groups = trial_groups(lab_forms(parcel.campaign_id, parcel.laboratory_id))
    dues = [g["forms"][0].trial.due_date for g in groups if g["forms"][0].trial.due_date]
    return render_template("parcels/label.html", parcel=parcel, groups=groups, due=min(dues) if dues else None,
                           scan_url=url_for("participants.scan_qr", qr_token=parcel.qr_token, _external=True))


# ───────────────────────── Côté administration ─────────────────────────

@parcels_bp.route("/campaign/<int:campaign_id>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def admin(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    lab_ids = [row[0] for row in db.session.query(ParticipantForm.laboratory_id).join(Trial)
               .filter(Trial.campaign_id == campaign.id).distinct()]
    rows = []
    for lab in Laboratory.query.filter(Laboratory.id.in_(lab_ids)).order_by(Laboratory.code).all():
        parcel = get_parcel(campaign.id, lab.id)
        groups = trial_groups(lab_forms(campaign.id, lab.id))
        items = parcel.items_dict()
        rows.append({"lab": lab, "parcel": parcel, "n_groups": len(groups),
                     "n_checked": sum(1 for g in groups if g["key"] in items)})
    return render_template("parcels/admin.html", campaign=campaign, rows=rows)


@parcels_bp.route("/<int:parcel_id>/update", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def update(parcel_id):
    parcel = Parcel.query.get_or_404(parcel_id)
    f = request.form
    parcel.carrier = (f.get("carrier") or "").strip() or None
    parcel.tracking_number = (f.get("tracking_number") or "").strip() or None
    qty = (f.get("sample_quantity") or "").strip()
    parcel.sample_quantity = int(qty) if qty.isdigit() else None
    try:
        parcel.dispatch_date = datetime.strptime(f.get("dispatch_date", ""), "%Y-%m-%d").date() if f.get("dispatch_date") else None
    except ValueError:
        flash("Date d'expédition invalide.", "danger")
        return redirect(request.referrer or url_for("parcels.admin", campaign_id=parcel.campaign_id))
    parcel.dispatch_confirmed = f.get("dispatch_confirmed") == "1" or bool(parcel.tracking_number and f.get("dispatch_confirmed") is None and parcel.dispatch_confirmed)
    if f.get("received") == "1" and not parcel.received:
        parcel.received, parcel.reception_date, parcel.received_by = True, datetime.utcnow(), current_user.full_name
    mirror_to_forms(parcel)
    db.session.commit()
    log_action("update_shipping", entity_type="Parcel", entity_id=parcel.id,
               details=f"Colis {parcel.campaign.reference} / {parcel.laboratory.code} (suivi : {parcel.tracking_number})")
    flash(f"Colis de {parcel.laboratory.code} mis à jour.", "success")
    return redirect(request.referrer or url_for("parcels.admin", campaign_id=parcel.campaign_id))
