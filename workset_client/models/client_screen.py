# -*- coding: utf-8 -*-
"""Екран «Умови клієнта» (М3, К1): client action workset_client.card (static/src/js/client_card.js) — ОДИН виклик сервера
workset_client_card на замовника й період. Картка нічого не рахує заново й не дублює полів: збирає те, що вже лежить у «Ставках»
(ставки, загальна ставка лінії × замовника, житло ліній), «Прибутковості» («Хто платить», картки DKV, рядки «лінія × місяць»), у
модулі «Агенти» (м'яко: моделі немає — «модуль агентів не встановлено») і своїх полях замовника, і показує підсумок після перекриття
«замовник точніший за лінію» та звідки він узятий (вибір — calc.py, тими самими функціями сусідів).

Кожне число й рядок відкриває своє джерело: сервер віддає опис 'open' (модель, домен, заголовок, контекст без «Створити»; res_id —
форма одного запису) — лише коли користувач сам може читати цю модель (_workset_client_open), інакше без кліку; правка — там, де правлять
зараз («Ставки», «Прибутковість»), картка лише веде туди. Свої поля правлять майстер «Заповнити» і «Позначити перевіреним» (редактор).

Хто бачить: група «Умови клієнтів: Перегляд» (на час збирання — кожен внутрішній користувач, security/workset_client_security.xml);
метод перевіряє групу сам (_workset_client_check_view), не лише меню. Цифри читаються sudo (слово власника 07.11: «всі, з часткою»), а
«Картка лінії ›» «Прибутковості» — лише тим, у кого є її група «Перегляд». Лише читання."""
from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.addons.workset_profit.models import calc as pcalc
from odoo.addons.workset_rates.models import calc as rcalc

from . import calc
from .res_partner import GROUP_VIEWER, OWN_FIELDS

