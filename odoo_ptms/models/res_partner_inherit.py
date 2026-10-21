# -*- coding: utf-8 -*-
from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_laboratory = fields.Boolean(
        string='Est un Laboratoire',
        default=False,
        help="Cochez cette case si ce contact est un laboratoire participant aux essais PTMS."
    )
    lab_code = fields.Char(
        string='Code Laboratoire',
        help="Code unique d'identification du laboratoire pour l'anonymat."
    )
    ptms_result_ids = fields.One2many(
        'ptms.result',
        'partner_id',
        string='Résultats PTMS'
    )
