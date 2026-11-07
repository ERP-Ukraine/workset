# -*- coding: utf-8 -*-
"""Нічний розрахунок агентів: читання бази, точки збереження, журнал (перенос tools/odoo_agents_calc/run.py + payrun.py: load_paid, apply_pay).

Запуск: плановe `cron_night` → `_cron_night()`; інструмент штабу через API → `run_now(dry=True)` (вхолосту: нічого не пише, ні нарахувань,
ні журналу; повертає план) або `run_now(dry=False)`; кнопки «Затвердити» / «Зупинити» → `_run(kind='approve' | 'stop')`. Читання й запис —
sudo. Розрахунок цілком до першого запису: помилка читання або розрахунку (і відсутній параметр) нічого не пише, лише рядок журналу
«Помилка». Запис — точками збереження в прежньому порядку: (1) нові нарахування й правки статусів / дат / сторожа, (2) сторно, (2б)
«Виплачено» за рахунком official і рядком WS (до кроку рішень: закриті виплати не потрапляють у нове рішення), (3) рішення й підготовка,
(4) автозатвердження — лише за параметром auto_approve = 1 і лише на копії. Збій однієї точки не відкочує інші — журнал «Частково».
Прод (is_copy ≠ 1) без night_prod_ok = 1 — запис не дозволено: журнал «ніч не дозволена», вихід (вхолосту — можна завжди)."""
import datetime
import json
import logging
import time

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from . import calc, common, office_bridge, payplan

_logger = logging.getLogger(__name__)

AC = 'workset.agent.accrual'
PD = 'workset.agent.deal.people'
CD = 'workset.agent.deal.client'
MEM = 'workset.agent.member'
PO = 'workset.agent.payout'
DEC = 'workset.agent.decision'
DL = 'workset.agent.decision.line'
LINE = 'workset.line'
MOVE_F = ['id', 'name', 'company_id', 'invoice_date', 'partner_id', 'commercial_partner_id', 'state', 'payment_state']
PD_F = ['id', 'agent_id', 'kind', 'state', 'date_from', 'date_to', 'company_id', 'rate', 'pay_path', 'mode']
CD_F = ['id', 'agent_id', 'kind', 'state', 'date_from', 'date_to', 'company_id', 'rate', 'pay_path', 'customer_ids', 'line_id', 'rate_full', 'mode']
AC_F = ['id', 'section', 'people_deal_id', 'client_deal_id', 'agent_id', 'kind', 'person_id', 'customer_id', 'move_id', 'hours', 'rate', 'amount',
        'earned_date', 'pay_date', 'company_id', 'line_id', 'state', 'guard', 'guard_note', 'key', 'storno_id', 'note', 'pay_line_id', 'payout_id', 'fund']
RUN_KINDS = ('night', 'manual', 'approve', 'stop', 'dry')
OWNER_KINDS = ('approve', 'stop')  # запуски від кнопок власника — без замка night_prod_ok


def rd(v):
    if v is False or v is None:
        return False
    if isinstance(v, datetime.datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, tuple):
        return list(v)
    return v


def conv(row):
    return dict((k, rd(row[k])) for k in row)


def uniq(ids):
    out = []
    for x in ids:
        if x and x not in out:
            out.append(x)
    return out


