# -*- coding: utf-8 -*-
from odoo import models, fields

class PtmsResult(models.Model):
    _name = 'ptms.result'
    _description = 'Résultat PTMS'
    
    trial_id = fields.Many2one(
        'ptms.trial',
        string='Essai',
        required=True,
        ondelete='cascade'
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Laboratoire',
        required=True,
        domain="[('is_laboratory', '=', True)]"
    )
    
    # Données saisies par le participant
    value_submitted = fields.Float(string='Valeur Soumise')
    date_submitted = fields.Datetime(string='Date de Soumission', default=fields.Datetime.now)
    method_used = fields.Char(string='Méthode Utilisée')
    equipment_used = fields.Char(string='Équipement')
    
    # Résultats statistiques
    z_score = fields.Float(string='Z-Score', readonly=True)
    is_outlier = fields.Boolean(string='Valeur Aberrante (Grubbs)', default=False, readonly=True)
    evaluation = fields.Selection([
        ('satisfactory', 'Satisfaisant'),
        ('questionable', 'Discutable'),
        ('unsatisfactory', 'Non Satisfaisant')
    ], string='Évaluation', readonly=True)

    _sql_constraints = [
        ('unique_trial_partner', 'unique(trial_id, partner_id)', 'Un laboratoire ne peut soumettre qu\'un seul résultat par essai.')
    ]
