# -*- coding: utf-8 -*-
"""Екрани «Прибутковості» (М3, круг 2): A «Огляд», B «Картка лінії», C «Лінії × місяці» — client actions workset_profit.overview,
workset_profit.line_card, workset_profit.matrix (static/src/js/profit_screens.js). Кожен екран — ОДИН виклик сервера: profit_overview,
profit_line_card, profit_matrix. Числа — збережений підсумок workset.profit.month (той, що пише перерахунок), звірка — «Звірка місяця»
(workset.profit.check); картка лінії ще раз розкладає аналітику й DKV місяця (_month_entries — ті самі записи, що в перерахунку), щоб
кожен рядок відкривав свої записи.

Хто бачить (п. 8 завдання круга 2): лише група «Прибутковість: перегляд» (редактор її містить) — методи перевіряють групу самі
(_check_screen), не лише видимість меню; адміністратор поза групою — відмова. Місяці — лише з calc.MIN_MONTH (січень 2026) до поточного.
Лише читання; sudo — для правил житла «Ставок», табелів і аналітики картки лінії (після перевірки групи); відкрити записи — за правами
самого користувача."""
from odoo import api, models
from odoo.exceptions import AccessError

from . import calc

MONTH = 'workset.profit.month'
GROUP_VIEWER = 'workset_profit.group_viewer'
ROW_FIELDS = ['name', 'month', 'row_kind', 'line_id', 'analytic_id', 'tag', 'hours_total', 'hours_rate', 'hours_no_rate', 'share', 'car_rent',
              'fund_in', 'housing', 'housing_customer', 'auto', 'fuel', 'tool', 'transport', 'partner_paid', 'fund_paid', 'fund_left',
              'housing_buffer', 'amount', 'net', 'net_per_hour']
IDS_MAX = 2000  # id в описі списку «Рядків за місяць» — не більше


