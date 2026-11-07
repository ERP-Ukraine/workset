# -*- coding: utf-8 -*-
"""Свої поля замовника «Умов клієнта» (М3, К1) на res.partner і список «Умови клієнтів».

Поля — лише те, чого ніде немає (інвентаризація 07.11): номер, дата й скан договору, хто погоджує табель, контакт і адреса об'єкта,
відсоток за виведення коштів (довідково, у прибуток не входить), особливі домовленості, коли й ким перевірено. Строк оплати — стандартне
поле property_payment_term_id (ним користуються рахунки), свого поля немає. Усі — з префіксом workset_client_, з історією змін
(tracking) і не копіюються з контактом. Читати — група «Перегляд» (поле groups), правити — «Редактор» і адміністратор (_check_edit у
create / write): майстер «Заповнити» й «Позначити перевіреним» пишуть через sudo після тієї самої перевірки (у редактора може не бути
права правити контакти).

Список «Умови клієнтів» — замовники з «Замовників» ліній або з діючими ставками (workset_client_is_customer); стовпці — нехранимі поля
одним читанням на сторінку (_compute_workset_client_list, sudo: ставки, лінії й правила житла лежать у «Ставках» з їхніми правами)."""
from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.addons.workset_profit.models import calc as pcalc
from odoo.addons.workset_rates.models import calc as rcalc

from . import calc

GROUP_VIEWER = 'workset_client.group_viewer'
GROUP_EDITOR = 'workset_client.group_editor'
CARD_TAG = 'workset_client.card'
# свої поля замовника: правка — лише редактор або адміністратор (_workset_client_check_edit)
OWN_FIELDS = frozenset(['workset_client_contract_no', 'workset_client_contract_date', 'workset_client_contract_scan_ids', 'workset_client_sign_id',
                        'workset_client_site_contact_id', 'workset_client_site_address', 'workset_client_withdraw_pct',
                        'workset_client_terms_note', 'workset_client_checked_date', 'workset_client_checked_by_id'])
CONTACT_DOMAIN = "[('commercial_partner_id', '=', commercial_partner_id), ('id', '!=', id)]"  # контакти цього замовника


