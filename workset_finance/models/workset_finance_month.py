# -*- coding: utf-8 -*-
"""«Місяць драбини» (Фінанси, круг 1) — хранимий підсумок для екрана «Власник»: від доходу за документами до чистого прибутку бізнесу
і «за грошима», поруч — власники, «II етап», неоплачено, посередництво, переказы на особисті рахунки, якість даних.

Звідки (правила ступенів — runtime/shared/2026-11-06-ekrany-po-rolyam/01-arhitektura.md, розділ B):
- 1 — рядки аналітики кореня плану рекрутингу за P&L-рахунками фірм периметра (проведені); контроль другим способом — проводки
  (account.move.line × analytic_distribution), розбіжність понад 0,01 — звірка «не зійшлася». Odoo 19 сам тримає розподіл у лад із рядками
  аналітики (account.analytic.line.unlink / write → move_line_id._update_analytic_distribution): видалення рядка через ORM стирає й розподіл,
  тож контроль ловить лише розбіжність повз ORM (SQL, міграція, контекст skip_analytic_sync), а рядок без розподілу видно в «без місця»;
  1а — вузли власників усередині тієї ж вибірки (повернення в прибуток); 1г — лише поправки + підрахунок кандидатів (сплачені рахунки клієнтів без аналітики, за датою оплати);
- 2 — Україна: фірма, план і рахунок ліквідності з параметрів; 3 — сума активних поправок ступеня 3; 4 = до часток − 3;
  4' = до часток − перекази партнерам нетто: проводки «II етапу» за місяць — рядок аналітики статті, а якщо в проводці його немає, рядки
  рахунку «II етапу» без аналітики (без подвійного рахунку; круг 1е; з іншою статтею — не в 4', примітка звірки, круг 2), що за текстом (name + ім'я партнера, підрядок, без регістру) потрапили на розпізнану
  лінію, × 9; транзитні фрази й нерозпізнані рядки в 4' не входять; AN — від'ємні рядки конверта як є);
- 5 — вузли власників, «частка» (статті з параметра) окремо; 6 — аналітика «II етапу» + баланс його рахунку;
- 7 — неоплачені рахунки клієнтів із часткою рекрутингу: замовники / Energy / міжфірмові й фірми поза периметром (не показуємо);
- якість даних — як «місяць правдивий» закриття місяця (--check) і сторож: міжфірмові (MC.truthTurnover), «без місця» (PG.identity, сума зі
  знаком), банк без документа (PG.bankNoDoc: 4 види, EUR за balance рядка на рахунку журналу, без дублів виписок, без вікна 14 днів), оборот
  із зовнішніми; частка = Σ|кошиків| ÷ оборот (MC.truthShare).
Фірми — усі активні мінус периметр «Ставок» (workset.rate._skip_companies). sudo бачить рядки всіх фірм, тому КОЖНЕ читання рядків аналітики,
проводок, документів, звірок оплат і виписок обмежене фірмами периметра явно (`('company_id', 'in', cos)`; ступінь 2 — одна фірма Україна).
«Свої» партнери — як partnerSets у tools/lib/odoo_pnl_guard.js: партнери фірм периметра й архівних — «міжфірмові», партнери фірм поза периметром
— окремий кошик (не «міжфірмові», не «без місця», не оборот). Пересчёт — finance_recompute (кнопка, щоночі, інструмент штабу);
звірка для інструмента — finance_verify (рахує наново, нічого не пише). sudo — лише всередині перерахунку й звірки, після перевірки групи.
Рядки пише лише код (права моделі)."""
import datetime
import json
import logging
from zoneinfo import ZoneInfo

from psycopg2.errors import UniqueViolation

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import mute_logger

from . import calc

_logger = logging.getLogger(__name__)

MONTH = 'workset.finance.month'
LOCK_NS = 7302  # простір ключів pg_advisory_xact_lock модуля (у «Прибутковості» — 7301)
SRC = [('fact', 'факт'), ('calc', 'розрах.'), ('adjust', 'поправка')]
GROUP = 'workset_finance.group_'


