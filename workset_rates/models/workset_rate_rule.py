# -*- coding: utf-8 -*-
"""«Загальна ставка» — правило лінії (замовник порожній) або тариф замовника, з датами. Особиста ставка його наслідує (на свою «Ставка з»).

- А7 «загальна ставка змінилась» — create / write полів RULE_TRIGGERS / unlink: «Ставка до» і статус правил тієї самої пари
  «лінія × замовник»; ставки самі НЕ змінюються — для цього кнопка «Застосувати з дати» (майстер workset.rate.rule.apply, з переглядом).
- Захищені поля (calc.RULE_PROTECTED) і правило рівня лінії цілком змінює лише група «Ставки: правила ліній» (рішення власника:
  частки лінії, мінімуми, фікс за оформлення — лише з його слова); редактор править тарифи замовників (ціна, ціна клієнта, джерело).
  Перевірка — у create / write, а не groups на полі: числа бачать усі, хто читає ставки.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import calc
from .workset_rate import ANCHORS, PAYERS, PRICE_MODES, RATE, SOURCES, STATES, cust_of

RULE = 'workset.rate.rule'
RULE_TRIGGERS = frozenset(['line_id', 'customer_id', 'date_from', 'anchor', 'price_mode', 'share_line_found', 'share_we_found', 'price',
                           'fixed_month', 'min_share', 'client_price', 'active'])
LEVELS = [('line', 'Лінія'), ('customer', 'Замовник')]


def rule_key(rule):
    """Пара правила «лінія × замовник» (замовник — комерційний контрагент; 0 — правило всієї лінії)."""
    return (rule.line_id.id, cust_of(rule.customer_id))


class WorksetRateRule(models.Model):
    _name = RULE
    _description = 'Загальна ставка'
    _inherit = ['mail.thread']
    _order = 'line_id, customer_id, date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    line_id = fields.Many2one('workset.line', string='Лінія', required=True, index=True, tracking=1, ondelete='restrict')
    customer_id = fields.Many2one('res.partner', string='Замовник', index=True, tracking=2, ondelete='restrict', domain=[('is_company', '=', True)],
                                  help='Порожньо — правило всієї лінії; замовник — тариф цього замовника (точніше за правило лінії)')
    level = fields.Selection(LEVELS, string='Рівень', compute='_compute_level', store=True, index=True)
    anchor = fields.Selection(ANCHORS, string='Якір', required=True, tracking=3,
                              help='Надбавка: росте зарплата працівника — росте «Платять нам». Тариф: «Платять нам» те саме — зменшується наша частка.')
    price_mode = fields.Selection(PRICE_MODES, string='Тип ціни', required=True, default='hour', tracking=4,
                                  help='За годину — замовник платить ціну за годину. Фікс за місяць — замовник платить зарплату працівника за години як є '
                                       'і фіксовану суму за оформлення людини на місяць.')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    # гроші правила — як у ставці, з усередненням: у рядку групи («Лінія») — середнє, не сума (розділ 3.9 спеціфікації М2)
    share_line_found = fields.Monetary(string='Знайшла лінія', currency_field='currency_id', tracking=5, aggregator='avg',
                                       help='€/год зверху зарплати працівника, якщо людину дала лінія (у тарифу — для стартової ціни нової людини)')
    share_we_found = fields.Monetary(string='Знайшли ми', currency_field='currency_id', tracking=6, aggregator='avg',
                                     help='€/год зверху зарплати працівника, якщо людину знайшли ми (у тарифу — для стартової ціни нової людини)')
    price = fields.Monetary(string='Платять нам', currency_field='currency_id', tracking=7, aggregator='avg',
                            help='У тарифу — скільки замовник платить нам за годину')
    fixed_month = fields.Monetary(string='Фікс за місяць', currency_field='currency_id', tracking=8, aggregator='avg',
                                  help='Замовник платить зарплату працівника за години як є і фіксовану суму за оформлення людини на місяць.')
    min_share = fields.Monetary(string='Мінімум частки', currency_field='currency_id', tracking=9, aggregator='avg',
                                help='Нижче цієї частки WorkSET (€/год) ставка червона. Порожньо — мінімум лінії, а де його немає — за замовчуванням.')
    client_price = fields.Monetary(string='Ціна клієнта', currency_field='currency_id', tracking=10, aggregator='avg',
                                   help='Скільки платить кінцевий клієнт посереднику (лінії), якщо відомо. У нашу частку не входить.')
    client_price_source = fields.Char(string='Джерело ціни')
    client_price_date = fields.Date(string='Дата ціни')
    people_agent_payer = fields.Selection(PAYERS, string='Хто платить агенту',
                                          help='Хто платить агенту людей. Порожньо — лінія. Частку WorkSET зменшує лише «WorkSET».')
    date_from = fields.Date(string='Ставка з', required=True, index=True, tracking=11)
    date_to = fields.Date(string='Ставка до', readonly=True, help='День перед «Ставка з» наступного правила тієї самої пари «лінія × замовник»')
    state = fields.Selection(STATES, string='Статус', readonly=True, index=True)
    source = fields.Selection(SOURCES, string='Джерело', required=True, tracking=12)
    source_note = fields.Char(string='Звідки саме', required=True)
    note = fields.Text(string='Нотатка')
    inherit_count = fields.Integer(string='Наслідують', compute='_compute_counts',
                                   help='Діючі й майбутні ставки, якими керує це правило і які не «Свої»')
    own_count = fields.Integer(string='Своїх', compute='_compute_counts', help='«Свої» ставки під цим правилом — загальна ставка їх не змінює')
    customer_text = fields.Char(string='Замовник (у списку)', compute='_compute_customer_text', help='Порожньо в правилі — «усі замовники лінії»')
    # правило лінії змінює лише «Ставки: правила ліній» — редактору кнопку «Нова з дати» в нього не показуємо (ревю М2c-1, № 5)
    can_edit_line = fields.Boolean(string='Може правити правила ліній', compute='_compute_can_edit_line')
    active = fields.Boolean(string='Активна', default=True, tracking=13)

    # ——— обчислювані ———
    @api.depends('line_id.name', 'customer_id.name', 'date_from')
    def _compute_name(self):
        # назва зберігається — українською (як інші підписи в записах модуля)
        for r in self:
            r.name = calc.t('r_name', 'uk') % (r.line_id.name or '—', r.customer_id.name or calc.t('r_all', 'uk'), calc.fmt_date(r.date_from) or '—')

    @api.depends('customer_id.name')
    @api.depends_context('lang')
    def _compute_customer_text(self):
        # список «Загальних ставок»: правило лінії — сірим «усі замовники лінії» (decoration-muted у виді)
        lang = calc.lang_of(self.env.lang)
        for r in self:
            r.customer_text = r.customer_id.name if r.customer_id else calc.t('r_all', lang)

    @api.depends_context('uid')
    def _compute_can_edit_line(self):
        ok = self._rule_admin()
        for r in self:
            r.can_edit_line = ok

    @api.depends('customer_id')
    def _compute_level(self):
        for r in self:
            r.level = 'customer' if r.customer_id else 'line'

    def _apply_day(self, today=None):
        """День, на який рахуються «Наслідують / Своїх»: «Ставка з» правила, а якщо вона минула — сьогодні."""
        today = calc.iso(today or self.env[RATE]._today())
        return max(calc.iso(self.date_from) or today, today)

    def _candidates(self, day):
        """Ставки, якими керує це правило при застосуванні з day → (наслідують, почалися після правила, свої):
        - наслідують — «Ставка з» раніше за «Ставка з» правила, ставка жива на day і на day правило — найточніше: отримають нову ставку з day;
        - почалися після правила — «Ставка з» між «Ставка з» правила і day включно, і на свою «Ставка з» правило — найточніше:
          перераховуються на місці (правило для них діяло з самого початку);
        - свої — «Своя» з обох груп: не змінюються. Смарт-кнопки й майстер рахують цією самою функцією."""
        Rate = self.env[RATE].sudo()
        start = calc.iso(self.date_from)
        base = [('line_id', '=', self.line_id.id)]
        if self.customer_id:
            base.append(('customer_id.commercial_partner_id', '=', cust_of(self.customer_id)))
        rules = Rate._rules_index()
        alive = Rate.search(base + [('date_from', '<', start), ('date_from', '<=', day), '|', ('date_to', '=', False), ('date_to', '>=', day)],
                            order='person_id, id')
        inh = Rate.browse([r.id for r in alive if calc.rule_pick(rules, r.line_id.id, cust_of(r.customer_id), day) == self.id])
        later = Rate.search(base + [('date_from', '>=', start), ('date_from', '<=', day)], order='person_id, date_from, id')
        late = Rate.browse([r.id for r in later if calc.rule_pick(rules, r.line_id.id, cust_of(r.customer_id), calc.iso(r.date_from)) == self.id])
        mine = inh | late
        return inh.filtered(lambda r: not r.is_own), late.filtered(lambda r: not r.is_own), mine.filtered('is_own')

    def _compute_counts(self):
        for r in self:
            if not isinstance(r.id, int) or not r.line_id:
                r.inherit_count = r.own_count = 0
                continue
            inh, late, own = r._candidates(r._apply_day())
            r.inherit_count = len(inh) + len(late)
            r.own_count = len(own)

    # ——— перевірки ———
    @api.constrains('line_id', 'customer_id', 'date_from', 'active', 'anchor', 'price_mode', 'share_line_found', 'share_we_found', 'price',
                    'fixed_month', 'min_share', 'client_price', 'client_price_source', 'client_price_date')
    def _check_rule(self):
        lang = calc.lang_of(self.env.lang)
        for r in self:
            neg = [r._fields[f].string for f in ('share_line_found', 'share_we_found', 'price', 'fixed_month', 'min_share', 'client_price') if (r[f] or 0) < 0]
            if neg:
                raise ValidationError(calc.t('negative', lang) % ', '.join(neg))
            if r.price_mode == 'fixed_month':
                if not r.fixed_month:
                    raise ValidationError(calc.t('r_fixed', lang))
            elif r.anchor == 'markup' and not (r.share_line_found or r.share_we_found):
                raise ValidationError(calc.t('r_markup', lang))
            elif r.anchor == 'tariff' and not (r.price or r.share_line_found or r.share_we_found):
                raise ValidationError(calc.t('r_tariff', lang))
            if r.client_price and not ((r.client_price_source or '').strip() and r.client_price_date):
                raise ValidationError(calc.t('client_src', lang))
            if r.active:
                same = self.sudo().search([('id', '!=', r.id), ('line_id', '=', r.line_id.id), ('date_from', '=', r.date_from),
                                           ('customer_id.commercial_partner_id', '=', cust_of(r.customer_id)) if r.customer_id else ('customer_id', '=', False)],
                                          limit=1)
                if same:
                    raise ValidationError(calc.t('r_dup', lang) % (r.name, calc.fmt_date(r.date_from), same.id))

    def _rule_admin(self):
        return self.env.su or self.env.user.has_group('workset_rates.group_rule_editor') or self.env.user.has_group('base.group_system')

    def _guard(self, bad):
        if bad:
            names = ', '.join(sorted({self._fields[f].string for f in bad}))
            raise UserError(calc.t('r_guard', calc.lang_of(self.env.lang)) % names)

    # ——— А7: створення, правка, видалення ———
    @api.model_create_multi
    def create(self, vals_list):
        Line = self.env['workset.line']
        for vals in vals_list:
            # якір тарифу замовника за замовчуванням — якір лінії (редактор його не обирає)
            if not vals.get('anchor') and vals.get('line_id'):
                anchor = Line.browse(vals['line_id']).anchor
                if anchor:
                    vals['anchor'] = anchor
        if not self._rule_admin():
            for vals in vals_list:
                level = 'customer' if vals.get('customer_id') else 'line'
                line = Line.browse(vals.get('line_id'))
                have = {'anchor': line.anchor or False, 'price_mode': 'hour'}
                self._guard(calc.rule_guard(level, have, vals))
        recs = super().create(vals_list)
        recs._sync_rules({rule_key(r) for r in recs})
        return recs

    def write(self, vals):
        if not self._rule_admin():
            want = {k: v for k, v in vals.items() if not k.startswith(('message_', 'activity_'))}
            for r in self:
                level = r.level
                if 'customer_id' in vals:
                    level = 'line' if (r.level == 'line' or not vals['customer_id']) else 'customer'
                self._guard(calc.rule_guard(level, {f: r[f] for f in calc.RULE_PROTECTED}, want))
        if not (RULE_TRIGGERS & set(vals)):
            return super().write(vals)
        keys = {rule_key(r) for r in self}
        res = super().write(vals)
        self._sync_rules(keys | {rule_key(r) for r in self})
        return res

    def unlink(self):
        keys = {rule_key(r) for r in self}
        res = super().unlink()
        self.env[RULE]._sync_rules(keys)
        return res

    @api.model
    def _sync_rules(self, keys=None, today=None):
        """«Ставка до» і статус правил кожної пари «лінія × замовник» (keys None — усі пари). Архівне — «Минула». → число змінених правил."""
        Rule = self.env[RULE].sudo().with_context(active_test=False)
        today = calc.iso(today or self.env[RATE]._today())
        allr = Rule.search([('line_id', '!=', False)]) if keys is None else Rule.search([('line_id', 'in', list({k[0] for k in keys if k[0]}))])
        groups = {}
        for r in allr:
            k = rule_key(r)
            if keys is None or k in keys:
                groups.setdefault(k, []).append(r)
        changed = 0
        for rules in groups.values():
            live = [r for r in rules if r.active]
            plan = calc.pair_plan([{'id': r.id, 'date_from': calc.iso(r.date_from)} for r in live], today)
            for r in rules:
                want = plan['states'].get(r.id) if r.active else {'date_to': calc.iso(r.date_to), 'state': 'past'}
                vals = {}
                if calc.iso(r.date_to) != want['date_to']:
                    vals['date_to'] = want['date_to'] or False
                if r.state != want['state']:
                    vals['state'] = want['state']
                if vals:
                    changed += 1
                    if not self.env[RATE]._dry():
                        r.write(vals)
        return changed

    # ——— кнопки ———
    def action_apply_wizard(self):
        """«Застосувати з дати»: майстер з переглядом. Дата — та сама, що в смарт-кнопок «Наслідують / Своїх» («Ставка з» правила, а якщо
        вона минула — сьогодні), можна пізніше."""
        self.ensure_one()
        self.env[RATE].check_access('write')
        return {'type': 'ir.actions.act_window', 'name': calc.t('a_title', calc.lang_of(self.env.lang)),
                'res_model': 'workset.rate.rule.apply', 'view_mode': 'form', 'views': [[False, 'form']], 'target': 'new',
                'context': {'default_rule_id': self.id, 'default_date': self._apply_day()}}

    def action_new_from_date(self):
        """«Нова з дати»: нова форма правила тієї самої пари «лінія × замовник» з числами цього й «Ставка з» = сьогодні; «Джерело» порожнє.
        Нічого не зберігає (захищені поля перевіряє збереження)."""
        self.ensure_one()
        self.check_access('write')
        r = self
        ctx = {'default_%s' % f: r[f] for f in ('anchor', 'price_mode', 'share_line_found', 'share_we_found', 'price', 'fixed_month', 'min_share',
                                                 'client_price', 'client_price_source', 'people_agent_payer')}
        ctx.update({'default_line_id': r.line_id.id, 'default_customer_id': r.customer_id.id or False,
                    'default_client_price_date': calc.iso(r.client_price_date) or False, 'default_date_from': calc.iso(self.env[RATE]._today())})
        return {'type': 'ir.actions.act_window', 'name': self._description, 'res_model': RULE, 'view_mode': 'form', 'views': [[False, 'form']],
                'target': 'current', 'context': ctx}

    def _rates_of(self, own):
        """Смарт-кнопки «Наслідують N» / «Своїх M»: ті самі ставки, що рахує _candidates (і майстер)."""
        self.ensure_one()
        inh, late, mine = self._candidates(self._apply_day())
        rates = mine if own else (inh | late)
        lang = calc.lang_of(self.env.lang)
        title = '%s · %s' % (calc.t('c_own' if own else 'c_inherit', lang), self.display_name)
        return self.env[RATE]._rates_action(title, [('id', 'in', rates.ids)], {'create': False})

    def action_open_inherit(self):
        return self._rates_of(False)

    def action_open_own(self):
        return self._rates_of(True)


class WorksetRateRuleApply(models.TransientModel):
    """Майстер «Застосувати з дати»: перегляд (хто наслідує — було → стане; хто «Свій» — не змінюється, список) і застосування: кожна
    ставка, що наслідує правило, отримує нову ставку з цієї дати (числа правила, джерело «Загальна ставка»); ставка, що почалася між «Ставка з»
    правила і датою застосування (включно), — перераховується на місці; пізніші ставки пари не чіпаються. Рішення — кнопкою, не саме при збереженні правила (тариф не має переписати
    ставки непомітно)."""
    _name = 'workset.rate.rule.apply'
    _description = 'Застосувати з дати'

    rule_id = fields.Many2one(RULE, string='Загальна ставка', required=True, ondelete='cascade')
    date = fields.Date(string='Застосувати з', required=True)
    governed_count = fields.Integer(string='Наслідують', compute='_compute_preview',
                                    help='Те саме число, що на смарт-кнопці правила «Наслідують»: усі, крім «Своїх»')
    inherit_count = fields.Integer(string='Отримають нову ставку', compute='_compute_preview')
    late_count = fields.Integer(string='Почалися після правила', compute='_compute_preview',
                                help='Ставки з «Ставка з» між «Ставка з» правила і датою застосування: перераховуються на місці, нової ставки немає')
    same_count = fields.Integer(string='Без змін', compute='_compute_preview')
    own_count = fields.Integer(string='Своїх', compute='_compute_preview')
    preview_html = fields.Html(string='Було → стане', compute='_compute_preview', sanitize=True)
    own_html = fields.Html(string='Свої — не змінено, вирішіть', compute='_compute_preview', sanitize=True)

    def _plan(self):
        """→ (нова ставка з дати [(ставка, нові числа {'price', 'price_mode', 'fixed_month', 'our_share'})], перерахунок на місці [те саме],
        без змін, свої). На місці — ставки, що почалися після «Ставка з» правила (або з самої дати застосування)."""
        self.ensure_one()
        rule = self.rule_id.sudo()
        day = calc.iso(self.date)
        inh, late, own = rule._candidates(day)
        Rate = self.env[RATE]
        rdict = Rate._rule_dict(rule)
        hours_default = Rate._hours_default()
        todo, here, same = [], [], []
        for r in inh | late:
            in_place = r in late or calc.iso(r.date_from) == day
            # тариф без ціни: нова ставка з дати тримає ціну цієї; перерахунок на місці — ціну попередньої ставки пари
            new = calc.rule_numbers(rdict, r.wage, r.found_by, r._prev_price() if in_place else r.price)
            if calc.same_numbers({'price': r.price, 'price_mode': r.price_mode, 'fixed_month': r.fixed_month}, new) and r.rule_id == rule:
                same.append(r)
                continue
            payer = (r.people_agent_payer if r.people_agent_payer_manual else rule.people_agent_payer) or 'line'
            sh = calc.shares(new['price_mode'], r.wage, new['price'], new['fixed_month'], r.month_hours, hours_default, r.agent_client_share,
                             r.agent_people_share, payer, r.office_bonus)
            (here if in_place else todo).append((r, dict(new, our_share=sh['our_share'])))
        return todo, here, same, own

    @api.depends('rule_id', 'date')
    @api.depends_context('lang')
    def _compute_preview(self):
        lang = calc.lang_of(self.env.lang)
        for w in self:
            if not w.rule_id or not w.date:
                w.governed_count = w.inherit_count = w.late_count = w.same_count = w.own_count = 0
                w.preview_html = w.own_html = False
                continue
            todo, here, same, own = w._plan()
            w.inherit_count = len(todo)
            w.late_count = len(here)
            w.same_count = len(same)
            w.own_count = len(own)
            w.governed_count = len(todo) + len(here) + len(same)
            cols = [calc.t('c_person', lang), calc.t('c_customer', lang), calc.t('c_from', lang), calc.t('c_was', lang), calc.t('c_will', lang)]

            def rows_of(pairs):
                return [[{'text': r.person_id.name or '', 'href': calc.link(RATE, r.id)}, r.customer_id.name or '', calc.fmt_date(r.date_from),
                         '%s / %s' % (calc.money(r.price), calc.money(r.our_share)), '%s / %s' % (calc.money(n['price']), calc.money(n['our_share']))]
                        for r, n in pairs]
            html = calc.table_html(cols, rows_of(todo)) if todo else '<p>%s</p>' % calc.esc(calc.t('a_none', lang))
            if here:
                html += '<p>%s</p>' % calc.esc(calc.t('a_late', lang) % (calc.fmt_date(w.rule_id.date_from), len(here))) + calc.table_html(cols, rows_of(here))
            w.preview_html = html
            orows = [[{'text': r.person_id.name or '', 'href': calc.link(RATE, r.id)}, r.customer_id.name or '', calc.money(r.price), r.own_reason or '']
                     for r in own]
            w.own_html = calc.table_html([calc.t('c_person', lang), calc.t('c_customer', lang), calc.t('c_rprice', lang), calc.t('c_why', lang)], orows) \
                if orows else '<p>%s</p>' % calc.esc(calc.t('a_own_none', lang))

    @api.constrains('rule_id', 'date')
    def _check_date(self):
        for w in self:
            if w.rule_id and w.date and w.date < w.rule_id.date_from:
                raise ValidationError(calc.t('r_apply_date', calc.lang_of(self.env.lang)) % calc.fmt_date(w.rule_id.date_from))

    def action_apply(self):
        self.ensure_one()
        Rate = self.env[RATE]
        Rate.check_access('create')
        lang = calc.lang_of(self.env.lang)
        rule = self.rule_id
        day = calc.iso(self.date)
        todo, here, same, own = self._plan()
        created = Rate.browse()
        redone = 0
        for r, _new in here:
            # почалася після правила або з самої дати — перерахунок на місці (дубля «працівник × замовник × Ставка з» не буває)
            r.write({'source': 'rule', 'source_note': rule.name})
            r._rechain()
            redone += 1
        for r, _new in todo:
            vals = {'person_id': r.person_id.id, 'customer_id': r.customer_id.id, 'line_id': r.line_id.id, 'found_by': r.found_by or False,
                    'wage': r.wage, 'date_from': day, 'source': 'rule', 'source_note': rule.name, 'wage_source': r.wage_source or 'manual'}
            if r.people_agent_payer_manual:
                vals['people_agent_payer'] = r.people_agent_payer
            if r.client_price_manual:
                vals.update({'client_price': r.client_price, 'client_price_source': r.client_price_source, 'client_price_date': r.client_price_date})
            created |= Rate.create(vals)
        own_names = ', '.join(r.person_id.name or '' for r in own)
        body = calc.t('r_applied', lang) % (calc.fmt_date(day), len(created), redone, len(own), (calc.t('r_own_list', lang) % own_names) if own else '')
        rule.sudo().message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': rule.display_name, 'message': body, 'type': 'success', 'sticky': False,
                           'next': {'type': 'ir.actions.act_window_close'}}}
