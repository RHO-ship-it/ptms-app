# -*- coding: utf-8 -*-
from odoo import models, fields

class PtmsCampaign(models.Model):
    _name = 'ptms.campaign'
    _description = 'Campagne PTMS'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc'

    name = fields.Char(string='Nom de la Campagne', required=True, tracking=True)
    reference = fields.Char(string='Référence', required=True, copy=False, tracking=True)
    date_start = fields.Date(string='Date de Début', required=True, tracking=True)
    date_end = fields.Date(string='Date de Fin', required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('active', 'En Cours'),
        ('closed', 'Clôturée'),
        ('cancelled', 'Annulée')
    ], string='Statut', default='draft', required=True, tracking=True)

    trial_ids = fields.One2many(
        'ptms.trial',
        'campaign_id',
        string='Essais'
    )
    description = fields.Html(string='Description')