class WorksetFinanceMonth(models.Model):
    _name = MONTH
    _description = 'Фінанси: місяць драбини'
    _order = 'month desc, id'
    _rec_name = 'month'

    month = fields.Date(string='Місяць', required=True, index=True, readonly=True, help='Перше число місяця, не раніше січня 2026')
    state = fields.Selection([('open', 'Не закрито'), ('closed', 'Закрито')], string='Стан місяця', default='open', readonly=True,
                             help='Закрито: дати блокування всіх фірм периметра не раніше останнього дня місяця')
    recomputed_at = fields.Datetime(string='Перераховано', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))

    s1_docs = fields.Monetary(string='1 Рекрутинг за документами', currency_field='currency_id', readonly=True,
                              help='Аналітика кореня плану рекрутингу за P&L-рахунками проведених документів фірм периметра')
    s1_docs_src = fields.Selection(SRC, string='1: звідки', readonly=True)
    s1a_owners_in_pl = fields.Monetary(string='1а Власники в P&L', currency_field='currency_id', readonly=True,
                                       help='Мінус те, що вузли власників дали у вибірці ступеня 1: ці гроші йдуть у ступінь 5')
    s1a_owners_in_pl_src = fields.Selection(SRC, string='1а: звідки', readonly=True)
    s1g_no_analytic = fields.Monetary(string='1г Дохід без аналітики', currency_field='currency_id', readonly=True,
                                      help='Лише поправки штабу (з підставою): сплачені рахунки клієнтів без аналітики, за датою оплати')
    s1g_no_analytic_src = fields.Selection(SRC, string='1г: звідки', readonly=True)
    s2_ukraine = fields.Monetary(string='2 Україна', currency_field='currency_id', readonly=True,
                                 help='Передача ліквідності в Україну: рядки аналітики фірми Україна на рахунку ліквідності за планом Україна')
    s2_ukraine_src = fields.Selection(SRC, string='2: звідки', readonly=True)
    base = fields.Monetary(string='До часток', currency_field='currency_id', readonly=True, help='1 + 1а + 1г + 2')
    base_src = fields.Selection(SRC, string='До часток: звідки', readonly=True)
    s3_shares = fields.Monetary(string='3 Частки партнерів', currency_field='currency_id', readonly=True,
                                help='Сума активних поправок ступеня 3 (додатне число — віднімається від «до часток»)')
    s3_shares_src = fields.Selection(SRC, string='3: звідки', readonly=True)
    s4_net = fields.Monetary(string='4 Чистий прибуток', currency_field='currency_id', readonly=True, help='До часток мінус частки партнерів')
    s4_net_src = fields.Selection(SRC, string='4: звідки', readonly=True)
    s4c_cash = fields.Monetary(string="4' За грошима", currency_field='currency_id', readonly=True,
                               help='До часток мінус перекази партнерам нетто (проводки «II етапу» на розпізнаних лініях × 9: рядок аналітики, без нього — рядки '
                                    'рахунку без аналітики; без транзиту; конверт AN)')
    s4c_cash_src = fields.Selection(SRC, string="4': звідки", readonly=True)
    s5_owners = fields.Monetary(string='5 Власники', currency_field='currency_id', readonly=True, help='Мінус аналітика вузлів власників (витрати — додатне число)')
    s5_owners_src = fields.Selection(SRC, string='5: звідки', readonly=True)
    s5_owners_share = fields.Monetary(string='5 з них частка', currency_field='currency_id', readonly=True,
                                      help='Частина ступеня 5 на статтях «частка» (параметр); решта — витрати компанії')
    s6_stage2 = fields.Monetary(string='6 II етап', currency_field='currency_id', readonly=True,
                                help='Аналітика статті «II етап» + баланс рахунку «II етапу» за проведеними рядками')
    s6_stage2_src = fields.Selection(SRC, string='6: звідки', readonly=True)
    s7_unpaid_customers = fields.Monetary(string='7 Не оплачено замовниками', currency_field='currency_id', readonly=True,
                                          help='Залишок неоплачених рахунків місяця з часткою рекрутингу; станом на момент перерахунку')
    s7_unpaid_customers_src = fields.Selection(SRC, string='7з: звідки', readonly=True)
    s7_unpaid_energy = fields.Monetary(string='7 Не оплачено Energy', currency_field='currency_id', readonly=True,
                                       help='Те саме для контрагентів групи Energy (параметр); міжфірмові й фірми поза периметром не показуємо')
    s7_unpaid_energy_src = fields.Selection(SRC, string='7е: звідки', readonly=True)

    revenue = fields.Monetary(string='Довідково: дохід', currency_field='currency_id', readonly=True, help='Дохідні рядки вибірки ступеня 1')
    payroll = fields.Monetary(string='Довідково: зарплата', currency_field='currency_id', readonly=True, help='Витрати вибірки ступеня 1 у журналах зарплати (параметр)')
    contrib_zus = fields.Monetary(string='Довідково: ZUS і PIT-4', currency_field='currency_id', readonly=True,
                                  help='Витрати вибірки ступеня 1 на контрагентах ZUS (параметр)')
    cash_out = fields.Monetary(string='Перекази партнерам нетто', currency_field='currency_id', readonly=True, help='Що віднімається в «за грошима»')
    mediation_income = fields.Monetary(string='Посередництво: дохід', currency_field='currency_id', readonly=True)
    mediation_cost = fields.Monetary(string='Посередництво: витрати', currency_field='currency_id', readonly=True)
    mediation_net = fields.Monetary(string='Посередництво: залишок', currency_field='currency_id', readonly=True)
    g_cand_amount = fields.Monetary(string='1г: кандидати, сума', currency_field='currency_id', readonly=True,
                                    help='Сплачені рахунки клієнтів без аналітики за датою оплати — для порівняння з поправкою 1г')
    g_cand_count = fields.Integer(string='1г: кандидатів', readonly=True, aggregator=False)
    q_ic = fields.Monetary(string='Якість: міжфірмові', currency_field='currency_id', readonly=True)
    q_unplaced = fields.Monetary(string='Якість: без місця', currency_field='currency_id', readonly=True,
                                 help='Як PG.identity закриття місяця: сума зі знаком (дохід мінус витрата), частка — від її модуля')
    q_nodoc = fields.Monetary(string='Якість: банк без документа', currency_field='currency_id', readonly=True,
                              help='Несвірені витрати виписок місяця 4 видів (ZUS і податки, без партнера, фірма й фізособа без рахунків), EUR')
    q_ext = fields.Monetary(string='Якість: оборот із зовнішніми', currency_field='currency_id', readonly=True)
    q_pct = fields.Float(string='Якість даних, %', readonly=True, digits=(16, 1), aggregator=False, help='(|міжфірмові| + |без місця| + |банк без документа|) ÷ оборот із зовнішніми')

    # Odoo 19: обмеження бази — models.Constraint
    _month_uniq = models.Constraint('UNIQUE(month)', 'Цей місяць вже є: рядок місяця один.')

    @api.constrains('month')
    def _check_month(self):
        for r in self:
            if not calc.month_ok(r.month):
                raise ValidationError(calc.t('month_range', self._lang()) % calc.iso(r.month))

    # ——— службове ———
    def _lang(self):
        return calc.lang_of(self.env.lang)

    def _is_admin(self):
        return self.env.is_superuser() or self.env.user.has_group('base.group_system')

    def _can_recompute(self):
        return self._is_admin() or self.env.user.has_group(GROUP + 'editor')

    def _check_screen(self, group='owner'):
        """Лише група «Фінанси: <група>» (і суперкористувач: плановое, тести). Адміністратор поза групою — відмова."""
        if self.env.su or self.env.user.has_group(GROUP + group):
            return
        raise AccessError(calc.t('only_group', self._lang()) % {'owner': 'Власник', 'editor': 'Поправки'}.get(group, group))

    @api.model
    def _today(self):
        forced = self.env.context.get('finance_today')
        if forced and self._is_admin():
            return calc.to_date(str(forced)[0:10])
        return datetime.datetime.now(ZoneInfo('Europe/Kyiv')).date()

    def _params(self):
        """Параметри workset_finance.* → {ім'я без префікса: значення}. Немає обов'язкового — UserError «параметр не посіяно»."""
        get = self.env['ir.config_parameter'].sudo().get_param
        values = {n: calc.param_value(kind, get(calc.P_PREFIX + n) or '') for n, kind, _req in calc.PARAM_SPECS}
        miss = calc.missing_params(values)
        if miss:
            raise UserError(calc.t('param_missing', self._lang()) % ', '.join(miss))
        return values

    def _param_notes(self, lang):
        """Примітки звірки про параметри (calc.param_notes): фрази-числа розбір пропускає — звірка називає параметр і скільки; посередники
        (mediator_partner_ids) не посіяно — примітка (круг 2, ревю 1е), щоб забутий --seed не ховав посередників в обороті."""
        get = self.env['ir.config_parameter'].sudo().get_param
        return calc.param_notes({n: get(calc.P_PREFIX + n) or '' for n, kind, _req in calc.PARAM_SPECS
                                 if kind in ('phrases', 'phrasepairs') or n == 'mediator_partner_ids'}, lang)

    def _perimeter(self):
        """Фірми периметра: усі активні мінус workset_rates.out_of_perimeter_company_ids."""
        skip = set(self.env['workset.rate'].sudo()._skip_companies())
        return sorted(c.id for c in self.env['res.company'].sudo().search([]) if c.id not in skip)

    def _partner_sets(self):
        """«Свої» партнери (calc.partner_sets) → (міжфірмові, поза периметром): усі фірми, і архівні; фірми поза периметром «Ставок» — окремо."""
        skip = self.env['workset.rate'].sudo()._skip_companies()
        firms = self.env['res.company'].sudo().with_context(active_test=False).search([])
        got = calc.partner_sets([(c.id, c.partner_id.id) for c in firms], skip)
        return set(got['ic']), set(got['out'])

    def _months(self, months):
        """Список місяців: немає — останні RECALC_MONTHS із поточним (не раніше 2026); є — перші числа, не раніше 2026, не більше MAX_MONTHS."""
        if not months:
            return calc.months_back(calc.iso(self._today()))
        out = sorted({calc.month_first(calc.iso(m)) for m in months})
        for m in out:
            if not calc.month_ok(m):
                raise ValidationError(calc.t('month_range', self._lang()) % m)
        if len(out) > calc.MAX_MONTHS:
            raise ValidationError(calc.t('month_many', self._lang()) % (len(out), calc.MAX_MONTHS))
        return out

    # ——— читання бази (self уже sudo) ———
    def _asum(self, dom, model='account.analytic.line', field='amount'):
        rows = self.env[model]._read_group(dom, [], ['%s:sum' % field])
        return calc.eur(calc.cents(rows[0][0] if rows else 0))

    def _maps(self, ids, p):
        """Рахунки аналітики з розподілів → ({id: корінь плану}, {id: 'rec' / 'med' / ''})."""
        Acc = self.env['account.analytic.account'].with_context(active_test=False)
        med = set(self.env['account.analytic.plan'].search([('id', 'child_of', p['mediation_plan_id'])]).ids) if p['mediation_plan_id'] else set()
        root_of, kind_of = {}, {}
        for a in Acc.browse(sorted(ids)).exists():
            root = a.root_plan_id.id
            root_of[a.id] = root
            kind_of[a.id] = 'rec' if root == p['recruting_plan_id'] else ('med' if a.plan_id.id in med else '')
        return root_of, kind_of

    @staticmethod
    def _item(kind, name, amount, **kw):
        dom = kw.pop('domain', None)
        out = {'kind': kind, 'name': name, 'amount': amount, 'count': kw.pop('count', 0), 'res_model': kw.pop('model', ''),
               'domain': json.dumps(dom) if dom is not None else '', 'note': kw.pop('note', '')}
        out.update(kw)
        return out

    def _closed(self, cos, last):
        """Закрито: дати блокування (фіскальна чи жорстка) кожної фірми периметра не раніше останнього дня місяця; у фірми з батьківською
        (parent_id) дата — найпізніша по ланцюгу вгору: Odoo застосовує блокування батька до дочірніх фірм."""
        Company = self.env['res.company']
        names = [n for n in ('fiscalyear_lock_date', 'hard_lock_date') if n in Company._fields]
        if not names or not cos:
            return False
        for c in Company.browse(cos):
            best, seen, cur = '', set(), c
            while cur and cur.id not in seen:
                seen.add(cur.id)
                best = max([best] + [calc.iso(cur[n]) for n in names if cur[n]])
                cur = cur.parent_id
            if not best or best < last:
                return False
        return True

    def _candidates(self, first, last, cos, excl):
        """1г: сплачені рахунки клієнтів без аналітики в рядках товару; місяць — за датою останньої оплати → ({партнер: [центи, ids]}, всього)."""
        Partial = self.env['account.partial.reconcile']
        partials = Partial.search([('company_id', 'in', cos), ('max_date', '>=', first), ('max_date', '<=', last)])
        moves = (partials.debit_move_id | partials.credit_move_id).move_id.filtered(
            lambda m: m.move_type == 'out_invoice' and m.state == 'posted' and m.payment_state in calc.PAID_STATES and m.company_id.id in cos
            and m.commercial_partner_id.id not in excl)
        by = {}
        for m in moves:
            recv = m.line_ids.filtered(lambda x: x.account_id.account_type == 'asset_receivable')
            dates = (recv.matched_debit_ids | recv.matched_credit_ids).mapped('max_date')
            if not dates or not (first <= calc.iso(max(dates)) <= last):
                continue
            free = m.line_ids.filtered(lambda x: x.display_type == 'product' and not x.analytic_distribution)
            c = calc.cents(-sum(free.mapped('balance')))
            if c:
                row = by.setdefault(m.commercial_partner_id.id, [0, []])
                row[0] += c
                row[1].append(m.id)
        return by

    def _pl_pass(self, first, last, cos, p, ic, out):
        """Один прохід по проведених P&L-рядках проводок фірм периметра (за id, як закриття місяця): контроль ступеня 1 (інший спосіб) і кошики
        «якості даних» → (контроль у центах, міжфірмові: центи, без місця: calc.identity_unplaced). Міжфірмові — як MC.truthTurnover: увесь рядок
        документа з партнером — своєю фірмою, крім транзиту; «без місця» — як PG.identity (частки інших планів — за будь-якого партнера,
        нерозподілений залишок — лише зовнішнього). Документи «транзиту Energy» (параметр) — у контролі є."""
        transit = set(p['quality_transit_move_ids'])
        mgmt = set(p['excluded_partner_ids'])
        ML = self.env['account.move.line']
        lines = ML.search([('parent_state', '=', 'posted'), ('account_id.account_type', 'in', list(calc.PL_TYPES)), ('company_id', 'in', cos),
                           ('date', '>=', first), ('date', '<=', last)], order='id')
        keys = set()
        for l in lines:
            for ids, _pc in calc.dist_keys(l.analytic_distribution):
                keys.update(ids)
        root_of, kind_of = self._maps(keys, p)
        control = ic_c = 0
        rows = []
        for l in lines:
            dist = l.analytic_distribution
            if dist:
                control += calc.rec_part_cents(l.balance, dist, root_of, p['recruting_plan_id'])
            move, partner = l.move_id.id, l.move_id.commercial_partner_id.id
            if move not in transit and partner in ic:
                ic_c += calc.cents(-l.balance)
            rows.append((l.id, l.balance, dist, calc.identity_bucket(move, partner, transit, ic, out, mgmt)))
        return control, ic_c, calc.identity_unplaced(rows, kind_of)

    def _eur_of(self, st, cos):
        """Рядки виписок → {рядок: сума в EUR}: balance рядків проводки на рахунку журналу (default_account_id), як eurOf закриття місяця —
        журнали в злотих мають amount у злотих."""
        if not st:
            return {}
        dom_eur = [('statement_line_id', 'in', st.ids), ('account_id', 'in', st.journal_id.default_account_id.ids), ('company_id', 'in', cos)]
        return {sl.id: bal or 0.0 for sl, bal in self.env['account.move.line']._read_group(dom_eur, ['statement_line_id'], ['balance:sum'])}

    def _bank_nodoc(self, first, last, cos, our, mediators):
        """«Банк без документа» як закриття місяця (--check): несвірені рядки виписок фірм периметра за місяць — без вікна «старше 14 днів»
        (на закритті все має бути розібрано; його ж частку бере сторож), сума в EUR (_eur_of), 4 види calc.NO_DOC_CATS, дублі виписок відсіяно →
        calc.bank_nodoc як є ({'cats', 'nodoc', 'dups'}; рядки — у cats[вид]['ids'])."""
        BSL = 'account.bank.statement.line'
        dom_bank = [('company_id', 'in', cos), ('is_reconciled', '=', False), ('date', '>=', first), ('date', '<=', last)]
        st = self.env[BSL].search(dom_bank, order='id')
        eur = self._eur_of(st, cos)
        commercial = {x.id: x.commercial_partner_id.id or x.id for x in st.partner_id}
        comm = self.env['res.partner'].with_context(active_test=False).browse(sorted(set(commercial.values())))
        emp = set(self.env['hr.employee'].with_context(active_test=False).search([('work_contact_id', '!=', False)]).work_contact_id.ids)
        bills = set()
        if comm:
            dom_bills = [('move_type', 'in', ['in_invoice', 'in_refund']), ('state', '=', 'posted'), ('commercial_partner_id', 'in', comm.ids),
                         ('date', '>=', calc.bills_from(last)), ('company_id', 'in', cos)]
            bills = {'%s|%s' % (c.id, co.id) for c, co in self.env['account.move']._read_group(dom_bills, ['commercial_partner_id', 'company_id'], [])}
        rows = [{'id': l.id, 'partner': l.partner_id.id or 0, 'company': l.company_id.id, 'journal': l.journal_id.id, 'date': calc.iso(l.date),
                 'amount': l.amount, 'ref': l.payment_ref or '', 'created': fields.Datetime.to_string(l.create_date) if l.create_date else ''} for l in st]
        return calc.bank_nodoc(rows, eur, {'commercial': commercial, 'is_company': {c.id: bool(c.is_company) for c in comm}, 'our': our,
                                           'mgmt': mediators, 'emp': emp, 'bills': bills})

    def _unpaid(self, first, last, cos, p, ic, out_partners):
        """7: неоплачені рахунки клієнтів місяця з часткою рекрутингу → ({'customer'|'energy': {партнер: [центи, ids]}}); міжфірмові й партнери
        фірм поза периметром (не з Energy) не показуємо."""
        Move = self.env['account.move']
        inv = Move.search([('move_type', 'in', list(calc.OUT_TYPES)), ('state', '=', 'posted'), ('company_id', 'in', cos),
                           ('invoice_date', '>=', first), ('invoice_date', '<=', last), ('amount_residual', '>', calc.UNPAID_MIN),
                           ('payment_state', 'not in', list(calc.UNPAID_SKIP))])
        lines = self.env['account.move.line'].search([('move_id', 'in', inv.ids), ('display_type', '=', 'product'),
                                                      ('company_id', 'in', cos)]).filtered('analytic_distribution')
        keys = set()
        for l in lines:
            for ids, _pc in calc.dist_keys(l.analytic_distribution):
                keys.update(ids)
        root_of, _kinds = self._maps(keys, p)
        rec_moves = {l.move_id.id for l in lines if calc.has_root(l.analytic_distribution, root_of, p['recruting_plan_id'])}
        energy = set(p['energy_partner_ids'])
        out = {'customer': {}, 'energy': {}}
        for m in inv.filtered(lambda x: x.id in rec_moves):
            partner = m.commercial_partner_id.id
            bucket = calc.unpaid_bucket(partner, ic, energy, out_partners)
            if bucket not in out:
                continue
            row = out[bucket].setdefault(partner, [0, []])
            row[0] += calc.cents((-1 if m.move_type == 'out_refund' else 1) * m.amount_residual)
            row[1].append(m.id)
        return out

    # ——— розрахунок місяця (нічого не пише) ———
    def _compute_month(self, first):
        """Усі ступені місяця й деталі для кліків → {'steps', 'src', 'items', 'control', 'cand', 'state'}. Викликати на sudo."""
        me = self.sudo()
        last = calc.month_last(first)
        p = me._params()
        cos = me._perimeter()
        dates = [('date', '>=', first), ('date', '<=', last)]
        AAL, ML = 'account.analytic.line', 'account.move.line'
        # «свої» партнери: міжфірмові (ic) і фірми поза периметром (out) — окремо; обидва, як і посередники-виняток, поза кандидатами 1г
        # (excl); «без місця», оборот і банк — за правилами закриття місяця (нижче, «якість даних»)
        ic, out = me._partner_sets()
        excl = ic | out | set(p['excluded_partner_ids'])
        items = []
        item = me._item

        # 1, 1а + справочно
        dom1 = [('account_id.root_plan_id', '=', p['recruting_plan_id']), ('general_account_id.account_type', 'in', list(calc.PL_TYPES)),
                ('company_id', 'in', cos), ('move_line_id.parent_state', '=', 'posted')] + dates
        dom1a = dom1 + [('account_id.plan_id', 'child_of', p['owners_plan_id'])]
        s1, s1a = me._asum(dom1), -me._asum(dom1a)
        revenue = me._asum(dom1 + [('general_account_id.account_type', 'in', list(calc.INCOME_TYPES))])
        payroll = -me._asum(dom1 + [('move_line_id.journal_id', 'in', p['payroll_journal_ids'])]) if p['payroll_journal_ids'] else 0.0
        zus = -me._asum(dom1 + [('partner_id', 'in', p['zus_partner_ids'])]) if p['zus_partner_ids'] else 0.0
        items.append(item('step', 's1_docs', s1, model=AAL, domain=dom1, note='s1_docs'))
        items.append(item('step', 's1a_owners_in_pl', s1a, model=AAL, domain=dom1a, note='s1a_owners_in_pl'))

        # 2 Україна
        dom2 = [('company_id', '=', p['ukraine_company_id']), ('general_account_id', '=', p['liquidity_account_id']),
                ('account_id.plan_id', 'child_of', p['ukraine_plan_id'])] + dates
        s2 = 0
        for acc, amount, n in me.env[AAL]._read_group(dom2, ['account_id'], ['amount:sum', '__count']):
            s2 += calc.cents(amount)
            items.append(item('ukraine', acc.display_name, calc.eur(calc.cents(amount)), count=n, analytic_id=acc.id, model=AAL,
                              domain=dom2 + [('account_id', '=', acc.id)]))
        s2 = calc.eur(s2)
        items.append(item('step', 's2_ukraine', s2, model=AAL, domain=dom2, note='s2_ukraine'))

        # 5 власники
        dom5 = [('account_id.plan_id', 'child_of', p['owners_plan_id']), ('company_id', 'in', cos)] + dates
        share_ids = set(p['owners_share_analytic_ids'])
        s5 = s5_share = 0
        for acc, amount, n in me.env[AAL]._read_group(dom5, ['account_id'], ['amount:sum', '__count']):
            c = calc.cents(-amount)
            s5 += c
            is_share = acc.id in share_ids
            s5_share += c if is_share else 0
            items.append(item('owner_share' if is_share else 'owner', acc.display_name, calc.eur(c), count=n, analytic_id=acc.id, model=AAL,
                              domain=dom5 + [('account_id', '=', acc.id)]))
        s5, s5_share = calc.eur(s5), calc.eur(s5_share)

        # 6 II етап і 4' (перекази партнерам нетто)
        dom6a = [('account_id', '=', p['stage2_analytic_id']), ('company_id', 'in', cos)] + dates
        dom6m = [('account_id', '=', p['stage2_account_id']), ('parent_state', '=', 'posted'), ('company_id', 'in', cos)] + dates
        a6 = me._asum(dom6a)
        items.append(item('stage2', 'analytic', a6, model=AAL, domain=dom6a, note='analytic'))
        m6 = 0
        lang = me._lang()
        for partner, bal, n in me.env[ML]._read_group(dom6m, ['partner_id'], ['balance:sum', '__count']):
            m6 += calc.cents(bal)
            items.append(item('stage2', partner.display_name or '—', calc.eur(calc.cents(bal)), count=n, partner_id=partner.id or False, model=ML,
                              domain=dom6m + [('partner_id', '=', partner.id)], note='account'))
        s6 = calc.eur(calc.cents(a6) + m6)
        # 4': одиниця — проводка «II етапу» (рахунок stage2_account_id): є рядок аналітики статті — береться він; немає — рядок рахунку цієї
        # проводки (знак і сума — як у рядка статті: додатна частка 10 %), без подвійного рахунку (проводка з обома — один раз; круг 1д:
        # «лише рядки статті» губило проводки без аналітики). Партнера майже немає — одержувач у тексті: фраза → лінія (приватний посів),
        # решта — «не розпізнано», транзитні фрази — «транзит»; рядків за місяць десятки, тому зіставлення — циклом у Python.
        # Текст для збігу = name рядка + ім'я партнера рядка (рядка аналітики — або партнера move_line_id, рядка рахунку — або партнера проводки).
        # Круг 2 (ревю 1е): у проводці без рядка статті в 4' — лише рядки рахунку БЕЗ аналітики (як документ v2: пара «+X без аналітики / −X зі
        # статтею» інакше давала 0, а рядок конверта AN рахувався б удруге); рядки з іншою статтею — не в 4', примітка звірки (calc.stage2_other)
        aal6 = me.env[AAL].search(dom6a, order='id')
        with_an = set(aal6.move_line_id.move_id.ids)
        ml6_all = me.env[ML].search(dom6m, order='id').filtered(lambda x: x.move_id.id not in with_an)
        ml6 = ml6_all.filtered(lambda x: not x.analytic_distribution)
        other6 = calc.stage2_other([(l.id, l.analytic_distribution, calc.cents(l.balance)) for l in ml6_all if l.analytic_distribution],
                                   p['an_envelope_analytic_id'])

        def text6(name, partner):
            return ' '.join(x for x in (name or '', partner.name or '') if x)
        rows6 = [('a%d' % l.id, text6(l.name, l.partner_id or l.move_line_id.partner_id), calc.cents(l.amount)) for l in aal6]
        rows6 += [('m%d' % l.id, text6(l.name, l.partner_id or l.move_id.partner_id), calc.cents(l.balance)) for l in ml6]
        amount6 = {rid: c for rid, _t, c in rows6}
        cash6 = calc.stage2_cash(rows6, p['stage2_partner_lines'], p['stage2_transit_phrases'])
        Line = me.env['workset.line']
        have_lines = set(Line.browse(sorted({g['line'] for g in cash6['groups'] if g['line']})).exists().ids)
        lost_lines = 0
        for g in cash6['groups']:
            line_ok = g['line'] in have_lines
            lost_lines += 1 if g['status'] == 'net' and not line_ok else 0
            # рядок деталі на джерело: рядки аналітики й рядки рахунку — різні моделі списку кліку
            for model, tag in ((AAL, 'a'), (ML, 'm')):
                rids = [r for r in g['ids'] if r[0] == tag]
                if not rids:
                    continue
                gross = sum(amount6[r] for r in rids)
                pd = [('id', 'in', [int(r[1:]) for r in rids])]
                src = '' if tag == 'a' else '; %s' % calc.t('no_analytic_row', lang)
                if g['status'] == 'net':
                    note = calc.t('gross', lang) % calc.money_text(calc.eur(gross * calc.STAGE2_BRUTTO))
                    items.append(item('cash', Line.browse(g['line']).display_name if line_ok else '—', calc.eur(gross * calc.STAGE2_MULT), count=len(rids),
                                      line_id=g['line'] if line_ok else False, model=model, domain=pd,
                                      note=(note if line_ok else '%s; %s' % (note, calc.t('line_gone', lang))) + src))
                else:
                    key = 'unknown' if g['status'] == 'unknown' else 'transit'
                    items.append(item('cash_other', calc.t(key, lang), calc.eur(gross * calc.STAGE2_MULT), count=len(rids), model=model, domain=pd,
                                      note='%s; %s%s' % (calc.t(key, lang), calc.t('not_in_4', lang), src)))
        partner_net = cash6['partner_net']
        notes = ([calc.t('line_missing', lang) % lost_lines] if lost_lines else []) + me._param_notes(lang)
        if other6['n']:
            notes.append(calc.t('stage2_other', lang) % (other6['n'], calc.money_text(calc.eur(other6['cents']))))
        an = 0.0
        if p['an_envelope_analytic_id']:
            dom_an = [('account_id', '=', p['an_envelope_analytic_id']), ('amount', '<', 0), ('company_id', 'in', cos)] + dates
            an = -me._asum(dom_an)
            items.append(item('cash', 'AN', an, model=AAL, domain=dom_an, note='AN'))
        cash_out = calc.eur(partner_net + calc.cents(an))

        # 3 і 1г: поправки
        adj = me.env['workset.finance.adjust'].search([('month', '=', first)])
        sp = calc.split_adjust([{'step': a.step, 'line': a.line_id.id, 'amount': a.amount} for a in adj])
        for line_id, c in sorted(sp['lines'].items()):
            items.append(item('adjust', 's3', calc.eur(c), line_id=line_id or False, model='workset.finance.adjust',
                              domain=[('month', '=', first), ('step', '=', 's3')] + ([('line_id', '=', line_id)] if line_id else []), note='s3'))
        if sp['s1g']:
            items.append(item('adjust', 's1g', calc.eur(sp['s1g']), model='workset.finance.adjust', domain=[('month', '=', first), ('step', '=', 's1g')], note='s1g'))
        cand = me._candidates(first, last, cos, excl)
        cand_total = sum(v[0] for v in cand.values())
        cand_n = sum(len(v[1]) for v in cand.values())
        for partner, (c, ids) in sorted(cand.items()):
            items.append(item('candidate', me.env['res.partner'].browse(partner).display_name or '—', calc.eur(c), count=len(ids), partner_id=partner,
                              model='account.move', domain=[('id', 'in', ids)]))

        # 7 неоплачено
        unpaid = me._unpaid(first, last, cos, p, ic, out)
        sums = {}
        for bucket, rows in unpaid.items():
            sums[bucket] = sum(v[0] for v in rows.values())
            for partner, (c, ids) in sorted(rows.items()):
                items.append(item('unpaid', me.env['res.partner'].browse(partner).display_name or '—', calc.eur(c), count=len(ids), partner_id=partner,
                                  model='account.move', domain=[('id', 'in', ids)], note=bucket))

        # переказы на особисті рахунки
        BSL = 'account.bank.statement.line'
        legs = [('payment_ref', 'ilike', ph) for ph in p['transfer_phrases']]
        if p['transfer_partner_ids']:
            legs.append(('partner_id', 'in', p['transfer_partner_ids']))
        if legs:
            # сума в EUR — balance рядка проводки на рахунку журналу (_eur_of): amount виписки — у валюті журналу (злоті як євро, круг 1е)
            domt = [('company_id', 'in', cos)] + dates + calc.or_domain(legs)
            tr = me.env[BSL].search(domt, order='id')
            eur_t = me._eur_of(tr, cos)
            Partner = me.env['res.partner']
            for partner, rec, c, ids in calc.transfer_groups([(l.id, l.partner_id.id, l.is_reconciled, eur_t.get(l.id)) for l in tr]):
                items.append(item('transfer', Partner.browse(partner).display_name if partner else '—', calc.eur(c), count=len(ids), partner_id=partner or False,
                                  model=BSL, domain=[('id', 'in', ids)], note=calc.t('cleared' if rec else 'nodoc', lang)))

        # посередництво
        med_in = med_cost = 0.0
        if p['mediation_plan_id']:
            dom_m = [('account_id.plan_id', 'child_of', p['mediation_plan_id']), ('company_id', 'in', cos)] + dates
            med_in = me._asum(dom_m + [('amount', '>', 0)])
            med_cost = -me._asum(dom_m + [('amount', '<', 0)])

        # якість даних — як «місяць правдивий» закриття місяця (tools/odoo_month_close.js --check: MC.truthTurnover, PG.identity, PG.bankNoDoc,
        # MC.truthShare); --verify звіряє кожен кошик, оборот і частку саме з ним
        control, ic_c, unp = me._pl_pass(first, last, cos, p, ic, out)
        transit_dom = [('move_id', 'not in', p['quality_transit_move_ids'])] if p['quality_transit_move_ids'] else []
        # оборот із зовнішніми — як truthTurnover: без своїх фірм, фірм поза периметром і посередників (параметр mediator_partner_ids)
        ext_excl = ic | out | set(p['mediator_partner_ids'])
        ext = -me._asum([('parent_state', '=', 'posted'), ('account_id.account_type', 'in', list(calc.INCOME_TYPES)), ('company_id', 'in', cos),
                         ('move_id.move_type', 'in', list(calc.EXT_TYPES)), ('move_id.commercial_partner_id', 'not in', sorted(ext_excl))] + transit_dom + dates,
                        model=ML, field='balance')
        bank = me._bank_nodoc(first, last, cos, ic | out, set(p['mediator_partner_ids']))
        nodoc = calc.eur(calc.cents(-bank['nodoc']['sum']))
        items.append(item('quality', 'ic', calc.eur(ic_c), model=ML, note='ic',
                          domain=[('parent_state', '=', 'posted'), ('account_id.account_type', 'in', list(calc.PL_TYPES)), ('company_id', 'in', cos),
                                  ('move_id.commercial_partner_id', 'in', sorted(ic))] + transit_dom + dates))
        # клік «без місця» — рівно ті рядки, що дали внесок (і частки інших планів, і залишок без розподілу), а не «без розподілу взагалі»
        items.append(item('quality', 'unplaced', calc.eur(calc.cents(unp['unplaced'])), count=len(unp['ids']), model=ML, note='unplaced',
                          domain=[('id', 'in', unp['ids'])]))
        for k in calc.NO_DOC_CATS:
            cat = bank['cats'][k]
            if cat['n']:
                items.append(item('quality', calc.t('nodoc_' + k, lang), calc.eur(calc.cents(-cat['sum'])), count=cat['n'], model=BSL,
                                  domain=[('id', 'in', cat['ids'])], note='nodoc:' + k))

        v = {'s1_docs': s1, 's1a_owners_in_pl': s1a, 's1g_no_analytic': calc.eur(sp['s1g']), 's2_ukraine': s2,
             's3_shares': calc.eur(sp['s3']), 'cash_out': cash_out}
        v.update(calc.ladder(v))
        v.update({'s5_owners': s5, 's5_owners_share': s5_share, 's6_stage2': s6, 's7_unpaid_customers': calc.eur(sums.get('customer', 0)),
                  's7_unpaid_energy': calc.eur(sums.get('energy', 0)), 'revenue': revenue, 'payroll': payroll, 'contrib_zus': zus,
                  'mediation_income': med_in, 'mediation_cost': med_cost, 'mediation_net': calc.eur(calc.cents(med_in) - calc.cents(med_cost)),
                  'g_cand_amount': calc.eur(cand_total), 'q_ic': calc.eur(ic_c), 'q_unplaced': calc.eur(calc.cents(unp['unplaced'])), 'q_nodoc': nodoc,
                  'q_ext': ext})
        steps = {k: v[k] for k in calc.MONEY}
        steps['g_cand_count'] = cand_n
        steps['q_pct'] = calc.pct_quality(v) or 0.0
        return {'steps': steps, 'src': calc.sources(calc.eur(sp['s1g']), calc.eur(sp['s3'])), 'items': items, 'control': control,
                'cand': {'count': cand_n, 'amount': calc.eur(cand_total)}, 'state': 'closed' if me._closed(cos, last) else 'open',
                'notes': notes, 'companies': cos, 'ic': sorted(ic), 'out': sorted(out)}

    # ——— перерахунок ———
    def _recompute_month(self, month):
        """Місяць заново: рядок місяця оновлено (або створено), деталі кліків замінено. Викликати на sudo."""
        first = calc.month_first(calc.iso(month))
        if not calc.month_ok(first):
            raise ValidationError(calc.t('month_range', self._lang()) % first)
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (LOCK_NS, int(first[0:4] + first[5:7])))
        res = self._compute_month(first)
        vals = dict(res['steps'], state=res['state'], recomputed_at=fields.Datetime.now())
        vals.update({k + '_src': s for k, s in res['src'].items()})
        rec = self.search([('month', '=', first)], limit=1)
        if rec:
            rec.write(vals)
        else:
            self.create(dict(vals, month=first))
        Item = self.env['workset.finance.item']
        Item.search([('month', '=', first)]).unlink()
        Item.create([dict(i, month=first, sort=n) for n, i in enumerate(res['items'])])
        return res

    def _check_of(self, month, res):
        return calc.check_result(calc.iso(month), res['steps'], res['control'], res['cand'], self._lang(), res.get('notes'))

    def _store_check(self, month, check):
        """Підсумок звірки — у «Звірку»: є запис місяця — оновити, немає — створити (під замком місяця; гонка двох транзакцій — як у «Прибутковості»)."""
        first = calc.month_first(calc.iso(month))
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (LOCK_NS, int(first[0:4] + first[5:7])))
        Check = self.env['workset.finance.check'].sudo()
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
                _logger.info('Звірка %s: запис місяця щойно створила інша транзакція — лишаємо її', first)

    # ——— публічне: інструмент штабу, кнопки, плановое, екран ———
    @api.model
    def finance_recompute(self, months=None):
        """Перерахунок місяців (немає — останні RECALC_MONTHS із поточним; лише 2026+) і звірка кожного. Лише редактор «Фінансів» або
        адміністратор. → {'months': [...], 'verify': [{'month', 'ok', 'text', ...}]}."""
        if not self._can_recompute():
            raise AccessError(calc.t('only_editor', self._lang()))
        ms = self._months(months)
        me = self.sudo()
        out = []
        for m in ms:
            res = me._recompute_month(m)
            chk = me._check_of(m, res)
            me._store_check(m, chk)
            out.append(dict(chk, month=m))
        _logger.info('Фінанси: перераховано %s; звірка: %s', ', '.join(ms), '; '.join(c['text'] for c in out))
        return {'months': ms, 'verify': out}

    @api.model
    def finance_verify(self, months=None):
        """Лише читання: числа місяців, пораховані наново (нічого не пишуться), — для зовнішньої звірки JS-драбиною. Право — «Власник».
        → {'months': [{'month', 'steps': {поле: число}, 'src', 'cand', 'ok', 'text', 'stale': True — збережене не збігається з тим, що зараз у
        книгах}], 'companies': [id фірм периметра], 'intercompany_partner_ids': [партнери фірм периметра й архівних — «міжфірмові»],
        'out_of_perimeter_partner_ids': [партнери фірм поза периметром]} — як модуль їх бачить; зовнішня звірка рахує «своїх» сама
        (partnerSets штабу) і порівнює з цими списками: розбіжність — знахідка."""
        self._check_screen('owner')
        me = self.sudo()
        out, cos = [], me._perimeter()
        ic, out_p = me._partner_sets()
        for m in self._months(months):
            res = me._compute_month(m)
            chk = me._check_of(m, res)
            rec = me.search([('month', '=', m)], limit=1)
            stale = (not rec) or any(calc.cents(rec[k]) != calc.cents(res['steps'][k]) for k in calc.MONEY)
            out.append(dict(chk, month=m, steps=res['steps'], src=res['src'], cand=res['cand'], stale=stale))
            cos, ic, out_p = res['companies'], res['ic'], res['out']
        return {'months': out, 'companies': cos, 'intercompany_partner_ids': sorted(ic), 'out_of_perimeter_partner_ids': sorted(out_p)}

    # екран «Власник» (finance_owner) — models/workset_finance_screens.py (круг 2)

    def _months_of(self):
        return sorted({calc.iso(r.month) for r in self})

    def action_recompute(self):
        """«Перерахувати»: місяці вибраних рядків, нічого не вибрано — останні місяці."""
        res = self.finance_recompute(self._months_of() or None)
        ok = all(c['ok'] for c in res['verify'])
        text = ' '.join([calc.t('recalc_done', self._lang()) % len(res['months'])] + [c['text'] for c in res['verify']])
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Фінанси'), 'message': text, 'type': 'success' if ok else 'warning', 'sticky': not ok,
                           'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    @api.model
    def _cron_recompute(self):
        return self.finance_recompute()
