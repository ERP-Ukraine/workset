# -*- coding: utf-8 -*-
"""Екран «Власник» (Фінанси, круг 2; макет A + таблиця макета B під ним, слово власника 07.11 «макет: A + таблиця B нижче»): client action
workset_finance.owner (static/src/js/finance_screens.js) — ОДИН виклик сервера finance_owner(month).

Числа — збережений «місяць драбини» (workset.finance.month — його пише перерахунок), деталі — «Рядки» (workset.finance.item) того самого
перерахунку, поправки й звірка — свої моделі: нічого не рахується наново з книг і нічого не пишеться. Плитки, «з початку року», водоспад, ряд
року, таблицю й формат «тис. €» рахують чисті функції models/calc.py. Кожне число віддається з описом списку для кліку ('open': модель,
заголовок, домен, контекст без «Створити»; res_id — форма одного запису) — екран лише виконує його (як «Прибутковість», М3).

Хто бачить: лише група «Власник» (_check_screen першим рядком; адміністратор поза групою — відмова). Читання — правами користувача
(«Власник» читає місяці, рядки, поправки й звірку); sudo — лише назви планів статей власників і назва групи Energy. Відкрити записи — теж
правами самого користувача: модель без права читання — число без кліку; список — по фірмах периметра, до яких у користувача є доступ
(allowed_company_ids у контексті дії, круг 2б), а не лише по відмічених у перемикачі фірм — інакше список не зійдеться з числом.

Круг 2б: колонки таблиці, ряд графіка, «Разом» і «з початку року» плиток — один набір місяців (січень … обраний); неповний місяць —
позначка «неповний, по ДД.ММ» (calc.partial_day); «Разом» ступенів 1, 1а, 2 — записи ступеня за всі ці місяці (домени рядків деталі через
«АБО», calc.or_domains)."""
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import calc

MONTH = 'workset.finance.month'
ITEM = 'workset.finance.item'
ADJUST = 'workset.finance.adjust'
CHECK = 'workset.finance.check'
TZ = 'Europe/Kyiv'   # «Перераховано ДД.ММ ГГ:ХХ» — за Києвом
ITEM_READ = ['kind', 'name', 'note', 'amount', 'res_model', 'domain', 'analytic_id']
STEP_ITEMS = ('s1_docs', 's1a_owners_in_pl', 's2_ukraine')   # ступені, у яких є рядок деталі «Ступінь» (домен записів)
ADJ_STEP = {'s1g_no_analytic': 's1g', 's3_shares': 's3'}      # ступені, що складаються лише з поправок
PROFIT_MODEL = 'workset.profit.month'                        # «Прибутковість ліній» установлена — є посилання
PROFIT_GROUP = 'workset_profit.group_viewer'
PROFIT_TAG = 'workset_profit.overview'
CLICK_COS = 'workset_finance_click_cos'   # ключ контексту: фірми списку кліку (calc.click_companies), рахуються раз на виклик екрана


