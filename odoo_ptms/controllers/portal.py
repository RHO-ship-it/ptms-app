# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager

class PtmsPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = request.env.user.partner_id
        if 'ptms_result_count' in counters:
            values['ptms_result_count'] = request.env['ptms.result'].search_count([
                ('partner_id', '=', partner.id)
            ])
        return values

    @http.route(['/my/ptms/results', '/my/ptms/results/page/<int:page>'], type='http', auth="user", website=True)
    def portal_my_ptms_results(self, page=1, **kwargs):
        partner = request.env.user.partner_id
        PtmsResult = request.env['ptms.result']
        
        domain = [('partner_id', '=', partner.id)]
        result_count = PtmsResult.search_count(domain)
        pager = portal_pager(
            url="/my/ptms/results",
            total=result_count,
            page=page,
            step=10
        )
        results = PtmsResult.search(domain, limit=10, offset=pager['offset'])
        
        values = {
            'results': results,
            'page_name': 'ptms_result',
            'pager': pager,
            'default_url': '/my/ptms/results',
        }
        return request.render("odoo_ptms.portal_my_ptms_results", values)

    @http.route(['/my/ptms/result/<int:result_id>'], type='http', auth="user", website=True)
    def portal_my_ptms_result_detail(self, result_id=None, **kwargs):
        partner = request.env.user.partner_id
        result = request.env['ptms.result'].browse(result_id)
        
        if result.partner_id.id != partner.id:
            return request.redirect('/my/ptms/results')
            
        values = {
            'result': result,
            'page_name': 'ptms_result_detail',
        }
        return request.render("odoo_ptms.portal_ptms_result_detail", values)

    @http.route(['/my/ptms/result/<int:result_id>/submit'], type='http', auth="user", website=True, methods=['POST'])
    def portal_submit_ptms_result(self, result_id, **post):
        partner = request.env.user.partner_id
        result = request.env['ptms.result'].browse(result_id)
        
        if result.partner_id.id == partner.id and result.trial_id.campaign_id.state == 'active':
            value_submitted = post.get('value_submitted')
            method_used = post.get('method_used')
            equipment_used = post.get('equipment_used')
            
            if value_submitted:
                result.sudo().write({
                    'value_submitted': float(value_submitted),
                    'method_used': method_used,
                    'equipment_used': equipment_used,
                })
                
        return request.redirect(f'/my/ptms/result/{result_id}')
