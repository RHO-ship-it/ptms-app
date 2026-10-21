# -*- coding: utf-8 -*-
"""
Point d'entrée WSGI pour serveur de production (Waitress).
Usage : python wsgi.py
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    from waitress import serve
    print("=" * 60)
    print("  PTMS - Serveur de Production")
    print("  Écoute sur http://0.0.0.0:5000")
    print("  Accessible en réseau local via l'IP de cette machine")
    print("=" * 60)
    serve(app, host="0.0.0.0", port=5000, threads=8)