class WorksetAgentRun(models.Model):
    _name = 'workset.agent.run'
    _description = 'Журнал розрахунку агентів'
    _order = 'id desc'

    name = fields.Char(string='Назва', readonly=True)
    at = fields.Datetime(string='Коли', readonly=True, index=True)
    result = fields.Char(string='Результат', readonly=True)
    summary = fields.Text(string='Підсумок', readonly=True)
    created = fields.Integer(string='Створено', readonly=True)
    changed = fields.Integer(string='Змінено', readonly=True)
    review = fields.Integer(string='На перевірці', readonly=True)
    seconds = fields.Integer(string='Секунд', readonly=True)
    before = fields.Text(string='Було', readonly=True, help='Стан до розрахунку у форматі JSON — для відкату')
    kind = fields.Char(string='Звідки', readonly=True, help=common.RUN_KINDS_HINT)
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    # ── читання ──
    def _rows(self, model, domain, names):
        return [conv(r) for r in self.env[model].sudo().with_context(active_test=False).search_read(domain, names, order='id asc')]

    def _with_archived_m2m(self, model, recs, name):
        # many2many з архівними записами: search_read їх викидає навіть з active_test=False, read — ні (копія 20, 05.11)
        if not recs or name not in recs[0]:
            return recs
        got = {}
        for r in self.env[model].sudo().with_context(active_test=False).browse([x['id'] for x in recs]).read([name]):
            got[r['id']] = list(r.get(name) or [])
        for x in recs:
            x[name] = got.get(x['id'], x.get(name) or [])
        return recs

    @api.model
    def _today(self, dry=False, today=False):
        # «сьогодні» — за Києвом; у пробному розрахунку і на копії — аргументом; параметр probe_today — лише копія
        copy = common.is_copy(self.env)
        if today and (dry or copy):
            return str(today)[0:10]
        if copy:
            probe = common.param(self.env, common.P_PROBE_TODAY, required=False)
            if probe:
                return probe[0:10]
        return common.kyiv_today()

    @api.model
    def _cfg(self):
        return calc.make_cfg(common.param_int(self.env, common.P_OFFICIAL), common.param_int(self.env, common.P_PERSONAL))

    def _state_since(self, deals_p, accruals):
        # НЕ чинні домовленості «кандидати» з чинними нарахуваннями — у пошуку перетинів; без «Діє по» кінець — дата останньої зміни статусу
        # з журналу змін (mail.tracking.value поля state)
        out = []
        with_acc = uniq([calc.m2o(a.get('people_deal_id')) for a in accruals if a.get('kind') == 'hour' and a.get('state') != 'cancelled'])
        nonlive = [d['id'] for d in deals_p if d.get('state') not in calc.LIVE and not d.get('date_to') and d['id'] in with_acc]
        if not nonlive:
            return out
        fid = self.env['ir.model.fields'].sudo()._get(PD, 'state')
        msgs = self._rows('mail.message', [('model', '=', PD), ('res_id', 'in', nonlive)], ['res_id', 'date'])
        if fid and msgs:
            when = dict((m['id'], m) for m in msgs)
            got = {}
            for t in self._rows('mail.tracking.value', [('mail_message_id', 'in', [m['id'] for m in msgs]), ('field_id', '=', fid.id)], ['mail_message_id']):
                m = when.get(calc.m2o(t.get('mail_message_id')))
                if m and calc.sval(m.get('date')) > got.get(m.get('res_id'), ''):
                    got[m.get('res_id')] = calc.sval(m.get('date'))
            for k in got:
                out.append([k, got[k][0:10]])
        return out

    @api.model
    def _load_data(self, today, dry=False, zvirka_deals=None):
        """→ D для calc.compute: домовленості, люди, нарахування, оплачені рахунки периметра, години за датами роботи, версії договорів фонду."""
        m2o = calc.m2o
        perim = common.param_ids(self.env, common.P_PERIMETER)
        deals_p = self._rows(PD, [], PD_F)
        deals_c = self._with_archived_m2m(CD, self._rows(CD, [], CD_F), 'customer_ids')
        # «Звірка з конвертами»: лише в ПРОБНОМУ розрахунку домовленості zvirka_deals ({'people': [id], 'client': [id]}) рахуються як «Звірка»
        zv = (zvirka_deals or {}) if dry else {}
        for pair_ in [[deals_p, 'people'], [deals_c, 'client']]:
            for d in pair_[0]:
                if d['id'] in (zv.get(pair_[1]) or []) and d.get('state') not in calc.LIVE:
                    d['state'] = 'zvirka'
        members = self._rows(MEM, [], ['deal_id', 'partner_id', 'date_from', 'date_to'])
        accruals = self._rows(AC, [], AC_F)
        line_customers = []
        for r in self._with_archived_m2m(LINE, self._rows(LINE, [], ['id', 'customer_ids']), 'customer_ids'):
            line_customers.append([r['id'], r.get('customer_ids') or []])
        live = calc.LIVE
        hour_p = [d for d in deals_p if d.get('state') in live and d.get('kind') == 'hour' and d.get('date_from')]
        hour_c = [d for d in deals_c if d.get('state') in live and d.get('kind') in ('hour', 'fixed', calc.FUND_KIND) and d.get('date_from')]
        starts = sorted([d['date_from'] for d in hour_p + hour_c])
        moves = []
        if starts:
            dom = [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), ('payment_state', 'in', list(calc.PAID_STATES)), ('company_id', 'in', perim),
                   ('invoice_date', '>=', starts[0])]
            if not hour_p:
                lc = dict((row[0], row[1]) for row in line_customers)
                custs = []
                for d in hour_c:
                    custs = uniq(custs + list(d.get('customer_ids') or []) + list(lc.get(m2o(d.get('line_id')), [])))
                dom.append(('commercial_partner_id', 'in', custs))
            moves = self._rows('account.move', dom, MOVE_F)
        have = [m['id'] for m in moves]
        # рахунки вже записаних нарахувань «за годину» — завжди: повернення або знята оплата дають сторно
        need = uniq([m2o(a.get('move_id')) for a in accruals if a.get('kind') == 'hour' and a.get('state') != 'cancelled' and m2o(a.get('move_id')) not in have])
        if need:
            moves = moves + self._rows('account.move', [('id', 'in', need)], MOVE_F)
        paid_ids = [m['id'] for m in moves if calc.paid(m)]
        hours, emp_contact, versions, emp_version = [], [], [], []
        full_field = False
        if paid_ids:
            hour = self.env.ref('uom.product_uom_hour', raise_if_not_found=False)
            if not hour:
                raise UserError(self.env._('немає одиниці «Години» (uom.product_uom_hour) — годин у рахунках не порахувати'))
            lines = self._rows('account.move.line', [('move_id', 'in', paid_ids), ('display_type', '=', 'product'), ('product_uom_id', '=', hour.id)],
                               ['move_id', 'quantity', 'sale_line_ids'])
            sls = []
            for l in lines:
                sls = uniq(sls + calc.ids_of(l.get('sale_line_ids')))
            maps = {}
            emps = []
            if sls:
                for r in self._rows('project.sale.line.employee.map', [('sale_line_id', 'in', sls)], ['employee_id', 'sale_line_id']):
                    k = str(m2o(r.get('sale_line_id')))
                    e = m2o(r.get('employee_id'))
                    if e and e not in maps.get(k, []):
                        maps[k] = maps.get(k, []) + [e]
                    emps = uniq(emps + [e])
            sheets = self._rows('account.analytic.line', [('timesheet_invoice_id', 'in', paid_ids)], ['timesheet_invoice_id', 'employee_id', 'unit_amount', 'so_line', 'date'])
            # день повної оплати — найпізніша звірка дебіторки; звірені з рахунком кредит-ноти — теж повернення годин
            rec = self._rows('account.move.line', [('move_id', 'in', paid_ids), ('account_type', '=', 'asset_receivable')], ['move_id'])
            line_move = dict((r['id'], m2o(r.get('move_id'))) for r in rec)
            last = {}
            parts = self._rows('account.partial.reconcile', [('debit_move_id', 'in', [r['id'] for r in rec])], ['debit_move_id', 'credit_move_id', 'max_date']) if rec else []
            cred_inv = {}
            for p in parts:
                mid = line_move.get(m2o(p.get('debit_move_id')))
                dt = calc.sval(p.get('max_date'))
                if mid and dt and dt > last.get(mid, ''):
                    last[mid] = dt
                if mid and m2o(p.get('credit_move_id')):
                    cred_inv[m2o(p.get('credit_move_id'))] = mid
            for m in moves:
                m['pay_date'] = last.get(m['id'], False)
            cred_move = {}
            if cred_inv:
                for r in self._rows('account.move.line', [('id', 'in', list(cred_inv.keys()))], ['move_id']):
                    cred_move[m2o(r.get('move_id'))] = cred_inv.get(r['id'])
            refunds = self._rows('account.move', [('move_type', '=', 'out_refund'), ('state', '=', 'posted'), '|', ('reversed_entry_id', 'in', paid_ids),
                                                  ('id', 'in', list(cred_move.keys()))], ['id', 'name', 'reversed_entry_id'])
            ref_lines = self._rows('account.move.line', [('move_id', 'in', [r['id'] for r in refunds]), ('display_type', '=', 'product'), ('product_uom_id', '=', hour.id)],
                                   ['move_id', 'quantity', 'sale_line_ids']) if refunds else []
            for l in ref_lines:
                for s in calc.ids_of(l.get('sale_line_ids')):
                    if s not in sls:
                        sls.append(s)
            if ref_lines:
                known = [int(k) for k in maps]
                extra_sl = [s for s in sls if s not in known]
                if extra_sl:
                    for r in self._rows('project.sale.line.employee.map', [('sale_line_id', 'in', extra_sl)], ['employee_id', 'sale_line_id']):
                        k = str(m2o(r.get('sale_line_id')))
                        e = m2o(r.get('employee_id'))
                        if e and e not in maps.get(k, []):
                            maps[k] = maps.get(k, []) + [e]
            by_move = {}
            for l in lines:
                k = m2o(l.get('move_id'))
                by_move[k] = by_move.get(k, []) + [l]
            sh_move = {}
            for t in sheets:
                k = m2o(t.get('timesheet_invoice_id'))
                sh_move[k] = sh_move.get(k, []) + [t]
                emps = uniq(emps + [m2o(t.get('employee_id'))])
            ref_of = {}
            ref_names = {}
            for r in refunds:
                inv_id = m2o(r.get('reversed_entry_id')) if m2o(r.get('reversed_entry_id')) in paid_ids else cred_move.get(r['id'])
                ref_of[r['id']] = inv_id
                ref_names[inv_id] = ref_names.get(inv_id, []) + [calc.sval(r.get('name'))]
            ref_move = {}
            for l in ref_lines:
                k = ref_of.get(m2o(l.get('move_id')))
                ref_move[k] = ref_move.get(k, []) + [l]
            for mid in paid_ids:
                if by_move.get(mid):
                    H = calc.hours_of(by_move.get(mid, []), maps, sh_move.get(mid, []))
                    if ref_move.get(mid):
                        R = calc.hours_of(ref_move[mid], maps, [])
                        R['names'] = ref_names.get(mid, [])
                        H = calc.net_hours(H, R)
                    hours.append([mid, H])
            # фонд працівників: поточна версія договору співробітника — запасна; «Повна ставка» — поле «Ставок» hr.version.workset_full_rate (м'яко)
            fund_on = len([d for d in hour_c if d.get('kind') == calc.FUND_KIND]) > 0
            Emp = self.env['hr.employee']
            emp_f = ['work_contact_id'] + (['current_version_id'] if fund_on and 'current_version_id' in Emp._fields else [])
            if emps:
                for r in self._rows('hr.employee', [('id', 'in', emps)], emp_f):
                    emp_contact.append([r['id'], m2o(r.get('work_contact_id'))])
                    if m2o(r.get('current_version_id')):
                        emp_version.append([r['id'], m2o(r.get('current_version_id'))])
            if fund_on and emps and 'hr.version' in self.env:
                Ver = self.env['hr.version']
                full_field = 'workset_full_rate' in Ver._fields
                if full_field:
                    vdom = [('employee_id', 'in', emps)] + ([('active', '=', True)] if 'active' in Ver._fields else [])
                    for r in self._rows('hr.version', vdom, ['employee_id', 'date_version', 'workset_full_rate']):
                        versions.append([m2o(r.get('employee_id')), calc.sval(r.get('date_version')), bool(r.get('workset_full_rate')), r['id']])
        return {'deals_people': deals_p, 'deals_client': deals_c, 'members': members, 'accruals': accruals, 'line_customers': line_customers, 'moves': moves,
                'state_since': self._state_since(deals_p, accruals), 'hours': hours, 'emp_contact': emp_contact, 'has_line': True,
                'versions': versions, 'emp_version': emp_version, 'full_rate_field': full_field, 'fund_field': True, 'work_dates': True, 'pay_links': True}

    @api.model
    def _load_paid(self, today):
        """Дані кроку «Виплачено»: відкриті виплати, рахунки official і їх звірка (рахунок → платіж → виписка), рядки журналів WS фірми personal."""
        m2o = calc.m2o
        pays = self._rows(PO, [('state', 'in', list(payplan.PAID_OPEN))],
                          ['id', 'agent_id', 'pay_path', 'company_id', 'state', 'net', 'wh10', 'gross', 'move_id', 'move_line_id', 'decision_id', 'pay_line_id',
                           'note', 'guard', 'guard_note', 'paid_date', 'paid_via', 'paid_amount', 'create_date', 'pay_day'])
        used = [m2o(r.get('move_line_id')) for r in self._rows(PO, [('move_line_id', '!=', False)], ['move_line_id'])]
        out = {'payouts': pays, 'used_lines': used, 'decisions': [], 'lines': [], 'accruals': [], 'bills': [], 'aml': [], 'partials': [], 'moves': [],
               'ws_lines': [], 'wh_lines': [], 'agent_partner': {}, 'wh_analytic': 0}
        if not pays:
            return out
        agents_all = uniq([m2o(p.get('agent_id')) for p in pays])
        for a in self.env['workset.agent'].sudo().with_context(active_test=False).browse(agents_all):
            out['agent_partner'][a.id] = a.partner_id.id
        decs = uniq([m2o(p.get('decision_id')) for p in pays])
        if decs:
            out['decisions'] = self._rows(DEC, [('id', 'in', decs)], ['approved_at', 'prepared_at'])
        dls = uniq([m2o(p.get('pay_line_id')) for p in pays])
        if dls:
            out['lines'] = self._rows(DL, [('id', 'in', dls)], ['buffer_out'])
        out['accruals'] = self._rows(AC, [('payout_id', 'in', [p['id'] for p in pays])], ['payout_id', 'state', 'note'])
        bill_ids = uniq([m2o(p.get('move_id')) for p in pays if calc.sval(p.get('pay_path')) == 'official_1'])
        if bill_ids:
            out['bills'] = self._rows('account.move', [('id', 'in', bill_ids)], ['state', 'payment_state', 'amount_total', 'amount_residual', 'move_type'])
            paid_bills = [b['id'] for b in out['bills'] if b.get('payment_state') == 'paid']
            if paid_bills:
                aml = self._rows('account.move.line', [('move_id', 'in', paid_bills), ('account_type', '=', 'liability_payable')], ['move_id', 'account_type', 'date'])
                seen = [l['id'] for l in aml]
                frontier = list(seen)
                for depth in range(2):
                    parts = self._rows('account.partial.reconcile', ['|', ('debit_move_id', 'in', frontier), ('credit_move_id', 'in', frontier)],
                                       ['debit_move_id', 'credit_move_id', 'max_date']) if frontier else []
                    out['partials'] = out['partials'] + parts
                    cp = []
                    for p in parts:
                        for x in [m2o(p.get('debit_move_id')), m2o(p.get('credit_move_id'))]:
                            if x and x not in seen and x not in cp:
                                cp.append(x)
                    got = self._rows('account.move.line', [('id', 'in', cp)], ['move_id', 'account_type', 'date']) if cp else []
                    mids = uniq([m2o(l.get('move_id')) for l in got])
                    mv = self._rows('account.move', [('id', 'in', mids)], ['statement_line_id', 'date']) if mids else []
                    out['moves'] = out['moves'] + mv
                    aml = aml + got
                    seen = seen + cp
                    pay_moves = [m['id'] for m in mv if not m2o(m.get('statement_line_id'))]
                    more = self._rows('account.move.line', [('move_id', 'in', pay_moves), ('id', 'not in', seen)], ['move_id', 'account_type', 'date']) if pay_moves and depth == 0 else []
                    aml = aml + more
                    frontier = [l['id'] for l in more]
                    seen = seen + frontier
                out['aml'] = aml
        pers = [p for p in pays if calc.sval(p.get('pay_path')) == 'personal_36' and p.get('state') in ('prep', 'docs')]
        agents = uniq([m2o(p.get('agent_id')) for p in pers])
        if agents:
            ws_company = common.param_int(self.env, common.P_PERSONAL)
            codes = common.param_list(self.env, common.P_WS_CODES)
            journals = self.env['account.journal'].sudo().with_context(active_test=False).search([('code', 'in', codes), ('company_id', '=', ws_company)])
            if not journals:
                raise UserError(self.env._('Агенти: журналів WS з кодами %s у фірмі personal немає (параметр %s).') % (', '.join(codes), common.P_WS_CODES))
            out['wh_analytic'] = common.param_int(self.env, common.P_WH_ANALYTIC)
            partners = uniq([out['agent_partner'].get(a) for a in agents])
            X = payplan.paid_ctx(out)
            since = sorted([payplan.since_of(p, X) for p in pers])[0]
            out['ws_lines'] = self._rows('account.move.line', [('journal_id', 'in', journals.ids), ('company_id', '=', ws_company), ('parent_state', '=', 'posted'),
                                                               ('partner_id', 'in', partners), ('date', '>=', since), ('balance', '<', 0)],
                                         ['move_id', 'partner_id', 'balance', 'date', 'journal_id'])
            out['wh_lines'] = self._rows('account.move.line', [('analytic_distribution', 'in', [out['wh_analytic']]), ('company_id', '=', ws_company),
                                                               ('parent_state', '=', 'posted'), ('partner_id', 'in', partners), ('date', '>=', since)],
                                         ['move_id', 'partner_id', 'balance', 'date'])
        return out

    # ── запис ──
    @api.model
    def _apply_ops(self, plan):
        """Операції плану по порядку; посилання 'd1' / 'l3' / 'p1' / 'b1' → id створеного; «{{b1}}» у рядках (fmt) → id; 'post' — запис в історію.
        → «було» для журналу: створене [модель, id], правки [модель, id, було], видалене [модель, id]."""
        refs = {}
        done = {'created': [], 'writes': [], 'unlinked': []}
        for o in plan['ops']:
            vals = dict(o.get('vals') or {})
            rf = o.get('refs') or {}
            for f in rf:
                vals[f] = refs.get(rf[f], False)
            if o.get('fmt'):
                for f in vals:
                    if isinstance(vals[f], str) and '{{' in vals[f]:
                        s = vals[f]
                        for r in refs:
                            s = s.replace('{{%s}}' % r, str(refs[r]))
                        vals[f] = s
            M = self.env[o['model']].sudo()
            if o.get('company'):
                M = M.with_company(o['company'])
            if o.get('quick'):
                M = M.with_context(mail_activity_quick_update=True)
            if o['op'] == 'create':
                rid = M.create(vals).id
                refs[o['ref']] = rid
                done['created'].append([o['model'], rid])
            elif o['op'] == 'write':
                rid = refs.get(o['id'], 0) if o.get('id_ref') else o['id']
                if vals and rid:
                    M.browse(rid).write(vals)
                    done['writes'].append([o['model'], rid, o.get('before') or {}])
            elif o['op'] == 'unlink':
                M.browse(o['id']).unlink()
                done['unlinked'].append([o['model'], o['id']])
            elif o['op'] == 'post':
                M.browse(o['id']).message_post(body=vals.get('body') or '', message_type='comment', subtype_xmlid='mail.mt_note')
            elif o['op'] == 'activity':
                # активність штабу (список офісу, тривога власнику): «{{b1}}» — і в тексті штабу; поля штабу ставить office_bridge
                hq = dict(o.get('hq') or {})
                if o.get('fmt'):
                    for f in hq:
                        if isinstance(hq[f], str) and '{{' in hq[f]:
                            s = hq[f]
                            for r in refs:
                                s = s.replace('{{%s}}' % r, str(refs[r]))
                            hq[f] = s
                rec, note = office_bridge.create(self.env, vals, hq, bool(o.get('quick')))
                refs[o['ref']] = rec.id
                done['created'].append([o['model'], rec.id])
                if note:
                    done.setdefault('notes', []).append(note)
        return done

    # ── крок рішень: факти й читання (перенос payrun.py load_pay, load_envelopes) ──
    @api.model
    def _pay_cfg(self):
        """Факти кроку виплат з параметрів: фірми, учётки, журнали WS, правила відсотків, автозатвердження."""
        env = self.env
        cfg = self._cfg()
        official = common.param_int(env, common.P_OFFICIAL)
        owner_uid = common.param_int(env, common.P_OWNER_UID, required=False)
        group = env.ref(common.G_OWNER, raise_if_not_found=False)
        members = []
        if group:
            fld = 'user_ids' if 'user_ids' in group._fields else 'users'
            members = group.sudo()[fld].ids
        owners = uniq(members + ([owner_uid] if owner_uid else []))
        root = env.ref('base.user_root', raise_if_not_found=False)
        codes = common.param_list(env, common.P_WS_CODES, required=False)
        ws = env['account.journal'].sudo().with_context(active_test=False).search(
            [('code', 'in', codes), ('company_id', '=', common.param_int(env, common.P_PERSONAL))]).ids if codes else []
        cfg.update({'personal_pct': common.PERSONAL_PCT, 'tax_pct': common.TAX_PCT, 'tax_free_countries': list(common.TAX_FREE_COUNTRIES),
                    'bill_company': official, 'owner_uid': owner_uid, 'owner_uids': owners,
                    'night_uids': uniq([root.id if root else 0] + ([owner_uid] if owner_uid else [])),
                    # список офісу — на OdooBot (uid планового), як в еталоні: доставку в бот «Офіс» робить рутина office-desk, а в учётці
                    # співробітника такі активності висіли б у «Мої дії» (П-2; сотрудникам — лише через бот «Офіс»)
                    'office_uid': root.id if root else 0,
                    'office_to': common.param(env, common.P_OFFICE_TO, required=False), 'owner_to': common.param(env, common.P_OWNER_TO, required=False),
                    'ws_journals': ws, 'auto_approve': self._auto_allowed()})
        return cfg

    def _model_id(self, name):
        m = self.env['ir.model'].sudo().search([('model', '=', name)], limit=1)
        return m.id

    def _account_id(self, code, company):
        if not code or not company:
            return 0
        a = self.env['account.account'].sudo().with_company(company).search([('code', '=', code), ('company_ids', 'in', [company])], limit=1)
        return a.id

    def _load_envelopes(self, accruals, today):
        # конверти агентів з нарахуваннями «Очікується» (не фонд) і їх рядки з сумою > 0 за ZV_ENV_DAYS днів; колонка рядка — за планом рахунку
        env_of = []
        lines = []
        ags = uniq([calc.m2o(a.get('agent_id')) for a in accruals if a.get('state') == 'expected' and not a.get('fund')])
        if not ags:
            return [env_of, lines]
        for ag in self.env['workset.agent'].sudo().with_context(active_test=False).browse(ags):
            if ag.envelope_id:
                env_of.append([ag.id, ag.envelope_id.id])
        AAL = self.env['account.analytic.line'].sudo()
        since = calc.add_days(today, -common.ZV_ENV_DAYS)
        for acc in self.env['account.analytic.account'].sudo().with_context(active_test=False).browse(uniq([e[1] for e in env_of])):
            plan = acc.root_plan_id
            col = plan._column_name() if plan and hasattr(plan, '_column_name') else 'account_id'
            if col not in AAL._fields:
                continue
            for l in self._rows('account.analytic.line', [(col, '=', acc.id), ('date', '>=', since), ('amount', '>', 0)], ['date', 'name', 'amount']):
                lines.append({'envelope': acc.id, 'id': l['id'], 'date': calc.sval(l.get('date')), 'name': calc.sval(l.get('name')), 'amount': calc.num(l.get('amount'))})
        return [env_of, lines]

    @api.model
    def _load_pay(self, today):
        """→ P для payplan.pay_plan / auto_plan: рішення, рядки, нарахування, домовленості, люди, профілі податків, банки контактів агентів,
        історія виплат і їх рядки оплати, рахунки замовників, ключі активностей штабу, довідники рахунку постачальника."""
        m2o = calc.m2o
        env = self.env
        cfg = self._pay_cfg()
        decisions = self._rows(DEC, [], ['id', 'pay_day', 'state', 'approved_by', 'approved_at', 'guard_note', 'auto', 'hq_advice', 'section'])
        lines = self._rows(DL, [], ['id', 'decision_id', 'key', 'agent_id', 'section', 'pay_path', 'company_id', 'state', 'accrued', 'review_amount', 'buffer_in',
                                    'buffer_from_id', 'available', 'wh_pct', 'tax_pct', 'wh_note', 'proposal', 'proposal_note', 'pay_now', 'buffer_out', 'cust_paid',
                                    'cust_unpaid', 'cust_overdue', 'cust_text', 'guard', 'guard_note', 'payout_id', 'seen_at', 'owner_edit', 'night_pay', 'zv_period',
                                    'zv_env', 'zv_diff', 'zv_note', 'overdue_move_ids', 'advice', 'advice_text', 'zv_missing'])
        accruals = self._rows(AC, [], ['id', 'section', 'people_deal_id', 'client_deal_id', 'agent_id', 'kind', 'customer_id', 'move_id', 'amount', 'pay_date', 'company_id',
                                       'state', 'key', 'pay_line_id', 'payout_id', 'earned_date', 'work_from', 'work_to', 'create_uid', 'write_uid', 'fund', 'hours', 'rate'])
        envs = self._load_envelopes(accruals, today)
        deals_p = self._rows(PD, [], ['id', 'agent_id', 'kind', 'state', 'date_from', 'date_to', 'company_id', 'pay_path', 'withhold_pct', 'write_date', 'mode'])
        deals_c = self._with_archived_m2m(CD, self._rows(CD, [], ['id', 'agent_id', 'kind', 'state', 'date_from', 'date_to', 'company_id', 'pay_path', 'withhold_pct',
                                                                  'customer_ids', 'write_date', 'mode']), 'customer_ids')
        members = self._rows(MEM, [], ['deal_id', 'partner_id', 'date_from', 'date_to', 'write_date'])
        agents = uniq([m2o(a.get('agent_id')) for a in accruals] + [m2o(x.get('agent_id')) for x in lines])
        Agent = env['workset.agent'].sudo().with_context(active_test=False)
        agent_partner = {}
        agent_names = []
        for ag in Agent.browse(agents):
            agent_partner[ag.id] = ag.partner_id.id
            agent_names.append([ag.id, ag.name])
        partners = uniq(list(agent_partner.values()))
        profiles = self._rows('workset.agent.tax', [('agent_id', 'in', agents)], ['agent_id', 'cert_until', 'tax_country']) if agents else []
        cids = uniq([m2o(p.get('tax_country')) for p in profiles])
        codes = dict((c['id'], c.get('code')) for c in self._rows('res.country', [('id', 'in', cids)], ['code'])) if cids else {}
        for p in profiles:
            p['country_code'] = codes.get(m2o(p.get('tax_country')), '')
        banks = self._rows('res.partner.bank', [('partner_id', 'in', partners), ('active', '=', True)],
                           ['partner_id', 'acc_number', 'acc_holder_name', 'allow_out_payment', 'write_date']) if partners else []
        done_pays = self._rows(PO, [('state', 'in', ['paid', 'paid_part']), ('agent_id', 'in', agents)],
                               ['agent_id', 'pay_path', 'state', 'bank_ref', 'create_date', 'paid_via', 'move_line_id', 'paid_manual', 'paid_by', 'net', 'move_id']) if agents else []
        hl = uniq([m2o(h.get('move_line_id')) for h in done_pays])
        hist_aml = self._rows('account.move.line', [('id', 'in', hl)], ['move_id', 'partner_id', 'journal_id', 'parent_state', 'balance', 'amount_currency']) if hl else []
        hm = uniq([m2o(l.get('move_id')) for l in hist_aml])
        hist_moves = self._rows('account.move', [('id', 'in', hm)], ['statement_line_id']) if hm else []
        hb = uniq([m2o(h.get('move_id')) for h in done_pays])
        hist_bills = self._rows('account.move', [('id', 'in', hb)], ['payment_state']) if hb else []
        custs = uniq([m2o(a.get('customer_id')) for a in accruals if a.get('state') != 'cancelled'])
        for d in deals_c:
            custs = uniq(custs + calc.ids_of(d.get('customer_ids')))
        names = [[r['id'], r.get('name')] for r in self._rows('res.partner', [('id', 'in', custs)], ['name'])] if custs else []
        perim = common.param_ids(env, common.P_PERIMETER)
        invoices = self._rows('account.move', [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), ('company_id', 'in', perim), ('commercial_partner_id', 'in', custs),
                                               ('payment_state', 'in', ['not_paid', 'partial'])], ['commercial_partner_id', 'amount_residual', 'invoice_date_due']) if custs else []
        mids = uniq([m2o(a.get('move_id')) for a in accruals if a.get('state') in ('due', 'expected', 'review')])
        move_totals = [[r['id'], r.get('amount_total')] for r in self._rows('account.move', [('id', 'in', mids)], ['amount_total'])] if mids else []
        act_keys = office_bridge.keys_like(env, [payplan.KEY_PREFIX, payplan.OWNER_KEY_PREFIX])
        # полів штабу немає — повтор активності власнику впізнаємо за рішенням і заголовком
        plain_acts = [] if office_bridge.ready(env) else [[r['res_id'], r.get('summary') or ''] for r in env['mail.activity'].sudo().with_context(active_test=False).search_read(
            [('res_model', '=', DEC)], ['res_id', 'summary'])]
        official = cfg['bill_company']
        jr = payplan.pick_journal(self._rows('account.journal', [('type', '=', 'purchase'), ('company_id', '=', official), ('active', '=', True)], ['id', 'code']),
                                  common.param(env, common.P_BILL_JOURNAL, required=False), official)
        an = common.param_int(env, common.P_ANALYTIC, required=False)
        an_ok = bool(an and env['account.analytic.account'].sudo().browse(an).exists())
        office_type = common.param_int(env, common.P_OFFICE_TYPE, required=False)
        cron = env.ref('workset_agents.cron_night', raise_if_not_found=False)
        eur = common.eur(env)
        todo = env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        refs = {'office_type': office_type, 'cron_id': cron.id if cron else 0, 'cron_model': self._model_id('ir.cron'), 'eur': eur.id if eur else 0,
                'journal': jr[0], 'journal_note': jr[1], 'fee_account': self._account_id(common.param(env, common.P_FEE_ACCOUNT, required=False), official),
                'tax_account': self._account_id(common.param(env, common.P_TAX_ACCOUNT, required=False), official), 'analytic': an if an_ok else 0,
                'owner_type': todo.id if todo else 0, 'dec_model': self._model_id(DEC), 'hq_fields': office_bridge.ready(env)}
        return {'cfg': cfg, 'decisions': decisions, 'lines': lines, 'accruals': accruals, 'deals_people': deals_p, 'deals_client': deals_c, 'members': members,
                'state_since': self._state_since(deals_p, accruals), 'profiles': profiles, 'banks': banks, 'names': names, 'agent_names': agent_names,
                'agent_partner': agent_partner, 'invoices': invoices, 'move_totals': move_totals, 'act_keys': act_keys, 'plain_acts': plain_acts, 'refs': refs, 'payouts_done': done_pays,
                'hist_aml': hist_aml, 'hist_moves': hist_moves, 'hist_bills': hist_bills, 'env_of': envs[0], 'env_lines': envs[1]}

    def _write_log(self, result, text, created, changed, review, started, before, kind):
        self.sudo().create({'name': common.kyiv_now().strftime('%d.%m.%Y %H:%M'), 'at': fields.Datetime.now(), 'result': result, 'summary': text,
                            'created': created, 'changed': changed, 'review': review, 'seconds': int(time.time() - started), 'before': before, 'kind': kind})

    def _point(self, fn, errs, label):
        # одна точка збереження: успіх — запис лишається; збій — відкат до точки, помилка в errs (ніч іде далі)
        try:
            with self.env.cr.savepoint():
                return fn()
        except Exception as e:  # noqa: BLE001 — будь-який збій точки: відкат і рядок «Частково» / «Помилка»
            errs.append('%s: %s' % (label, str(e)[0:250]))
            self.env.invalidate_all(flush=False)
            return None

    @api.model
    def _auto_allowed(self):
        # автозатвердження — лише на копії і лише за параметром auto_approve = 1 (прод — ні, Р-1)
        return common.is_copy(self.env) and common.param(self.env, common.P_AUTO_APPROVE, required=False) == '1'

    @api.model
    def _night_allowed(self, dry, kind='night'):
        # замок night_prod_ok — лише для планового (night) і ручного run_now (manual): «Затвердити» / «Зупинити» — явна дія власника, кнопка
        # одразу запускає розрахунок, як в еталоні (рішення coordinator 07.11, П-1)
        if dry or kind in OWNER_KINDS or common.is_copy(self.env):
            return True
        return common.param(self.env, common.P_NIGHT_PROD_OK, required=False) == '1'

    @api.model
    def _run(self, kind='night', dry=False, today=False, zvirka_deals=None):
        """Ніч: → res (план і що записано). dry — нічого не пише (ні нарахувань, ні журналу)."""
        started = time.time()
        if not self._night_allowed(dry, kind):
            self._write_log('Не дозволено', 'ніч не дозволена: прод без параметра %s = 1 (ставить інструмент штабу за словом власника)' % common.P_NIGHT_PROD_OK,
                            0, 0, 0, started, '', kind)
            return {'error': 'ніч не дозволена', 'skipped': True}
        res = None
        err = ''
        try:
            TODAY = self._today(dry, today)
            cfg = self._cfg()
            res = calc.compute(self._load_data(TODAY, dry, zvirka_deals), TODAY, cfg)
        except Exception as e:  # noqa: BLE001 — помилка до запису: нічого не пишемо, лише журнал
            err = str(e)[0:300]
        if err:
            _logger.error('Агенти: розрахунок не вдався: %s', err)
            if not dry:
                self._write_log('Помилка', err, 0, 0, 0, started, '', kind)
            return {'error': err}
        done = {'created': [], 'writes': [], 'stornos': [], 'orig': []}
        errs = []
        groups = 0
        order = []  # порядок точок збереження (для тестів і журналу)
        Acc = self.env[AC].sudo()
        if not dry:
            if res['creates'] or res['writes']:
                groups += 1

                def p1():
                    made = [Acc.create(c['vals']).id for c in res['creates']]
                    for w in res['writes']:
                        Acc.browse(w['id']).write(w['vals'])
                    return made
                made = self._point(p1, errs, 'нарахування')
                order.append('accruals')
                if made is not None:
                    done['created'] = made
                    done['writes'] = [{'id': w['id'], 'before': w['before']} for w in res['writes']]
            if res['stornos']:
                groups += 1

                def p2():
                    made, orig = [], []
                    for s in res['stornos']:
                        made.append(Acc.create(s['vals']).id)
                        if s['orig_vals']:
                            Acc.browse(s['orig']).write(s['orig_vals'])
                            orig.append({'id': s['orig'], 'before': s['orig_before']})
                    return [made, orig]
                got = self._point(p2, errs, 'сторно')
                order.append('stornos')
                if got is not None:
                    done['stornos'], done['orig'] = got
        # (2б) «Виплачено» — ДО кроку рішень
        paid_p = None
        try:
            paid_p = payplan.paid_plan(self._load_paid(self._today(dry, today)), self._today(dry, today))
        except Exception as e:  # noqa: BLE001
            groups += 1
            errs.append('виплачено (розрахунок): ' + str(e)[0:250])
        if paid_p and paid_p['ops'] and not dry:
            groups += 1
            got = self._point(lambda: self._apply_ops(paid_p), errs, 'виплачено')
            order.append('paid')
            if got is not None:
                done['paid'] = got
        if paid_p:
            res['notes'] = res['notes'] + paid_p['notes']
        # (3) рішення й підготовка — за базою ПІСЛЯ запису нарахувань цієї ночі; (4) автозатвердження — лише копія й параметр
        now = fields.Datetime.to_string(fields.Datetime.now())
        Dec = self.env[DEC].sudo()
        # (4) іде щоночі, як у ручній збірці (тривоги власнику — і без автозатвердження); затверджує сам лише при _auto_allowed()
        steps = [['pay', 'виплати', Dec._night_pay_step], ['auto', 'автозатвердження', Dec._night_auto_step]]
        plans = {}
        for key, label, fn in steps:
            plan = None
            try:
                plan = fn(self._today(dry, today), now, dry)
            except Exception as e:  # noqa: BLE001
                groups += 1
                errs.append('%s (розрахунок): %s' % (label, str(e)[0:250]))
            if plan and plan['ops'] and not dry:
                groups += 1
                got = self._point(lambda plan=plan: self._apply_ops(plan), errs, label)
                order.append(key)
                if got is not None:
                    done[key] = got
                    res['notes'] = res['notes'] + (got.get('notes') or [])
            if plan:
                res['notes'] = res['notes'] + plan['notes']
            plans[key] = plan
        text = payplan.summary_text(res, dry, errs)
        for part in [[paid_p, 'виплачено'], [plans.get('pay'), 'рішення'], [plans.get('auto'), 'автозатвердження']]:
            if part[0]:
                text = ('%s. %s' % (text, payplan.pay_summary(part[0], part[1])))[0:2000]
        if not dry:
            result = payplan.run_result(errs, groups)
            nc = len(done['created']) + len(done['stornos'])
            nw = len(done['writes']) + len(done['orig'])
            for k in ['paid', 'pay', 'auto']:
                pd_ = done.get(k) or {'created': [], 'writes': [], 'unlinked': []}
                nc += len(pd_['created'])
                nw += len(pd_['writes']) + len(pd_['unlinked'])
            self._write_log(result, text, nc, nw, res['summary']['review'], started, json.dumps(done), kind)
            if errs:
                _logger.error('Агенти: запис не вдався, відкочено: %s', '; '.join(errs))
        _logger.info('Агенти: %s', text)
        res['done'] = done
        res['errors'] = errs
        res['order'] = order
        res['paid'] = paid_p
        res['pay'] = plans.get('pay')
        res['auto'] = plans.get('auto')
        res['text'] = text
        return res

    @api.model
    def _cron_night(self):
        return self._run(kind='night', dry=False)

    @api.model
    def run_now(self, dry=True, today=False, zvirka_deals=False):
        """Ніч зараз (інструмент штабу через API): dry=True — вхолосту, нічого не пише; dry=False — із записом (прод — лише з night_prod_ok).
        Лише група «Власник» і системні адміністратори."""
        if not (self.env.su or common.is_owner(self.env) or self.env.user.has_group('base.group_system')):
            raise AccessError(self.env._('Розрахунок агентів запускає лише власник.'))
        return self._run(kind='dry' if dry else 'manual', dry=bool(dry), today=today, zvirka_deals=zvirka_deals or None)
