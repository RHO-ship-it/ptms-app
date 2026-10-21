# -*- coding: utf-8 -*-
from odoo import models, fields, api

class PtmsTrial(models.Model):
    _name = 'ptms.trial'
    _description = 'Essai PTMS'
    _order = 'campaign_id, name'

    name = fields.Char(string='Nom de l\'Essai', required=True)
    campaign_id = fields.Many2one(
        'ptms.campaign',
        string='Campagne',
        required=True,
        ondelete='cascade'
    )
    parameter = fields.Char(string='Paramètre à mesurer', required=True)
    unit = fields.Char(string='Unité', required=True)
    
    # Statistiques pré-calculées
    assigned_value = fields.Float(string='Valeur Assignée (X)', readonly=True)
    standard_deviation = fields.Float(string='Écart-type (σ)', readonly=True)
    
    result_ids = fields.One2many(
        'ptms.result',
        'trial_id',
        string='Résultats des participants'
    )

    def action_calculate_statistics(self):
        """
        Calcul de la valeur assignée et de l'écart-type avec l'algorithme A.
        Mise à jour des Z-scores pour chaque résultat.
        """
        from .statistics import algorithm_a, compute_z_score, grubbs_test
        
        for trial in self:
            results = trial.result_ids.filtered(lambda r: r.value_submitted is not None)
            values = [r.value_submitted for r in results]
            
            if len(values) >= 2:
                # 1. Test de Grubbs
                grubbs = grubbs_test(values)
                outlier_val = grubbs.get('outlier_value')
                
                # 2. Algorithme A
                stats = algorithm_a(values)
                trial.assigned_value = stats.get('x_pt')
                trial.standard_deviation = stats.get('sigma_pt')
                
                # 3. Z-scores pour chaque résultat
                for result in results:
                    if result.value_submitted == outlier_val:
                        result.is_outlier = True
                        
                    z_score, is_anomaly = compute_z_score(
                        result.value_submitted, 
                        trial.assigned_value, 
                        trial.standard_deviation,
                        stats.get('u_xpt')
                    )
                    
                    result.z_score = z_score
                    if z_score is not None:
                        az = abs(z_score)
                        if az <= 2.0:
                            result.evaluation = 'satisfactory'
                        elif az < 3.0:
                            result.evaluation = 'questionable'
                        else:
                            result.evaluation = 'unsatisfactory'