class ResPartner(models.Model):
    _inherit = 'res.partner'

    workset_client_contract_no = fields.Char(string='Номер договору', tracking=True, copy=False, groups=GROUP_VIEWER)
    workset_client_contract_date = fields.Date(string='Дата договору', tracking=True, copy=False, groups=GROUP_VIEWER)
    workset_client_contract_scan_ids = fields.Many2many('ir.attachment', 'workset_client_partner_scan_rel', 'partner_id', 'attachment_id',
                                                        string='Скан договору', tracking=True, copy=False, groups=GROUP_VIEWER)
    workset_client_sign_id = fields.Many2one('res.partner', string='Хто погоджує табель', tracking=True, copy=False, ondelete='set null',
                                             domain=CONTACT_DOMAIN, groups=GROUP_VIEWER, help='Контакт замовника, який підписує табель годин')
    workset_client_site_contact_id = fields.Many2one('res.partner', string="Контакт на об'єкті", tracking=True, copy=False, ondelete='set null',
                                                     domain=CONTACT_DOMAIN, groups=GROUP_VIEWER)
    workset_client_site_address = fields.Char(string="Адреса об'єкта", tracking=True, copy=False, groups=GROUP_VIEWER)
    workset_client_withdraw_pct = fields.Float(string='Відсоток за виведення коштів', digits=(16, 2), tracking=True, copy=False,
                                               groups=GROUP_VIEWER, help='%; довідково, у розрахунок прибутку не входить')
    workset_client_terms_note = fields.Text(string='Особливі домовленості', tracking=True, copy=False, groups=GROUP_VIEWER)
    workset_client_checked_date = fields.Date(string='Перевірено', tracking=True, copy=False, groups=GROUP_VIEWER,
                                              help='Коли умови клієнта звірено з договором і словами власника')
    workset_client_checked_by_id = fields.Many2one('res.users', string='Ким перевірено', tracking=True, copy=False, ondelete='set null',
                                                   groups=GROUP_VIEWER)

    # список «Умови клієнтів» і смарт-кнопка «Умови» — нехранимі, одним читанням на сторінку
    workset_client_is_customer = fields.Boolean(string='Замовник у «Ставках»', compute='_compute_workset_client_list',
                                                search='_search_workset_client_is_customer', groups=GROUP_VIEWER,
                                                help='Є в «Замовниках» лінії або має діючі ставки')
    workset_client_line_text = fields.Char(string='Лінія замовника', compute='_compute_workset_client_list', groups=GROUP_VIEWER)
    workset_client_rate_count = fields.Integer(string='Ставок діє', compute='_compute_workset_client_list', groups=GROUP_VIEWER)
    workset_client_share_avg = fields.Float(string='Частка сер.', digits=(16, 2), compute='_compute_workset_client_list', groups=GROUP_VIEWER,
                                            help='Середня «Частка WorkSET» діючих ставок, € год')
    workset_client_housing_text = fields.Char(string='Житло — хто платить', compute='_compute_workset_client_list', groups=GROUP_VIEWER)
    workset_client_has_contract = fields.Boolean(string='Договір є', compute='_compute_workset_client_list', groups=GROUP_VIEWER)
    workset_client_contract_text = fields.Char(string='Договір', compute='_compute_workset_client_list', groups=GROUP_VIEWER)
    workset_client_term_text = fields.Char(string='Термін оплати', compute='_compute_workset_client_list', groups=GROUP_VIEWER)
    workset_client_checked_text = fields.Char(string='Перевірено коли', compute='_compute_workset_client_list', groups=GROUP_VIEWER)

    # ——— права ———
    def _workset_client_can_edit(self):
        return self.env.su or self.env.user.has_group(GROUP_EDITOR) or self.env.user.has_group('base.group_system')

    def _workset_client_check_edit(self):
        if not self._workset_client_can_edit():
            raise AccessError(calc.t('only_editor', calc.lang_of(self.env.lang)))

    @api.model_create_multi
    def create(self, vals_list):
        if any(OWN_FIELDS.intersection(vals) for vals in vals_list):
            self._workset_client_check_edit()
        return super().create(vals_list)

    def write(self, vals):
        if OWN_FIELDS.intersection(vals):
            self._workset_client_check_edit()
        return super().write(vals)

    # ——— список «Умови клієнтів» ———
    @api.model
    def _workset_client_today(self):
        # «сьогодні» — як на екранах «Прибутковості» (підміна profit_today — лише адміністратору: тести, проба на копії)
        return pcalc.iso(self.env['workset.profit.month']._today())

    @api.model
    def _workset_client_index(self, custs=None):
        """Лінії й діючі ставки замовників (комерційні контрагенти custs; None — усі) одним читанням: {'lines': {замовник: [лінія за
        порядком «Ставок»]}, 'rates': {замовник: [ставка]}}. sudo — дані «Ставок» з їхніми правами; назовні — лише числа й назви."""
        lines = {}
        for line in self.env['workset.line'].sudo().search([]):
            for c in line.customer_ids:
                cid = pcalc.cust_of(c)
                if (custs is None or cid in custs) and line not in lines.get(cid, []):
                    lines.setdefault(cid, []).append(line)
        dom = [('state', '=', 'current')] + ([('customer_id.commercial_partner_id', 'in', list(custs))] if custs is not None else [])
        rates = {}
        for r in self.env['workset.rate'].sudo().search(dom):
            rates.setdefault(pcalc.cust_of(r.customer_id), []).append(r)
        return {'lines': lines, 'rates': rates}

    @api.model
    def _workset_client_customer_ids(self):
        idx = self._workset_client_index()
        return sorted(set(idx['lines']) | set(idx['rates']))

    def _search_workset_client_is_customer(self, operator, value):
        # оператор і значення в будь-якому вигляді Odoo 19 — rcalc.bool_search «Ставок»
        return rcalc.bool_search(operator, value, self._workset_client_customer_ids())

    def _workset_client_labels(self):
        """Підписи значень вибору правил (перекладені мовою користувача) для calc.pays_rows / housing_row."""
        Housing = self.env['workset.line.housing']
        Cost = self.env['workset.line.cost.rule']
        lang = calc.lang_of(self.env.lang)

        def sel(model, name):
            return dict(model._fields[name]._description_selection(self.env)) if name in model._fields else {}
        return {'housing_payer': sel(Housing, 'payer'), 'housing_kind': sel(Housing, 'kind'), 'unit': sel(Housing, 'amount_unit'),
                'source': sel(Housing, 'source'), 'cost_payer': sel(Cost, 'payer'), 'per_hour': calc.t('per_hour', lang)}

    def _workset_client_housing_src(self, line_ids):
        """Правила житла «Ставок» для calc.housing_row: усі активні (rcalc.housing_pick вибирає сам) і поля правил ліній line_ids."""
        Housing = self.env['workset.line.housing'].sudo()
        rules = Housing._rules()
        mine = [r['id'] for r in rules if r['line_id'] in set(line_ids)]
        info = {}
        for h in Housing.browse(mine):
            info[h.id] = {'payer': h.payer or '', 'kind': h.kind or '', 'amount': h.amount or 0.0, 'unit': h.amount_unit or '',
                          'date_from': pcalc.iso(h.date_from), 'source': h.source or '', 'source_note': h.source_note or '',
                          'customer_id': pcalc.cust_of(h.customer_id)}
        return {'housing_rules': rules, 'housing': info}

    def _compute_workset_client_list(self):
        lang = calc.lang_of(self.env.lang)
        custs = {pcalc.cust_of(p) for p in self if isinstance(p.id, int)}
        idx = self._workset_client_index(custs) if custs else {'lines': {}, 'rates': {}}
        line_ids = [line.id for ls in idx['lines'].values() for line in ls]
        src = self._workset_client_housing_src(line_ids) if line_ids else {}
        labels = self._workset_client_labels() if line_ids else {}
        today = self._workset_client_today()
        for p in self:
            cid = pcalc.cust_of(p) if isinstance(p.id, int) else 0
            lines, rates = idx['lines'].get(cid, []), idx['rates'].get(cid, [])
            c = p.sudo().commercial_partner_id
            p.workset_client_is_customer = bool(lines or rates)
            p.workset_client_line_text = ', '.join(x.display_name for x in lines)
            p.workset_client_rate_count = len(rates)
            p.workset_client_share_avg = calc.share_avg([r.our_share for r in rates]) or 0.0
            p.workset_client_housing_text = calc.housing_row(src, lines[0].id, cid, today, labels, lang)['payer'] if lines else ''
            has = bool(c.workset_client_contract_no or c.workset_client_contract_scan_ids)
            p.workset_client_has_contract = has
            p.workset_client_contract_text = calc.t('l_yes', lang) if has else calc.t('e_none', lang)
            term = c.property_payment_term_id
            p.workset_client_term_text = term.display_name if term else calc.t('e_unset', lang)
            p.workset_client_checked_text = calc.checked_info(c.workset_client_checked_date, '')['date'] or calc.t('l_checked_no', lang)

    # ——— дії ———
    def action_workset_client_card(self):
        """Смарт-кнопка «Умови» й рядок списку — екран «Умови клієнта» комерційного контрагента."""
        self.ensure_one()
        cust = self.commercial_partner_id
        return {'type': 'ir.actions.client', 'tag': CARD_TAG, 'name': cust.display_name, 'params': {'partner_id': cust.id}}

    def workset_client_mark_checked(self):
        """«Позначити перевіреним»: сьогодні (за Києвом) і поточний користувач — лише редактор або адміністратор; запис — sudo після
        перевірки (у редактора може не бути права правити контакти), історія змін — від імені користувача."""
        self._workset_client_check_edit()
        self.mapped('commercial_partner_id').sudo().write({'workset_client_checked_date': self._workset_client_today(),
                                                           'workset_client_checked_by_id': self.env.user.id})
        return True
