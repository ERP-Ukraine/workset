# -*- coding: utf-8 -*-
"""Екрани «Прибутковості» (М3, круг 2): A «Огляд», B «Картка лінії», C «Лінії × місяці» — client actions workset_profit.overview,
workset_profit.line_card, workset_profit.matrix (static/src/js/profit_screens.js). Кожен екран — ОДИН виклик сервера: profit_overview,
profit_line_card, profit_matrix. Числа — збережений підсумок workset.profit.month (той, що пише перерахунок), звірка — «Звірка місяця»
(workset.profit.check); картка лінії ще раз розкладає аналітику й DKV місяця (_month_entries — ті самі записи, що в перерахунку), щоб
кожен рядок відкривав свої записи.

Круг 3в (слово власника «экраны: должно быть все кликабельно»): кожне число відкриває те, з чого воно складене. Сервер віддає в елемента
опис 'open' (модель, домен, заголовок, контекст без «Створити»; res_id — форма одного запису) — він рахується лише тут (_open,
_line_open, _kind_open, _ts_domain), екран лише виконує його (openList); нових методів на клік немає.

Круг 5 (слово власника 07.11 «период который я хочу выбрать и год, не только информация за месяц»; «хочу видеть что туда входит»): A і
B — за місяць, квартал, рік або «з–по» (calc.screen_span; підсумки — суми, € год — середньозважене, calc.rows_sum); клік по «ZUS і
загальні» — вікно «Що входить» (опис {'dialog', 'ref'} — _dialog; вміст — oh_details тієї самої відповіді, _oh_detail), нового виклику
немає. Період з кількох місяців на A місяць не розкладає (кліки — рядки й табелі за датами періоду); B («Рядки» за статтями) — круг 5б:
складає склад місяців, який записав перерахунок у рядок лінії (card_groups), розкладає лише місяць без складу й місяць, обраний сам.
Квартал і рік — до останнього закритого місяця (calc.screen_span, круг 5б).

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
ROW_FIELDS = ['name', 'month', 'row_kind', 'line_id', 'analytic_id', 'card_id', 'tag', 'hours_total', 'hours_rate', 'hours_no_rate', 'share',
              'car_rent', 'fund_in', 'housing', 'housing_customer', 'auto', 'fuel', 'tool', 'transport', 'partner_paid', 'fund_paid', 'fund_left',
              'housing_buffer', 'amount', 'net', 'net_per_hour', 'zus_amount', 'office_amount', 'remain', 'remain_per_hour', 'write_date']
IDS_MAX = 2000  # id в описі списку «Рядків за місяць» — не більше
COST_FIELDS = tuple(f for f in calc.CHAIN if f != 'share')  # між часткою й «чисто»: оренда авто плюсом, витрати лінії мінусом
# витрати з аналітики й транзакцій DKV (без оренди авто — вона зі ставок, і без житла, яке вираховує замовник, — воно з правил)
RECORD_COSTS = tuple(f for f in COST_FIELDS if f not in ('car_rent', 'housing_customer'))
OVERHEAD_READ = ['month', 'zus', 'pit4', 'office', 'office_dup', 'total', 'hours', 'per_hour', 'move_ids', 'unlabeled_ids', 'breakdown']
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

    def _dialog(self, ref, lang):
        """Круг 5: опис вікна «Що входить у ZUS і загальні» для кліку — вміст уже у відповіді екрана (oh_details[ref], _oh_detail), нового
        виклику немає; hint — підказка елемента замість «Відкрити записи»."""
        return {'dialog': 'overhead', 'ref': ref, 'hint': calc.t('oh_open', lang)}

    def _span_month(self, info, lang):
        """Місяць екрана для підписів шаблону (data.month): перший місяць періоду, назви — періоду (calc.span_info); за місяць — як і раніше
        calc.month_info."""
        return dict(calc.month_info(info['from'], lang), title=info['title'], title_in=info['title_in'])

    def _line_open(self, line_id, line_name, months, label, lang):
        """Рядки лінії за місяці періоду в «Рядках розрахунку» (частка, «чисто», витрати картки B, оренда авто — зі ставок); один місяць —
        ('month', '=', …), як і раніше (calc.span_domain)."""
        return self._open(calc.t('o_line', lang) % (line_name, label),
                          calc.span_domain(months) + [('row_kind', '=', 'line'), ('line_id', '=', line_id)])

    def _ts_domain(self, first, last, custs):
        """Табелі місяця замовників custs — той самий відбір, що _timesheets перерахунку (з чого «Години» рядків ліній)."""
        dom = [('project_id', '!=', False), ('general_account_id', '=', False), ('employee_id', '!=', False), ('unit_amount', '>', 0),
               ('date', '>=', first), ('date', '<=', last), ('project_id.partner_id.commercial_partner_id', 'in', sorted(custs))]
        skip = self.env['workset.rate'].sudo()._skip_companies()
        if skip:
            dom.append(('project_id.company_id', 'not in', skip))
        return dom

    def _ts_people(self, first, last, custs):
        """Рядок «Табелі» картки лінії (круг 5 — і за період): години й люди (склеєні картки, calc.glue_people) одним _read_group за
        працівниками — рядки табелів не читаються; відбір — _ts_domain (той самий список відкриває рядок). → (години, людей)."""
        if not custs:
            return 0.0, 0
        AAL = self.env['account.analytic.line'].sudo().with_context(active_test=False)
        groups = [(e, h) for e, h in AAL._read_group(self._ts_domain(first, last, custs), ['employee_id'], ['unit_amount:sum']) if e]
        emps = self.env['hr.employee'].sudo().with_context(active_test=False).browse([e.id for e, _h in groups])
        glue = self.sudo()._people(emps)
        return round(sum(float(h or 0) for _e, h in groups), 2), len({glue.get(e.id, e.id) for e, _h in groups})

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
        """«ZUS і загальні місяця» (workset.profit.overhead — пише перерахунок) → {місяць: {'id', 'zus', 'pit4', 'office', 'office_dup',
        'total', 'hours', 'per_hour', 'move_ids', 'unlabeled_ids', 'breakdown'}}; місяця немає (перерахунку круга 4 ще не було) — його немає
        в словнику. sudo: id рахунків читаються й тоді, коли бухгалтерії в користувача немає (відкрити їх — _moves_open, за його правами)."""
        out = {}
        for o in self.env['workset.profit.overhead'].sudo().search_read([('month', 'in', list(months))], OVERHEAD_READ):
            out[calc.iso(o['month'])] = o
        return out

    def _moves_open(self, ids, title):
        """Рахунки ZUS / PIT-4 (account.move) — список; рахунків немає або прав на бухгалтерію немає — False (без кліку)."""
        if not ids or not self.env['account.move'].has_access('read'):
            return False
        return self._open(title, [('id', 'in', sorted(ids)[:IDS_MAX])], 'account.move')

    def _office_open(self, months, title, ids, ohs):
        """Загальні фірм — рядки «Офіс», з яких складено число (круг 4б: клік = число): у місяцях без аналітики рахунків ZUS / PIT-4 в
        «Офісі» (office_dup = 0) — усі рядки «Офіс» цих місяців (домен за місяцями); є — рівно рядки числа ids (calc.office_split →
        calc.office_rows_in: рядок, складений цілком із неї, не входить). Рядків немає — False."""
        if not ids:
            return False
        if not any(calc.cents((ohs.get(m) or {}).get('office_dup')) for m in months):
            return self._open(title, [('month', 'in', sorted(months)), ('row_kind', '=', 'office')])
        return self._open(title, [('id', 'in', sorted(ids)[:IDS_MAX])])

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

    def _kind_open(self, line_id, line_name, field, months, label, kinds, lang):
        """Клік по виду витрати лінії за період (крок водоспаду B, сегмент «Куди йде частка» A) → ті самі записи, що в «Рядках» картки
        (kinds — calc.kind_refs розкладу місяців періоду): лише аналітика — її рядки, лише транзакції DKV — вони; житло, яке вирахував
        замовник, — правила житла лінії «Ставок»; частка, оренда авто (зі ставок), «чисто» і вид, складений з аналітики й DKV разом (один
        список — одна модель), — рядки лінії за період."""
        title = calc.t('o_kind', lang) % (calc.t('s_' + field, lang), line_name, label)
        g = (kinds.get(line_id) or {}).get(field) or {}
        aal, txn = g.get('aal') or [], g.get('txn') or []
        if field == 'housing_customer' and self.env['workset.line.housing'].has_access('read'):
            return self._open(title, [('line_id', '=', line_id), ('payer', '=', 'customer')], 'workset.line.housing')
        if aal and not txn and self.env['account.analytic.line'].has_access('read'):
            return self._open(title, [('id', 'in', aal[:IDS_MAX])], 'account.analytic.line')
        if txn and not aal and self.env['workset.dkv.txn'].has_access('read'):
            return self._open(title, [('id', 'in', txn[:IDS_MAX])], 'workset.dkv.txn')
        return self._line_open(line_id, line_name, months, label, lang)

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

    # ——— круг 5: «Що входить у ZUS і загальні» ———
    def _oh_records(self, months, ohs, office_rows, parts=None):
        """Записи, з яких складено «ZUS і загальні» місяців months (вікно «Що входить»): рахунки ZUS / PIT-4 підсумків місяців — вид і сума
        зі складу breakdown, який пише перерахунок (місяць до 19.0.4 — _contrib_moves, та сама функція, що в перерахунку), фірма рахунку;
        частини рядків «Офіс» office_rows (calc.office_split: аналітика рахунків ZUS / PIT-4 за статтями — зі складу, до 19.0.4 — розклад
        місяця, лише коли office_dup ≠ 0; parts — {місяць: розклад}, уже прочитаний запитом); рахунки з міткою поза вікном, проведені в цих
        місяцях (довідково). → {'zus': [{'month', 'move_id', 'company', 'company_name', 'kind', 'amount', 'labeled'}], 'office': [{'month',
        'id', 'key', 'cents'}], 'outside': [{'month', 'move_id', 'amount'}], 'names': {стаття: (назва, план)}}."""
        done = [m for m in sorted(months) if m in ohs]
        me = self.sudo()
        zus, old = [], {}
        for m in done:
            oh = ohs[m]
            unl = set(oh.get('unlabeled_ids') or [])
            bd = oh.get('breakdown') or {}
            if 'moves' in bd:
                zus += [{'month': m, 'move_id': i, 'kind': k, 'amount': a, 'labeled': i not in unl} for i, k, a in bd['moves']]
            elif oh.get('move_ids'):
                old[m] = unl
        if old:
            month_of = {i: m for m in old for i in ohs[m]['move_ids']}
            for it in me._contrib_moves([('id', 'in', sorted(month_of))]):
                m = month_of[it['move_id']]
                zus.append({'month': m, 'move_id': it['move_id'], 'kind': it['kind'], 'amount': it['amount'], 'labeled': it['move_id'] not in old[m]})
        if zus:
            moves = self.env['account.move'].sudo().browse(sorted({z['move_id'] for z in zus})).exists()
            firm = {x.id: x.company_id for x in moves}
            for z in zus:
                c = firm.get(z['move_id'])
                z.update(company=c.id if c else 0, company_name=(c.name if c else '') or '—')
        office = []
        for m in done:
            rows = [{'id': r['id'], 'analytic_id': r['analytic_id'][0] if r.get('analytic_id') else 0,
                     'card_id': r['card_id'][0] if r.get('card_id') else 0, 'amount': r['amount']} for r in office_rows if r['month'] == m]
            if not rows:
                continue
            dup = {}
            if calc.cents(ohs[m].get('office_dup')):
                bd = ohs[m].get('breakdown') or {}
                if 'office_dup' in bd:
                    dup = bd['office_dup']
                else:
                    p = (parts or {}).get(m) or self._month_parts(m, with_ts=False)
                    dup = me._contrib_in_rows(p['entries'], p['refs'])['by_analytic']
            office += [dict(x, month=m) for x in calc.office_split(rows, dup)]
        names = {}
        keys = sorted({x['key'] for x in office if isinstance(x['key'], int) and x['key']})
        for a in self.env['account.analytic.account'].sudo().with_context(active_test=False).browse(keys).exists():
            names[a.id] = (a.display_name, a.plan_id.display_name or '')
        outside = []
        if done:
            sel = set(done)
            for it in me._contrib_outside_moves(done[0], calc.month_last(done[-1])):
                if calc.month_first(it['date']) in sel:
                    outside.append({'month': calc.month_first(it['date']), 'move_id': it['move_id'], 'amount': it['amount']})
        return {'zus': zus, 'office': office, 'outside': outside, 'names': names}

    def _oh_detail(self, sel, recs, ohs, part, label, lang, missing=False):
        """Вікно «Що входить у ZUS і загальні: <період>» за місяці sel із записів recs (_oh_records): ZUS і PIT-4 за фірмами (calc.zus_groups;
        клік — рахунки фірми й виду), «ZUS без мітки місяця» (уже в сумі) і «з міткою поза вікном» (довідково); загальні фірм за статтями
        (calc.office_groups; картки DKV з метою «офіс» — «DKV · офіс»; клік — рядки «Офіс» статті, що ввійшли в число), довідково «Рахунки
        ZUS / PIT-4 в «Офісі»»; «Як поділено» (calc.overhead_how; part — частка лінії чи всіх ліній {'hours', 'zus', 'office', 'label'},
        None — без неї). Суми — ті самі, що в числі, по якому клікнули: ZUS — zus підсумків місяців, загальні — office, частка — рядки лінії.
        missing — у періоді є місяці без підсумку (не перераховано після круга 4): підказка."""
        dset = {m for m in sel if m in ohs}
        done = sorted(dset)
        z = calc.zus_groups([x for x in recs['zus'] if x['month'] in dset])
        o = calc.office_groups([x for x in recs['office'] if x['month'] in dset])
        out_items = [x for x in recs['outside'] if x['month'] in dset]
        zt = calc.t('s_zus', lang)
        rows = [{'company': r['company'], 'name': r['name'], 'zus': r['zus'], 'pit4': r['pit4'], 'total': r['total'], 'n': r['n'],
                 'open': self._moves_open(r['ids'], calc.t('o_zus_firm', lang) % (zt, r['name'], label)),
                 'zus_open': self._moves_open(r['zus_ids'], calc.t('o_zus_firm', lang) % ('ZUS', r['name'], label)),
                 'pit4_open': self._moves_open(r['pit4_ids'], calc.t('o_zus_firm', lang) % ('PIT-4', r['name'], label))} for r in z['rows']]
        tot = z['total']
        unl = z['unlabeled']
        out_ids = sorted({x['move_id'] for x in out_items})
        zus = {'rows': rows,
               'total': {'zus': tot['zus'], 'pit4': tot['pit4'], 'total': tot['total'], 'n': tot['n'],
                         'open': self._moves_open(tot['ids'], calc.t('o_zus', lang) % label)},
               'unlabeled': ({'n': unl['n'], 'amount': unl['amount'], 'open': self._moves_open(unl['ids'], calc.t('o_unlabeled', lang) % label)}
                             if unl['n'] else False),
               'outside': ({'n': len(out_ids), 'amount': calc.eur(sum(calc.cents(x['amount']) for x in out_items)),
                            'open': self._moves_open(out_ids, calc.t('o_outside', lang) % label)} if out_ids else False)}
        orows = []
        for r in o['rows']:
            name, plan = (calc.t('oh_dkv', lang), '') if r['key'] == 'dkv' else recs['names'].get(r['key'], ('—', ''))
            orows.append({'key': str(r['key']), 'name': name, 'plan': plan, 'amount': r['amount'], 'n': r['n'],
                          'open': self._open(calc.t('o_office_item', lang) % (name, label), [('id', 'in', r['ids'][:IDS_MAX])])})
        office = {'rows': orows, 'dup': calc.eur(sum(calc.cents(ohs[m].get('office_dup')) for m in done)),
                  'total': {'amount': o['total']['amount'], 'n': o['total']['n'],
                            'open': self._office_open(done, calc.t('o_office', lang) % label, o['total']['ids'], ohs)}}
        return {'title': calc.t('oh_title', lang) % label, 'zus': zus, 'office': office, 'how': calc.overhead_how([ohs[m] for m in done], part),
                'part_label': (part or {}).get('label') or '', 'hint': calc.t('oh_hint', lang), 'missing': calc.t('oh_missing', lang) if missing else ''}

    # ——— A «Огляд» ———
    @api.model
    def profit_overview(self, month=None, kind=None, date_to=None):
        """Екран A — один виклик: період (круг 5, calc.screen_span: kind — 'month' / 'quarter' / 'year' / 'range', month — місяць періоду,
        для «з–по» — «з», date_to — «по»; немає — останній закритий місяць; лише від calc.MIN_MONTH до поточного), підсумки періоду по рядках
        ліній (calc.totals: суми, € год — на години зі ставкою), тривоги з описом списку для кліку, плитки ліній (рядок лінії за період —
        calc.rows_sum: чисто, €/год, частка, стрілка до попереднього періоду того самого виду, житло не з наших грошей, «Куди йде частка»),
        ряди «Чисто по місяцях» (усі лінії разом і кожна) від MIN_MONTH до останнього закритого місяця з віссю без викидів (calc.chart_axis)
        і смугою обраного періоду (band); плитки — за «чисто € год» від збиткових (calc.tile_order); вікно «Що входить у ZUS і загальні»
        (oh_details). За місяць кліки ведуть на записи розкладу місяця; за кілька місяців місяці не розкладаються (рік — 12 розкладів): кліки —
        рядки й табелі за датами періоду. Лише читання. → {'month', 'span', 'prev', 'next', 'choices', 'months', 'has', 'totals', 'opens',
        'alerts', 'lines', 'series', 'split_keys', 'oh_details'}."""
        self._check_screen()
        lang = self._lang()
        today = calc.iso(self._today())
        span = calc.screen_span(kind, month, date_to, today)
        months = span['months']
        sel = set(months)
        exact = span['kind'] == 'month'
        info = calc.span_info(span, lang)
        label = info['title_in']
        all_months = calc.month_list(calc.MIN_MONTH, self._cur_month(today))
        # графік — лише закриті місяці (поточний неповний — не «падіння»), круг 3а
        chart_months = calc.month_list(calc.MIN_MONTH, calc.last_closed(today))
        order = self._line_order()
        rows = self._screen_rows(all_months)
        line_rows = [r for r in rows if r['row_kind'] == 'line']
        mine = [r for r in rows if r['month'] in sel]
        now = sorted([r for r in mine if r['row_kind'] == 'line'], key=lambda r: (order.get(r['line'], 10 ** 6), r['line'], r['month']))
        by_line = {}
        for r in now:
            by_line.setdefault(r['line'], []).append(r)
        # стрілка плитки — до попереднього періоду того самого виду (за місяць — до минулого місяця, як і раніше)
        prev = calc.span_step(span, -1, today)
        prev_sel = set(calc.month_list(prev['from'], prev['to'])) if prev else set()
        prev_by = {}
        for r in line_rows:
            if r['month'] in prev_sel:
                prev_by.setdefault(r['line'], []).append(r)
        ohs = self._overheads(months)
        day = min(calc.month_last(months[-1]), today)
        hr = self._housing_rules()
        # круг 3в: розклад місяця — лише коли є лінії (сегменти «Куди йде частка» й «Години» відкривають свої записи); табелі — лише за
        # потреби (круг 4: «Огляду» вони не потрібні — _month_parts with_ts=False); круг 5: лише за місяць
        parts = self._month_parts(months[0], hr, with_ts=False) if (now and exact) else None
        lines = []
        for lid, src in by_line.items():
            r = calc.rows_sum(src)
            name = src[0]['line_id'][1]
            p = calc.rows_sum(prev_by[lid]) if lid in prev_by else None
            # годин зі ставкою немає (лише витрати) — «—» на плитці й без стрілки від нуля (як клітинка матриці C)
            net_ph = r['net_per_hour'] if r['hours_rate'] else None
            split = calc.share_split(r)
            for s in split:
                # сегмент — записи свого виду лінії за місяць; «чисто» — рядок лінії; житло без своєї аналітики — житло, яке вирахував
                # замовник; житло з обох частин (аналітика + правило замовника, круг 4) — рядок лінії: у ньому обидві; круг 5: за
                # кілька місяців — рядки лінії за період
                field = s['key']
                if field == 'housing' and not calc.cents(r['housing']):
                    field = 'housing_customer'
                both = field == 'housing' and calc.cents(r['housing_customer'])
                s['open'] = (self._line_open(lid, name, months, label, lang) if (field == 'net' or both or not exact)
                             else self._kind_open(lid, name, field, months, label, parts['kinds'], lang))
            # «залишається» — лише коли всі місяці лінії перераховано після круга 4 (інакше сума неповна, круг 4б)
            done = all(m in ohs for m in r['months'])
            remain_ph = r['remain_per_hour'] if (r['hours_rate'] and done) else None
            lines.append({'id': lid, 'row_id': src[-1]['id'], 'name': name, 'hours': r['hours_total'],
                          'share_ph': calc.per_hour(r['share'], r['hours_rate']), 'net_ph': net_ph, 'net': r['net'],
                          'tone': 'neg' if calc.cents(r['net']) < 0 else '',
                          'trend': calc.trend(net_ph, p['net_per_hour'] if (p and p['hours_rate']) else None),
                          # круг 4: під «чисто» плитки — «залишається» (мінус — червоним)
                          'remain': r['remain'] if done else None, 'remain_ph': remain_ph,
                          'remain_tone': 'neg' if (done and calc.cents(r['remain']) < 0) else '',
                          'housing_from': self._housing_from(lid, day, hr), 'split': split})
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
            # круг 5: обраний період на графіку — смуга від першого до останнього його місяця на осі
            'band': calc.span_band(chart_months, months),
        }
        # вісь без викидів: ряди ліній, потім «усі лінії» — номер ряду в 'out' = номер набору даних графіка
        series['axis'] = calc.chart_axis(series['lines'] + [{'name': series['total_label'], 'values': series['total']}], series['labels'])
        # круг 4: «ZUS і загальні» і «Залишається» — з рядків ліній (частки за годинами), рахунки й чип «без мітки» — з підсумку місяця;
        # круг 4б: місяць без підсумку (пораховано до круга 4) — «—» і підказка «не перераховано», а не «Залишається 0 €»
        full = bool(now) and all(r['month'] in ohs for r in now)
        tot = dict(calc.totals(now), **calc.remain_totals(now))
        if now and not full:
            tot = self._no_overhead(tot)
        # круг 5: вікно «Що входить» — місяці з підсумком; частка ліній — їхні рядки цих місяців (те саме число, що «ZUS і загальні»)
        details = {}
        if any(m in ohs for m in months):
            recs = self._oh_records(months, ohs, [r for r in mine if r['row_kind'] == 'office'], {months[0]: parts} if parts else None)
            ps = calc.rows_sum([r for r in now if r['month'] in ohs])
            # круг 5б: і на годину зі ставкою — рівно число «ZUS і загальні € год», по якому клікнули (є, коли всі місяці перераховано)
            part = {'hours': ps['hours_total'], 'zus': ps['zus_amount'], 'office': ps['office_amount'], 'hours_rate': ps['hours_rate'],
                    'screen': full, 'label': calc.t('oh_part_lines', lang)}
            details['span'] = self._oh_detail(months, recs, ohs, part if now else None, label, lang, missing=bool(now) and not full)
        choices = calc.span_choices({r['month'] for r in line_rows}, span, today, lang)
        return {
            'month': self._span_month(info, lang), 'span': info, 'prev': prev, 'next': calc.span_step(span, 1, today),
            'choices': choices, 'months': choices['months'],
            'has': bool(now), 'totals': tot, 'oh_missing': bool(now) and not full, 'oh_hint': calc.t('oh_missing', lang),
            'opens': self._overview_opens(months, now, parts, label, lang, full, details),
            'alerts': self._overview_alerts(months, mine, line_rows, lang, ohs, info, exact),
            'lines': calc.tile_order(lines), 'series': series,
            # легенда «Куди йде частка» — лише частини з помітним сегментом хоч в одній лінії (calc.split_legend, круг 3б)
            'split_keys': calc.split_legend([x['split'] for x in lines]),
            'oh_details': details,
        }

    def _overview_opens(self, months, now, parts, label, lang, full=True, details=None):
        """Підсумки «Огляду» → опис списку кожного (круг 3в): «Години» — табелі періоду замовників ліній (з них години рядків ліній),
        «Частка», «Чисто», «Чисто за місяць» — рядки ліній періоду, «Житло, авто, паливо, інструмент» — за місяць записи витрат ліній
        (_costs_open; круг 4, ревю 3в), за кілька місяців — рядки ліній з витратами; круг 4 — «Залишається», «Залишається за місяць» —
        рядки ліній; круг 5 — «ZUS і загальні» — вікно «Що входить» (details['span']). Ліній немає — False."""
        if not now:
            return False
        line_dom = calc.span_domain(months) + [('row_kind', '=', 'line')]
        rows = self._open(calc.t('o_lines', lang) % label, line_dom)
        mine = {r['line'] for r in now}
        cust_lines = parts['cust_lines'] if parts else self.sudo()._lines_index()[0]
        custs = {c for c, l in calc.pairs_map(cust_lines).items() if l in mine}
        hours = self.env['account.analytic.line'].has_access('read') and self._open(
            calc.t('o_ts', lang) % (calc.t('l_all', lang), label), self._ts_domain(months[0], calc.month_last(months[-1]), custs),
            'account.analytic.line')
        fallback = self._open(calc.t('o_costs', lang) % label,
                              line_dom + ['|'] * (len(COST_FIELDS) - 1) + [(f, '!=', 0) for f in COST_FIELDS])
        costs = self._costs_open(now, parts, fallback, label, lang) if parts else fallback
        # круг 4б: місяць без підсумку «ZUS і загальні» — «Залишається» невідоме: без кліку
        remain = rows if full else False
        return {'hours': hours, 'share': rows, 'costs': costs, 'net': rows, 'net_month': rows,
                'overhead': bool((details or {}).get('span')) and self._dialog('span', lang), 'remain': remain, 'remain_month': remain}

    def _housing_from(self, line_id, day, hr):
        """Житло лінії не з наших грошей на day (правило лінії й правила її замовників — як _housing_at «Ставок», з правил hr, прочитаних
        раз на запит): 'partner' — з буфера лінії, 'worker_fund' — з фонду працівників, інакше '' (calc.housing_from). Вид правила не
        важить: «Житло не наше» з «Хто платить» = партнер — теж буфер лінії."""
        Partner = self.env['res.partner'].sudo()
        line = self.env['workset.line'].sudo().browse(line_id)
        ids = [self._housing_pick(hr, line.id, calc.cust_of(cust), day) for cust in [Partner] + list(line.customer_ids)]
        return calc.housing_from([hr['payer'].get(i) for i in ids if i])

    def _overview_alerts(self, months, mine, line_rows, lang, ohs, info, exact=True):
        """Чипи «Огляду» за період, у порядку на сторінці; кожен — з описом списку рядків (число на чипі = рядки списку): «Не розподілено»
        (€, чисто рядків «Не розподілено»), «Години без ставки», «Години без лінії», «Житло не в своєму місяці» (calc.housing_shift по
        кожному місяцю періоду; число — рядки «лінія × місяць»), «ZUS без мітки місяця» (круг 4: рахунки ZUS / PIT-4 без єдиної мітки
        «ММ/РРРР» — місяць за датою проводки; список рахунків), «Звірка» (за місяць — зі «Звірки місяця»; круг 5, за кілька — «зійшлося N з
        M», клік — звірки місяців), нейтральний «Власники» (сума й кожна стаття за період; круг 3а: не тривога, кольору немає). Нуля немає —
        чипа немає."""
        label = info['title']
        dom = calc.span_domain(months)
        out = []
        # «Не розподілено» на 0 € (буфер житла, гроші лінії без лінії — не в «чисто») — не тривога: чипа немає
        un = [r for r in mine if r['row_kind'] == 'unallocated']
        un_c = sum(calc.cents(r['net']) for r in un)
        if un_c:
            out.append({'key': 'unallocated', 'amount': calc.eur(un_c), 'tone': 'danger',
                        'open': self._open(calc.t('t_unalloc', lang) % label, dom + [('row_kind', '=', 'unallocated')])})
        # години на чипах — цілі, як скрізь (calc.whole_hours, круг 3б); чип — коли годин є хоч якісь (0,3 год — «0», але чип є)
        no_rate = round(sum(r['hours_no_rate'] for r in mine if r['row_kind'] in ('line', 'noline')), 2)
        if no_rate:
            out.append({'key': 'no_rate', 'hours': calc.whole_hours(no_rate), 'tone': 'warning',
                        'open': self._open(calc.t('t_no_rate', lang) % label, dom + [('hours_no_rate', '>', 0)])})
        noline = round(sum(r['hours_total'] for r in mine if r['row_kind'] == 'noline'), 2)
        if noline:
            out.append({'key': 'noline', 'hours': calc.whole_hours(noline), 'tone': 'warning',
                        'open': self._open(calc.t('t_noline', lang) % label, dom + [('row_kind', '=', 'noline')])})
        ratio = calc.parse_num(self._param(calc.P_HOUSING_SHIFT), calc.HOUSING_SHIFT_DEFAULT)
        src = [{'line': r['line'], 'month': r['month'], 'housing': r['housing'], 'hours': r['hours_total']}
               for r in line_rows if calc.month_first(r['month']) >= calc.MIN_MONTH]
        shift = []
        for m in months:
            got = set(calc.housing_shift(src, m, ratio))
            shift += [r['id'] for r in line_rows if r['month'] == m and r['line'] in got]
        if shift:
            out.append({'key': 'housing_shift', 'count': len(shift), 'tone': 'warning',
                        'open': self._open(calc.t('t_shift', lang) % label, [('id', 'in', shift)])})
        unlabeled = sorted({i for m in months for i in (ohs.get(m) or {}).get('unlabeled_ids') or []})
        if unlabeled:
            out.append({'key': 'zus_unlabeled', 'count': len(unlabeled), 'tone': 'warning',
                        'open': self._moves_open(unlabeled, calc.t('o_unlabeled', lang) % label)})
        checks = self._checks(months)
        if exact:
            check = checks.get(months[0])
            if check:
                out.append({'key': 'check', 'state': check['state'], 'text': check['text'], 'tone': '' if check['state'] == 'ok' else 'danger'})
        elif checks:
            # круг 5: за період — «зійшлося 9 з 9» (місяць без звірки — не зійшовся); клік — звірки місяців періоду
            summ = calc.checks_summary(months, {m: c['state'] for m, c in checks.items()}, lang)
            out.append({'key': 'check', 'state': summ['state'], 'text': summ['text'], 'summary': summ['short'],
                        'tone': '' if summ['state'] == 'ok' else 'danger',
                        'open': self._open(calc.t('o_checks', lang) % info['title_in'], [('month', 'in', months)], 'workset.profit.check')})
        owners = {}
        for r in mine:
            if r['row_kind'] == 'owner' and calc.cents(r['net']):
                owners.setdefault(r['analytic_id'][1] if r['analytic_id'] else '—', []).append(r)
        # стаття власника за період — сума її місяців; стаття, що за період дала 0, — без пункту
        parts = [{'code': code, 'amount': calc.eur(sum(calc.cents(r['net']) for r in owners[code]))} for code in sorted(owners)]
        parts = [p for p in parts if calc.cents(p['amount'])]
        if parts:
            ids = sorted(r['id'] for p in parts for r in owners[p['code']])
            out.append({'key': 'owners', 'amount': calc.eur(sum(calc.cents(p['amount']) for p in parts)), 'tone': '', 'parts': parts,
                        'open': self._open(calc.t('t_owners', lang) % label, [('id', 'in', ids)])})
        return out

    # ——— B «Картка лінії» ———
    @api.model
    def profit_line_card(self, line_id=None, month=None, kind=None, date_to=None):
        """Екран B — один виклик: лінія (немає або не знайдено — перша лінія періоду) і період (як в «Огляді», круг 5), сусідні періоди для
        стрілок (prev / next — calc.span_step), п'ять підсумків лінії за період, водоспад «частка → … → чисто» (calc.chain_steps / waterfall
        рядка періоду calc.rows_sum: нульових кроків немає), «Хто платить» на останній день періоду, «Не в нашому чисто», ряди «Чисто по
        місяцях» (смуга — обраний період) і «Рядки за період» (ті самі записи, що в перерахунку, — за статтею з сумою й числом записів:
        місяць — розклад наново, кілька — склад місяців card_groups, круг 5б), кожен відкриває свої записи; вікно «Що входить у ZUS і
        загальні» (oh_details). Рядків лінії за
        період немає — лише лінія, період, стрілки й ряди ('has': False). Лише читання."""
        self._check_screen()
        lang = self._lang()
        today = calc.iso(self._today())
        span = calc.screen_span(kind, month, date_to, today)
        months = span['months']
        info = calc.span_info(span, lang)
        label = info['title_in']
        cur = self._cur_month(today)
        Line = self.env['workset.line']
        # line_id з params / localStorage — будь-що: не число — як «не знайдено» (перша лінія періоду), не помилка сервера
        lid = calc.parse_id(line_id)
        line = Line.browse(lid).exists() if lid else Line
        if not line:
            first = self._screen_rows(months, ['line'])
            order = self._line_order()
            first.sort(key=lambda r: (order.get(r['line'], 10 ** 6), r['line']))
            line = Line.browse(first[0]['line']) if first else Line
        out = {'month': self._span_month(info, lang), 'span': info, 'prev': calc.span_step(span, -1, today),
               'next': calc.span_step(span, 1, today), 'choices': calc.span_choices([], span, today, lang), 'line': False, 'has': False,
               'oh_details': {}}
        if not line:
            return out
        all_months = calc.month_list(calc.MIN_MONTH, cur)
        rows = [r for r in self._screen_rows(all_months, ['line']) if r['line'] == line.id]
        by_month = {r['month']: r for r in rows}
        out['choices'] = calc.span_choices(sorted(by_month), span, today, lang)
        out['line'] = {'id': line.id, 'name': line.sudo().display_name}
        # графік — лише місяці, де в лінії є години зі ставкою (€ год є), без порожніх місяців до старту лінії (круг 3а)
        data_months = [m for m in all_months if m in by_month and by_month[m]['hours_rate']]
        ohs = self._overheads(sorted(set(data_months) | set(months)))
        done = set(ohs)
        # круг 4: друга серія — «Залишається» € год (лінія поверх стовпців «чисто»); круг 5 — смуга обраного періоду
        out['series'] = {'months': [calc.month_info(m, lang) for m in data_months], 'labels': calc.month_axis(data_months, lang),
                         'net_label': calc.t('l_net', lang), 'share_label': calc.t('l_share', lang), 'remain_label': calc.t('l_remain', lang),
                         'net': [by_month[m]['net_per_hour'] for m in data_months],
                         # місяць без підсумку «ZUS і загальні» (не перераховано, круг 4б) — без точки, не 0
                         'remain': [by_month[m]['remain_per_hour'] if m in done else None for m in data_months],
                         'share': [calc.per_hour(by_month[m]['share'], by_month[m]['hours_rate']) for m in data_months],
                         'band': calc.span_band(data_months, months)}
        mine = [by_month[m] for m in months if m in by_month]
        if not mine:
            return out
        row = calc.rows_sum(mine)
        name = line.sudo().display_name
        # «Залишається» і кроки ZUS / загальні — лише коли всі місяці лінії періоду перераховано після круга 4 (круг 4б)
        full = all(r['month'] in ohs for r in mine)
        hr = self._housing_rules()
        # «Рядки» й записи кроків водоспаду — групи місяців (calc.card_groups): один місяць розкладається наново, як і раніше (той самий
        # розклад, що в перерахунку: _month_entries; мітка «рядки змінилися після перерахунку»); круг 5б — за кілька місяців склад, який
        # записав перерахунок у рядок лінії (card_groups), лише складається (рік — не 12 розкладів); місяць без складу (перераховано до
        # 19.0.5) — розклад наново. Табелі — лише за потреби
        stored = {}
        if len(mine) > 1:
            for x in self.search_read([('id', 'in', [r['id'] for r in mine])], ['month', 'card_groups']):
                if isinstance(x['card_groups'], dict) and 'groups' in x['card_groups']:
                    stored[calc.iso(x['month'])] = x['card_groups']['groups']
        parts = {r['month']: self._month_parts(r['month'], hr, with_ts=False) for r in mine if r['month'] not in stored}
        groups = calc.groups_merge([stored[m] if m in stored else
                                    (calc.card_groups(parts[m]['entries'], parts[m]['refs']).get(line.id) or {}).get('groups') or []
                                    for m in sorted(r['month'] for r in mine)])
        kinds = {line.id: calc.groups_kinds(groups)}
        line_open = self._line_open(line.id, name, months, label, lang)
        # круг 5: «ZUS, PIT-4» і «Загальні фірм» (кроки водоспаду, група «Рядків») — вікно «Що входить» з часткою лінії
        if any(m in ohs for m in months):
            recs = self._oh_records(months, ohs, self._screen_rows(months, ['office']), parts)
            ps = calc.rows_sum([r for r in mine if r['month'] in ohs])
            # круг 5б: і на годину зі ставкою (B: кроки водоспаду € год — окремо ZUS і загальні, тож «як на екрані» немає)
            part = {'hours': ps['hours_total'], 'zus': ps['zus_amount'], 'office': ps['office_amount'], 'hours_rate': ps['hours_rate'],
                    'label': calc.t('oh_part_line', lang)}
            out['oh_details'] = {'span': self._oh_detail(months, recs, ohs, part, label, lang, missing=not full)}
        dialog = bool(out['oh_details']) and self._dialog('span', lang)
        # круг 4: після «чисто» — − ZUS, PIT-4 → − Загальні фірм → = Залишається (calc.remain_steps; часток немає — кроків немає); круг 4б:
        # підсумку місяця немає (не перераховано після оновлення) — кроків теж немає, «Залишається» — «—»
        steps = calc.chain_steps(row) + (calc.remain_steps(row) if full else [])
        for s in steps:
            s['label'] = calc.t('s_' + s['key'], lang)
            # круг 3в: крок водоспаду — записи свого виду (як група «Рядків»); частка й «чисто» — рядки лінії; круг 4: «Залишається» —
            # рядки лінії; круг 5: ZUS і загальні — вікно «Що входить»
            if s['key'] in ('share', 'net', 'remain'):
                s['open'] = line_open
            elif s['key'] in ('zus', 'office'):
                s['open'] = dialog
            else:
                s['open'] = self._kind_open(line.id, name, s['key'], months, label, kinds, lang)
        day = min(calc.month_last(months[-1]), today)
        pays = self._who_pays(line, day, lang, hr)
        # години без ставки лінії — жовтим чипом у підсумках; клік — рядки лінії періоду з такими годинами (як чип «Огляду», лише ця лінія)
        no_rate = False
        if row['hours_no_rate']:
            no_rate = {'hours': calc.whole_hours(row['hours_no_rate']),
                       'open': self._open(calc.t('t_no_rate_line', lang) % (name, info['title']),
                                          calc.span_domain(months) + [('line_id', '=', line.id), ('hours_no_rate', '>', 0)])}
        fold = calc.pays_fold(pays, lang)
        # згорнутий рядок «Усе платимо ми» — правила «Хто платить» лінії (немає — порожній список: «правил немає → ми»)
        fold['open'] = self._open(calc.t('o_rules', lang) % name, [('line_id', '=', line.id)], COST_RULE)
        totals = dict(calc.totals(mine), **calc.remain_totals(mine))
        stamps = [r['write_date'] for r in mine if r.get('write_date')]
        out.update({'has': True, 'row_id': mine[-1]['id'], 'totals': totals if full else self._no_overhead(totals),
                    'oh_missing': not full, 'oh_hint': calc.t('oh_missing', lang), 'waterfall': calc.waterfall(steps), 'pays': pays, 'pays_fold': fold, 'no_rate': no_rate,
                    'aside': [{'key': f, 'label': calc.aside_label(f, lang), 'amount': row[f]} for f in calc.ASIDE if calc.cents(row[f])],
                    # за період — найдавніший перерахунок його місяців
                    'recomputed': self._stamp(min(stamps)) if stamps else ''})
        cust_lines = next(iter(parts.values()))['cust_lines'] if parts else self.sudo()._lines_index()[0]
        overhead = {'hours': sum(float(ohs[m].get('hours') or 0) for m in months if m in ohs), 'open': dialog} if full else None
        out.update(self._card_rows(line, months, row, lang, groups, cust_lines, info, overhead))
        # підсумки (круг 3в): «Години» — табелі лінії (той самий список, що рядок табелів), решта — рядки лінії за період; круг 4 — шостий,
        # «Залишається», теж рядки лінії
        ts = out['rows'][-1]['open']
        out['opens'] = {'hours': ts, 'share': line_open, 'costs': line_open, 'net': line_open, 'net_month': line_open,
                        'remain': line_open if full else False}
        return out

    def _stamp(self, dt):
        """Коли перераховано рядок (write_date рядка підсумку: перерахунок пише рядки місяця заново) → «06.11.2026 23:04» у часовому поясі
        користувача (немає — calc.TZ); немає — ''."""
        if not dt:
            return ''
        me = self.with_context(tz=self.env.user.tz or calc.TZ)
        return fields.Datetime.context_timestamp(me, fields.Datetime.to_datetime(dt)).strftime('%d.%m.%Y %H:%M')

    def _who_pays(self, line, day, lang, hr):
        """«Хто платить» лінії на day: житло — правило житла лінії «Ставок» (з правил hr, прочитаних раз на запит — _housing_rules),
        авто, паливо, інструмент, перевезення — правила «Хто платить» (правила лінії; правила замовників лінії з іншим платником —
        винятками); паливо — ще картки DKV лінії на day. Кожен рядок і виняток — з описом 'open' (круг 3в): правило, за яким визначено
        платника (форма правила житла «Ставок» чи правила «Хто платить»); правила немає — список правил лінії цього виду (порожній — теж
        відповідь: «правил немає → ми»). → [{'key', 'label', 'payer', 'payer_key', 'open', 'except': [{'name', 'payer', 'payer_key', 'open'}],
        'cards': [назва]}]."""
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

    def _card_rows(self, line, months, row, lang, groups, cust_lines, info, overhead=None):
        """«Рядки за період» картки лінії: групи записів, що лягли на лінію (groups — calc.groups_merge місяців періоду: розклад місяця або,
        круг 5б, склад card_groups, який записав перерахунок, — ті самі записи), за статтею й рахунком (аналітика), карткою (DKV) або
        «перевезення за прибуттями» — з сумою й (круг 5) числом записів: рік — групи, а не сотні рядків; ще оренда авто й житло, яке
        вирахував замовник (зі ставок і правил), і табелі лінії одним рядком (_ts_people). Рядок — {'title', 'sub', 'amount', 'chain' (у
        «чисто»), 'open' (опис списку або False — без кліку)}. Порядок: ланцюжок (найбільші витрати першими), «не в нашому чисто», круг 4 —
        група «ZUS і загальні (за годинами)» (рядки 'overhead': частки лінії ZUS, PIT-4 і загальних фірм; overhead — {'hours' (усі години
        місяців), 'open' (вікно «Що входить»)}, None — не всі місяці перераховано після круга 4: групи немає), табелі останніми; показано
        CARD_ROWS — {'rows', 'rows_total', 'stale' (частка + рядки ланцюжка ≠ збереженому «Чисто»: аналітику, DKV або прибуття змінили після
        перерахунку; склад місяця записано разом із рядком — для нього мітки немає)}."""
        first, last = months[0], calc.month_last(months[-1])
        can_aal = self.env['account.analytic.line'].has_access('read')
        can_txn = self.env['workset.dkv.txn'].has_access('read')
        Analytic = self.env['account.analytic.account'].sudo().with_context(active_test=False)
        Account = self.env['account.account'].sudo().with_context(active_test=False)
        Card = self.env['workset.dkv.card'].sudo().with_context(active_test=False)
        # круг 5б: назви статей, рахунків і карток — одним читанням на модель (за рік груп — десятки), далі — з кешу
        for model, pos, kind in ((Analytic, 1, 'aal'), (Account, 2, 'aal'), (Card, 1, 'card')):
            model.browse(sorted({g['k'][pos] for g in groups if g['k'][0] == kind and g['k'][pos]})).mapped('display_name')
        label = info['title']
        out = []
        for g in groups:
            k = tuple(g['k'])
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
            # круг 5: група за період — скільки в ній записів (у картки DKV вони вже в назві: «транзакцій: N»)
            if k[0] != 'card':
                sub = '%s · %s' % (sub, calc.records_text(len(set(g['aal'])) + len(set(g['txn'])), lang))
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
        # круг 4: частки лінії ZUS, PIT-4 і загальних фірм — окремою групою (не ланцюжок «чисто», не «не в нашому чисто»), завжди видно;
        # круг 5: клік — вікно «Що входить»
        oh = overhead or {}
        extra = [{'title': calc.t(key, lang), 'sub': calc.t('r_hours', lang) % (calc.num_text(calc.whole_hours(row['hours_total'])),
                                                                               calc.num_text(calc.whole_hours(oh.get('hours') or 0))),
                  'amount': row[field], 'chain': False, 'overhead': True, 'open': oh.get('open') or False}
                 for key, field in (('r_zus', 'zus_amount'), ('r_office', 'office_amount')) if overhead and calc.cents(row[field])]
        total = len(out) + len(extra) + 1
        shown = out[:calc.CARD_ROWS - 1] + extra
        # табелі лінії: замовники, які належать цій лінії (перша лінія замовника), години й склеєні люди — ті самі, що в «Годин»
        custs = sorted({c for c, l in calc.pairs_map(cust_lines).items() if l == line.id})
        hours, people = self._ts_people(first, last, custs)
        # табелі лінії — і рядок списку, і підсумок «Години» (круг 3в): «Табелі: <лінія>, <період>»
        title = calc.t('o_ts', lang) % (line.sudo().display_name, info['title_in'])
        shown.append({'title': calc.t('r_timesheets', lang) % (calc.num_text(hours), people),
                      'sub': '', 'amount': None, 'chain': False, 'timesheets': True,
                      'open': can_aal and self._open(title, self._ts_domain(first, last, custs), 'account.analytic.line')})
        return {'rows': shown, 'rows_total': total, 'stale': stale}

    # ——— C «Лінії × місяці» ———
    @api.model
    def profit_matrix(self, date_from=None, date_to=None):
        """Екран C — один виклик: місяці періоду (calc.screen_period: за замовчуванням — останні закриті з січня 2026, не більше
        MATRIX_MAX), лінії × місяці (чисто €/год і € за місяць, колір calc.cell_tone з порогом-параметром calc.P_DROP, ланцюжок місяця
        для спливаючого вікна), підсумок періоду, нижче — «Не розподілено», «Власник <стаття>», «ZUS, PIT-4», «Загальні фірм» (круг 5:
        клік — вікно «Що входить» місяця чи періоду, oh_details) і «Звірка» по місяцях. Лише читання."""
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
        # круг 5: вікно «Що входить» — для кожного місяця з підсумком і для всього періоду (записи читаються один раз)
        details = {}
        if any(m in ohs for m in months):
            recs = self._oh_records(months, ohs, [r for r in in_period if r['row_kind'] == 'office'])
            for m in months:
                if m in ohs:
                    details[m] = self._oh_detail([m], recs, ohs, None, calc.month_info(m, lang)['title_in'], lang)
            gaps = any(r['month'] not in ohs for r in in_period if r['row_kind'] == 'line')
            details['period'] = self._oh_detail(months, recs, ohs, None, span, lang, missing=gaps)

        def overhead_row(field):
            # круг 4: «ZUS, PIT-4» і «Загальні фірм» — суми місяця з підсумку «ZUS і загальні місяця» (уся компанія, не частки ліній);
            # круг 5: клітинка — вікно «Що входить» місяця, «N місяців» — періоду; сум немає в жодному місяці — рядка немає
            cells = [calc.eur(calc.cents((ohs.get(m) or {}).get(field))) for m in months]
            if not any(calc.cents(v) for v in cells):
                return False
            out = [{'month': m, 'has': bool(calc.cents(v)), 'amount': v, 'open': bool(calc.cents(v)) and m in details and self._dialog(m, lang)}
                   for m, v in zip(months, cells)]
            return {'cells': out, 'total': calc.eur(sum(calc.cents(v) for v in cells)), 'open': bool(details) and self._dialog('period', lang)}

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
            'zus': overhead_row('zus'),
            'office': overhead_row('office'),
            'checks': [check_cell(m) for m in months],
            'oh_details': details,
        }
