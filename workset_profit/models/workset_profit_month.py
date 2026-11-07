# -*- coding: utf-8 -*-
"""«Лінія × місяць» (М3) — хранимий підсумок прибутковості: години → частка WorkSET (зі «Ставок») → − житло → − авто → − паливо →
− інструмент → − перевезення = чисто €/міс і €/год; поруч — гроші лінії (партнер), фонд працівників (у фонд і з фонду), буфер житла
(довідково); рядки компанії — «Без лінії», «Не розподілено» (за статтями), «Офіс», «Власник», «Не враховано», рахунки DKV. Нижче «чисто»
(круг 4) — − ZUS і PIT-4 − загальні фірм за годинами табеля = «Залишається WorkSET» (поля рядка й підсумок workset.profit.overhead).

Звідки:
- години — табелі (account.analytic.line з проєктом і працівником, без рахунку, години > 0) у фірмах периметра «Ставок»; людина — робочий
  контакт картки (hr.employee.work_contact_id — та сама зв'язка, що в «Ставках»), замовник — комерційний контрагент проєкту, лінія — за
  «Замовниками» лінії; частка — workset.rate._plan_at на перший день кожного відрізка місяця між новими ставками пари;
- витрати — аналітика плану рекрутингу з рахунком (корені — параметр calc.P_ROOT_PLANS, немає — корені вузлів ліній), сума в EUR (курс
  дати рядка), місяць — за періодом послуги рядка рахунку (deferred_start_date / deferred_end_date, якщо поля є, параметр
  calc.P_SPLIT не «ні» і в рахунку немає проводок відстрочки Odoo — calc.service_period) або датою рядка; куди — розмітка
  (workset_profit_map.py), хто платить — правила «Хто платить» і житло лінії «Ставок» (_housing_at); житло, яке вираховує замовник, —
  сумою правила житла за одиницею (calc.customer_housing);
- DKV — транзакції карток (брутто EUR) за періодом картки; рахунок DKV із транзакціями (move_id або номер рахунку = «Референс» рахунку) —
  своєю аналітикою не вдруге, різниця «рахунок − транзакції» — «Не розподілено: DKV різниця» (calc.dkv_bill_entries); «Перевезення
  людей» — між лініями за прибуттями місяця (картки однієї людини в різних фірмах склеєно — calc.glue_people).
Пересчёт — profit_recompute (кнопка «Перерахувати», плановое щоночі за RECALC_MONTHS місяців, інструмент штабу); звірка — _verify /
profit_verify (суми до цента, окремий підрахунок аналітики за датою). sudo — лише всередині перерахунку й звірки: підсумок бачать групи
модуля, а табелі, рахунки й аналітика лежать у фірмах і правах поза ними. Правити рядки — лише код (права моделі)."""
import datetime
import logging
from zoneinfo import ZoneInfo

from psycopg2.errors import UniqueViolation

from odoo import _, api, fields, models
from odoo.addons.workset_rates.models import calc as rates_calc
from odoo.exceptions import AccessError
from odoo.tools import mute_logger

from . import calc
from .workset_profit_map import MAP_KINDS

_logger = logging.getLogger(__name__)

MONTH = 'workset.profit.month'
ROW_KINDS = [('line', 'Лінія'), ('noline', 'Без лінії'), ('unallocated', 'Не розподілено'), ('office', 'Офіс'), ('owner', 'Власник'),
             ('ignore', 'Не враховано'), ('dkv', 'Рахунки DKV (розкладено)')]
SORT = {'line': 1, 'noline': 2, 'unallocated': 3, 'office': 4, 'owner': 5, 'ignore': 8, 'dkv': 9}
LOCK_NS = 7301  # простір ключів pg_advisory_xact_lock модуля: другий перерахунок того самого місяця чекає першого


