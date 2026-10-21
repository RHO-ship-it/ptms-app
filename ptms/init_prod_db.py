# -*- coding: utf-8 -*-
"""
Initialisation d'une base de données de PRODUCTION VIERGE pour PTMS.
Crée uniquement le schéma de tables et le compte administrateur racine.
"""
from app import create_app
from extensions import db
from models import User

app = create_app()

with app.app_context():
    # Crée toutes les tables vides
    db.create_all()

    # Création de l'Administrateur Racine
    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(
            username="admin",
            email="admin@tts.com.tn",
            full_name="Administrateur PTMS",
            role="admin",
            active=True
        )
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("[OK] Base de production vierge creee avec succes !")
        print("[OK] Compte administrateur cree : admin / admin123")
    else:
        print("[INFO] L'administrateur existe deja.")
