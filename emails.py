import logging

logger = logging.getLogger(__name__)

def send_email(to_address, subject, body_text):
    """
    Fonction simulée pour l'envoi d'emails.
    Dans un environnement de production, on utiliserait Flask-Mail ou smtplib.
    Pour l'instant, on se contente de logger l'envoi.
    """
    try:
        # Simulation d'envoi
        print(f"\n{'='*50}")
        print(f"📧 EMAIL ENVOYÉ À : {to_address}")
        print(f"📌 SUJET : {subject}")
        print(f"💬 MESSAGE : \n{body_text}")
        print(f"{'='*50}\n")
        
        logger.info(f"Email envoyé à {to_address}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'email à {to_address}: {str(e)}")
        return False
