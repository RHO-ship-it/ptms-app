# -*- coding: utf-8 -*-
{
    'name': "PTMS - Proficiency Testing Management System",

    'summary': "Gestion des campagnes d'essais d'aptitude (ISO 17043)",

    'description': """
        Ce module remplace l'ancienne application Flask PTMS.
        Il gère :
        - Les laboratoires (Participants)
        - Les campagnes d'essais interlaboratoires
        - La saisie des résultats via le portail
        - Le calcul automatique des statistiques (Z-score, Algo A/S, Grubbs)
        - La génération des rapports finaux
    """,

    'author': "Expert Odoo",
    'website': "https://www.example.com",

    # Cible Odoo 18
    'category': 'Operations/PTMS',
    'version': '18.0.1.0.0',

    # Dépendances standards remplaçant le code custom
    'depends': ['base', 'mail', 'portal'],

    # Vues et données qui seront chargées
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/ptms_campaign_views.xml',
        'views/ptms_trial_views.xml',
        'views/res_partner_views.xml',
        'views/menu_views.xml',
        'views/portal_templates.xml',
        'reports/report_templates.xml',
    ],
    
    'installable': True,
    'application': True,
}