class WorksetProfitMonth(models.Model):
    _name = MONTH
    _description = 'Прибутковість: лінія × місяць'
    _order = 'month desc, sort_key, name, id'

    name = fields.Char(string='Рядок', readonly=True)
    month = fields.Date(string='Місяць', required=True, index=True, readonly=True, help='Перше число місяця')
    row_kind = fields.Selection(ROW_KINDS, string='Вид рядка', required=True, index=True, readonly=True)
    sort_key = fields.Integer(string='Порядок', readonly=True, aggregator=False)
    line_id = fields.Many2one('workset.line', string='Лінія', index=True, readonly=True, ondelete='cascade')
    customer_id = fields.Many2one('res.partner', string='Замовник', readonly=True, ondelete='set null')
    analytic_id = fields.Many2one('account.analytic.account', string='Стаття аналітики', readonly=True, ondelete='set null')
    account_id = fields.Many2one('account.account', string='Рахунок', readonly=True, ondelete='set null')
    card_id = fields.Many2one('workset.dkv.card', string='Картка DKV', readonly=True, ondelete='set null')
    tag = fields.Char(string='Тег', readonly=True, help='Службове: чому рядок «Не розподілено» (вид без лінії, DKV, перевезення)')
    hint_kind = fields.Selection(MAP_KINDS, string='Підказка виду', readonly=True,
                                 help='Вид за назвою статті — лише підказка для розмітки; рахує розмітка')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    hours_total = fields.Float(string='Годин усього', readonly=True, digits=(16, 2))
    hours_rate = fields.Float(string='Годин зі ставкою', readonly=True, digits=(16, 2))
    hours_no_rate = fields.Float(string='Годин без ставки', readonly=True, digits=(16, 2),
                                 help='Години пар без ставки на дату — у частку не входять (розібрати у «Ставках»)')
    share = fields.Monetary(string='Частка WorkSET', currency_field='currency_id', readonly=True,
                            help='Години × «Після бонусів офісу» ставки на дату; «Фікс за місяць» — фікс за частку годин пари мінус бонус офісу')
    car_rent = fields.Monetary(string='Оренда авто', currency_field='currency_id', readonly=True, help='Години × «Оренда авто» ставки: наш дохід')
    fund_in = fields.Monetary(string='У фонд працівників', currency_field='currency_id', readonly=True,
                              help='Години × «Фонд працівників» ставки: гроші працівників, не наші')
    housing = fields.Monetary(string='Житло', currency_field='currency_id', readonly=True, help='Наше житло лінії (мінус — витрата)')
    housing_customer = fields.Monetary(string='Житло (вирахував замовник)', currency_field='currency_id', readonly=True,
                                       help='Житло, яке замовник вираховує з оплати (правило житла лінії «Ставок», «Хто платить» — замовник): '
                                            'сума правила × одиниця (за місяць на об\'єкт — одна сума; за місяць за особу — люди з годинами в '
                                            'цього замовника; за добу за особу; за годину; % з оплати). Наш мінус — у «чисто»')
    auto = fields.Monetary(string='Авто', currency_field='currency_id', readonly=True)
    fuel = fields.Monetary(string='Паливо', currency_field='currency_id', readonly=True)
    tool = fields.Monetary(string='Інструмент', currency_field='currency_id', readonly=True)
    transport = fields.Monetary(string='Перевезення', currency_field='currency_id', readonly=True,
                                help='Наша частка рейсів місяця — за числом прибуттів лінії')
    net = fields.Monetary(string='Чисто', currency_field='currency_id', readonly=True,
                          help='Лінія: частка + оренда авто + житло + житло, яке вирахував замовник, + авто + паливо + інструмент + перевезення '
                               '(наші, до ZUS і загальних). '
                               'Рядок компанії — його сума')
    net_per_hour = fields.Monetary(string='Чисто, €/год', currency_field='currency_id', readonly=True, aggregator=False,
                                   help='Чисто ÷ години зі ставкою')
    partner_paid = fields.Monetary(string='З грошей лінії', currency_field='currency_id', readonly=True,
                                   help='Заплачено з грошей партнера лінії (буфер партнера) — не в нашому «чисто»')
    fund_paid = fields.Monetary(string='З фонду працівників', currency_field='currency_id', readonly=True,
                                help='Заплачено з фонду працівників — не в нашому «чисто»')
    fund_left = fields.Monetary(string='Фонд: залишок місяця', currency_field='currency_id', readonly=True, help='У фонд + з фонду')
    housing_buffer = fields.Monetary(string='Буфер житла', currency_field='currency_id', readonly=True,
                                     help='Довідково: частина оплати замовника, передана в буфер житла, — не дохід, у «чисто» не входить')
    # круг 4: «− ZUS − загальні = Залишається WorkSET» (лінія й «без лінії»; версія 1 — за годинами табеля, calc.overhead)
    zus_amount = fields.Monetary(string='ZUS, PIT-4 (частка)', currency_field='currency_id', readonly=True,
                                 help='ZUS і PIT-4 місяця роботи × години рядка ÷ усі години табеля місяця (мінус — витрата)')
    office_amount = fields.Monetary(string='Загальні фірм (частка)', currency_field='currency_id', readonly=True,
                                    help='Загальні фірм місяця (рядки «Офіс» без рахунків ZUS / PIT-4) × години рядка ÷ усі години табеля')
    remain = fields.Monetary(string='Залишається WorkSET', currency_field='currency_id', readonly=True,
                             help='Чисто + частка ZUS, PIT-4 + частка загальних фірм. Власники, «Не розподілено» й «Не враховано» — не загальні')
    remain_per_hour = fields.Monetary(string='Залишається, €/год', currency_field='currency_id', readonly=True, aggregator=False,
                                      help='Залишається ÷ години зі ставкою')
    amount = fields.Monetary(string='Сума', currency_field='currency_id', readonly=True, help='Сума рядка компанії')
    aal_amount = fields.Monetary(string='З аналітики', currency_field='currency_id', readonly=True, help='Службове для звірки')
    dkv_amount = fields.Monetary(string='З транзакцій DKV', currency_field='currency_id', readonly=True, help='Службове для звірки')
    # круг 5б (19.0.5): склад «Рядків» картки лінії за місяць (calc.card_groups — ті самі записи розкладу, що в числах рядка): картка за
    # період лише складає місяці, а не розкладає кожен наново; рядок, перерахований до 19.0.5 (поля немає), картка розкладає сама — повільніше
    card_groups = fields.Json(string='Склад «Рядків» картки', readonly=True,
                              help='Службове для картки лінії за період: групи «Рядків» місяця (стаття й рахунок аналітики, картка DKV, '
                                   'перевезення за прибуттями) — сума й записи. Пише перерахунок')

    # ——— службове ———
    def _lang(self):
        return calc.lang_of(self.env.lang)

    def _is_admin(self):
        return self.env.is_superuser() or self.env.user.has_group('base.group_system')

    def _can_recompute(self):
        return self._is_admin() or self.env.user.has_group('workset_profit.group_editor')

    @api.model
    def _today(self):
        # підміна «сьогодні» — лише адміністратору (тести, проба на копії)
        forced = self.env.context.get('profit_today')
        if forced and self._is_admin():
            return calc.to_date(str(forced)[0:10])
        return datetime.datetime.now(ZoneInfo(calc.TZ)).date()

    def _param(self, key):
        return self.env['ir.config_parameter'].sudo().get_param(key) or ''

    # ——— читання бази (викликається лише з перерахунку й звірки — self уже sudo) ———
    def _lines_index(self):
        """Лінії за порядком (перша виграє): [[замовник, лінія]] (комерційний замовник) і [[вузол плану, лінія]]."""
        cust_lines, line_nodes = [], []
        for line in self.env['workset.line'].sudo().search([]):
            cust_lines += [[calc.cust_of(c), line.id] for c in line.customer_ids]
            line_nodes += [[p.id, line.id] for p in line.plan_ids]
        return cust_lines, line_nodes

    def _scope_plans(self):
        """Вузли плану рекрутингу (усі під коренями) → список id або None (коренів не знайдено — без фільтра за планом)."""
        Plan = self.env['account.analytic.plan'].sudo()
        roots = calc.parse_ids(self._param(calc.P_ROOT_PLANS))
        if not roots:
            nodes = self.env['workset.line'].sudo().search([]).plan_ids
            roots = sorted({calc.plan_chain(p.parent_path)[0] for p in nodes if calc.plan_chain(p.parent_path)})
        if not roots:
            return None
        return Plan.search([('id', 'child_of', roots)]).ids

    def _timesheets(self, date_from, date_to):
        """Табелі «з – по» у фірмах периметра «Ставок»: [{'employee', 'person', 'cust', 'date', 'hours'}] (людина × проєкт × день)."""
        AAL = self.env['account.analytic.line'].sudo().with_context(active_test=False)
        dom = [('project_id', '!=', False), ('general_account_id', '=', False), ('employee_id', '!=', False), ('unit_amount', '>', 0),
               ('date', '>=', date_from), ('date', '<=', date_to)]
        skip = self.env['workset.rate'].sudo()._skip_companies()
        if skip:
            dom.append(('project_id.company_id', 'not in', skip))
        groups = AAL._read_group(dom, ['employee_id', 'project_id', 'date:day'], ['unit_amount:sum'])
        emps = self.env['hr.employee'].sudo().with_context(active_test=False).browse(sorted({g[0].id for g in groups}))
        person = {e.id: e.work_contact_id.id for e in emps}
        glue = self._people(emps)
        projects = self.env['project.project'].sudo().with_context(active_test=False).browse(sorted({g[1].id for g in groups}))
        cust = {p.id: calc.cust_of(p.partner_id) for p in projects}
        # person — контакт картки (ставка шукається за ним, як у «Ставках»); glue — склеєна людина (прибуття, люди для житла замовника)
        return [{'employee': emp.id, 'person': person.get(emp.id, 0) or 0, 'glue': glue.get(emp.id, emp.id), 'cust': cust.get(proj.id, 0),
                 'date': calc.iso(day), 'hours': hours} for emp, proj, day, hours in groups if emp and proj]

    def _people(self, emps):
        """Картки → склеєна людина (calc.glue_people: спільний телефон, ім'я без приписки фірми, спільний контакт) → {картка: людина}.
        Особистий телефон картки — поле Odoo 19 private_phone (немає — лише робочий мобільний)."""
        private = 'private_phone' in emps._fields
        rows = [{'id': e.id, 'name': e.name or '', 'contact': e.work_contact_id.id,
                 'phones': [p for p in ((e.private_phone if private else False), e.mobile_phone) if p]} for e in emps]
        return dict(calc.glue_people(rows))

    def _rate_days(self, persons, last):
        """«Ставка з» ставок людей (активні, з датою ≤ last) → [{'person', 'cust', 'date_from'}] для calc.segments."""
        rates = self.env['workset.rate'].sudo().search([('person_id', 'in', persons), ('date_from', '<=', last)])
        return [{'person': r.person_id.id, 'cust': calc.cust_of(r.customer_id), 'date_from': calc.iso(r.date_from)} for r in rates]

    def _plans(self, segs):
        """План ставки на перший день кожного відрізка: один виклик workset.rate._plan_at на день → [{'person', 'cust', 'from', 'plan'}]."""
        Rate = self.env['workset.rate'].sudo()
        by_day = {}
        for s in segs:
            if s['person'] and s['cust']:
                by_day.setdefault(s['from'], set()).add((s['person'], s['cust']))
        out = []
        for day in sorted(by_day):
            pairs = sorted(by_day[day])
            got = Rate._plan_at(pairs, day)
            out += [{'person': p[0], 'cust': p[1], 'from': day, 'plan': got.get(p)} for p in pairs]
        return out

    def _deferred(self):
        # період послуги рядка рахунку — поля бухгалтерії (є не в кожній збірці): немає — місяць за датою рядка
        f = self.env['account.move.line']._fields
        return 'deferred_start_date' in f and 'deferred_end_date' in f

    def _split_on(self):
        """Параметр calc.P_SPLIT «ділити за періодом послуги»: так / ні (немає — так)."""
        return calc.parse_flag(self._param(calc.P_SPLIT), True)

    def _service_periods(self, move_lines):
        """Період послуги рядків рахунку → {рядок: (з, по)} (лише заповнені). Полів немає — {}."""
        if not self._deferred():
            return {}
        return {ml.id: (calc.iso(ml.deferred_start_date), calc.iso(ml.deferred_end_date)) for ml in move_lines
                if ml.deferred_start_date and ml.deferred_end_date}

    def _deferral_moves(self, moves):
        """Рахунки, що мають проводки відстрочки Odoo (породили їх — deferred_move_ids, або самі є такою проводкою —
        deferred_original_move_ids): їхні суми вже рознесені по місяцях датами проводок → множина id. Полів немає — порожньо."""
        f = self.env['account.move']._fields
        names = [n for n in ('deferred_move_ids', 'deferred_original_move_ids') if n in f]
        if not names or not moves:
            return set()
        return {m.id for m in moves if any(m[n] for n in names)}

    def _bounds(self, first, last):
        """Домен аналітики плану рекрутингу з рахунком (без дат) і дати місяця."""
        dom = [('general_account_id', '!=', False), ('account_id', '!=', False)]
        plans = self._scope_plans()
        if plans is not None:
            dom.append(('account_id.plan_id', 'in', plans))
        return dom, [('date', '>=', first), ('date', '<=', last)]

    def _analytic_items(self, first, last):
        """Аналітика плану рекрутингу з рахунком, що припадає на місяць (у EUR; частина періоду послуги — calc.service_period) →
        [{'aal_id', 'amount', 'date', 'analytic_id', 'analytic_name', 'plans', 'account_id', 'account_name', 'partner_id', 'cust',
        'move_id', 'in_month', 'line_eur', 'currency', 'line_cur'}]. Рядки, датовані місяцем, — завжди (і з частиною 0: окремий підрахунок
        звірки бачить їх повними сумами — line_eur, line_cur у валюті фірми); датовані іншим місяцем — лише ті, чий період послуги
        заходить у місяць."""
        AAL = self.env['account.analytic.line'].sudo().with_context(active_test=False)
        base, dates = self._bounds(first, last)
        split_on = self._split_on()
        if split_on and self._deferred():
            dom = base + ['|', '&', dates[0], dates[1],
                          '&', ('move_line_id.deferred_start_date', '<=', last), ('move_line_id.deferred_end_date', '>=', first)]
        else:
            dom = base + dates
        lines = AAL.search(dom, order='date, id')
        periods = self._service_periods(lines.move_line_id) if split_on else {}
        deferral = self._deferral_moves(lines.move_line_id.move_id) if periods else set()
        eur = self.env.ref('base.EUR')
        out = []
        for line in lines:
            amount = line.amount
            cur = line.company_id.currency_id or line.currency_id
            if cur and cur != eur:
                amount = cur._convert(amount, eur, line.company_id, line.date)
            ml = line.move_line_id
            start, end = calc.service_period(split_on, *(periods.get(ml.id) or ('', '')), ml.move_id.id in deferral if ml else False)
            day = calc.iso(line.date)
            in_month = first <= day <= last
            part = calc.month_part(amount, day, start, end, first)
            if not in_month and not calc.cents(part):
                continue
            if not in_month:
                day = max(start, first) if start else first
            acc = line.account_id
            out.append({'aal_id': line.id, 'amount': part, 'date': day, 'analytic_id': acc.id, 'analytic_name': acc.name or '',
                        'plans': calc.plan_chain(acc.plan_id.parent_path), 'account_id': line.general_account_id.id,
                        'account_name': line.general_account_id.name or '', 'partner_id': calc.cust_of(line.partner_id),
                        'cust': calc.cust_of(acc.partner_id), 'move_id': ml.move_id.id if ml else 0, 'in_month': in_month,
                        'line_eur': round(float(amount or 0), 2), 'currency': cur.name or '', 'line_cur': line.amount})
        return out

    def _raw_by_currency(self, first, last):
        """Окремий підрахунок для звірки: аналітика плану рекрутингу з рахунком, датована місяцем, — _read_group за фірмами (не через
        _analytic_items) → {валюта фірми: сума у валюті}."""
        base, dates = self._bounds(first, last)
        out = {}
        for company, amount in self.env['account.analytic.line'].sudo()._read_group(base + dates, ['company_id'], ['amount:sum']):
            name = company.currency_id.name or ''
            out[name] = round(out.get(name, 0.0) + float(amount or 0), 2)
        return out

    def _bill_totals(self, moves):
        """Уся аналітика плану рекрутингу рахунків (усі дати), EUR → {рахунок: сума}."""
        if not moves:
            return {}
        base, _dates = self._bounds('', '')
        eur = self.env.ref('base.EUR')
        out = {}
        for line in self.env['account.analytic.line'].sudo().search(base + [('move_line_id.move_id', 'in', list(moves))]):
            cur = line.company_id.currency_id or line.currency_id
            amount = cur._convert(line.amount, eur, line.company_id, line.date) if (cur and cur != eur) else line.amount
            out[line.move_line_id.move_id.id] = out.get(line.move_line_id.move_id.id, 0) + calc.cents(amount)
        return {k: calc.eur(v) for k, v in out.items()}

    def _dkv_gross(self, moves):
        """Брутто транзакцій DKV рахунків (усі дати): привʼязаних полем «Рахунок постачальника» і — без нього — за номером рахунку DKV,
        що дорівнює «Референсу» рахунку → {рахунок: брутто}. Рахунок без транзакцій — немає в словнику."""
        if not moves:
            return {}
        Txn = self.env['workset.dkv.txn'].sudo()
        out = {}
        for move, gross in Txn._read_group([('move_id', 'in', list(moves))], ['move_id'], ['amount_gross:sum']):
            out[move.id] = float(gross or 0)
        refs = {}
        for m in self.env['account.move'].sudo().browse(list(moves)):
            if (m.ref or '').strip():
                refs.setdefault(m.ref.strip(), m.id)
        if refs:
            for no, gross in Txn._read_group([('move_id', '=', False), ('invoice_no', 'in', list(refs))], ['invoice_no'], ['amount_gross:sum']):
                mid = refs.get((no or '').strip())
                if mid:
                    out[mid] = out.get(mid, 0.0) + float(gross or 0)
        return out

    def _nobill(self, first, last):
        """Транзакції DKV місяця без рахунку в Odoo: поле «Рахунок постачальника» порожнє, а номер рахунку DKV не збігається з «Референсом»
        жодного рахунку → {'n', 'amount'} (брутто)."""
        txns = self.env['workset.dkv.txn'].sudo().search([('date', '>=', first), ('date', '<=', last), ('move_id', '=', False)])
        nos = sorted({(x.invoice_no or '').strip() for x in txns} - {''})
        known = set()
        if nos:
            known = {(m.ref or '').strip() for m in self.env['account.move'].sudo().search([('ref', 'in', nos)])}
        free = txns.filtered(lambda x: (x.invoice_no or '').strip() not in known)
        return {'n': len(free), 'amount': round(sum(free.mapped('amount_gross')), 2)}

    def _housing_rules(self):
        """Правила житла «Ставок» — один раз на перерахунок місяця або запит екрана (Housing._housing_at читає всі правила на кожен виклик):
        {'rules' (Housing._rules() — для rates_calc.housing_pick, тієї ж функції, що в _housing_at), 'payer', 'amount', 'unit': {id: …}}."""
        Housing = self.env['workset.line.housing'].sudo()
        rules = Housing._rules()
        got = Housing.browse([r['id'] for r in rules]).read(['payer', 'amount', 'amount_unit'])
        return {'rules': rules, 'payer': {h['id']: h['payer'] or '' for h in got}, 'amount': {h['id']: h['amount'] or 0.0 for h in got},
                'unit': {h['id']: h['amount_unit'] or '' for h in got}}

    def _housing_pick(self, hr, line_id, cust_id, day):
        """Правило житла пари «лінія × замовник» (id; замовник — комерційний, 0 — без замовника) на day з правил hr (як Housing._housing_at:
        правило замовника, а немає — лінії) → id або 0."""
        return rates_calc.housing_pick(hr['rules'], line_id, cust_id or 0, calc.iso(day)) if line_id else 0

    def _housing_payer(self, hr, line_id, cust_id, day):
        # хто платить житло — правило житла лінії «Ставок» (правило замовника, а немає — лінії) на день суми
        rid = self._housing_pick(hr, line_id, cust_id, day)
        return calc.housing_payer(hr['payer'].get(rid, '') if rid else '')

    def _customer_housing_input(self, month_ts, segs, plans, cust_lines, hr):
        """Дані для calc.customer_housing: людино-дні ліній (calc.housing_days) і правило житла (_housing_pick з правил hr) на кожен
        «лінія × замовник × день» із годинами → (days, day_rules)."""
        days = calc.housing_days(month_ts, segs, plans, cust_lines)
        day_rules = []
        for line, cust, day in sorted({(d['line'], d['cust'], d['date']) for d in days}):
            rid = self._housing_pick(hr, line, cust, day)
            if rid:
                day_rules.append({'line': line, 'cust': cust, 'date': day, 'payer': hr['payer'].get(rid, ''), 'amount': hr['amount'].get(rid, 0.0),
                                  'unit': hr['unit'].get(rid, '')})
        return days, day_rules

    def _txns(self, first, last):
        return self.env['workset.dkv.txn'].sudo().search_read([('date', '>=', first), ('date', '<=', last)], ['date', 'card_id', 'amount_gross'])

    # ——— перерахунок ———
    def _recompute_month(self, month):
        """Місяць заново: старі рядки місяця — геть, нові — з табелів, ставок, аналітики й DKV. → число рядків. Викликати на sudo."""
        first = calc.month_first(calc.iso(month))
        last = calc.month_last(first)
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (LOCK_NS, int(first[0:4] + first[5:7])))
        cust_lines, line_nodes = self._lines_index()
        # 1. години й частка: табелі від first − розрив − 1 (прибуття) до last; години місяця — частина
        ts = self._timesheets(calc.add_days(first, -(calc.STINT_GAP_DAYS + 1)), last)
        month_ts = [r for r in ts if first <= r['date'] <= last]
        persons = sorted({r['person'] for r in month_ts if r['person']})
        segs = calc.segments(month_ts, self._rate_days(persons, last) if persons else [], first, last)
        plans = self._plans(segs)
        hours = calc.hour_rows(segs, plans, cust_lines)
        # 1б. житло, яке вираховує замовник: правило житла лінії на кожен день із годинами → сума за одиницею правила; правила житла —
        # одне читання на місяць (і для «Хто платить» житла в кроці 2)
        hr = self._housing_rules()
        hours += [{'key': calc.key_of('line', line=line), 'housing_customer': v}
                  for line, v in calc.customer_housing(*self._customer_housing_input(month_ts, segs, plans, cust_lines, hr))]
        # 2–4. аналітика, DKV, перевезення за прибуттями (_month_entries — ті самі записи бачить картка лінії)
        entries, refs = self._month_entries(first, last, cust_lines, line_nodes, ts, hr)
        rows = calc.assemble(hours, entries)
        # 4б (круг 4). ZUS і PIT-4 за місяцем роботи + загальні фірм (рядки «Офіс» без аналітики рахунків ZUS / PIT-4 — вона вже в ZUS) →
        # частки ліній і «без лінії» за годинами табеля; чисто й решта рядків — без змін
        items = self._contrib_items(first)
        contrib = calc.contrib_sum(items)
        dup = self._contrib_in_rows(entries, refs)
        office = calc.eur(sum(calc.cents(r['net']) for r in rows if r['row_kind'] == 'office') - calc.cents(dup['office']))
        rows, oh = calc.overhead(rows, contrib['zus'], office)
        # 4в (круг 5б). склад «Рядків» картки кожної лінії — з тих самих записів розкладу (картка за період лише складає місяці)
        groups = calc.card_groups(entries, refs)
        # 5. запис: місяць заново (рядки пише лише код)
        self.search([('month', '=', first)]).unlink()
        Overhead = self.env['workset.profit.overhead'].sudo()
        Overhead.search([('month', '=', first)]).unlink()
        names = self._names(rows)
        vals = [self._row_vals(r, first, names) for r in rows]
        for v in vals:
            if v['row_kind'] == 'line':
                v['card_groups'] = groups.get(v['line_id']) or {'groups': []}
        self.create(vals)
        Overhead.create({'month': first, 'zus': oh['zus'], 'pit4': contrib['pit4'], 'office': oh['office'], 'office_dup': dup['office'],
                         'zus_elsewhere': dup['elsewhere'], 'total': oh['total'], 'hours': oh['hours'], 'per_hour': oh['per_hour'],
                         'zus_rest': oh['zus_rest'], 'office_rest': oh['office_rest'], 'move_ids': [(6, 0, contrib['move_ids'])],
                         'unlabeled_ids': [(6, 0, contrib['unlabeled_ids'])],
                         # круг 5: склад для вікна «Що входить» (ті самі рахунки й аналітика, з яких числа вище; рахує не він)
                         'breakdown': calc.overhead_breakdown(items, dup['by_analytic'])})
        return len(rows)

    # ——— ZUS і PIT-4 (круг 4; викликається з перерахунку й звірки — self уже sudo) ———
    def _move_texts(self, move):
        """Тексти рахунку для мітки місяця й «PIT-4»: ref і назви рядків (як інструмент штабу odoo_contrib_redate.js)."""
        return [move.ref or ''] + [ml.name or '' for ml in move.line_ids]

    def _contrib_items(self, first):
        """Рахунки ZUS і PIT-4 місяця роботи first: проведені рахунки постачальника (calc.CONTRIB_TYPES) фірм периметра «Ставок» з
        партнером ZUS або податкової, датовані у вікні calc.contrib_window (мітка буває на місяць уперед і до року назад), вид —
        calc.contrib_kind, місяць роботи — calc.contrib_month. Сума — рядки рахунку (display_type 'product': без податку й кредиторки),
        balance у валюті фірми → EUR за датою рядка (те саме правило, що в аналітики), мінусом. → [{'move_id', 'kind', 'amount', 'labeled'}]."""
        lo, hi = calc.contrib_window(first)
        dom = [('state', '=', 'posted'), ('move_type', 'in', list(calc.CONTRIB_TYPES)), ('date', '>=', lo), ('date', '<=', hi),
               '|', ('partner_id.name', 'ilike', 'ubezpiecze'), ('partner_id.name', 'ilike', 'skarbow')]
        skip = self.env['workset.rate'].sudo()._skip_companies()
        if skip:
            dom.append(('company_id', 'not in', skip))
        return [dict(it, labeled=calc.contrib_month(it['texts'], it['date'])['labeled'])
                for it in self._contrib_moves(dom) if calc.contrib_month(it['texts'], it['date'])['month'] == first]

    def _contrib_moves(self, dom):
        """Рахунки ZUS / PIT-4 за доменом account.move (вид — calc.contrib_kind) → [{'move_id', 'kind', 'amount', 'date', 'texts'}]; сума —
        рядки рахунку (display_type 'product': без податку й кредиторки), balance у валюті фірми → EUR за датою рядка, мінусом."""
        eur = self.env.ref('base.EUR')
        out = []
        for move in self.env['account.move'].sudo().search(dom, order='date, id'):
            texts = self._move_texts(move)
            kind = calc.contrib_kind(move.partner_id.name, texts)
            if not kind:
                continue
            cur = move.company_id.currency_id
            total = 0
            for ml in move.line_ids.filtered(lambda x: x.display_type == 'product'):
                amount = -ml.balance
                if cur and cur != eur:
                    amount = cur._convert(amount, eur, move.company_id, ml.date)
                total += calc.cents(amount)
            out.append({'move_id': move.id, 'kind': kind, 'amount': calc.eur(total), 'date': calc.iso(move.date), 'texts': texts})
        return out

    def _contrib_outside_moves(self, first, last):
        """Круг 4б: рахунки ZUS / PIT-4, проведені в місяці first – last, з міткою, до вікна якої дата проводки не входить
        (calc.contrib_outside) — їх не рахує жоден місяць → [{'move_id', 'kind', 'amount', 'date', 'texts'}] (_contrib_moves). Круг 5: їх
        показує й вікно «Що входить» (довідково)."""
        dom = [('state', '=', 'posted'), ('move_type', 'in', list(calc.CONTRIB_TYPES)), ('date', '>=', first), ('date', '<=', last),
               '|', ('partner_id.name', 'ilike', 'ubezpiecze'), ('partner_id.name', 'ilike', 'skarbow')]
        skip = self.env['workset.rate'].sudo()._skip_companies()
        if skip:
            dom.append(('company_id', 'not in', skip))
        # спершу мітка (тексти й дата), суми — лише рахункам поза вікном (зазвичай їх немає): переводу валют на кожен рахунок періоду
        # не потрібно (круг 5: вікно «Що входить» питає це й за рік)
        ids = [m.id for m in self.env['account.move'].sudo().search(dom) if calc.contrib_outside(self._move_texts(m), calc.iso(m.date))]
        if not ids:
            return []
        got = [it for it in self._contrib_moves(dom + [('id', 'in', ids)]) if calc.contrib_outside(it['texts'], it['date'])]
        return got

    def _contrib_outside(self, first, last):
        """Рахунки з міткою поза вікном, проведені в місяці first – last (_contrib_outside_moves) → {'n', 'amount'} для звірки (довідково)."""
        got = self._contrib_outside_moves(first, last)
        return {'n': len(got), 'amount': calc.eur(sum(calc.cents(it['amount']) for it in got))}

    def _contrib_ids(self, move_ids):
        """Які з рахунків move_ids — ZUS / PIT-4 (партнер і calc.contrib_kind) → множина id. Тексти рядків читаються лише в рахунків
        партнера ZUS чи податкової."""
        out = set()
        for move in self.env['account.move'].sudo().browse(sorted(move_ids)):
            name = move.partner_id.name or ''
            if (calc.ZUS_RE.search(name) or calc.TAX_RE.search(name)) and calc.contrib_kind(name, self._move_texts(move)):
                out.add(move.id)
        return out

    def _contrib_in_rows(self, entries, refs):
        """Аналітика рахунків ZUS / PIT-4 у розкладі місяця (за рахунком рядка аналітики — refs[i]['move']) → {'office' (у рядках «Офіс»:
        із загальних виключається — інакше двічі), 'elsewhere' (у лініях, «без лінії», «Не розподілено», «Власник» — довідково в
        звірці), 'by_analytic' (круг 4б: {стаття рядка «Офіс»: центи} — для calc.office_rows_in)}, EUR."""
        moves = {(ref or {}).get('move') for ref in refs or []} - {0, None}
        mine = self._contrib_ids(moves) if moves else set()
        office = elsewhere = 0
        by_analytic = {}
        for e, ref in zip(entries or [], refs or []):
            if (ref or {}).get('move') not in mine:
                continue
            if e['key'][0] == 'office':
                office += calc.cents(e['amount'])
                by_analytic[e['key'][3]] = by_analytic.get(e['key'][3], 0) + calc.cents(e['amount'])
            elif e['key'][0] in ('line', 'noline', 'unallocated', 'owner'):
                elsewhere += calc.cents(e['amount'])
        return {'office': calc.eur(office), 'elsewhere': calc.eur(elsewhere), 'by_analytic': by_analytic}

    def _month_entries(self, first, last, cust_lines, line_nodes, ts=None, hr=None):
        """Записи розкладу місяця (кроки 2–4 перерахунку): аналітика за розміткою, транзакції DKV за періодом картки, перевезення без лінії —
        між лініями за прибуттями. → (entries, refs): refs[i] — звідки entries[i]: {'aal': [id], 'txn': [id], 'analytic', 'account',
        'move' (рахунок рядка аналітики), 'card', 'pool'} — для «Рядків за місяць» картки лінії (ті самі записи, що в перерахунку, — без розбіжності). ts — табелі від
        first − розрив − 1 до last (немає — читаються, лише коли є перевезення без лінії); hr — правила житла (_housing_rules; немає —
        читаються тут один раз). Викликати на sudo."""
        # 2. аналітика: розмітка → вид, лінія, замовник → хто платить → рядок і стовпець
        rules = self.env['workset.profit.analytic.map']._rules()
        by_id = {r['id']: r for r in rules}
        cost_rules = self.env['workset.line.cost.rule']._rules()
        items = [i for i in self._analytic_items(first, last) if calc.cents(i['amount'])]
        # рахунки DKV із транзакціями: їхня аналітика — рядок 'dkv' і «DKV різниця» (calc.dkv_bill_entries), не вдруге
        gross = self._dkv_gross({i['move_id'] for i in items if i['move_id']})
        totals = self._bill_totals(set(gross))
        hr = hr if hr is not None else self._housing_rules()
        entries, refs, pool, bills = [], [], [], {}
        for it in items:
            if it['move_id'] in gross:
                bills.setdefault(it['move_id'], []).append(it)
                continue
            c = calc.classify(it, by_id.get(calc.map_pick(rules, it)), line_nodes, cust_lines)
            payer = ''
            if c['line_id'] and c['kind'] == 'housing':
                payer = self._housing_payer(hr, c['line_id'], c['cust'], it['date'])
            elif c['line_id'] and c['kind'] in calc.COST_KINDS:
                payer = calc.cost_payer(cost_rules, c['line_id'], c['cust'], c['kind'], it['date'])
            tgt = calc.target(c, it, payer)
            # move — рахунок рядка аналітики (круг 4: аналітика рахунків ZUS / PIT-4 — не в загальні, _contrib_in_rows)
            ref = {'aal': [it['aal_id']], 'analytic': it['analytic_id'], 'account': it['account_id'], 'move': it['move_id']}
            if tgt.get('pool'):
                pool.append(({'amount': it['amount'], 'src': 'aal', 'date': it['date']}, ref))
            else:
                entries.append(calc.entry(tgt, it['amount'], 'aal', '' if c['kind'] else c['hint']))
                refs.append(ref)
        for move in sorted(bills):
            got = calc.dkv_bill_entries(bills[move], totals.get(move, 0.0), gross[move])
            entries += got
            refs += [{'aal': [i['aal_id'] for i in bills[move]], 'analytic': bills[move][0]['analytic_id'],
                      'account': bills[move][0]['account_id']}] * len(got)
        # 3. DKV: транзакція → період картки на її дату
        periods = self.env['workset.dkv.card.period']._periods()
        for x in self._txns(first, last):
            day = calc.iso(x['date'])
            card = x['card_id'][0] if x['card_id'] else 0
            p = calc.card_period(periods, card, day)
            payer = calc.cost_payer(cost_rules, p['line_id'], p['customer_id'], 'fuel', day) if (p and p['target'] == 'line' and p['line_id']) else ''
            tgt = calc.dkv_target(p, card, payer)
            ref = {'txn': [x['id']], 'card': card}
            if tgt.get('pool'):
                pool.append(({'amount': -x['amount_gross'], 'src': 'dkv', 'date': day}, ref))
            else:
                entries.append(calc.entry(tgt, -x['amount_gross'], 'dkv'))
                refs.append(ref)
        # 4. перевезення без лінії — між лініями за прибуттями місяця (людина — склеєні картки); кожна сума — окремо (як і раніше)
        if pool:
            if ts is None:
                ts = self._timesheets(calc.add_days(first, -(calc.STINT_GAP_DAYS + 1)), last)
            weights = calc.arrival_weights(calc.arrivals([dict(r, person=r['glue']) for r in ts], first, last), cust_lines)
            for p, ref in pool:
                got = calc.pool_entries([p], weights, cost_rules)
                entries += got
                refs += [dict(ref, pool=True)] * len(got)
        return entries, refs

    def _store_check(self, month, check):
        """Підсумок звірки місяця — у «Звірку місяця» (екрани читають його, а не звіряють наново): є запис місяця — оновити, немає —
        створити. Замок місяця (той самий, що в перерахунку) — першим, пошук — уже під ним: другий запис чекає, поки перший закінчить
        транзакцію. Але знімок бази транзакції Odoo (REPEATABLE READ) фіксується першим читанням — у «Звірити» ще до замка, тож запис,
        який перерахунок створив, поки «Звірити» чекав, пошук не бачить. Тоді створення впирається в унікальність month (точка збереження,
        без помилки в журналі): ще раз пошук і оновлення; не видно й тепер — запис чужої транзакції новіший за наш знімок (її звірка
        бачила рядки, яких ми ще не бачили), лишаємо його. Викликати на sudo."""
        first = calc.month_first(calc.iso(month))
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (LOCK_NS, int(first[0:4] + first[5:7])))
        Check = self.env['workset.profit.check'].sudo()
        vals = {'ok': bool(check.get('ok')), 'text': check.get('text') or '', 'checked_at': fields.Datetime.now()}
        rec = Check.search([('month', '=', first)], limit=1)
        if rec:
            rec.write(vals)
            return
        try:
            with mute_logger('odoo.sql_db'), self.env.cr.savepoint():
                Check.create(dict(vals, month=first))
        except UniqueViolation:
            rec = Check.search([('month', '=', first)], limit=1)
            if rec:
                rec.write(vals)
            else:
                _logger.info('Звірка %s: запис місяця щойно створила інша транзакція — лишаємо її (новіша за наш знімок)', first)

    def _names(self, rows):
        def of(model, key):
            ids = sorted({r[key] for r in rows if r[key]})
            return {x.id: x.display_name for x in self.env[model].sudo().with_context(active_test=False).browse(ids)}
        return {'line': of('workset.line', 'line_id'), 'customer': of('res.partner', 'customer_id'),
                'analytic': of('account.analytic.account', 'analytic_id'), 'account': of('account.account', 'account_id'),
                'card': of('workset.dkv.card', 'card_id')}

    def _row_vals(self, r, first, names):
        # назва рядка зберігається українською (як назви записів «Ставок»)
        label = calc.row_name(r, {k: names[k].get(r[k + '_id'], '') for k in names}, 'uk')
        vals = {'name': label, 'month': first, 'row_kind': r['row_kind'], 'sort_key': SORT.get(r['row_kind'], 7),
                'line_id': r['line_id'] or False, 'customer_id': r['customer_id'] or False, 'analytic_id': r['analytic_id'] or False,
                'account_id': r['account_id'] or False, 'card_id': r['card_id'] or False, 'tag': r['tag'] or False,
                'hint_kind': r['hint'] or False, 'net': r['net'], 'net_per_hour': r['net_per_hour'], 'fund_left': r['fund_left']}
        vals.update({f: r[f] for f in calc.HOURS + calc.MONEY})
        # remain_per_hour без годин зі ставкою — пусто (calc.overhead → None), як «немає числа», а не 0 (круг 4б)
        vals.update({f: (r.get(f) if r.get(f) is not None else False) for f in calc.OVERHEAD_FIELDS})
        return vals

    # ——— звірка ———
    def _verify(self, month):
        """Звірка місяця (calc.verify): гроші аналітики в рядках = аналітика плану за місяць до цента; окремий підрахунок аналітики за
        датою (_read_group за фірмами, не через _analytic_items) = ті самі рядки в розкладі, у валюті фірми; гроші DKV = мінус брутто
        транзакцій місяця; у кожному рядку стовпці дають його суму; довідково — перенесене періодами послуги й транзакції DKV без рахунку.
        → {'ok', 'aal', 'aal_expected', 'dkv', 'dkv_expected', 'bad', 'raw_bad', 'text'}."""
        first = calc.month_first(calc.iso(month))
        last = calc.month_last(first)
        me = self.sudo()
        rows = me.search_read([('month', '=', first)], ['name', 'tag', 'row_kind', 'aal_amount', 'dkv_amount', 'zus_amount', 'office_amount']
                              + list(calc.SPREAD))
        items = me._analytic_items(first, last)
        aal = calc.eur(sum(calc.cents(i['amount']) for i in items))
        dkv = calc.eur(-sum(calc.cents(x['amount_gross']) for x in me._txns(first, last)))
        books = me._raw_by_currency(first, last)
        mine = {}
        for i in items:
            if i['in_month']:
                mine[i['currency']] = mine.get(i['currency'], 0) + calc.cents(i['line_cur'])
        raw = [{'currency': c, 'books': books.get(c, 0.0), 'calc': calc.eur(mine.get(c, 0))} for c in sorted(set(books) | set(mine))]
        moved = calc.eur(sum(calc.cents(i['amount']) for i in items) - sum(calc.cents(i['line_eur']) for i in items if i['in_month']))
        return calc.verify(rows, aal, dkv, first, self._lang(), raw=raw, moved=moved, nobill=me._nobill(first, last),
                           overhead=me._overhead_check(first, rows))

    def _overhead_check(self, first, rows):
        """Звірка ZUS і загальних (круг 4) для calc.verify: ZUS і PIT-4 за рахунками — наново (_contrib_items), загальні — рядки «Офіс»
        без аналітики рахунків ZUS / PIT-4 (office_dup — з підсумку місяця); у рядках — частки + не розподілене (немає годин). Підсумку
        «ZUS і загальні» місяця немає (рахували до круга 4) — None: не звіряється, до «Перерахувати». Викликати на sudo."""
        oh = self.env['workset.profit.overhead'].sudo().search([('month', '=', first)], limit=1)
        if not oh:
            return None
        contrib = calc.contrib_sum(self._contrib_items(first))
        outside = self._contrib_outside(first, calc.month_last(first))
        office = sum(calc.cents(r['amount']) for r in rows if r.get('row_kind') == 'office') - calc.cents(oh.office_dup)
        return {'zus_books': contrib['zus'], 'zus_rows': calc.eur(sum(calc.cents(r.get('zus_amount')) for r in rows) + calc.cents(oh.zus_rest)),
                'office_books': calc.eur(office),
                'office_rows': calc.eur(sum(calc.cents(r.get('office_amount')) for r in rows) + calc.cents(oh.office_rest)),
                'unlabeled_n': len(contrib['unlabeled_ids']), 'unlabeled': contrib['unlabeled'], 'elsewhere': oh.zus_elsewhere,
                'outside_n': outside['n'], 'outside': outside['amount']}

    # ——— публічне: інструмент штабу, кнопки, плановое ———
    @api.model
    def profit_recompute(self, date_from=None, date_to=None):
        """Перерахунок місяців від date_from до date_to («РРРР-ММ-ДД»; немає — останні RECALC_MONTHS місяців) і звірка кожного.
        Лише редактор прибутковості або адміністратор. → {'months', 'rows', 'verify': [{'month', 'ok', 'text', …}]}."""
        lang = self._lang()
        if not self._can_recompute():
            raise AccessError(calc.t('only_editor', lang))
        if date_from:
            months = calc.month_list(calc.iso(date_from), calc.iso(date_to or date_from))[-calc.MAX_MONTHS:]
        else:
            months = calc.months_back(calc.iso(self._today()))
        me = self.sudo()
        n = 0
        checks = []
        for m in months:
            n += me._recompute_month(m)
            checks.append(dict(me._verify(m), month=m))
            me._store_check(m, checks[-1])
        _logger.info('Прибутковість: перераховано %s — рядків %d; звірка: %s', ', '.join(months), n,
                     '; '.join(c['text'] for c in checks))
        return {'months': months, 'rows': n, 'verify': checks}

    @api.model
    def profit_verify(self, date_from=None, date_to=None):
        """Лише читання: звірка місяців від date_from до date_to («РРРР-ММ-ДД»; немає — останні RECALC_MONTHS місяців) — для інструмента
        штабу (--verify). Право — читати підсумок. → [{'month', 'ok', 'text', …}]."""
        self.check_access('read')
        if date_from:
            months = calc.month_list(calc.iso(date_from), calc.iso(date_to or date_from))[-calc.MAX_MONTHS:]
        else:
            months = calc.months_back(calc.iso(self._today()))
        return [dict(self._verify(m), month=m) for m in months]

    def _months_of(self):
        return sorted({calc.iso(r.month) for r in self})

    def action_recompute(self):
        """«Перерахувати»: місяці вибраних рядків, нічого не вибрано — останні RECALC_MONTHS місяців."""
        lang = self._lang()
        months = self._months_of()
        res = self.profit_recompute(months[0], months[-1]) if months else self.profit_recompute()
        ok = all(c['ok'] for c in res['verify'])
        text = ' '.join([calc.t('recalc_done', lang) % (len(res['months']), res['rows'])] + [c['text'] for c in res['verify']])
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Прибутковість'), 'message': text, 'type': 'success' if ok else 'warning', 'sticky': not ok,
                           'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_verify(self):
        """«Звірити»: місяці вибраних рядків, нічого не вибрано — поточний місяць. Підсумок — і в «Звірку місяця» (його показують екрани)."""
        self.check_access('read')
        months = self._months_of() or [calc.month_first(calc.iso(self._today()))]
        checks = [self._verify(m) for m in months]
        for m, c in zip(months, checks):
            if self.sudo().search_count([('month', '=', calc.month_first(m))]):
                self.sudo()._store_check(m, c)
        ok = all(c['ok'] for c in checks)
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Прибутковість'), 'message': ' '.join(c['text'] for c in checks), 'type': 'success' if ok else 'warning',
                           'sticky': not ok}}

    def action_open_card(self):
        """Кнопка рядка лінії в списку й картці рядка — картка лінії (екран B) за цей місяць. Рядок до calc.MIN_MONTH (кнопку вид ховає) —
        не січень 2026 мовчки, а повідомлення: картка — лише з 2026."""
        self.ensure_one()
        if calc.iso(self.month) < calc.MIN_MONTH:
            return {'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'title': _('Прибутковість'), 'type': 'warning',
                               'message': calc.t('card_before', self._lang()) % calc.month_label(self.month, self._lang())}}
        return {'type': 'ir.actions.client', 'tag': 'workset_profit.line_card', 'name': self.line_id.name or '',
                'params': {'line_id': self.line_id.id, 'month': calc.iso(self.month)}}

    @api.model
    def _cron_recompute(self):
        return self.profit_recompute()
