# -*- coding: utf-8 -*-
"""
Lance le serveur PTMS en production (Waitress) + ouvre un tunnel Ngrok
pour rendre le site accessible depuis l'extérieur.

Usage : python start_external.py
"""
from app import create_app
import threading
import time

app = create_app()

def start_server():
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000, threads=8)

def start_ngrok():
    try:
        from pyngrok import ngrok
        time.sleep(2)  # attendre que le serveur démarre
        
        # Ouvre un tunnel vers le port 5000
        public_url = ngrok.connect(5000)
        
        print()
        print("=" * 60)
        print("  🌐 PTMS EST MAINTENANT ACCESSIBLE DEPUIS L'EXTÉRIEUR !")
        print(f"  URL PUBLIQUE : {public_url}")
        print("=" * 60)
        print()
        print("  Partagez ce lien avec vos collaborateurs externes.")
        print("  Le tunnel restera actif tant que ce script tourne.")
        print("  Appuyez sur Ctrl+C pour arrêter.")
        print()
        
        # Garder le thread actif
        ngrok_process = ngrok.get_ngrok_process()
        ngrok_process.proc.wait()
        
    except ImportError:
        print("❌ pyngrok n'est pas installé. Lancez : pip install pyngrok")
    except Exception as e:
        print(f"❌ Erreur Ngrok : {e}")
        print("💡 Assurez-vous que ngrok est configuré (ngrok authtoken VOTRE_TOKEN)")
        print("   Inscrivez-vous gratuitement sur https://ngrok.com")

if __name__ == "__main__":
    print("🚀 Démarrage de PTMS en mode externe...")
    print("   Serveur de production : Waitress (port 5000)")
    print("   Tunnel externe : Ngrok")
    print()
    
    # Lancer le serveur dans un thread séparé
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    # Lancer ngrok dans le thread principal
    start_ngrok()
