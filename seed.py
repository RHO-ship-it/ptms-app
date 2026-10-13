"""
Initialise la base de données et crée les comptes de démonstration.
Usage : python seed.py
"""
from app import create_app
from extensions import db
from models import User, Laboratory

app = create_app()

with app.app_context():
    db.create_all()

    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            email="admin@ptms.local",
            full_name="Administrateur Système",
            role="admin",
            active=True,
        )
        admin.set_password("admin123")
        db.session.add(admin)

    if not User.query.filter_by(username="chef.technique").first():
        chef = User(
            username="chef.technique",
            email="chef.technique@ptms.local",
            full_name="Chef Technique Démo",
            role="chef_technique",
            active=True,
        )
        chef.set_password("chef123")
        db.session.add(chef)

    if not User.query.filter_by(username="auditeur").first():
        aud = User(
            username="auditeur",
            email="auditeur@ptms.local",
            full_name="Auditeur Démo",
            role="auditeur",
            active=True,
        )
        aud.set_password("audit123")
        db.session.add(aud)

    if not Laboratory.query.filter_by(code="LAB001").first():
        lab = Laboratory(
            code="LAB001",
            name="Laboratoire de Démonstration",
            contact_name="Jean Dupont",
            email="contact@lab001.local",
            phone="+216 00 000 000",
            address="Zone Industrielle, Tunis",
            active=True,
        )
        db.session.add(lab)

    db.session.commit()

    print("Base de données initialisée avec succès.")
    print("-" * 50)
    print("Comptes de démonstration créés :")
    print("  Admin          -> admin / admin123")
    print("  Chef Technique -> chef.technique / chef123")
    print("  Auditeur       -> auditeur / audit123")
    print("-" * 50)
    print("Un laboratoire de démonstration (LAB001) a été créé.")
    print("Utilisez le Campaign Wizard (menu 'Nouvelle campagne') pour créer")
    print("une campagne, ce qui générera automatiquement un compte participant.")
