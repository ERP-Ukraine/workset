# -*- coding: utf-8 -*-
from odoo import api, fields, models, tools

from . import calc

DIMS = [('line', 'Лінія'), ('customer', 'Замовник'), ('company', 'Фірма'), ('project', 'Проєкт')]
DIM_CODE = {'line': 1, 'customer': 2, 'company': 3, 'project': 4}
DIM_BASE = 100000000  # id рядка = код розрізу × DIM_BASE + id запису розрізу (стабільний між запитами: кнопка рядка відкриває ставки)


class WorksetRateAvg(models.Model):
    """«Середні» — представлення SQL (_auto = False): середні по діючих ставках тих, хто працює («Працює» + «Новий»), за розрізами
    лінія / замовник / фірма / проєкт. Рахується при кожному читанні — запису й перерахунку немає. Середня частка — без ставок з часткою ≤ 0
    (їх число — «Без частки»), так само, як у полосі підсумків над списком (calc.summary)."""
    _name = 'workset.rate.avg'
    _description = 'Середні'
    _auto = False
    _order = 'dim, work_count desc, id'

    name = fields.Char(string='Розріз', compute='_compute_name', search='_search_name')
    dim = fields.Selection(DIMS, string='Розріз за', readonly=True)
    key = fields.Integer(string='Ключ розрізу', readonly=True)
    line_id = fields.Many2one('workset.line', string='Лінія', readonly=True)
    customer_id = fields.Many2one('res.partner', string='Замовник', readonly=True)
    company_id = fields.Many2one('res.company', string='Фірма', readonly=True)
    project_id = fields.Many2one('project.project', string='Проєкт', readonly=True)
    work_count = fields.Integer(string='Працюють', readonly=True, help='Діючі ставки зі статусом роботи «Працює» або «Новий»')
    avg_wage = fields.Float(string='Середня на руки', readonly=True, help='Середнє «На руки», €/год, по тих, хто працює')
    avg_price = fields.Float(string='Середня ціна', readonly=True, help='Середня «Ціна», €/год, по тих, хто працює')
    avg_share = fields.Float(string='Середня частка WorkSET', readonly=True,
                             help='Середня «Частка WorkSET», €/год, по тих, хто працює; ставки з часткою ≤ 0 у середню не входять (їх число — у «Без частки»)')
    total = fields.Integer(string='Усього ставок', readonly=True, help='Усі діючі ставки розрізу, і працюючі, і ні')
    zero_share = fields.Integer(string='Без частки', readonly=True, help='Працюючі ставки з часткою WorkSET ≤ 0 — у середню частку не входять')
    note = fields.Char(string='Станом на', compute='_compute_name', help='Опорна дата «Працює» і вікно в днях')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                WITH w AS (
                    SELECT r.id AS rate_id, r.line_id, p.commercial_partner_id AS customer, r.wage, r.price, r.our_share,
                           (r.work IN ('work', 'work_new')) AS working
                      FROM workset_rate r
                      JOIN res_partner p ON p.id = r.customer_id
                     WHERE r.state = 'current' AND r.active
                ), d AS (
                    SELECT 'line' AS dim, COALESCE(w.line_id, 0) AS key, w.* FROM w
                    UNION ALL SELECT 'customer', w.customer, w.* FROM w
                    UNION ALL SELECT 'company', rel.company_id, w.* FROM w JOIN workset_rate_company_rel rel ON rel.rate_id = w.rate_id
                    UNION ALL SELECT 'project', rel.project_id, w.* FROM w JOIN workset_rate_project_rel rel ON rel.rate_id = w.rate_id
                )
                SELECT (CASE d.dim WHEN 'line' THEN 1 WHEN 'customer' THEN 2 WHEN 'company' THEN 3 ELSE 4 END) * %d + d.key AS id,
                       d.dim AS dim,
                       d.key AS key,
                       CASE WHEN d.dim = 'line' AND d.key > 0 THEN d.key END AS line_id,
                       CASE WHEN d.dim = 'customer' THEN d.key END AS customer_id,
                       CASE WHEN d.dim = 'company' THEN d.key END AS company_id,
                       CASE WHEN d.dim = 'project' THEN d.key END AS project_id,
                       COUNT(*) FILTER (WHERE d.working) AS work_count,
                       COALESCE(ROUND((AVG(d.wage) FILTER (WHERE d.working))::numeric, 2), 0) AS avg_wage,
                       COALESCE(ROUND((AVG(d.price) FILTER (WHERE d.working))::numeric, 2), 0) AS avg_price,
                       COALESCE(ROUND((AVG(d.our_share) FILTER (WHERE d.working AND d.our_share > 0))::numeric, 2), 0) AS avg_share,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE d.working AND COALESCE(d.our_share, 0) <= 0) AS zero_share
                  FROM d
                 GROUP BY d.dim, d.key
            )""" % (self._table, DIM_BASE))

    @api.depends_context('lang')
    def _compute_name(self):
        Rate = self.env['workset.rate']
        lang = calc.lang_of(self.env.lang)
        note = '%s · %d' % (calc.fmt_date(Rate._work_ref(Rate._today())), Rate._work_days())
        for a in self:
            if a.dim == 'line':
                a.name = a.line_id.name or calc.t('no_line', lang)
            elif a.dim == 'customer':
                a.name = a.customer_id.name or ''
            elif a.dim == 'company':
                a.name = a.company_id.name or ''
            else:
                a.name = a.project_id.display_name or ''
            a.note = note

    def _search_name(self, operator, value):
        return ['|', '|', '|', ('line_id.name', operator, value), ('customer_id.name', operator, value),
                ('company_id.name', operator, value), ('project_id.name', operator, value)]

    def action_open_rates(self):
        # кнопка рядка: ставки цього розрізу з фільтрами «Діють» і «Працюють зараз» (зняти — хрестиком у пошуку)
        self.ensure_one()
        if self.dim == 'line':
            dom = [('line_id', '=', self.key or False)]
        elif self.dim == 'customer':
            dom = [('customer_id.commercial_partner_id', '=', self.key)]
        elif self.dim == 'company':
            dom = [('company_ids', 'in', [self.key])]
        else:
            dom = [('project_ids', 'in', [self.key])]
        return self.env['workset.rate']._rates_action(self.name or '', dom, {'search_default_current': 1, 'search_default_work_now': 1})
