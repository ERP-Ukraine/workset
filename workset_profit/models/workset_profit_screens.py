# -*- coding: utf-8 -*-
"""Екрани «Прибутковості» (М3, круг 2): A «Огляд», B «Картка лінії», C «Лінії × місяці» — client actions workset_profit.overview,
workset_profit.line_card, workset_profit.matrix (static/src/js/profit_screens.js). Кожен екран — ОДИН виклик сервера: profit_overview,
profit_line_card, profit_matrix. Числа — збережений підсумок workset.profit.month (той, що пише перерахунок), звірка — «Звірка місяця»
(workset.profit.check); картка лінії ще раз розкладає аналітику й DKV місяця (_month_entries — ті самі записи, що в перерахунку), щоб
кожен рядок відкривав свої записи.

Круг 3в (слово власника «экраны: должно быть все кликабельно»): кожне число відкриває те, з чого воно складене. Сервер віддає в елемента
опис 'open' (модель, домен, заголовок, контекст без «Створити»; res_id — форма одного запису) — він рахується лише тут (_open,
_line_open, _kind_open, _ts_domain), екран лише виконує його (openList); нових методів на клік немає.

Хто бачить (п. 8 завдання круга 2): лише група «Прибутковість: перегляд» (редактор її містить) — методи перевіряють групу самі
(_check_screen), не лише видимість меню; адміністратор поза групою — відмова. Місяці — лише з calc.MIN_MONTH (січень 2026) до поточного.
Лише читання; sudo — для правил житла «Ставок», табелів і аналітики картки лінії (після перевірки групи); відкрити записи — за правами
самого користувача."""
from odoo import api, fields, models
from odoo.exceptions import AccessError

from . import calc

MONTH = 'workset.profit.month'
COST_RULE = 'workset.line.cost.rule'
GROUP_VIEWER = 'workset_profit.group_viewer'
ROW_FIELDS = ['name', 'month', 'row_kind', 'line_id', 'analytic_id', 'tag', 'hours_total', 'hours_rate', 'hours_no_rate', 'share', 'car_rent',
              'fund_in', 'housing', 'housing_customer', 'auto', 'fuel', 'tool', 'transport', 'partner_paid', 'fund_paid', 'fund_left',
              'housing_buffer', 'amount', 'net', 'net_per_hour', 'zus_amount', 'office_amount', 'remain', 'remain_per_hour', 'write_date']