class WorksetFinanceScreens(models.Model):
    _inherit = MONTH

    # ——— службове ———
    def _open(self, title, model, domain=None, record=None, context=None):
        """Опис списку для кліку: модель, заголовок, домен, контекст без «Створити» (екран лише показує) і з allowed_company_ids — фірми
        периметра, доступні користувачу (CLICK_COS; інакше список бачив би лише фірми, відмічені в перемикачі); record — id одного запису
        (форма). Моделі немає або права читання в користувача немає — False (число без кліку)."""
        if not model or model not in self.env or not self.env[model].has_access('read'):
            return False
        ctx = {'create': False}
        cos = self.env.context.get(CLICK_COS)
        if cos:
            ctx['allowed_company_ids'] = list(cos)
        out = {'model': model, 'title': title, 'domain': domain or [], 'context': dict(ctx, **(context or {}))}
        if record:
            out['res_id'] = record
        return out

    def _click_companies(self):
        """Фірми списку кліку: периметр (як у перерахунку) ∩ фірми користувача, поточна — першою (calc.click_companies)."""
        return calc.click_companies(self._perimeter(), self.env.user.company_ids.ids, self.env.company.id)

    def _item_dom(self, item):
        """Домен рядка деталі (JSON, який записав перерахунок) → список; немає чи не JSON — None."""
        try:
            dom = json.loads(item.get('domain') or '[]') if item and item.get('res_model') else None
        except ValueError:
            return None
        return dom if isinstance(dom, list) else None

    def _item_open(self, item, title):
        """Рядок деталі → опис його записів (модель і домен, які записав перерахунок); домену немає чи він не JSON — False."""
        dom = self._item_dom(item)
        if dom is None:
            return False
        return self._open(title, item['res_model'], dom)

    def _energy_name(self):
        """Назва групи Energy для «Не оплачено …» — з Odoo під час роботи, не з коду (назва — у списку заборон публічного репозиторію):
        фірми, чий партнер у параметрі energy_partner_ids, а таких немає — самі партнери (комерційні); спільний початок назв —
        calc.common_name. Параметра немає — ''. sudo — лише назви."""
        ids = calc.param_value('ids', self.env['ir.config_parameter'].sudo().get_param(calc.P_PREFIX + 'energy_partner_ids') or '')
        if not ids:
            return ''
        names = self.env['res.company'].sudo().with_context(active_test=False).search([('partner_id', 'in', ids)], order='id').mapped('name')
        if not names:
            partners = self.env['res.partner'].sudo().with_context(active_test=False).browse(ids).exists()
            names = [p.commercial_partner_id.name or p.name for p in partners.sorted('id')]
        return calc.common_name(names)

    def _kyiv(self, dt, fmt):
        """Час (UTC з бази) → рядок за Києвом у форматі fmt; немає — ''."""
        if not dt:
            return ''
        return fields.Datetime.context_timestamp(self.with_context(tz=TZ), fields.Datetime.to_datetime(dt)).strftime(fmt)

    def _stamp(self, dt):
        """Коли перераховано → «07.11 06:30» за Києвом; немає — ''."""
        return self._kyiv(dt, '%d.%m %H:%M')

    def _step_open(self, key, months, rec_ids, steps, title):
        """Клік по числу ступеня key за місяці months (одна клітинка — один місяць, «Разом» — усі колонки: січень … обраний): 1, 1а, 2 —
        записи ступеня (домен рядка деталі «Ступінь»; за кілька місяців — домени місяців через «АБО», сума списку = «Разом», круг 2б; домену
        якогось місяця немає — самі рядки деталі, у кожного кнопка «Записи»); 1г і 3 — поправки; «до часток» і 4 — картка місяця (за кілька
        — список місяців); 4' — що віднімається (рядки «за грошима» й «не в за грошима» цих місяців, групами за видом, усередині — за лінією:
        одна лінія може дати два рядки деталі — рядки аналітики й рядки рахунку, круг 1е, — на екрані це одна група з одною сумою, не
        дробимо)."""
        one = len(months) == 1
        if key in STEP_ITEMS:
            got = [steps.get((m, key)) for m in months]
            if one:
                return self._item_open(got[0], title)
            doms = [self._item_dom(g) for g in got]
            same = all(d is not None for d in doms) and len({g['res_model'] for g in got}) == 1
            dom = calc.or_domains(doms) if same else None
            if dom:
                return self._open(title, got[0]['res_model'], dom)
            return self._open(title, ITEM, [('month', 'in', months), ('kind', '=', 'step'), ('note', '=', key)])
        if key in ADJ_STEP:
            return self._open(title, ADJUST, [('month', 'in', months), ('step', '=', ADJ_STEP[key])])
        if key == 's4c_cash':
            return self._open(title, ITEM, [('month', 'in', months), ('kind', 'in', ['cash', 'cash_other'])], context={'group_by': ['kind', 'line_id']})
        if one and rec_ids.get(months[0]):
            return self._open(title, MONTH, [('id', '=', rec_ids[months[0]])], record=rec_ids[months[0]])
        return self._open(title, MONTH, [('month', 'in', months)])

    def _owner_keys(self, items):
        """Стаття власника → [ключ, код]: стаття у своєму вузлі нижче вузла «власники» (параметр) — ключ і код — цей вузол (частка й витрати
        одного власника — один рядок); стаття прямо у вузлі «власники» — сама стаття, код — назва рядка деталі. sudo — лише назви планів."""
        root = calc.param_value('id', self.env['ir.config_parameter'].sudo().get_param(calc.P_PREFIX + 'owners_plan_id') or '')
        ids = sorted({it['analytic_id'][0] for it in items if it.get('analytic_id')})
        name = {it['analytic_id'][0]: it.get('name') or '—' for it in items if it.get('analytic_id')}
        out = {}
        for a in self.env['account.analytic.account'].sudo().with_context(active_test=False).browse(ids).exists():
            plan = a.plan_id
            out[a.id] = ['p%d' % plan.id, plan.name or '—'] if plan and root and plan.id != root else ['a%d' % a.id, name[a.id]]
        return out

    # ——— екран ———
    @api.model
    def finance_owner(self, month=None):
        """Екран «Власник» — один виклик: місяць (лише з calc.MIN_MONTH; раніше — відмова; немає — calc.default_month: останній закритий, а
        закритих немає — останній завершений календарний), вибір місяців (лише ті, по яких є запис), п'ять плиток місяця й «з початку року»,
        чипи (лише коли є що сказати), водоспад ступенів (нульові сховано, сходиться до s4_net), ряд року (стовпці 4 і лінія «до часток»),
        «Власники» (за кодами власників: частка й витрати компанії), «Перекази на особисті рахунки», «Окремо від прибутку», таблиця «Драбина»
        (пораховані місяці від січня до обраного + «Разом» = «з початку року» плиток); у кожного числа — опис його записів ('open'). Лише
        читання. → {'month' (і 'partial' — неповний місяць), 'year', 'choices', 'has', 'state', 'threshold', 'labels', 'tiles', 'chips',
        'waterfall', 'series', 'owners', 'transfers', 'aside', 'ladder'}."""
        self._check_screen('owner')
        lang = self._lang()
        heads = [(calc.iso(r['month']), r['state']) for r in self.search_read([('month', '>=', calc.MIN_MONTH)], ['month', 'state'], order='month')]
        if month:
            first = calc.month_first(calc.iso(month))
            if not calc.month_ok(first):
                raise ValidationError(calc.t('month_range', lang) % calc.iso(month))
        else:
            first = calc.default_month(heads, calc.iso(self._today()))
        # фірми списків кліку — раз на виклик (периметр ∩ фірми користувача), далі _open бере їх із контексту
        self = self.with_context(**{CLICK_COS: self._click_companies()})
        year = first[0:4]
        today = calc.iso(self._today())
        src_fields = [k + '_src' for k in calc.STEPS if k + '_src' in self._fields]
        # колонки, ряд графіка, «Разом» і «з початку року» — січень … обраний місяць (круг 2б: місяці після обраного не показуються)
        rows = self.search_read([('month', '>=', max(year + '-01-01', calc.MIN_MONTH)), ('month', '<=', first)],
                                list(calc.MONEY) + src_fields + ['month', 'state', 'recomputed_at'], order='month')
        partial = {}
        for r in rows:
            r['month'] = calc.iso(r['month'])
            # неповний місяць: перераховано (день за Києвом) не пізніше його останнього дня — «неповний, по ДД.ММ»
            partial[r['month']] = calc.partial_day(r['month'], today, self._kyiv(r['recomputed_at'], '%Y-%m-%d'))
        cur = next((r for r in rows if r['month'] == first), None)
        info = calc.month_info(first, lang, partial.get(first) or (calc.partial_day(first, today) if not cur else ''))
        months = [r['month'] for r in rows]
        rec_ids = {r['month']: r['id'] for r in rows}
        out = {'month': info, 'year': year, 'choices': calc.month_choices(heads, lang), 'has': bool(cur), 'state': cur['state'] if cur else '',
               'threshold': calc.TRUTH_PCT, 'labels': {'open': calc.t('h_open', lang)}, 'tiles': [], 'chips': [], 'waterfall': False,
               'series': False, 'owners': False, 'transfers': False, 'aside': False, 'ladder': False}
        if not rows:
            return out
        steps = {(calc.iso(i['month']), i['note']): i for i in self.env[ITEM].search_read(
            [('month', 'in', months), ('kind', '=', 'step')], ['month', 'note', 'res_model', 'domain'])}
        bases, all_bases = {}, {}
        for a in self.env[ADJUST].search_read([('month', 'in', months)], ['month', 'step', 'basis']):
            bases.setdefault(calc.iso(a['month']), {}).setdefault(a['step'], []).append(a['basis'] or '')
            all_bases.setdefault(a['step'], []).append(a['basis'] or '')
        of = calc.t('o_of', lang)
        # заголовок списку «Разом»: «січень – вересень 2026» (один місяць — він сам)
        span = info['title_in'] if len(months) == 1 else '%s – %s' % (calc.month_info(months[0], lang)['name'], calc.month_info(months[-1], lang)['title_in'])

        # таблиця «Драбина» і ряд року — ті самі колонки (січень … обраний)
        table = calc.ladder_table(rows, first, lang, partial)
        for r in table['rows']:
            label = '%s %s' % (r['num'], r['label'])
            for c in r['cells']:
                c['hint'] = calc.src_hint(r['key'], c['src'], bases.get(c['month']), lang)
                # нуль — «—» без кліку
                c['open'] = bool(calc.cents(c['value'])) and self._step_open(r['key'], [c['month']], rec_ids, steps,
                                                                             of % (label, calc.month_info(c['month'], lang)['title_in']))
            r['total']['hint'] = calc.src_hint(r['key'], r['total']['src'], all_bases, lang)
            r['total']['open'] = bool(calc.cents(r['total']['value'])) and self._step_open(r['key'], months, rec_ids, steps, of % (label, span))
        out['ladder'] = table
        out['series'] = calc.chart_series(table, lang)
        if not cur:
            return out
        title_in = info['title_in']

        # плитки місяця: число — свої записи; «з початку року» — Σ місяців року до обраного (той самий набір, що «Разом» таблиці)
        tile_open = {
            's4_net': self._step_open('s4_net', [first], rec_ids, steps, of % (calc.t('r_s4_net', lang), title_in)),
            's4c_cash': self._step_open('s4c_cash', [first], rec_ids, steps, of % (calc.t('o_cash', lang), title_in)),
            'recruiting': self._open(of % (calc.t('o_recruiting', lang), title_in), ITEM, [
                ('month', '=', first), '|', '&', ('kind', '=', 'step'), ('note', 'in', ['s1_docs', 's1a_owners_in_pl']),
                '&', ('kind', '=', 'adjust'), ('note', '=', 's1g')]),
            's2_ukraine': self._step_open('s2_ukraine', [first], rec_ids, steps, of % (calc.t('w_s2_ukraine', lang), title_in)),
            's3_shares': self._step_open('s3_shares', [first], rec_ids, steps, of % (calc.t('w_s3_shares', lang), title_in)),
        }
        out['tiles'] = [dict(x, open=bool(calc.cents(x['value'])) and tile_open[x['key']]) for x in calc.owner_tiles(rows, first)]

        # чипи — лише коли є що сказати: місяць не закрито, звірка, якість даних, поправки, коли перераховано
        chips = []
        if cur['state'] != 'closed':
            chips.append({'key': 'open'})
        check = self.env[CHECK].search_read([('month', '=', first)], ['ok', 'text'], limit=1)
        if check:
            chips.append({'key': 'check', 'ok': bool(check[0]['ok']), 'text': check[0]['text'] or '',
                          'open': self._open(of % (calc.t('o_check', lang), title_in), CHECK, [('month', '=', first)])})
        q = calc.quality_chip(cur, lang)
        if q:
            # клік — вікно «три кошики» з тієї самої відповіді (нового виклику немає): підписи — за кодом примітки рядка деталі мовою
            # користувача (calc.quality_baskets), не збережений текст мовою того, хто перераховував; кошик — свої записи. Поріг і «не
            # розібрано X € із Y €» — у підказці чипа й у вікні (чип — в один рядок, круг 2б)
            qi = self.env[ITEM].search_read([('month', '=', first), ('kind', '=', 'quality')], ITEM_READ + ['count'], order='sort, id')
            baskets = calc.quality_baskets(qi, lang)
            by_q = {it['id']: it for it in qi}
            for b in baskets:
                b['open'] = self._item_open(by_q[b['id']], of % (b['label'], title_in))
            chips.append(dict(q, key='quality', baskets=baskets, title=of % (calc.t('o_quality', lang), title_in),
                              open={'dialog': 'quality', 'hint': q['hint']} if baskets else False))
        n_adj = self.env[ADJUST].search_count([('month', '=', first)])
        if n_adj:
            chips.append({'key': 'adjust', 'count': n_adj, 'open': self._open(of % (calc.t('o_adjust', lang), title_in), ADJUST, [('month', '=', first)])})
        if cur['recomputed_at']:
            chips.append({'key': 'stamp', 'text': self._stamp(cur['recomputed_at'])})
        out['chips'] = chips

        # водоспад: кожен стовпець — записи свого ступеня (як клітинка таблиці)
        fall = calc.fall_bars(cur, lang)
        for b in fall['bars']:
            b['open'] = self._step_open(b['key'], [first], rec_ids, steps, of % (b['label'], title_in))
        out['waterfall'] = fall

        items = self.env[ITEM].search_read([('month', '=', first), ('kind', 'in', ['owner', 'owner_share', 'transfer', 'unpaid'])], ITEM_READ,
                                           order='sort, id')
        by_id = {it['id']: it for it in items}
        # «Власники»: рядок на код власника, усередині — частка окремо від витрат компанії; клік — рядки аналітики
        own = [it for it in items if it['kind'] in ('owner', 'owner_share')]
        groups = calc.owner_groups([dict(it, analytic=it['analytic_id'][0] if it['analytic_id'] else 0) for it in own], self._owner_keys(own))
        if groups:
            t_own = of % (calc.t('o_owners', lang), title_in)
            for g in groups:
                g['open'] = (self._item_open(by_id[g['ids'][0]], of % (g['code'], title_in)) if len(g['ids']) == 1
                             else self._open(of % (g['code'], title_in), ITEM, [('id', 'in', g['ids'])]))
            out['owners'] = {'total': cur['s5_owners'], 'share': cur['s5_owners_share'], 'rows': groups,
                             'open': self._open(t_own, ITEM, [('month', '=', first), ('kind', 'in', ['owner', 'owner_share'])])}
        # «Перекази на особисті рахунки»: одержувач × сума × «розібрано» / «без документа»; клік — рядки виписки
        tr = calc.transfer_rows([it for it in items if it['kind'] == 'transfer'])
        if tr['rows']:
            for r in tr['rows']:
                r['open'] = self._item_open(by_id[r['id']], of % (r['name'], title_in))
            t_tr = of % (calc.t('o_transfers', lang), title_in)
            tr['open'] = self._open(t_tr, ITEM, [('month', '=', first), ('kind', '=', 'transfer')])
            out['transfers'] = tr
        # «Окремо від прибутку»: II етап, не оплачено замовниками й Energy (жовта крапка; назва групи — з Odoo, _energy_name); посилання —
        # «Прибутковість ліній», якщо встановлена
        aside = []
        if calc.cents(cur['s6_stage2']):
            aside.append({'key': 'stage2', 'amount': cur['s6_stage2'],
                          'open': self._open(of % (calc.t('o_stage2', lang), title_in), ITEM, [('month', '=', first), ('kind', '=', 'stage2')])})
        for key, field, bucket in (('customers', 's7_unpaid_customers', 'customer'), ('energy', 's7_unpaid_energy', 'energy')):
            if not calc.cents(cur[field]):
                continue
            mine = [it for it in items if it['kind'] == 'unpaid' and it['note'] == bucket]
            ids = calc.ids_union([it['domain'] for it in mine]) if all(it['res_model'] == 'account.move' for it in mine) else None
            firm = (self._energy_name() or 'Energy') if key == 'energy' else ''
            title = of % (calc.t('o_unpaid_energy', lang) % firm if key == 'energy' else calc.t('o_unpaid_customer', lang), title_in)
            aside.append({'key': key, 'amount': cur[field], 'warn': key == 'energy', 'firm': firm,
                          'open': self._open(title, 'account.move', [('id', 'in', ids)]) if ids else self._open(title, ITEM, [('id', 'in', [it['id'] for it in mine])])})
        profit = PROFIT_MODEL in self.env and self.env.user.has_group(PROFIT_GROUP)
        if aside:
            out['aside'] = {'rows': aside, 'profit': {'tag': PROFIT_TAG, 'name': calc.t('o_profit', lang), 'month': first} if profit else False}
        return out
