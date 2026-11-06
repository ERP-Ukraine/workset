# -*- coding: utf-8 -*-
"""Рівень B (розділ 6б спеціфікації М2): сторінка «Огляд ставок» і «Стрічка».

- «Огляд» — client action workset_rates.overview (static/src/js/rates_overview.js) робить ОДИН виклик rates_overview(domain, months): плитки,
  картки ліній, ряди графіків, тривоги, списки для фільтра. Лише читання: права — check_access('read') на ставках, далі — звичайні правила
  доступу користувача. Плитки — ті самі числа, що й полоса підсумків (rates_summary) по діючих ставках фільтра, коли «тільки працюють»;
  вимкнено — середні по всіх діючих (calc.overview_summary). Історія — активні ставки, що діяли на 1-ше число кожного місяця («Ставка з» /
  «Ставка до»; архівні не входять), середні — як у полосі (calc.month_series).
- «Стрічка» — вид gantt: «Смуга до» (gantt_stop, нехранима, з пошуком), колір смуги (gantt_color), підпис «Діє · формула» (display_name
  в контексті workset_rates_gantt). Перенос смуги (запис із «Смуга до») — лише в часі й у тому самому рядку; не адміністратору — лише
  майбутні ставки й лише на дату після сьогодні. Правка «Ставка з» у формі ставки — без змін (як до рівня B).
Формули М2 тут не змінюються — лише читаються поля, які пише ланцюжок."""
from odoo import api, fields, models
from odoo.exceptions import UserError

from . import calc
from .workset_rate import INTERNAL, RAISE, TALK_TYPE, cust_of

CHECK = 'workset.rate.check'
GANTT_CTX = 'workset_rates_gantt'                    # контекст дії «Стрічка»: підпис смуги
GANTT_ROW_FIELDS = frozenset(['line_id', 'person_id', 'customer_id'])  # рядок «Стрічки»: смугу в інший рядок не переносять
GANTT_MOVE_FIELDS = GANTT_ROW_FIELDS | {'date_from'}                   # у контексті «Стрічки» — це перенос смуги
LABELS = ('count', 'wage', 'price', 'share', 'net', 'people', 'found_us', 'found_line', 'found_none', 'found_empty', 'agent_from',
          'fund', 'tip_check', 'tip_contract', 'tip_people', 'tip_price', 'tip_net', 'tip_below', 'tip_line', 'line_list', 'anchor_markup',
          'anchor_tariff', 'agent_wait', 'share_short', 'g_action', 'g_contract', 'g_data')  # calc.TX: ov_<ключ>
BUCKET_STEP = 2    # «Розподіл частки»: крок стовпця, €/год (круг вигляду 06.11: з кроком 1 половина стовпців порожні)
ALERT_ROWS = 5     # рядків у тривозі «Огляду»; решта — списком за кліком на заголовок
ALERT_IDS = 1000    # id для списку тривоги не більше