RATE = 'workset.rate'
RULE = 'workset.rate.rule'
LINE = 'workset.line'
HOUSING = 'workset.line.housing'
COST = 'workset.line.cost.rule'
PERIOD = 'workset.dkv.card.period'
CARD = 'workset.dkv.card'
MONTH = 'workset.profit.month'
DEAL = 'workset.agent.deal.client'  # модуль «Агенти» (workset_agents): немає моделі — блок пише «модуль агентів не встановлено»
DEAL_NEED = ('state', 'kind', 'customer_ids', 'line_id', 'rate', 'agent_id', 'date_from', 'date_to')
PROFIT_VIEWER = 'workset_profit.group_viewer'
PROJECTS_MAX = 3  # проєкти в шапці — стільки назв, решта — «+N»
ROW_FIELDS = ['month', 'hours_total', 'hours_rate', 'share', 'car_rent', 'housing', 'housing_customer', 'auto', 'fuel', 'tool', 'transport', 'net']
OVERHEAD_FIELDS = ['zus_amount', 'office_amount', 'remain']  # «Прибутковість» 19.0.3 (круг 4); немає — рядків ZUS і «Залишається» немає
# колонка значення трекінгу Odoo за типом поля (mail.tracking.value: old_value_* / new_value_*; немає колонки — _char)
TRACK_COLS = {'integer': 'integer', 'boolean': 'integer', 'float': 'float', 'monetary': 'float', 'date': 'datetime', 'datetime': 'datetime',
              'text': 'text'}


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # ——— службове ———
    def _workset_client_check_view(self):
        """Екран — лише групі «Умови клієнтів: Перегляд» (і суперкористувачу: тести). Інші — AccessError, навіть адміністратор."""
        if self.env.su or self.env.user.has_group(GROUP_VIEWER):
            return
        raise AccessError(calc.t('only_viewer', calc.lang_of(self.env.lang)))

    def _workset_client_open(self, title, domain, model, record=None):
        """Опис списку для кліку (JS лише виконує його): модель, заголовок, домен, контекст без «Створити»; record — id одного запису (форма,
        ключ res_id). Користувач сам не читає модель — False (без кліку): відкрити — за його правами, не sudo."""
        if model not in self.env or not self.env[model].has_access('read'):
            return False
        out = {'model': model, 'title': title, 'domain': domain, 'context': {'create': False}}
        if record:
            out['res_id'] = record
        return out

    def _workset_client_rates_dom(self, cust):
        return [('customer_id.commercial_partner_id', '=', cust.id), ('state', '=', 'current')]

    def _workset_client_rates_line(self, rates):
        """Лінія замовника, якого немає в «Замовниках» жодної лінії: лінія більшості його діючих ставок (рівно — менший id)."""
        count = {}
        for r in rates:
            if r.line_id:
                count[r.line_id.id] = count.get(r.line_id.id, 0) + 1
        best = sorted(count, key=lambda i: (-count[i], i))[:1]
        return self.env[LINE].sudo().browse(best)

    def _workset_client_deals(self):
        """Домовленості агентів по замовниках (модуль «Агенти»), діючі (rcalc.LIVE_DEAL_STATES) — словники для rcalc.deal_fits
        (замовники — комерційні). Моделі немає або бракує полів — None. sudo: картка показує, правка — у «Агентах»."""
        if DEAL not in self.env or any(f not in self.env[DEAL]._fields for f in DEAL_NEED):
            return None
        Deal = self.env[DEAL].sudo()
        have = Deal._fields
        out = []
        for d in Deal.search([('state', 'in', list(rcalc.LIVE_DEAL_STATES))]):
            note = (d.terms_source if 'terms_source' in have else '') or (d.note if 'note' in have else '') or ''
            out.append({'id': d.id, 'kind': d.kind or 'hour', 'state': d.state, 'customer_ids': [pcalc.cust_of(c) for c in d.customer_ids],
                        'line_id': d.line_id.id or 0, 'date_from': pcalc.iso(d.date_from), 'date_to': pcalc.iso(d.date_to), 'rate': d.rate or 0.0,
                        'fixed': (d.fixed_advance or 0.0) if 'fixed_advance' in have else 0.0, 'agent': d.agent_id.display_name or '',
                        'note': note})
        return out

    def _workset_client_rule_open(self, rule, line, cust, lang):
        """Клік рядка «Хто платить» — правило-джерело (форма) або, коли правила немає, правила цього виду лінії (список, і порожній —
        відповідь); правити — там, де правлять зараз (групи «Ставок» і «Прибутковості»)."""
        if not rule:
            return False
        model, rid, kind = rule['model'], rule.get('id') or 0, rule.get('kind') or ''
        lname = line.display_name if line else ''
        if model == 'housing':
            return self._workset_client_open(calc.t('o_housing', lang) % lname, [('id', '=', rid)] if rid else [('line_id', '=', line.id)],
                                             HOUSING, rid)
        if model == 'cost':
            dom = [('id', '=', rid)] if rid else [('line_id', '=', line.id), ('kind', '=', kind)]
            return self._workset_client_open(calc.t('o_cost', lang) % (calc.t('k_' + kind, lang), lname), dom, COST, rid)
        if model == 'rate_rule':
            return self._workset_client_open(calc.t('o_rule', lang) % lname, [('id', '=', rid)] if rid else [('line_id', '=', line.id)], RULE, rid)
        if model == 'deal':
            ids = rule.get('ids') or ([rid] if rid else [])
            return self._workset_client_open(calc.t('o_deals', lang) % cust.display_name, [('id', 'in', ids)], DEAL, rid)
        if model == 'fund_rates':
            return self._workset_client_open(calc.t('o_fund', lang) % cust.display_name,
                                             self._workset_client_rates_dom(cust) + [('worker_fund_share', '>', 0)], RATE)
        return False

    def _workset_client_track(self, tv, side, lang):
        """Значення трекінгу (side — 'old' / 'new') текстом calc.track_value; текстові поля — колонка _text, а немає її чи вона порожня —
        _char."""
        ttype = tv.field_id.ttype
        name = '%s_value_%s' % (side, TRACK_COLS.get(ttype, 'char'))
        value = tv[name] if name in tv._fields else False
        if (not value and ttype == 'text') or name not in tv._fields:
            value = tv['%s_value_char' % side] if ('%s_value_char' % side) in tv._fields else False
        return calc.track_value(ttype, value, lang)

    # ——— екран ———
    @api.model
    def workset_client_card(self, partner_id=None, period=None, month=None):
        """Екран «Умови клієнта» — один виклик: замовник (комерційний контрагент partner_id; немає — 'partner': False і підказка «оберіть у
        списку»), період «Що з цього виходить» (period — 'month' / 'quarter' / 'year', month — місяць-якір; calc.period_info: за
        замовчуванням — останній закритий місяць, лише з січня 2026). → {'partner' (шапка: назва, країна, ПДВ, лінії, проєкти, перевірено),
        'period', 'can_edit', 'money', 'pays', 'agent', 'contract', 'profit', 'changes'}; кожен елемент із кліком — з описом 'open'."""
        self._workset_client_check_view()
        lang = calc.lang_of(self.env.lang)
        today = self._workset_client_today()
        info = calc.period_info(period, month, today, lang)
        pid = pcalc.parse_id(partner_id)
        found = self.sudo().browse(pid).exists() if pid else self.browse()
        out = {'partner': False, 'period': info, 'can_edit': bool(self._workset_client_can_edit())}
        if not found:
            return out
        cust = found.commercial_partner_id
        name = cust.display_name
        idx = self._workset_client_index({cust.id})
        rates = self.env[RATE].sudo().browse([r.id for r in idx['rates'].get(cust.id, [])])
        lines = self.env[LINE].sudo().browse([x.id for x in idx['lines'].get(cust.id, [])])
        line = lines[:1] or self._workset_client_rates_line(rates)
        rates_dom = self._workset_client_rates_dom(cust)
        rates_open = self._workset_client_open(calc.t('o_rates', lang) % name, rates_dom, RATE)
        out['partner'] = self._workset_client_head(cust, lines or line, lang)

        # «Гроші за годину»: діючі ставки замовника на сьогодні; мінімум і оренда авто — загальна ставка пари «лінія × замовник»
        Rule = self.env[RULE].sudo()
        rule_recs = Rule.search([('line_id', '=', line.id)]) if line else Rule
        rules = [{'id': r.id, 'line_id': r.line_id.id, 'customer_id': pcalc.cust_of(r.customer_id), 'date_from': pcalc.iso(r.date_from)}
                 for r in rule_recs]
        ms = calc.min_share(rules, {r.id: r.min_share for r in rule_recs}, line.id, cust.id, today)
        rr = Rule.browse(ms['rule_id']) if ms['rule_id'] else Rule
        rule_open = self._workset_client_rule_open({'model': 'rate_rule', 'id': rr.id}, line, cust, lang) if line else False
        withdraw = cust.workset_client_withdraw_pct
        out['money'] = {'price': calc.money_range(rates.mapped('price')), 'wage': calc.money_range(rates.mapped('wage')), 'count': len(rates),
                        'share': calc.share_avg(rates.mapped('our_share')), 'min_share': ms['value'], 'withdraw': withdraw or None,
                        'open': rates_open, 'rule_open': rule_open}

        # «Хто платить»
        src = self._workset_client_housing_src([line.id]) if line else {'housing_rules': [], 'housing': {}}
        cost = self.env[COST].sudo().search([('line_id', '=', line.id)]) if line else self.env[COST]
        src['cost_rules'] = [{'id': c.id, 'line_id': c.line_id.id, 'customer_id': pcalc.cust_of(c.customer_id), 'kind': c.kind, 'payer': c.payer,
                              'date_from': pcalc.iso(c.date_from), 'date_to': pcalc.iso(c.date_to), 'note': c.note or ''} for c in cost]
        periods = self.env[PERIOD]._periods()
        src['periods'] = periods
        src['cards'] = {c.id: c.display_name for c in self.env[CARD].sudo().browse(sorted({p['card_id'] for p in periods}))}
        src['rate_rule'] = {'id': rr.id, 'car_rent': rr.car_rent, 'date_from': pcalc.iso(rr.date_from), 'source_note': rr.source_note or '',
                            'customer_id': pcalc.cust_of(rr.customer_id)} if rr else None
        deals = self._workset_client_deals()
        src['fund_deals'] = deals or []
        src['fund_rates'] = rates.mapped('worker_fund_share') if 'worker_fund_share' in rates._fields else []
        pays = calc.pays_rows(src, line.id or 0, cust.id, today, self._workset_client_labels(), lang)
        for row in pays:
            row['open'] = self._workset_client_rule_open(row.pop('rule'), line, cust, lang)
            if row.get('card_ids'):
                row['cards_open'] = self._workset_client_open(calc.t('o_cards', lang) % name, [('id', 'in', row['card_ids'])], CARD)
        out['pays'] = pays

        # «Агент і хто знайшов»
        items = calc.agent_items(deals, cust.id, line.id or 0, today) if deals is not None else []
        agent_open = items and self._workset_client_open(calc.t('o_deals', lang) % name, [('id', 'in', sorted({i['id'] for i in items}))], DEAL,
                                                         items[0]['id'] if len({i['id'] for i in items}) == 1 else None)
        line_agent = line.agent_id if line else self.env['res.partner']
        out['agent'] = {
            'installed': deals is not None, 'items': items, 'open': agent_open or False,
            'line_agent': line_agent.display_name or '',
            'line_agent_open': bool(line_agent) and self._workset_client_open(calc.t('o_agent', lang) % line_agent.display_name,
                                                                              [('id', '=', line_agent.id)], 'res.partner', line_agent.id),
            'found': calc.found_info(rates.mapped('found_by'), lang), 'found_open': rates_open,
            'missing_open': self._workset_client_open(calc.t('o_nofound', lang) % name, rates_dom + [('found_by', '=', False)], RATE),
        }

        # «Договір і оплата»: свої поля й стандартний строк оплати
        term = cust.property_payment_term_id
        scans = cust.workset_client_contract_scan_ids
        terms = calc.contract_items({
            'contract_no': cust.workset_client_contract_no, 'contract_date': pcalc.iso(cust.workset_client_contract_date), 'scan': len(scans),
            'payment_term': term.display_name if term else '', 'sign': cust.workset_client_sign_id.name or '',
            'site_contact': cust.workset_client_site_contact_id.name or '', 'site_address': cust.workset_client_site_address or '',
            'note': cust.workset_client_terms_note or ''}, lang)
        for it in terms:
            it['open'] = (it['key'] == 'scan' and bool(scans) and self._workset_client_open(calc.t('o_scan', lang) % name,
                                                                                           [('id', 'in', scans.ids)], 'ir.attachment')) or False
        out['contract'] = {'items': terms, 'missing': sum(1 for it in terms if it['empty'])}

        out['profit'] = self._workset_client_profit(line, info, lang)
        out['changes'] = self._workset_client_changes(cust, line, lang)
        return out

    def _workset_client_head(self, cust, lines, lang):
        """Шапка: назва, країна, ПДВ, лінії (з «Замовників» ліній; немає — лінія ставок), проєкти замовника (усі фірми), «Перевірено»."""
        name = cust.display_name
        projects = self.env['project.project'].sudo().search([('partner_id.commercial_partner_id', '=', cust.id)], order='name, id')
        names = sorted({p.name for p in projects if p.name})
        return {'id': cust.id, 'name': name, 'country': cust.country_id.display_name or '', 'vat': cust.vat or '',
                'open': self._workset_client_open(calc.t('o_partner', lang) % name, [('id', '=', cust.id)], 'res.partner', cust.id),
                'lines': [{'id': x.id, 'name': x.display_name,
                           'open': self._workset_client_open(calc.t('o_line', lang) % x.display_name, [('id', '=', x.id)], LINE, x.id)}
                          for x in lines],
                'projects': names[:PROJECTS_MAX], 'projects_more': max(len(names) - PROJECTS_MAX, 0),
                'projects_open': bool(projects) and self._workset_client_open(calc.t('o_projects', lang) % name,
                                                                              [('id', 'in', projects.ids)], 'project.project'),
                'checked': calc.checked_info(cust.workset_client_checked_date, cust.workset_client_checked_by_id.name)}

    def _workset_client_profit(self, line, info, lang):
        """«Що з цього виходить»: рядки лінії workset.profit.month за місяці періоду (sudo — «всі, з часткою» на час збирання; рядки
        пише перерахунок «Прибутковості») → calc.period_rows; клік — ці рядки (за правами користувача); «Картка лінії ›» — лише групі
        «Прибутковість: Перегляд»."""
        Month = self.env[MONTH].sudo()
        months = info['months']
        if not line:
            return {'has': False, 'rows': [], 'line': False, 'open': False, 'link': False, 'oh_missing': False}
        has_oh = all(f in Month._fields for f in OVERHEAD_FIELDS)
        dom = [('month', 'in', months), ('row_kind', '=', 'line'), ('line_id', '=', line.id)]
        rows = Month.search_read(dom, ROW_FIELDS + (OVERHEAD_FIELDS if has_oh else [])) if months else []
        overhead = None
        if has_oh and hasattr(Month, '_overheads'):
            done = set(Month._overheads(months))
            overhead = all(pcalc.iso(r['month']) in done for r in rows)
        res = calc.period_rows(rows, overhead, lang)
        opened = bool(rows) and self._workset_client_open(calc.t('o_profit', lang) % (line.display_name, info['title']), dom, MONTH)
        for r in res['rows']:
            r['open'] = opened or False
        link = {'line_id': line.id, 'month': months[-1]} if months and self.env.user.has_group(PROFIT_VIEWER) else False
        res.update({'line': {'id': line.id, 'name': line.display_name}, 'open': opened or False, 'link': link})
        return res

    def _workset_client_changes(self, cust, line, lang):
        """«Останні зміни» (CHANGES_MAX найсвіжіших): трекінг своїх полів замовника, його ставок (усіх, і минулих), правил житла й «Хто
        платить» лінії × замовника («було → стало», хто); створення цих правил і періодів карток DKV замовника або лінії (і правка
        періоду). Час — у поясі користувача (немає — Київ). sudo — читання історії; відкрити запис — за правами користувача."""
        me = self.with_context(tz=self.env.user.tz or pcalc.TZ)

        def at(dt):
            return fields.Datetime.context_timestamp(me, fields.Datetime.to_datetime(dt)).strftime('%Y-%m-%d %H:%M:%S') if dt else ''
        rates = self.env[RATE].sudo().with_context(active_test=False).search([('customer_id.commercial_partner_id', '=', cust.id)])
        mine = ['|', ('customer_id', '=', False), ('customer_id.commercial_partner_id', '=', cust.id)]
        housing = self.env[HOUSING].sudo().with_context(active_test=False).search([('line_id', '=', line.id)] + mine) if line else self.env[HOUSING]
        cost = self.env[COST].sudo().with_context(active_test=False).search([('line_id', '=', line.id)] + mine) if line else self.env[COST]
        pdom = [('customer_id.commercial_partner_id', '=', cust.id)]
        if line:
            pdom = ['|', '&', ('line_id', '=', line.id), ('customer_id', '=', False)] + pdom
        periods = self.env[PERIOD].sudo().search(pdom)
        events = []

        def event(when, key, what, rec, model, who, old='', new=''):
            events.append({'at': at(when), 'what': '%s: %s' % (calc.t(key, lang), what), 'record': rec.display_name if model != 'res.partner' else '',
                           'old': old, 'new': new, 'who': who or '',
                           'open': self._workset_client_open(rec.display_name, [('id', '=', rec.id)], model, rec.id)})
        Track = self.env['mail.tracking.value'].sudo()
        if 'field_id' in Track._fields:
            for model, recs, only, key in (('res.partner', cust, sorted(OWN_FIELDS), 'm_partner'), (RATE, rates, None, 'm_rate'),
                                           (HOUSING, housing, None, 'm_housing'), (COST, cost, None, 'm_cost')):
                if not recs:
                    continue
                dom = [('mail_message_id.model', '=', model), ('mail_message_id.res_id', 'in', recs.ids)]
                if only:
                    dom.append(('field_id.name', 'in', only))
                for tv in Track.search(dom, order='id desc', limit=calc.CHANGES_MAX):
                    msg = tv.mail_message_id
                    event(msg.date or tv.create_date, key, tv.field_id.field_description or tv.field_id.name, self.env[model].sudo().browse(msg.res_id),
                          model, msg.author_id.name, self._workset_client_track(tv, 'old', lang), self._workset_client_track(tv, 'new', lang))
        for recs, model, key, what in ((housing, HOUSING, 'm_housing', 'ch_created'), (cost, COST, 'm_cost', 'ch_created'),
                                       (periods, PERIOD, 'm_dkv', 'ch_period')):
            for rec in recs:
                event(rec.create_date, key, calc.t(what, lang), rec, model, rec.create_uid.name)
        for p in periods:
            # період картки без історії змін (не mail.thread): правка пізніше за створення — окремим рядком «змінено»
            if p.write_date and p.create_date and (p.write_date - p.create_date).total_seconds() > 60:
                event(p.write_date, 'm_dkv', calc.t('ch_changed', lang), p, PERIOD, p.write_uid.name)
        return calc.changes_top(events)