IDS_MAX = 2000  # id в описі списку «Рядків за місяць» — не більше
COST_FIELDS = tuple(f for f in calc.CHAIN if f != 'share')  # між часткою й «чисто»: оренда авто плюсом, витрати лінії мінусом
# витрати з аналітики й транзакцій DKV (без оренди авто — вона зі ставок, і без житла, яке вираховує замовник, — воно з правил)
RECORD_COSTS = tuple(f for f in COST_FIELDS if f not in ('car_rent', 'housing_customer'))
OVERHEAD_READ = ['month', 'zus', 'office', 'office_dup', 'total', 'hours', 'per_hour', 'move_ids', 'unlabeled_ids']
REMAIN_KEYS = ('zus', 'office', 'overhead', 'remain', 'zus_ph', 'office_ph', 'overhead_ph', 'remain_ph')  # calc.remain_totals


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

    def _open(self, title, domain, model=MONTH, record=None):
        """Опис списку для кліку (JS лише виконує його; круг 3в — одне місце для всіх кліків екранів): модель, заголовок, домен, контекст
        без «Створити» (екрани лише показують записи); record — id одного запису: екран відкриває його форму (ключ res_id)."""
        out = {'model': model, 'title': title, 'domain': domain, 'context': {'create': False}}
        if record:
            out['res_id'] = record
        return out

    def _line_open(self, line_id, line_name, month, label, lang):
        """Рядок лінії місяця в «Рядках розрахунку» (частка, «чисто», витрати картки B, оренда авто — зі ставок)."""
        return self._open(calc.t('o_line', lang) % (line_name, label),
                          [('month', '=', month), ('row_kind', '=', 'line'), ('line_id', '=', line_id)])

    def _ts_domain(self, first, last, custs):
        """Табелі місяця замовників custs — той самий відбір, що _timesheets перерахунку (з чого «Години» рядків ліній)."""
        dom = [('project_id', '!=', False), ('general_account_id', '=', False), ('employee_id', '!=', False), ('unit_amount', '>', 0),
               ('date', '>=', first), ('date', '<=', last), ('project_id.partner_id.commercial_partner_id', 'in', sorted(custs))]
        skip = self.env['workset.rate'].sudo()._skip_companies()
        if skip:
            dom.append(('project_id.company_id', 'not in', skip))
        return dom

    def _month_parts(self, month, hr=None, with_ts=True):
        """Розклад місяця (той самий, що в перерахунку: _month_entries) для кліків екранів → {'entries', 'refs', 'ts' (табелі від місяця
        мінус розрив), 'cust_lines', 'kinds' (calc.kind_refs: записи кожного виду кожної лінії)}. hr — правила житла, уже прочитані
        запитом (немає — читаються тут). with_ts False («Огляд», круг 4: табелі йому не потрібні) — табелі читаються лише тоді, коли в
        місяці є перевезення без лінії (_month_entries ділить їх за прибуттями), 'ts' — None: менше запитів на кожен показ."""
        me = self.sudo()
        first, last = month, calc.month_last(month)
        cust_lines, line_nodes = me._lines_index()
        ts = me._timesheets(calc.add_days(first, -(calc.STINT_GAP_DAYS + 1)), last) if with_ts else None
        entries, refs = me._month_entries(first, last, cust_lines, line_nodes, ts, hr)
        return {'month': month, 'entries': entries, 'refs': refs, 'ts': ts, 'cust_lines': cust_lines, 'kinds': calc.kind_refs(entries, refs)}

    def _overheads(self, months):
        """«ZUS і загальні місяця» (workset.profit.overhead — пише перерахунок) → {місяць: {'id', 'zus', 'office', 'total', 'hours',
        'per_hour', 'move_ids', 'unlabeled_ids'}}; місяця немає (перерахунку круга 4 ще не було) — його немає в словнику. sudo: id рахунків
        читаються й тоді, коли бухгалтерії в користувача немає (відкрити їх — _moves_open, за його правами)."""
        out = {}
        for o in self.env['workset.profit.overhead'].sudo().search_read([('month', 'in', list(months))], OVERHEAD_READ):
            out[calc.iso(o['month'])] = o
        return out

    def _moves_open(self, ids, title):
        """Рахунки ZUS / PIT-4 (account.move) — список; рахунків немає або прав на бухгалтерію немає — False (без кліку)."""
        if not ids or not self.env['account.move'].has_access('read'):
            return False
        return self._open(title, [('id', 'in', sorted(ids)[:IDS_MAX])], 'account.move')

    def _office_open(self, months, title, parts=None, ohs=None):
        """Загальні фірм — рядки «Офіс» місяців months, з яких складено число (круг 4б: клік = число): місяць без аналітики рахунків
        ZUS / PIT-4 в «Офісі» (office_dup = 0) — усі рядки «Офіс»; є — без рядків, складених цілком із неї (calc.office_rows_in; розклад
        місяця — parts, якщо це той самий місяць, інакше _month_parts без табелів). ohs — підсумки місяців (_overheads; немає — читаються).
        Рядків немає — False."""
        months = sorted(months)
        ohs = ohs if ohs is not None else self._overheads(months)
        if not any(calc.cents((ohs.get(m) or {}).get('office_dup')) for m in months):
            dom = [('month', 'in', months), ('row_kind', '=', 'office')]
            return bool(self.search_count(dom)) and self._open(title, dom)
        ids = []
        for m in months:
            rows = self.search_read([('month', '=', m), ('row_kind', '=', 'office')], ['analytic_id', 'amount'])
            for r in rows:
                r['analytic_id'] = r['analytic_id'][0] if r['analytic_id'] else 0
            dup = {}
            if calc.cents((ohs.get(m) or {}).get('office_dup')):
                p = parts if (parts and parts.get('month') == m) else self._month_parts(m, with_ts=False)
                dup = self.sudo()._contrib_in_rows(p['entries'], p['refs'])['by_analytic']
            ids += calc.office_rows_in(rows, dup)['ids']
        return bool(ids) and self._open(title, [('id', 'in', sorted(ids)[:IDS_MAX])])

    def _no_overhead(self, totals):
        """Круг 4б: місяць пораховано до круга 4 (підсумку «ZUS і загальні» немає) — «Залишається» й частки невідомі, а не 0: None."""
        return dict(totals, **{k: None for k in REMAIN_KEYS})

    def _costs_open(self, now, parts, fallback, label, lang):
        """«Житло, авто, паливо, інструмент» «Огляду» (круг 4, ревю 3в): записи витрат ліній місяця, як кроки водоспаду B — рядки аналітики
        видів RECORD_COSTS (є аналітика — список аналітики), лише транзакції DKV — їхній список; один список — одна модель, тож коли є і
        те, і те, — аналітика (транзакції DKV — у кроці «Паливо» картки лінії); нічого немає або прав немає — fallback (рядки ліній з
        витратами)."""
        aal, txn = set(), set()
        for r in now:
            for f in RECORD_COSTS:
                g = ((parts or {}).get('kinds', {}).get(r['line']) or {}).get(f) or {}
                aal.update(g.get('aal') or [])
                txn.update(g.get('txn') or [])
        if aal and self.env['account.analytic.line'].has_access('read'):
            return self._open(calc.t('o_costs_aal', lang) % label, [('id', 'in', sorted(aal)[:IDS_MAX])], 'account.analytic.line')
        if txn and self.env['workset.dkv.txn'].has_access('read'):
            return self._open(calc.t('o_costs_txn', lang) % label, [('id', 'in', sorted(txn)[:IDS_MAX])], 'workset.dkv.txn')
        return fallback

    def _kind_open(self, line_id, line_name, field, month, label, kinds, lang):
        """Клік по виду витрати лінії за місяць (крок водоспаду B, сегмент «Куди йде частка» A) → ті самі записи, що в «Рядках» картки:
        лише аналітика — її рядки, лише транзакції DKV — вони; житло, яке вирахував замовник, — правила житла лінії «Ставок»; частка, оренда
        авто (зі ставок), «чисто» і вид, складений з аналітики й DKV разом (один список — одна модель), — рядок лінії місяця."""
        title = calc.t('o_kind', lang) % (calc.t('s_' + field, lang), line_name, label)
        g = (kinds.get(line_id) or {}).get(field) or {}
        aal, txn = g.get('aal') or [], g.get('txn') or []
        if field == 'housing_customer' and self.env['workset.line.housing'].has_access('read'):
            return self._open(title, [('line_id', '=', line_id), ('payer', '=', 'customer')], 'workset.line.housing')
        if aal and not txn and self.env['account.analytic.line'].has_access('read'):
            return self._open(title, [('id', 'in', aal[:IDS_MAX])], 'account.analytic.line')
        if txn and not aal and self.env['workset.dkv.txn'].has_access('read'):
            return self._open(title, [('id', 'in', txn[:IDS_MAX])], 'workset.dkv.txn')
        return self._line_open(line_id, line_name, month, label, lang)

    def _checks(self, months):
        """«Звірка місяця» → {місяць: {'id', 'state': 'ok' / 'bad', 'text'}}; місяця немає — його немає в словнику."""
        out = {}
        for c in self.env['workset.profit.check'].sudo().search_read([('month', 'in', list(months))], ['month', 'ok', 'text']):
            out[calc.iso(c['month'])] = {'id': c['id'], 'state': 'ok' if c['ok'] else 'bad', 'text': c['text'] or ''}
        return out

    def _cur_month(self, today):
        return max(calc.month_first(today), calc.MIN_MONTH)

    # правила житла «Ставок» — _housing_rules / _housing_pick моделі підсумку (workset_profit_month.py): один раз на запит, та сама
    # функція rates_calc.housing_pick, що в _housing_at і в перерахунку (раніше _housing_at читав усі правила на кожну пару: в «Огляді»
    # 30–40 читань)

    # ——— A «Огляд» ———
    @api.model
    def profit_overview(self, month=None):
        """Екран A — один виклик: місяць (немає — останній закритий; лише від calc.MIN_MONTH до поточного), п'ять підсумків місяця по
        рядках ліній (calc.totals), тривоги з описом списку для кліку, плитки ліній (рядок лінії як є: чисто, €/год, частка, стрілка до
        минулого місяця, житло не з наших грошей, «Куди йде частка»), ряди «Чисто по місяцях» (усі лінії разом і кожна) від MIN_MONTH до
        останнього закритого місяця з віссю без викидів (calc.chart_axis); плитки — за «чисто € год» від збиткових (calc.tile_order). Лише
        читання. → {'month', 'months', 'has', 'totals', 'opens' (опис списку кожного підсумку), 'alerts', 'lines' (сегмент смуги — з 'open'),
        'series', 'split_keys'}."""
        self._check_screen()
        lang = self._lang()
        today = calc.iso(self._today())
        month = calc.screen_month(month, today)
        all_months = calc.month_list(calc.MIN_MONTH, self._cur_month(today))
        # графік — лише закриті місяці (поточний неповний — не «падіння»), круг 3а
        chart_months = calc.month_list(calc.MIN_MONTH, calc.last_closed(today))
        order = self._line_order()
        rows = self._screen_rows(all_months)
        line_rows = [r for r in rows if r['row_kind'] == 'line']
        mine = [r for r in rows if r['month'] == month]
        now = sorted([r for r in mine if r['row_kind'] == 'line'], key=lambda r: (order.get(r['line'], 10 ** 6), r['line']))
        prev_m = calc.add_months(month, -1)
        prev = {r['line']: r for r in line_rows if r['month'] == prev_m and r['hours_rate']}
        day = min(calc.month_last(month), today)
        hr = self._housing_rules()
        label = calc.month_info(month, lang)['title_in']
        # круг 3в: розклад місяця — лише коли є лінії (сегменти «Куди йде частка» й «Години» відкривають свої записи); табелі — лише за
        # потреби (круг 4: «Огляду» вони не потрібні — _month_parts with_ts=False)
        parts = self._month_parts(month, hr, with_ts=False) if now else None
        lines = []
        for r in now:
            p = prev.get(r['line'])
            # годин зі ставкою немає (лише витрати) — «—» на плитці й без стрілки від нуля (як клітинка матриці C)
            net_ph = r['net_per_hour'] if r['hours_rate'] else None
            split = calc.share_split(r)
            for s in split:
                # сегмент — записи свого виду лінії за місяць; «чисто» — рядок лінії; житло без своєї аналітики — житло, яке вирахував
                # замовник; житло з обох частин (аналітика + правило замовника, круг 4) — рядок лінії: у ньому обидві
                field = s['key']
                if field == 'housing' and not calc.cents(r['housing']):
                    field = 'housing_customer'
                both = field == 'housing' and calc.cents(r['housing_customer'])
                s['open'] = (self._line_open(r['line'], r['line_id'][1], month, label, lang) if (field == 'net' or both)
                             else self._kind_open(r['line'], r['line_id'][1], field, month, label, parts['kinds'], lang))
            remain_ph = r['remain_per_hour'] if r['hours_rate'] else None
            lines.append({'id': r['line'], 'row_id': r['id'], 'name': r['line_id'][1], 'hours': r['hours_total'],
                          'share_ph': calc.per_hour(r['share'], r['hours_rate']), 'net_ph': net_ph, 'net': r['net'],
                          'tone': 'neg' if calc.cents(r['net']) < 0 else '', 'trend': calc.trend(net_ph, p['net_per_hour'] if p else None),
                          # круг 4: під «чисто» плитки — «залишається» (мінус — червоним)
                          'remain': r['remain'], 'remain_ph': remain_ph, 'remain_tone': 'neg' if calc.cents(r['remain']) < 0 else '',
                          'housing_from': self._housing_from(r['line'], day, hr), 'split': split})
        by_month = {}
        for r in line_rows:
            by_month.setdefault(r['month'], []).append(r)
        names = {}
        for r in line_rows:
            names.setdefault(r['line'], r['line_id'][1])
        series = {
            'months': [calc.month_info(m, lang) for m in chart_months], 'labels': calc.month_axis(chart_months, lang),
            'total_label': calc.t('l_all', lang), 'each_label': calc.t('l_each', lang),
            'total': [calc.totals(by_month[m])['net_ph'] if m in by_month else None for m in chart_months],
            'lines': [{'id': lid, 'name': names[lid],
                       'values': [next((x['net_per_hour'] for x in by_month.get(m, []) if x['line'] == lid and x['hours_rate']), None)
                                  for m in chart_months]}
                      for lid in sorted(names, key=lambda i: (order.get(i, 10 ** 6), i))],
        }
        # вісь без викидів: ряди ліній, потім «усі лінії» — номер ряду в 'out' = номер набору даних графіка
        series['axis'] = calc.chart_axis(series['lines'] + [{'name': series['total_label'], 'values': series['total']}], series['labels'])
        # круг 4: «ZUS і загальні» і «Залишається» — з рядків ліній (частки за годинами), рахунки й чип «без мітки» — з підсумку місяця
        oh = self._overheads([month]).get(month)
        # круг 4б: підсумку місяця немає (пораховано до круга 4) — «—» і підказка «не перераховано», а не «Залишається 0 €»
        tot = dict(calc.totals(now), **calc.remain_totals(now))
        if now and not oh:
            tot = self._no_overhead(tot)
            for x in lines:
                x.update(remain=None, remain_ph=None, remain_tone='')
        return {
            'month': calc.month_info(month, lang),
            'months': [calc.month_info(m, lang) for m in sorted({r['month'] for r in line_rows} | {month}, reverse=True)],
            'has': bool(now), 'totals': tot, 'oh_missing': bool(now) and not oh, 'oh_hint': calc.t('oh_missing', lang),
            'opens': self._overview_opens(month, now, parts, label, lang, mine, oh),
            'alerts': self._overview_alerts(month, mine, line_rows, lang, oh),
            'lines': calc.tile_order(lines), 'series': series,
            # легенда «Куди йде частка» — лише частини з помітним сегментом хоч в одній лінії (calc.split_legend, круг 3б)
            'split_keys': calc.split_legend([x['split'] for x in lines]),
        }

    def _overview_opens(self, month, now, parts, label, lang, month_rows=(), oh=None):
        """Підсумки «Огляду» → опис списку кожного (круг 3в): «Години» — табелі місяця замовників ліній (з них години рядків ліній),
        «Частка», «Чисто», «Чисто за місяць» — рядки ліній місяця, «Житло, авто, паливо, інструмент» — записи витрат ліній місяця
        (_costs_open; круг 4, ревю 3в — раніше рядки ліній). Круг 4: «ZUS» — рахунки ZUS і PIT-4 місяця роботи (з підсумку місяця oh),
        «загальні» — рядки «Офіс» місяця (є — month_rows), «Залишається», «Залишається за місяць» — рядки ліній. Ліній немає — False."""
        if not now:
            return False
        line_dom = [('month', '=', month), ('row_kind', '=', 'line')]
        rows = self._open(calc.t('o_lines', lang) % label, line_dom)
        mine = {r['line'] for r in now}
        custs = {c for c, l in calc.pairs_map(parts['cust_lines']).items() if l in mine}
        hours = self.env['account.analytic.line'].has_access('read') and self._open(
            calc.t('o_ts', lang) % (calc.t('l_all', lang), label), self._ts_domain(month, calc.month_last(month), custs), 'account.analytic.line')
        fallback = self._open(calc.t('o_costs', lang) % label,
                              line_dom + ['|'] * (len(COST_FIELDS) - 1) + [(f, '!=', 0) for f in COST_FIELDS])
        costs = self._costs_open(now, parts, fallback, label, lang)
        zus = self._moves_open((oh or {}).get('move_ids'), calc.t('o_zus', lang) % label)
        office = any(r['row_kind'] == 'office' for r in month_rows) and self._office_open(
            [month], calc.t('o_office', lang) % label, parts, {month: oh} if oh else {})
        # круг 4б: місяць без підсумку «ZUS і загальні» — «Залишається» невідоме: без кліку
        remain = rows if oh else False
        return {'hours': hours, 'share': rows, 'costs': costs, 'net': rows, 'net_month': rows, 'zus': zus, 'office': office,
                'remain': remain, 'remain_month': remain}

    def _housing_from(self, line_id, day, hr):
        """Житло лінії не з наших грошей на day (правило лінії й правила її замовників — як _housing_at «Ставок», з правил hr, прочитаних
        раз на запит): 'partner' — з буфера лінії, 'worker_fund' — з фонду працівників, інакше '' (calc.housing_from). Вид правила не
        важить: «Житло не наше» з «Хто платить» = партнер — теж буфер лінії."""
        Partner = self.env['res.partner'].sudo()
        line = self.env['workset.line'].sudo().browse(line_id)
        ids = [self._housing_pick(hr, line.id, calc.cust_of(cust), day) for cust in [Partner] + list(line.customer_ids)]
        return calc.housing_from([hr['payer'].get(i) for i in ids if i])

    def _overview_alerts(self, month, mine, line_rows, lang, oh=None):
        """Чипи «Огляду» за місяць, у порядку на сторінці; кожен — з описом списку рядків (число на чипі = рядки списку):
        «Не розподілено» (€, чисто рядків «Не розподілено»), «Години без ставки», «Години без лінії», «Житло не в своєму місяці»
        (calc.housing_shift), «ZUS без мітки місяця» (круг 4: рахунки ZUS / PIT-4 без єдиної мітки «ММ/РРРР» — місяць за датою
        проводки; список рахунків), «Звірка» (зі «Звірки місяця»), нейтральний «Власники» (сума й кожна стаття окремо — parts; круг 3а:
        не тривога, кольору немає). Нуля немає — чипа немає."""
        label = calc.month_info(month, lang)['title']
        out = []
        # «Не розподілено» на 0 € (буфер житла, гроші лінії без лінії — не в «чисто») — не тривога: чипа немає
        un = [r for r in mine if r['row_kind'] == 'unallocated']
        un_c = sum(calc.cents(r['net']) for r in un)
        if un_c:
            out.append({'key': 'unallocated', 'amount': calc.eur(un_c), 'tone': 'danger',
                        'open': self._open(calc.t('t_unalloc', lang) % label, [('month', '=', month), ('row_kind', '=', 'unallocated')])})
        # години на чипах — цілі, як скрізь (calc.whole_hours, круг 3б); чип — коли годин є хоч якісь (0,3 год — «0», але чип є)
        no_rate = round(sum(r['hours_no_rate'] for r in mine if r['row_kind'] in ('line', 'noline')), 2)
        if no_rate:
            out.append({'key': 'no_rate', 'hours': calc.whole_hours(no_rate), 'tone': 'warning',
                        'open': self._open(calc.t('t_no_rate', lang) % label, [('month', '=', month), ('hours_no_rate', '>', 0)])})
        noline = round(sum(r['hours_total'] for r in mine if r['row_kind'] == 'noline'), 2)
        if noline:
            out.append({'key': 'noline', 'hours': calc.whole_hours(noline), 'tone': 'warning',
                        'open': self._open(calc.t('t_noline', lang) % label, [('month', '=', month), ('row_kind', '=', 'noline')])})
        ratio = calc.parse_num(self._param(calc.P_HOUSING_SHIFT), calc.HOUSING_SHIFT_DEFAULT)
        shift = calc.housing_shift([{'line': r['line'], 'month': r['month'], 'housing': r['housing'], 'hours': r['hours_total']}
                                    for r in line_rows if calc.month_first(r['month']) >= calc.MIN_MONTH], month, ratio)
        if shift:
            out.append({'key': 'housing_shift', 'count': len(shift), 'tone': 'warning',
                        'open': self._open(calc.t('t_shift', lang) % label,
                                           [('month', '=', month), ('row_kind', '=', 'line'), ('line_id', 'in', shift)])})
        unlabeled = (oh or {}).get('unlabeled_ids') or []
        if unlabeled:
            out.append({'key': 'zus_unlabeled', 'count': len(unlabeled), 'tone': 'warning',
                        'open': self._moves_open(unlabeled, calc.t('o_unlabeled', lang) % label)})
        check = self._checks([month]).get(month)
        if check:
            out.append({'key': 'check', 'state': check['state'], 'text': check['text'], 'tone': '' if check['state'] == 'ok' else 'danger'})
        owners = sorted([r for r in mine if r['row_kind'] == 'owner' and calc.cents(r['net'])],
                        key=lambda r: r['analytic_id'][1] if r['analytic_id'] else '')
        if owners:
            out.append({'key': 'owners', 'amount': calc.eur(sum(calc.cents(r['net']) for r in owners)), 'tone': '',
                        'parts': [{'code': r['analytic_id'][1] if r['analytic_id'] else '—', 'amount': r['net']} for r in owners],
                        'open': self._open(calc.t('t_owners', lang) % label, [('id', 'in', [r['id'] for r in owners])])})
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
        # line_id з params / localStorage — будь-що: не число — як «не знайдено» (перша лінія місяця), не помилка сервера
        lid = calc.parse_id(line_id)
        line = Line.browse(lid).exists() if lid else Line
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
        # графік — лише місяці, де в лінії є години зі ставкою (€ год є), без порожніх місяців до старту лінії (круг 3а)
        data_months = [m for m in all_months if m in by_month and by_month[m]['hours_rate']]
        done = set(self._overheads(data_months))
        # круг 4: друга серія — «Залишається» € год (лінія поверх стовпців «чисто»)
        out['series'] = {'months': [calc.month_info(m, lang) for m in data_months], 'labels': calc.month_axis(data_months, lang),
                         'net_label': calc.t('l_net', lang), 'share_label': calc.t('l_share', lang), 'remain_label': calc.t('l_remain', lang),
                         'net': [by_month[m]['net_per_hour'] for m in data_months],
                         # місяць без підсумку «ZUS і загальні» (не перераховано, круг 4б) — без точки, не 0
                         'remain': [by_month[m]['remain_per_hour'] if m in done else None for m in data_months],
                         'share': [calc.per_hour(by_month[m]['share'], by_month[m]['hours_rate']) for m in data_months]}
        row = by_month.get(month)
        if not row:
            return out
        name = line.sudo().display_name
        label = calc.month_info(month, lang)['title_in']
        parts = self._month_parts(month)
        line_open = self._line_open(line.id, name, month, label, lang)
        oh = self._overheads([month]).get(month) or {}
        zus_open = self._moves_open(oh.get('move_ids'), calc.t('o_zus', lang) % label)
        office_open = self._office_open([month], calc.t('o_office', lang) % label, parts, {month: oh} if oh else {})
        # круг 4: після «чисто» — − ZUS, PIT-4 → − Загальні фірм → = Залишається (calc.remain_steps; часток немає — кроків немає); круг 4б:
        # підсумку місяця немає (не перераховано після оновлення) — кроків теж немає, «Залишається» — «—»
        steps = calc.chain_steps(row) + (calc.remain_steps(row) if oh else [])
        for s in steps:
            s['label'] = calc.t('s_' + s['key'], lang)
            # круг 3в: крок водоспаду — записи свого виду (як група «Рядків»); частка й «чисто» — рядок лінії; круг 4: ZUS — рахунки
            # місяця, загальні — рядки «Офіс», «Залишається» — рядок лінії
            if s['key'] in ('share', 'net', 'remain'):
                s['open'] = line_open
            elif s['key'] == 'zus':
                s['open'] = zus_open
            elif s['key'] == 'office':
                s['open'] = office_open
            else:
                s['open'] = self._kind_open(line.id, name, s['key'], month, label, parts['kinds'], lang)
        day = min(calc.month_last(month), today)
        pays = self._who_pays(line, day, lang)
        # години без ставки лінії — жовтим чипом у підсумках; клік — рядки місяця лінії з такими годинами (як чип «Огляду», лише ця лінія)
        no_rate = False
        if row['hours_no_rate']:
            no_rate = {'hours': calc.whole_hours(row['hours_no_rate']),
                       'open': self._open(calc.t('t_no_rate_line', lang) % (line.sudo().display_name, calc.month_info(month, lang)['title']),
                                          [('month', '=', month), ('line_id', '=', line.id), ('hours_no_rate', '>', 0)])}
        fold = calc.pays_fold(pays, lang)
        # згорнутий рядок «Усе платимо ми» — правила «Хто платить» лінії (немає — порожній список: «правил немає → ми»)
        fold['open'] = self._open(calc.t('o_rules', lang) % name, [('line_id', '=', line.id)], COST_RULE)
        totals = dict(calc.totals([row]), **calc.remain_totals([row]))
        out.update({'has': True, 'row_id': row['id'], 'totals': totals if oh else self._no_overhead(totals),
                    'oh_missing': not oh, 'oh_hint': calc.t('oh_missing', lang), 'waterfall': calc.waterfall(steps), 'pays': pays, 'pays_fold': fold, 'no_rate': no_rate,
                    'aside': [{'key': f, 'label': calc.aside_label(f, lang), 'amount': row[f]} for f in calc.ASIDE if calc.cents(row[f])],
                    'recomputed': self._stamp(row.get('write_date'))})
        out.update(self._card_rows(line, month, row, lang, parts, {'hours': oh.get('hours') or 0, 'zus': zus_open, 'office': office_open}))
        # підсумки (круг 3в): «Години» — табелі лінії (той самий список, що рядок табелів), решта — рядок лінії місяця; круг 4 — шостий,
        # «Залишається», теж рядок лінії
        ts = out['rows'][-1]['open']
        out['opens'] = {'hours': ts, 'share': line_open, 'costs': line_open, 'net': line_open, 'net_month': line_open,
                        'remain': line_open if oh else False}
        return out

    def _stamp(self, dt):
        """Коли перераховано рядок (write_date рядка підсумку: перерахунок пише рядки місяця заново) → «06.11.2026 23:04» у часовому поясі
        користувача (немає — calc.TZ); немає — ''."""
        if not dt:
            return ''
        me = self.with_context(tz=self.env.user.tz or calc.TZ)
        return fields.Datetime.context_timestamp(me, fields.Datetime.to_datetime(dt)).strftime('%d.%m.%Y %H:%M')

    def _who_pays(self, line, day, lang):
        """«Хто платить» лінії на day: житло — правило житла лінії «Ставок» (_housing_at), авто, паливо, інструмент, перевезення — правила
        «Хто платить» (правила лінії; правила замовників лінії з іншим платником — винятками); паливо — ще картки DKV лінії на day.
        Кожен рядок і виняток — з описом 'open' (круг 3в): правило, за яким визначено платника (форма правила житла «Ставок» чи правила
        «Хто платить»); правила немає — список правил лінії цього виду (порожній — теж відповідь: «правил немає → ми»).
        → [{'key', 'label', 'payer', 'payer_key', 'open', 'except': [{'name', 'payer', 'payer_key', 'open'}], 'cards': [назва]}]."""
        Housing = self.env['workset.line.housing'].sudo()
        Cost = self.env['workset.line.cost.rule']
        line = line.sudo()
        h_labels = dict(Housing._fields['payer']._description_selection(self.env))
        c_labels = dict(Cost._fields['payer']._description_selection(self.env))
        can_h = self.env['workset.line.housing'].has_access('read')
        can_c = Cost.has_access('read')

        def rule_open(model, rid, kind, can):
            if not can:
                return False
            title = calc.t('o_rule', lang) % (calc.t('s_' + kind, lang), line.display_name)
            if rid:
                return self._open(title, [('id', '=', rid)], model, rid)
            dom = [('line_id', '=', line.id)] + ([('kind', '=', kind)] if model == COST_RULE else [])
            return self._open(title, dom, model)
        custs = list(line.customer_ids)
        hr = self._housing_rules()
        base = self._housing_pick(hr, line.id, 0, day)
        base_payer = hr['payer'].get(base, '') if base else ''
        # payer_key — ключ calc.PAYER_KEYS (житло «Ставок» — через calc.housing_payer): по ньому calc.pays_fold згортає однакові рядки
        item = {'key': 'housing', 'label': calc.t('s_housing', lang), 'payer': h_labels.get(base_payer) or c_labels.get('we', ''),
                'payer_key': calc.housing_payer(base_payer), 'open': rule_open('workset.line.housing', base, 'housing', can_h),
                'except': [], 'cards': []}
        for cust in custs:
            rid = self._housing_pick(hr, line.id, calc.cust_of(cust), day)
            payer = hr['payer'].get(rid, '') if rid else ''
            if rid and rid != base and payer != base_payer:
                item['except'].append({'name': cust.display_name, 'payer': h_labels.get(payer) or c_labels.get('we', ''),
                                       'payer_key': calc.housing_payer(payer), 'open': rule_open('workset.line.housing', rid, 'housing', can_h)})
        out = [item]
        rules = Cost._rules()
        periods = self.env['workset.dkv.card.period']._periods()
        for kind in calc.COST_KINDS:
            got = calc.cost_rule_pick(rules, line.id, 0, kind, day) or {}
            payer = got.get('payer') or 'we'
            item = {'key': kind, 'label': calc.t('s_' + kind, lang), 'payer': c_labels.get(payer, ''), 'payer_key': payer,
                    'open': rule_open(COST_RULE, got.get('id'), kind, can_c), 'except': [], 'cards': []}
            for cust in custs:
                rule = calc.cost_rule_pick(rules, line.id, calc.cust_of(cust), kind, day)
                if rule and rule.get('customer_id') and rule['payer'] != payer:
                    item['except'].append({'name': cust.display_name, 'payer': c_labels.get(rule['payer'], ''), 'payer_key': rule['payer'],
                                           'open': rule_open(COST_RULE, rule['id'], kind, can_c)})
            if kind == 'fuel':
                cards = sorted({p['card_id'] for p in periods})
                mine = [c for c in cards if (calc.card_period(periods, c, day) or {}).get('target') == 'line'
                        and calc.card_period(periods, c, day).get('line_id') == line.id]
                item['cards'] = [c.name for c in self.env['workset.dkv.card'].sudo().browse(mine)]
            out.append(item)
        return out

    def _card_rows(self, line, month, row, lang, parts, overhead=None):
        """«Рядки за місяць» картки лінії: записи розкладу місяця, що лягли на лінію (_month_entries — як у перерахунку), за статтею й
        рахунком (аналітика), карткою (DKV) або «перевезення за прибуттями»; ще оренда авто й житло, яке вирахував замовник (зі ставок і
        правил), і табелі лінії одним рядком. Рядок — {'title', 'sub', 'amount', 'chain' (у «чисто»), 'open' (опис списку або False — без
        кліку)}. Порядок: ланцюжок (найбільші витрати першими), «не в нашому чисто», круг 4 — група «ZUS і загальні (за годинами)»
        (рядки 'overhead': частки лінії ZUS, PIT-4 і загальних фірм; overhead — {'hours' (усі години місяця), 'zus', 'office' (описи
        списків)}), табелі останніми; показано CARD_ROWS — {'rows', 'rows_total', 'stale' (частка + рядки ланцюжка ≠ збереженому
        «Чисто»: аналітику, DKV або прибуття змінили після перерахунку)}. parts — розклад місяця (_month_parts: той самий, що для кроків
        водоспаду)."""
        first, last = month, calc.month_last(month)
        cust_lines, ts, entries, refs = parts['cust_lines'], parts['ts'], parts['entries'], parts['refs']
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
        label = calc.month_info(month, lang)['title']
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
        # ланцюжок — зі збереженого рядка, список — розклад наново: частка + рядки ланцюжка (усі, не лише показані) ≠ «Чисто» — після
        # перерахунку змінили аналітику, транзакції DKV або прибуття (перевезення ділиться за табелями); частка, оренда авто й житло, яке
        # вирахував замовник, — зі збереженого рядка, тож правку годин чи ставок у табелях мітка не ловить (до «Перерахувати»)
        stale = calc.cents(row['share']) + sum(calc.cents(r['amount']) for r in out if r['chain']) != calc.cents(row['net'])
        # круг 4: частки лінії ZUS, PIT-4 і загальних фірм — окремою групою (не ланцюжок «чисто», не «не в нашому чисто»), завжди видно
        oh = overhead or {}
        extra = [{'title': calc.t(key, lang), 'sub': calc.t('r_hours', lang) % (calc.num_text(calc.whole_hours(row['hours_total'])),
                                                                               calc.num_text(calc.whole_hours(oh.get('hours') or 0))),
                  'amount': row[field], 'chain': False, 'overhead': True, 'open': oh.get(spec) or False}
                 for key, field, spec in (('r_zus', 'zus_amount', 'zus'), ('r_office', 'office_amount', 'office')) if calc.cents(row[field])]
        total = len(out) + len(extra) + 1
        shown = out[:calc.CARD_ROWS - 1] + extra
        # табелі лінії: замовники, які належать цій лінії (перша лінія замовника), години й склеєні люди — ті самі, що в «Годин»
        custs = sorted({c for c, l in calc.pairs_map(cust_lines).items() if l == line.id})
        mine = [r for r in ts if first <= r['date'] <= last and r['cust'] in custs]
        hours = round(sum(r['hours'] for r in mine), 2)
        # табелі лінії — і рядок списку, і підсумок «Години» (круг 3в): «Табелі: <лінія>, <місяць>»
        title = calc.t('o_ts', lang) % (line.sudo().display_name, calc.month_info(month, lang)['title_in'])
        shown.append({'title': calc.t('r_timesheets', lang) % (calc.num_text(hours), len({r['glue'] for r in mine})),
                      'sub': '', 'amount': None, 'chain': False, 'timesheets': True,
                      'open': can_aal and self._open(title, self._ts_domain(first, last, custs), 'account.analytic.line')})
        return {'rows': shown, 'rows_total': total, 'stale': stale}

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
        # колонка «N місяців» відкриває рядки за весь період таблиці: «січень 2026 — жовтень 2026»
        span = '%s — %s' % (calc.month_info(months[0], lang)['title_in'], calc.month_info(months[-1], lang)['title_in'])
        period = [('month', '>=', months[0]), ('month', '<=', months[-1])]
        # круг 4б: місяці з підсумком «ZUS і загальні» (разом із попереднім — для тону); без нього «Залишається» невідоме — «—» без тону
        ohs = self._overheads(([before] if before >= calc.MIN_MONTH else []) + months)
        lines = []
        for lid in sorted({r['line'] for r in in_period if r['row_kind'] == 'line'}, key=lambda i: (order.get(i, 10 ** 6), i)):
            mine = by_line[lid]
            cells = []
            for m in months:
                r = mine.get(m)
                if not r:
                    cells.append({'month': m, 'has': False})
                    continue
                pm = calc.add_months(m, -1)
                p = mine.get(pm)
                prev_ph = p['net_per_hour'] if p and p['hours_rate'] else None
                net_ph = r['net_per_hour'] if r['hours_rate'] else None
                # круг 4: показник «Залишається» — ті самі правила тону на remain / remain_per_hour
                done = m in ohs
                prev_rph = p['remain_per_hour'] if p and p['hours_rate'] and pm in ohs else None
                remain_ph = r['remain_per_hour'] if r['hours_rate'] and done else None
                steps = calc.chain_steps(r)
                more = calc.remain_steps(r) if done else []
                for s in steps + more:
                    s['label'] = calc.t('s_' + s['key'], lang)
                cells.append({'month': m, 'has': True, 'net': r['net'], 'net_ph': net_ph,
                              # годин зі ставкою немає — без заливки (calc.cell_tone: 'neg_text' — дрібно червоним € за місяць)
                              'tone': calc.cell_tone(r['net'], net_ph, prev_ph, threshold),
                              'remain': r['remain'] if done else None, 'remain_ph': remain_ph, 'oh_missing': not done,
                              'remain_tone': calc.cell_tone(r['remain'], remain_ph, prev_rph, threshold) if done else '',
                              'chain': steps, 'remain_chain': more})
            mine_rows = [mine[m] for m in months if m in mine]
            tot = dict(calc.totals(mine_rows), **calc.remain_totals(mine_rows))
            # «Залишається» за період — лише коли всі місяці лінії перераховано після круга 4 (інакше сума неповна)
            full = all(m in ohs for m in months if m in mine)
            # підсумок періоду нижче нуля — червоним, як клітинки (круг 3б); клік — рядки лінії за весь період таблиці (круг 3в)
            lines.append({'id': lid, 'name': names[lid], 'cells': cells,
                          'total': {'net': tot['net'], 'net_ph': tot['net_ph'] if tot['hours_rate'] else None,
                                    'tone': 'neg' if calc.cents(tot['net']) < 0 else '',
                                    'remain': tot['remain'] if full else None,
                                    'remain_ph': tot['remain_ph'] if (tot['hours_rate'] and full) else None, 'oh_missing': not full,
                                    'remain_tone': 'neg' if (full and calc.cents(tot['remain']) < 0) else '',
                                    'open': self._open(calc.t('o_line', lang) % (names[lid], span), period + [('row_kind', '=', 'line'),
                                                                                                               ('line_id', '=', lid)])}})

        def company(rows_of, kind, title):
            # клітинка — рядки цього виду за місяць (id — саме ті, що в сумі: «Власник» — лише своя стаття), «10 місяців» — за період
            cells = [calc.eur(sum(calc.cents(r['net']) for r in rows_of if r['month'] == m)) for m in months]
            out = []
            for m, v in zip(months, cells):
                ids = [r['id'] for r in rows_of if r['month'] == m]
                out.append({'month': m, 'has': bool(ids), 'amount': v,
                            'open': bool(ids) and self._open(title(calc.month_info(m, lang)['title_in']),
                                                             [('month', '=', m), ('row_kind', '=', kind), ('id', 'in', ids)])})
            return {'cells': out, 'total': calc.eur(sum(calc.cents(v) for v in cells)),
                    'open': self._open(title(span), period + [('row_kind', '=', kind), ('id', 'in', [r['id'] for r in rows_of])])}
        un = [r for r in in_period if r['row_kind'] == 'unallocated']
        owners = {}
        for r in in_period:
            if r['row_kind'] == 'owner':
                owners.setdefault(r['analytic_id'][1] if r['analytic_id'] else '—', []).append(r)
        checks = self._checks(months)

        def overhead_row(field, opener, title):
            # круг 4: «ZUS, PIT-4» і «Загальні фірм» — суми місяця з підсумку «ZUS і загальні місяця» (уся компанія, не частки ліній);
            # клітинка — записи місяця, «N місяців» — за період; сум немає в жодному місяці — рядка немає
            cells = [calc.eur(calc.cents((ohs.get(m) or {}).get(field))) for m in months]
            if not any(calc.cents(v) for v in cells):
                return False
            out = [{'month': m, 'has': bool(calc.cents(v)), 'amount': v,
                    'open': bool(calc.cents(v)) and opener([m], title(calc.month_info(m, lang)['title_in']))} for m, v in zip(months, cells)]
            full = [m for m, v in zip(months, cells) if calc.cents(v)]
            return {'cells': out, 'total': calc.eur(sum(calc.cents(v) for v in cells)), 'open': opener(full, title(span))}

        def zus_open(ms, title):
            return self._moves_open(sorted({i for m in ms for i in (ohs.get(m) or {}).get('move_ids') or []}), title)

        def check_cell(m):
            # «Звірка» місяця — її запис (форма); звірки не було — без кліку
            c = checks.get(m)
            if not c:
                return {'state': '', 'text': calc.t('v_none_short', lang), 'month': m, 'open': False}
            return {'state': c['state'], 'text': c['text'], 'month': m,
                    'open': self._open(calc.t('o_check', lang) % calc.month_info(m, lang)['title_in'], [('id', '=', c['id'])],
                                       'workset.profit.check', c['id'])}
        return {
            'months': [calc.month_info(m, lang) for m in months], 'heads': calc.month_axis(months, lang, short=False),
            'period': {'from': months[0], 'to': months[-1], 'text': calc.months_text(len(months), lang)},
            'options': [calc.month_info(m, lang) for m in calc.month_list(calc.MIN_MONTH, self._cur_month(today))],
            'threshold': threshold, 'has': bool(lines), 'lines': lines,
            'labels': {'hour': calc.t('l_hour', lang), 'month': calc.t('l_month', lang), 'oh_missing': calc.t('oh_missing', lang)},
            'unallocated': company(un, 'unallocated', lambda x: calc.t('t_unalloc', lang) % x) if un else False,
            'owners': [dict(company(owners[code], 'owner', lambda x, code=code: calc.t('t_owner', lang) % (code, x)), code=code)
                       for code in sorted(owners)],
            'zus': overhead_row('zus', zus_open, lambda x: calc.t('o_zus', lang) % x),
            'office': overhead_row('office', lambda ms, title: self._office_open(ms, title, None, ohs), lambda x: calc.t('o_office', lang) % x),
            'checks': [check_cell(m) for m in months],
        }