class WorksetRateOverview(models.Model):
    _inherit = 'workset.rate'

    gantt_stop = fields.Date(string='Смуга до', compute='_compute_gantt', search='_search_gantt_stop', inverse='_inverse_gantt_stop',
                             help='Кінець смуги у «Стрічці»: «Ставка до»; без неї діюча — сьогодні + 30 днів, майбутня — «Ставка з» + 30 днів.')
    gantt_color = fields.Integer(string='Колір смуги', compute='_compute_gantt')

    # ——— «Стрічка» ———
    @api.depends('state', 'date_from', 'date_to', 'share_level', 'net_level')
    @api.depends_context('rates_today')
    def _compute_gantt(self):
        today = calc.iso(self._today())
        for r in self:
            r.gantt_stop = calc.gantt_stop(r.state, calc.iso(r.date_from), calc.iso(r.date_to), today) or False
            r.gantt_color = calc.gantt_color(r.state, 'low' if 'low' in (r.share_level, r.net_level) else (r.share_level or ''))

    def _search_gantt_stop(self, operator, value):
        # gantt шукає «<» / «>»; = / != порожньо (фільтр «встановлено») — за «Ставка з»; інше — calc.gantt_search, без помилки
        return [tuple(x) if isinstance(x, list) else x for x in calc.gantt_search(operator, value, calc.iso(self._today()))]

    def _inverse_gantt_stop(self):
        # кінець ставки рахує код («Ставка до» — день перед наступною ставкою пари): розтягнута смуга нічого не змінює
        return

    @api.model
    def get_gantt_data(self, *args, **kwargs):
        # «Стрічка»: рядки ліній — як є, «без лінії» (група з порожнім line_id) — в кінець. Обгортка не змінює склад даних і мовчки
        # лишає відповідь web_gantt як є, якщо її форма інша (групувань немає, поле не line_id)
        res = super().get_gantt_data(*args, **kwargs)
        groupby = kwargs.get('groupby') or (args[1] if len(args) > 1 else None)
        if isinstance(res, dict) and 'groups' in res and isinstance(groupby, (list, tuple)) and groupby and groupby[0] == 'line_id':
            res['groups'] = calc.gantt_groups_last(res.get('groups'), 'line_id')
        return res

    @api.depends('name', 'state', 'formula')
    @api.depends_context(GANTT_CTX, 'lang')
    def _compute_display_name(self):
        if not self.env.context.get(GANTT_CTX):
            return super()._compute_display_name()
        labels = dict(self._fields['state']._description_selection(self.env))
        for r in self:
            r.display_name = calc.gantt_label(labels.get(r.state, ''), r.formula)

    def write(self, vals):
        # перенос смуги «Стрічки»: запис із «Смуга до» (форма ставки цього поля не пише) або, в контексті дії «Стрічка», зміна дати чи рядка
        # (растяжка за лівий край — лише «Ставка з», перенос униз без зсуву — лише працівник). Лише в часі й у тому самому рядку (незмінні
        # поля рядка не рахуються); не адміністратору — лише майбутні й лише на дату після сьогодні. Решта полів (наприклад, «На руки» з
        # картки, відкритої зі «Стрічки») — без перевірок. Правка «Ставка з» у «Усіх ставках» — як до рівня B; службові записи (INTERNAL) — теж.
        # INTERNAL — лише від суперкористувача: з RPC він знімається (_strip_internal), щоб редактор не обійшов перевірку переносу
        self = self._strip_internal()
        if not self.env.context.get(INTERNAL) and (
                'gantt_stop' in vals or (self.env.context.get(GANTT_CTX) and vals.keys() & GANTT_MOVE_FIELDS)):
            self._gantt_check(vals)
        return super().write(vals)

    def _gantt_check(self, vals):
        lang = self._lang()
        row = {k: v for k, v in vals.items() if k in GANTT_ROW_FIELDS}
        for r in self:
            old = {k: (r[k].id if r._fields[k].type == 'many2one' else r[k]) for k in row if k in r._fields}
            if calc.gantt_moved(old, row):
                raise UserError(calc.t('gantt_row', lang))
        if 'date_from' not in vals or self._is_admin():
            return
        new = calc.iso(vals['date_from'])
        moved = self.filtered(lambda r: calc.iso(r.date_from) != new)
        if any(r.state != 'future' for r in moved):
            raise UserError(calc.t('gantt_move', lang))
        today = calc.iso(self._today())
        if moved and new <= today:
            raise UserError(calc.t('gantt_past', lang) % calc.fmt_date(today))

    # ——— «Огляд ставок»: один виклик ———
    @api.model
    def rates_overview(self, domain=None, months=calc.OVERVIEW_DEFAULT, only_working=True):
        """domain — фільтр сторінки (лінії, замовник) на workset.rate; months — 6 / 12 / 24 (інше — 12); only_working — «тільки працюють».
        → {'today', 'months', 'tiles', 'lines', 'series', 'buckets', 'found', 'open', 'alerts', 'check', 'options', 'labels'}; без ставок — нулі
        й порожні списки. Кожне число, по якому клікають, має поруч опис свого списку (calc.ov_list): плитки й сектори — 'open', картки ліній і
        стовпці розподілу — 'open' у своєму записі; число картки лінії — 'count' (працюють або, коли «тільки працюють» вимкнено, усі діючі)."""
        self.check_access('read')
        lang = self._lang()
        months = calc.overview_months(months)
        today = calc.iso(self._today())
        base = list(domain or [])
        scope = base + [('state', '=', 'current')] + ([('work_group', '=', 'g1_work')] if only_working else [])
        rows = self.search_read(scope, ['line_id', 'customer_id', 'work', 'wage', 'price', 'our_share', 'net_share', 'share_level', 'net_level',
                                        'agent_client_share', 'agent_client_note', 'worker_fund_share', 'found_by', 'contract_ok'])
        if only_working:
            tiles = self.rates_summary(scope)  # ті самі числа, що полоса підсумків над «Працюють зараз» (і «Договір ≠ база» — з неї)
        else:
            # вимкнено «тільки працюють»: середні, «нижче мінімуму», «тимчасових» і «Договір ≠ база» — по всіх діючих ставках фільтра
            tiles = calc.overview_summary(self._summary_rows(rows), False)
            tiles['raises'] = self.search_count(scope + [('has_raise', '=', True)])
        tiles['no_line'] = len([r for r in rows if not r['line_id']])
        tiles['to_check'] = self.search_count(scope + [('to_check', '=', True)])
        tiles['all'] = self.search_count(base + [('state', '=', 'current')])  # «Ставок» — усі діючі фільтра, хоч би що з «тільки працюють»
        min_default = calc.parse_float(self._param(calc.P_MIN_SHARE))
        good_default = calc.parse_float(self._param(calc.P_GOOD_SHARE))
        have = bool(tiles['working'] if only_working else tiles['total'])
        tiles['share_tone'] = calc.share_tone(tiles['avg_share'], have, min_default, good_default)
        tiles['net_tone'] = calc.share_tone(tiles['avg_net'], have, min_default, good_default)
        # історія: ставки фільтра (будь-якого статусу), що діяли хоч на одне перше число періоду
        days = calc.month_starts(today, max(months, calc.SPARK_MONTHS))
        hist = self.search_read(base + [('date_from', '<=', days[-1]), '|', ('date_to', '=', False), ('date_to', '>=', days[0])],
                                ['date_from', 'date_to', 'wage', 'price', 'our_share', 'net_share', 'line_id'])

        def h(r):
            return {'date_from': calc.iso(r['date_from']), 'date_to': calc.iso(r['date_to']), 'wage': r['wage'], 'price': r['price'],
                    'share': r['our_share'], 'net': r['net_share']}

        shown = days[-months:]
        series = {'days': shown, 'total': calc.month_series([h(r) for r in hist], shown), 'by_line': []}
        lines = self._overview_lines(rows, hist, days, months, h, only_working, base, lang)
        series['by_line'] = [{'id': c['id'], 'name': c['name'], 'color': c['color'], 'share': c.pop('_share')} for c in lines]
        working = bool(only_working)
        buckets = calc.share_buckets([r['our_share'] for r in rows], step=BUCKET_STEP)
        for b in buckets:
            b['open'] = calc.ov_list(calc.t('ov_t_bucket', lang) % b['label'], base, calc.bucket_domain(b), working=working)
        found_open = {k: calc.ov_list(calc.t('ov_t_found', lang) % calc.t('ov_found_' + k, lang), base, calc.found_domain(k), working=working)
                      for k in ('us', 'line', 'none')}
        alerts = self._overview_alerts(base, scope, today, lang)
        # розділ звірки «Проєкти без ставки» — та сама біда, що жива тривога «Працюють без ставки»: коли вона є, розділ не дублюється
        live = {a['key'] for a in alerts if a['count']}
        return {
            'today': today, 'months': months, 'tiles': tiles, 'lines': lines, 'series': series, 'buckets': buckets,
            'found': calc.found_split([r['found_by'] for r in rows]),
            'open': {'tiles': calc.ov_tiles_open(base, only_working, lang), 'found': found_open},
            'alerts': alerts,
            'check': self._overview_check(lang, skip=('nomap',) if 'no_rate' in live else ()),
            'options': self._overview_options(),
            'labels': {k: calc.t('ov_' + k, lang) for k in LABELS},
        }

    @staticmethod
    def _summary_rows(rows):
        # рядки search_read (лише діючі ставки) → вхід calc.summary / overview_summary; 'contract' — як у rates_summary
        return [{'work': r['work'], 'wage': r['wage'], 'price': r['price'], 'share': r['our_share'], 'net': r['net_share'],
                 'low': 'low' in (r['share_level'], r['net_level']), 'contract': not r['contract_ok']} for r in rows]

    def _overview_lines(self, rows, hist, days, months, h, only_working=True, base=(), lang='uk'):
        # картки ліній: діючі ставки фільтра по лінії (як плитки), посередник, міні-графік частки за SPARK_MONTHS місяців; число картки
        # ('count') і її список ('open') — ті самі ставки: «тільки працюють» — ті, хто працює, вимкнено — усі діючі лінії
        by_line = {}
        for r in rows:
            if r['line_id']:
                by_line.setdefault(r['line_id'][0], []).append(r)
        hist_by = {}
        for r in hist:
            if r['line_id']:
                hist_by.setdefault(r['line_id'][0], []).append(h(r))
        out = []
        for line in self.env['workset.line'].browse(list(by_line)):
            mine = by_line[line.id]
            s = calc.overview_summary(self._summary_rows(mine), only_working)
            share = [p['avg_share'] for p in calc.month_series(hist_by.get(line.id, []), days)]
            spark = share[-calc.SPARK_MONTHS:]
            out.append({'id': line.id, 'name': line.name or '', 'anchor': line.anchor or '', 'working': s['working'], 'total': s['total'],
                        'count': s['working'] if only_working else s['total'],
                        'open': calc.ov_list(calc.t('ov_t_line', lang) % (line.name or ''), base, working=bool(only_working), line_id=line.id),
                        'avg_wage': s['avg_wage'], 'avg_price': s['avg_price'], 'avg_share': s['avg_share'], 'avg_net': s['avg_net'],
                        'below_min': s['below_min'],
                        'agent': calc.agent_status(any((r['agent_client_share'] or 0) > 0 for r in mine), line.agent_expected),
                        'agents': calc.agent_items([(r['agent_client_share'], r['agent_client_note'], r['customer_id'][1] if r['customer_id'] else '')
                                                    for r in mine]),
                        'fund': calc.fund_badge([r['worker_fund_share'] for r in mine]),
                        'spark': calc.spark_path(spark), 'spark_values': spark, '_share': share[-months:]})
        # один ряд від більшого числа до меншого; 'color' — місце в ряду (смуга картки й ряд «По лініях» — один колір)
        return calc.line_cards_order(out)

    def _rate_rows(self, rates, sub):
        return [{'id': r.id, 'title': r.person_id.display_name or '', 'sub': sub(r)} for r in rates[:ALERT_ROWS]]

    def _overview_alerts(self, base, scope, today, lang):
        """Тривоги «Огляду»: [{'key', 'model', 'count', 'ids', 'rows': [{'id', 'title', 'sub'}]}] — порядок як на сторінці."""
        def cust(r):
            return r.customer_id.display_name or ''

        out = []
        # тимчасові зміни пар фільтра, що закінчуються ≤ RAISE_SOON_DAYS днів (фільтра немає — усі)
        soon = calc.add_days(today, calc.RAISE_SOON_DAYS)
        raises = self.env[RAISE].search([('state', 'in', ['future', 'current']), ('date_to', '>=', today), ('date_to', '<=', soon)],
                                        order='date_to, id')
        if base:
            pairs = {(r.person_id.id, cust_of(r.customer_id)) for r in self.search(base)}
            raises = raises.filtered(lambda x: (x.person_id.id, cust_of(x.customer_id)) in pairs)
        out.append({'key': 'raise_soon', 'model': RAISE, 'count': len(raises), 'ids': raises.ids[:ALERT_IDS],
                    'rows': [{'id': x.id, 'title': x.person_id.display_name or '',
                              'sub': '%s · %s' % (cust(x), calc.t('raise_badge', lang) % (('+' if x.amount > 0 else '') + calc.num_group(x.amount),
                                                                                          calc.short_date(x.date_to)))}
                             for x in raises[:ALERT_ROWS]]})
        future = self.search(base + [('state', '=', 'future'), ('date_from', '<=', calc.add_days(today, calc.FUTURE_SOON_DAYS))], order='date_from, id')
        out.append({'key': 'future', 'model': self._name, 'count': len(future), 'ids': future.ids[:ALERT_IDS],
                    'rows': self._rate_rows(future, lambda r: '%s · %s' % (cust(r), calc.fmt_date(r.date_from)))})
        no_line = self.search(scope + [('line_id', '=', False)], order='customer_id, person_id, id')
        out.append({'key': 'no_line', 'model': self._name, 'count': len(no_line), 'ids': no_line.ids[:ALERT_IDS],
                    'rows': self._rate_rows(no_line, cust)})
        check = self.search(scope + [('to_check', '=', True)], order='customer_id, person_id, id')
        out.append({'key': 'to_check', 'model': self._name, 'count': len(check), 'ids': check.ids[:ALERT_IDS],
                    'rows': self._rate_rows(check, lambda r: ' · '.join([p for p in [cust(r), r.check_reason or ''] if p]))})
        # «Працюють без ставки»: табелі за 60 днів, а діючої ставки пари немає (ті самі пари, що в звірці «Проєкти без ставки»). Рядок — нова
        # ставка цієї пари (форма з працівником і замовником), заголовок — останні табелі пар
        custs = self._base_customers(base)
        pairs = [p for p in self._norate_pairs(today) if custs is None or p['cust'] in custs]
        Partner = self.env['res.partner'].sudo()
        Emp = self.env['hr.employee'].sudo().with_context(active_test=False)
        aal_ids = [p['aal'] for p in pairs][:ALERT_IDS]
        # без права читати ці табелі (немає прав на табелі) — заголовок не посилання; рядок — нова ставка пари (лише тим, хто створює ставки),
        # інакше — табель, а не можна й його — рядок без кліку (клік не дає помилки доступу)
        can_aal = self.env['account.analytic.line'].browse(aal_ids).has_access('read')
        can_new = self.has_access('create')
        rows = []
        for p in pairs[:ALERT_ROWS]:
            who = Partner.browse(p['person']).name if p['person'] else Emp.browse(p['cards'][0][0]).name
            new = {'default_person_id': p['person'], 'default_customer_id': p['cust']} if (p['person'] and can_new) else False
            rows.append({'id': p['aal'], 'title': who or '',
                         # години — з роздільником тисяч (calc.num_group), як усі числа «Огляду»
                         'sub': calc.t('ov_norate_sub', lang) % (Partner.browse(p['cust']).name or '', calc.num_group(p['hours']), calc.RECENT_DAYS),
                         'new': new, 'click': bool(new or can_aal)})
        out.append({'key': 'no_rate', 'model': 'account.analytic.line', 'count': len(pairs), 'ids': aal_ids, 'link': can_aal, 'rows': rows})
        # «Переговорити»: відкриті задачі типу «Переговорити» (кнопка рядка списку «Працюють зараз») на ставках фільтра; інші задачі на ставці
        # (поставлені з чаттера іншого типу) — ні. Рядок — ставка
        talk = self.env.ref(TALK_TYPE, raise_if_not_found=False)
        acts = self.env['mail.activity'].search([('res_model', '=', self._name), ('activity_type_id', '=', talk.id if talk else 0)]
                                                + ([('res_id', 'in', self.search(base).ids)] if base else []), order='date_deadline, id')
        rates = self.browse(list(dict.fromkeys(acts.mapped('res_id')))).exists()
        out.append({'key': 'talk', 'model': self._name, 'count': len(acts), 'ids': rates.ids[:ALERT_IDS],
                    'rows': [{'id': a.res_id, 'title': self.browse(a.res_id).person_id.display_name or '',
                              'sub': ' · '.join([x for x in [a.user_id.name or '', calc.fmt_date(a.date_deadline), a.summary or ''] if x])}
                             for a in acts[:ALERT_ROWS]]})
        return out

    def _base_customers(self, base):
        """Фільтр сторінки «Огляду» (лінії, замовник — так його складає JS) → комерційні замовники, яких він стосується; фільтра немає — None.
        Для тривоги «Працюють без ставки»: пари без ставки в ставки фільтра не потрапляють, тож замовники лінії — з самої лінії."""
        if not base:
            return None
        out = None
        for cond in base:
            if not isinstance(cond, (list, tuple)) or len(cond) != 3:
                continue
            field, op, value = cond
            ids = value if isinstance(value, (list, tuple)) else [value]
            if field == 'line_id' and op in ('in', '='):
                found = {cust_of(c) for c in self.env['workset.line'].browse(ids).customer_ids}
            elif field == 'customer_id' and op in ('in', '='):
                found = {cust_of(c) for c in self.env['res.partner'].browse(ids)}
            else:
                continue
            out = found if out is None else out & found
        return out if out is not None else None

    def _overview_check(self, lang='uk', skip=()):
        # остання «Звірка ставок»: розділи й число пунктів у кожному (з її запису; розділи пише А4). groups — «Тривоги» трьома групами
        # мовою користувача (calc.check_groups: запис звірки може бути іншою мовою); head — «Звірка від 06.11 · 444 пункти»
        last = self.env[CHECK].search([], order='date desc, id desc', limit=1)
        if not last:
            return False
        sections = calc.check_sections(last.body)
        head = calc.t('ov_check_head', lang) % (calc.short_date(last.date), calc.points_text(last.issue_count, lang))
        if last.done:
            head += ' · ' + calc.t('ov_check_done', lang)
        return {'id': last.id, 'name': last.name or '', 'date': calc.iso(last.date), 'done': bool(last.done), 'issues': last.issue_count,
                'head': head, 'sections': sections, 'groups': calc.check_groups(sections, lang, skip)}

    def _overview_options(self):
        # фільтр сторінки: усі активні лінії; замовники — ті, в кого є діючі ставки
        lines = [{'id': x.id, 'name': x.name or ''} for x in self.env['workset.line'].search([], order='sequence, name')]
        groups = self._read_group([('state', '=', 'current')], ['customer_id'], ['__count'])
        customers = sorted([{'id': p.id, 'name': p.display_name or ''} for p, _n in groups if p], key=lambda c: c['name'])
        return {'lines': lines, 'customers': customers}
