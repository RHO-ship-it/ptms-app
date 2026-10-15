from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import Laboratory
from utils import log_action
from blueprints.auth import roles_required

laboratories_bp = Blueprint("laboratories", __name__, template_folder="../templates/laboratories")


@laboratories_bp.route("/")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def list_labs():
    q = request.args.get("q", "").strip()
    query = Laboratory.query
    if q:
        query = query.filter(
            (Laboratory.name.ilike(f"%{q}%")) | (Laboratory.code.ilike(f"%{q}%"))
        )
    labs = query.order_by(Laboratory.code).all()
    return render_template("laboratories/list.html", labs=labs, q=q)


@laboratories_bp.route("/new", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def new_lab():
    if request.method == "POST":
        lab = Laboratory(
            code=request.form["code"].strip(),
            name=request.form["name"].strip(),
            contact_name=request.form.get("contact_name", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            address=request.form.get("address", "").strip(),
            active=True,
        )
        db.session.add(lab)
        db.session.commit()
        log_action("create", "Laboratory", lab.id, f"Création laboratoire {lab.code}")
        flash("Laboratoire créé avec succès.", "success")
        return redirect(url_for("laboratories.list_labs"))
    return render_template("laboratories/form.html", lab=None)


@laboratories_bp.route("/<int:lab_id>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def view_lab(lab_id):
    lab = Laboratory.query.get_or_404(lab_id)
    return render_template("laboratories/view.html", lab=lab)


@laboratories_bp.route("/<int:lab_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def edit_lab(lab_id):
    lab = Laboratory.query.get_or_404(lab_id)
    if request.method == "POST":
        lab.code = request.form["code"].strip()
        lab.name = request.form["name"].strip()
        lab.contact_name = request.form.get("contact_name", "").strip()
        lab.email = request.form.get("email", "").strip()
        lab.phone = request.form.get("phone", "").strip()
        lab.address = request.form.get("address", "").strip()
        lab.active = "active" in request.form
        db.session.commit()
        log_action("update", "Laboratory", lab.id, f"Modification laboratoire {lab.code}")
        flash("Laboratoire mis à jour.", "success")
        return redirect(url_for("laboratories.view_lab", lab_id=lab.id))
    return render_template("laboratories/form.html", lab=lab)


@laboratories_bp.route("/<int:lab_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_lab(lab_id):
    lab = Laboratory.query.get_or_404(lab_id)
    code = lab.code
    db.session.delete(lab)
    db.session.commit()
    log_action("delete", "Laboratory", lab_id, f"Suppression laboratoire {code}")
    flash("Laboratoire supprimé.", "info")
    return redirect(url_for("laboratories.list_labs"))