class WorksetProfitScreens(models.Model):
    _inherit = MONTH

    # ——— службове ———
    def _check_screen(self):
        """Екрани — лише групі «Прибутковість: перегляд» (і суперкористувачу: тести, плановое). Інші — AccessError, навіть адміністратор."""
        if self.env.su or self.env.user.has_group(GROUP_VIEWER):
            return
        raise AccessError(calc.t('only_viewer', self._lang()))

    def _screen_rows(self, months, kinds=None):
        """Рядки підсумку місяців (правами користувача: перегляд читає підсумок) → список словників, місяць — «РРРР-ММ-ДД»."""
        dom = [('month', 'in', list(months))] + ([('row_kind', 'in', list(kinds))] if kinds else [])
        rows = self.search_read(dom, ROW_FIELDS, order='month, sort_key, id')
        for r in rows:
            r['month'] = calc.iso(r['month'])
            r['line'] = r['line_id'][0] if r['line_id'] else 0
        return rows

    def _line_order(self):
        # лінії — за порядком «Ставок» (послідовність, назва): плитки й рядки таблиці щоразу на тих самих місцях
        return {x.id: i for i, x in enumerate(self.env['workset.line'].sudo().with_context(active_test=False).search([]))}

    def _open(self, title, domain, model=MONTH):
        """Опис списку для кліку (JS лише виконує його): модель, заголовок, домен."""
        return {'model': model, 'title': title, 'domain': domain}

    def _checks(self, months):
        """«Звірка місяця» → {місяць: {'state': 'ok' / 'bad', 'text'}}; місяця немає — його немає в словнику."""
        out = {}
        for c in self.env['workset.profit.check'].sudo().search_read([('month', 'in', list(months))], ['month', 'ok', 'text']):
            out[calc.iso(c['month'])] = {'state': 'ok' if c['ok'] else 'bad', 'text': c['text'] or ''}
        return out

    def _cur_month(self, today):
        return max(calc.month_first(today), calc.MIN_MONTH)

    # ——— A «Огляд» ———
    @api.model
    def profit_overview(self, month=None):
        """Екран A — один виклик: місяць (немає — останній закритий; лише від calc.MIN_MONTH до поточного), п'ять підсумків місяця по
        рядках ліній (calc.totals), тривоги з описом списку для кліку, плитки ліній (рядок лінії як є: чисто, €/год, частка, стрілка до
        минулого місяця, житло не з наших грошей, «Куди йде частка»), ряди «Чисто по місяцях» (усі лінії разом і кожна) від MIN_MONTH до
        поточного місяця. Лише читання. → {'month', 'months', 'has', 'totals', 'alerts', 'lines', 'series'}."""
        self._check_screen()
        lang = self._lang()
        today = calc.iso(self._today())
        month = calc.screen_month(month, today)
        all_months = calc.month_list(calc.MIN_MONTH, self._cur_month(today))
        order = self._line_order()
        rows = self._screen_rows(all_months)
        line_rows = [r for r in rows if r['row_kind'] == 'line']
        mine = [r for r in rows if r['month'] == month]
        now = sorted([r for r in mine if r['row_kind'] == 'line'], key=lambda r: (order.get(r['line'], 10 ** 6), r['line']))
        prev_m = calc.add_months(month, -1)
        prev = {r['line']: r for r in line_rows if r['month'] == prev_m and r['hours_rate']}
        day = min(calc.month_last(month), today)
        lines = []
        for r in now:
            p = prev.get(r['line'])
            lines.append({'id': r['line'], 'row_id': r['id'], 'name': r['line_id'][1], 'hours': r['hours_total'],
                          'share_ph': calc.per_hour(r['share'], r['hours_rate']), 'net_ph': r['net_per_hour'], 'net': r['net'],
                          'tone': 'neg' if calc.cents(r['net']) < 0 else '', 'trend': calc.trend(r['net_per_hour'], p['net_per_hour'] if p else None),
                          'housing_from': self._housing_from(r['line'], day), 'split': calc.share_split(r)})
        by_month = {}
        for r in line_rows:
            by_month.setdefault(r['month'], []).append(r)
        names = {}
        for r in line_rows:
            names.setdefault(r['line'], r['line_id'][1])
        series = {
            'months': [calc.month_info(m, lang) for m in all_months], 'total_label': calc.t('l_all', lang),
            'total': [(calc.totals(by_month[m])['net_ph'] if sum(x['hours_rate'] for x in by_month[m]) else None) if m in by_month else None
                      for m in all_months],
            'lines': [{'id': lid, 'name': names[lid],
                       'values': [next((x['net_per_hour'] for x in by_month.get(m, []) if x['line'] == lid and x['hours_rate']), None)
                                  for m in all_months]}
                      for lid in sorted(names, key=lambda i: (order.get(i, 10 ** 6), i))],
        }
        return {
            'month': calc.month_info(month, lang),
            'months': [calc.month_info(m, lang) for m in sorted({r['month'] for r in line_rows} | {month}, reverse=True)],
            'has': bool(now), 'totals': calc.totals(now), 'alerts': self._overview_alerts(month, mine, line_rows, lang),
            'lines': lines, 'series': series,
        }

    def _housing_from(self, line_id, day):
        """Житло лінії не з наших грошей на day (правило лінії й правила її замовників, _housing_at «Ставок»): 'partner' — з буфера лінії,
        'worker_fund' — з фонду працівників, інакше ''. Вид правила не важить: «Житло не наше» з «Хто платить» = партнер — теж буфер лінії."""
        Housing = self.env['workset.line.housing'].sudo()
        Partner = self.env['res.partner'].sudo()
        line = self.env['workset.line'].sudo().browse(line_id)
        payers = set()
        for cust in [Partner] + list(line.customer_ids):
            rule = Housing._housing_at(line, cust, day)
            if rule:
                payers.add(calc.housing_payer(rule.payer))
        return 'partner' if 'partner' in payers else ('worker_fund' if 'worker_fund' in payers else '')

    def _overview_alerts(self, month, mine, line_rows, lang):
        """Чипи «Огляду» за місяць, у порядку на сторінці; кожен — з описом списку рядків (число на чипі = рядки списку):
        «Не розподілено» (€, чисто рядків «Не розподілено»), «Години без ставки», «Години без лінії», «Житло не в своєму місяці»
        (calc.housing_shift), «Власник <стаття>» (€ кожного), «Звірка» (зі «Звірки місяця»). Нуля немає — чипа немає."""
        label = calc.month_label(month, lang)
        out = []
        un = [r for r in mine if r['row_kind'] == 'unallocated']
        if un:
            amount = calc.eur(sum(calc.cents(r['net']) for r in un))
            out.append({'key': 'unallocated', 'amount': amount, 'tone': 'danger' if calc.cents(amount) else '',
                        'open': self._open(calc.t('t_unalloc', lang) % label, [('month', '=', month), ('row_kind', '=', 'unallocated')])})
        no_rate = round(sum(r['hours_no_rate'] for r in mine if r['row_kind'] in ('line', 'noline')), 2)
        if no_rate:
            out.append({'key': 'no_rate', 'hours': no_rate, 'tone': 'warning',
                        'open': self._open(calc.t('t_no_rate', lang) % label, [('month', '=', month), ('hours_no_rate', '>', 0)])})
        noline = round(sum(r['hours_total'] for r in mine if r['row_kind'] == 'noline'), 2)
        if noline:
            out.append({'key': 'noline', 'hours': noline, 'tone': 'warning',
                        'open': self._open(calc.t('t_noline', lang) % label, [('month', '=', month), ('row_kind', '=', 'noline')])})
        ratio = calc.parse_num(self._param(calc.P_HOUSING_SHIFT), calc.HOUSING_SHIFT_DEFAULT)
        shift = calc.housing_shift([{'line': r['line'], 'month': r['month'], 'housing': r['housing'], 'hours': r['hours_total']}
                                    for r in line_rows if calc.month_first(r['month']) >= calc.MIN_MONTH], month, ratio)
        if shift:
            out.append({'key': 'housing_shift', 'count': len(shift), 'tone': 'warning',
                        'open': self._open(calc.t('t_shift', lang) % label,
                                           [('month', '=', month), ('row_kind', '=', 'line'), ('line_id', 'in', shift)])})
        for r in sorted([r for r in mine if r['row_kind'] == 'owner'], key=lambda r: r['analytic_id'][1] if r['analytic_id'] else ''):
            code = r['analytic_id'][1] if r['analytic_id'] else '—'
            out.append({'key': 'owner', 'code': code, 'amount': r['net'], 'tone': '',
                        'open': self._open(calc.t('t_owner', lang) % (code, label), [('id', '=', r['id'])])})
        check = self._checks([month]).get(month)
        if check:
            out.append({'key': 'check', 'state': check['state'], 'text': check['text'], 'tone': '' if check['state'] == 'ok' else 'danger'})
        return out

    # ——— B «Картка лінії» ———
    @api.model
    def profit_line_card(self, line_id=None, month=None):
        """Екран B — один виклик: лінія (немає або не знайдено — перша лінія місяця) і місяць (як в «Огляді»), сусідні місяці для стрілок
        (лише від MIN_MONTH до поточного), п'ять підсумків лінії, водоспад «частка → … → чисто» (calc.chain_steps / waterfall: нульових
        кроків немає), «Хто платить» на останній день місяця, «Не в нашому чисто», ряди «Чисто по місяцях» і «Рядки за місяць» — кожен
        відкриває свої записи. Рядка лінії за місяць немає — лише лінія, місяць, стрілки й ряди ('has': False). Лише читання."""
        self._check_screen()
        lang = self._lang()
        today = calc.iso(self._today())
        month = calc.screen_month(month, today)
        cur = self._cur_month(today)
        Line = self.env['workset.line']
        line = Line.browse(int(line_id)).exists() if line_id else Line
        if not line:
            first = self._screen_rows([month], ['line'])
            order = self._line_order()
            first.sort(key=lambda r: (order.get(r['line'], 10 ** 6), r['line']))
            line = Line.browse(first[0]['line']) if first else Line
        prev_m, next_m = calc.add_months(month, -1), calc.add_months(month, 1)
        out = {'month': calc.month_info(month, lang), 'prev': calc.month_info(prev_m, lang) if prev_m >= calc.MIN_MONTH else False,
               'next': calc.month_info(next_m, lang) if next_m <= cur else False, 'line': False, 'has': False}
        if not line:
            return out
        all_months = calc.month_list(calc.MIN_MONTH, cur)
        rows = [r for r in self._screen_rows(all_months, ['line']) if r['line'] == line.id]
        by_month = {r['month']: r for r in rows}
        out['line'] = {'id': line.id, 'name': line.sudo().display_name}
        out['series'] = {'months': [calc.month_info(m, lang) for m in all_months], 'net_label': calc.t('l_net', lang),
                         'share_label': calc.t('l_share', lang),
                         'net': [by_month[m]['net_per_hour'] if m in by_month and by_month[m]['hours_rate'] else None for m in all_months],
                         'share': [calc.per_hour(by_month[m]['share'], by_month[m]['hours_rate']) if m in by_month and by_month[m]['hours_rate']
                                   else None for m in all_months]}
        row = by_month.get(month)
        if not row:
            return out
        steps = calc.chain_steps(row)
        for s in steps:
            s['label'] = calc.t('s_' + s['key'], lang)
        day = min(calc.month_last(month), today)
        out.update({'has': True, 'row_id': row['id'], 'totals': calc.totals([row]), 'waterfall': calc.waterfall(steps),
                    'pays': self._who_pays(line, day, lang),
                    'aside': [{'key': f, 'label': calc.t('a_' + f, lang), 'amount': row[f]} for f in calc.ASIDE if calc.cents(row[f])]})
        out.update(self._card_rows(line, month, row, lang))
        return out

    def _who_pays(self, line, day, lang):
        """«Хто платить» лінії на day: житло — правило житла лінії «Ставок» (_housing_at), авто, паливо, інструмент, перевезення — правила
        «Хто платить» (правила лінії; правила замовників лінії з іншим платником — винятками); паливо — ще картки DKV лінії на day.
        → [{'key', 'label', 'payer', 'except': [{'name', 'payer'}], 'cards': [назва]}]."""
        Housing = self.env['workset.line.housing'].sudo()
        Cost = self.env['workset.line.cost.rule']
        Partner = self.env['res.partner'].sudo()
        line = line.sudo()
        h_labels = dict(Housing._fields['payer']._description_selection(self.env))
        c_labels = dict(Cost._fields['payer']._description_selection(self.env))
        custs = list(line.customer_ids)
        base = Housing._housing_at(line, Partner, day)
        base_payer = base.payer if base else ''
        item = {'key': 'housing', 'label': calc.t('s_housing', lang), 'payer': h_labels.get(base_payer) or c_labels.get('we', ''),
                'except': [], 'cards': []}
        for cust in custs:
            rule = Housing._housing_at(line, cust, day)
            if rule and rule != base and (rule.payer or '') != base_payer:
                item['except'].append({'name': cust.display_name, 'payer': h_labels.get(rule.payer) or c_labels.get('we', '')})
        out = [item]
        rules = Cost._rules()
        periods = self.env['workset.dkv.card.period']._periods()
        for kind in calc.COST_KINDS:
            payer = (calc.cost_rule_pick(rules, line.id, 0, kind, day) or {}).get('payer') or 'we'
            item = {'key': kind, 'label': calc.t('s_' + kind, lang), 'payer': c_labels.get(payer, ''), 'except': [], 'cards': []}
            for cust in custs:
                rule = calc.cost_rule_pick(rules, line.id, calc.cust_of(cust), kind, day)
                if rule and rule.get('customer_id') and rule['payer'] != payer:
                    item['except'].append({'name': cust.display_name, 'payer': c_labels.get(rule['payer'], '')})
            if kind == 'fuel':
                cards = sorted({p['card_id'] for p in periods})
                mine = [c for c in cards if (calc.card_period(periods, c, day) or {}).get('target') == 'line'
                        and calc.card_period(periods, c, day).get('line_id') == line.id]
                item['cards'] = [c.name for c in self.env['workset.dkv.card'].sudo().browse(mine)]
            out.append(item)
        return out

    def _card_rows(self, line, month, row, lang):
        """«Рядки за місяць» картки лінії: записи розкладу місяця, що лягли на лінію (_month_entries — як у перерахунку), за статтею й
        рахунком (аналітика), карткою (DKV) або «перевезення за прибуттями»; ще оренда авто й житло, яке вирахував замовник (зі ставок і
        правил), і табелі лінії одним рядком. Рядок — {'title', 'sub', 'amount', 'chain' (у «чисто»), 'open' (опис списку або False — без
        кліку)}. Порядок: ланцюжок (найбільші витрати першими), «не в нашому чисто», табелі; показано CARD_ROWS — {'rows', 'rows_total'}."""
        me = self.sudo()
        first, last = month, calc.month_last(month)
        cust_lines, line_nodes = me._lines_index()
        ts = me._timesheets(calc.add_days(first, -(calc.STINT_GAP_DAYS + 1)), last)
        entries, refs = me._month_entries(first, last, cust_lines, line_nodes, ts)
        groups = {}
        for e, ref in zip(entries, refs):
            if e['key'][0] != 'line' or e['key'][1] != line.id:
                continue
            if ref.get('pool'):
                k = ('pool', e['field'])
            elif ref.get('txn'):
                k = ('card', ref.get('card') or 0, e['field'])
            else:
                k = ('aal', ref.get('analytic') or 0, ref.get('account') or 0, e['field'])
            g = groups.setdefault(k, {'amount': 0, 'aal': [], 'txn': []})
            g['amount'] += calc.cents(e['amount'])
            g['aal'] += ref.get('aal') or []
            g['txn'] += ref.get('txn') or []
        can_aal = self.env['account.analytic.line'].has_access('read')
        can_txn = self.env['workset.dkv.txn'].has_access('read')
        Analytic = self.env['account.analytic.account'].sudo().with_context(active_test=False)
        Account = self.env['account.account'].sudo().with_context(active_test=False)
        Card = self.env['workset.dkv.card'].sudo().with_context(active_test=False)
        label = calc.month_label(month, lang)
        out = []
        for k, g in groups.items():
            field = k[-1]
            sub = calc.t(('s_' if field in calc.CHAIN else 'a_') + field, lang)
            if k[0] == 'aal':
                title = Analytic.browse(k[1]).display_name if k[1] else '—'
                if k[2]:
                    sub = '%s · %s' % (sub, Account.browse(k[2]).display_name)
            elif k[0] == 'card':
                title = calc.t('r_card', lang) % (Card.browse(k[1]).display_name if k[1] else '—', len(set(g['txn'])))
            else:
                title = calc.t('r_pool', lang)
            spec = False
            if g['txn'] and can_txn and not g['aal']:
                spec = self._open(calc.t('r_rows', lang) % (title, label), [('id', 'in', sorted(set(g['txn']))[:IDS_MAX])], 'workset.dkv.txn')
            elif g['aal'] and can_aal and not g['txn']:
                spec = self._open(calc.t('r_rows', lang) % (title, label), [('id', 'in', sorted(set(g['aal']))[:IDS_MAX])],
                                  'account.analytic.line')
            out.append({'title': title, 'sub': sub, 'amount': calc.eur(g['amount']), 'chain': field in calc.CHAIN, 'open': spec})
        if calc.cents(row['car_rent']):
            out.append({'title': calc.t('r_rent', lang), 'sub': calc.t('s_car_rent', lang), 'amount': row['car_rent'], 'chain': True,
                        'open': False})
        if calc.cents(row['housing_customer']):
            can = self.env['workset.line.housing'].has_access('read')
            out.append({'title': calc.t('r_hc', lang), 'sub': calc.t('s_housing_customer', lang), 'amount': row['housing_customer'],
                        'chain': True, 'open': can and self._open(calc.t('r_hc_title', lang), [('line_id', '=', line.id), ('payer', '=', 'customer')],
                                                                  'workset.line.housing')})
        out.sort(key=lambda r: (0 if r['chain'] else 1, calc.cents(r['amount']) if r['chain'] else -abs(calc.cents(r['amount']))))
        total = len(out) + 1
        shown = out[:calc.CARD_ROWS - 1]
        # табелі лінії: замовники, які належать цій лінії (перша лінія замовника), години й склеєні люди — ті самі, що в «Годин»
        custs = sorted({c for c, l in calc.pairs_map(cust_lines).items() if l == line.id})
        mine = [r for r in ts if first <= r['date'] <= last and r['cust'] in custs]
        hours = round(sum(r['hours'] for r in mine), 2)
        dom = [('project_id', '!=', False), ('general_account_id', '=', False), ('employee_id', '!=', False), ('unit_amount', '>', 0),
               ('date', '>=', first), ('date', '<=', last), ('project_id.partner_id.commercial_partner_id', 'in', custs)]
        skip = me.env['workset.rate']._skip_companies()
        if skip:
            dom.append(('project_id.company_id', 'not in', skip))
        shown.append({'title': calc.t('r_timesheets', lang) % (calc.num_text(hours), len({r['glue'] for r in mine})),
                      'sub': '', 'amount': None, 'chain': False, 'timesheets': True,
                      'open': can_aal and self._open(calc.t('r_rows', lang) % (line.sudo().display_name, label), dom, 'account.analytic.line')})
        return {'rows': shown, 'rows_total': total}

    # ——— C «Лінії × місяці» ———
    @api.model
    def profit_matrix(self, date_from=None, date_to=None):
        """Екран C — один виклик: місяці періоду (calc.screen_period: за замовчуванням — останні закриті з січня 2026, не більше
        MATRIX_MAX), лінії × місяці (чисто €/год і € за місяць, колір calc.cell_tone з порогом-параметром calc.P_DROP, ланцюжок місяця
        для спливаючого вікна), підсумок періоду, нижче — «Не розподілено», «Власник <стаття>» і «Звірка» по місяцях. Лише читання."""
        self._check_screen()
        lang = self._lang()
        today = calc.iso(self._today())
        months = calc.screen_period(date_from, date_to, today)
        before = calc.add_months(months[0], -1)
        rows = self._screen_rows(([before] if before >= calc.MIN_MONTH else []) + months)
        threshold = calc.parse_num(self._param(calc.P_DROP), calc.DROP_DEFAULT)
        order = self._line_order()
        in_period = [r for r in rows if r['month'] in months]
        by_line, names = {}, {}
        for r in rows:
            if r['row_kind'] == 'line':
                by_line.setdefault(r['line'], {})[r['month']] = r
                names.setdefault(r['line'], r['line_id'][1])
        lines = []
        for lid in sorted({r['line'] for r in in_period if r['row_kind'] == 'line'}, key=lambda i: (order.get(i, 10 ** 6), i)):
            mine = by_line[lid]
            cells = []
            for m in months:
                r = mine.get(m)
                if not r:
                    cells.append({'month': m, 'has': False})
                    continue
                p = mine.get(calc.add_months(m, -1))
                prev_ph = p['net_per_hour'] if p and p['hours_rate'] else None
                net_ph = r['net_per_hour'] if r['hours_rate'] else None
                steps = calc.chain_steps(r)
                for s in steps:
                    s['label'] = calc.t('s_' + s['key'], lang)
                cells.append({'month': m, 'has': True, 'net': r['net'], 'net_ph': net_ph,
                              'tone': calc.cell_tone(r['net'], net_ph or 0, prev_ph if net_ph is not None else None, threshold),
                              'chain': steps})
            tot = calc.totals([mine[m] for m in months if m in mine])
            lines.append({'id': lid, 'name': names[lid], 'cells': cells,
                          'total': {'net': tot['net'], 'net_ph': tot['net_ph'] if tot['hours_rate'] else None}})

        def company(rows_of):
            cells = [calc.eur(sum(calc.cents(r['net']) for r in rows_of if r['month'] == m)) for m in months]
            return {'cells': [{'month': m, 'has': any(r['month'] == m for r in rows_of), 'amount': v} for m, v in zip(months, cells)],
                    'total': calc.eur(sum(calc.cents(v) for v in cells))}
        un = [r for r in in_period if r['row_kind'] == 'unallocated']
        owners = {}
        for r in in_period:
            if r['row_kind'] == 'owner':
                owners.setdefault(r['analytic_id'][1] if r['analytic_id'] else '—', []).append(r)
        checks = self._checks(months)
        return {
            'months': [calc.month_info(m, lang) for m in months],
            'period': {'from': months[0], 'to': months[-1], 'text': calc.months_text(len(months), lang)},
            'options': [calc.month_info(m, lang) for m in calc.month_list(calc.MIN_MONTH, self._cur_month(today))],
            'threshold': threshold, 'has': bool(lines), 'lines': lines,
            'labels': {'hour': calc.t('l_hour', lang), 'month': calc.t('l_month', lang)},
            'unallocated': company(un) if un else False,
            'owners': [dict(company(owners[code]), code=code) for code in sorted(owners)],
            'checks': [dict(checks.get(m) or {'state': '', 'text': calc.t('v_none_short', lang)}, month=m) for m in months],
        }
