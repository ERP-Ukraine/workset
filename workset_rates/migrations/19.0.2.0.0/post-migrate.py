# -*- coding: utf-8 -*-
"""Перенос 19.0.1 → 19.0.2, після оновлення моделей (розділ 9 спеціфікації М2): лише те, що вже є в базі, бізнес-чисел у коді немає.

1. Для кожної лінії з частками М1 (тимчасова таблиця pre-migrate) — «Загальна ставка» рівня лінії: якір «Надбавка», «Ставка з» — найраніша
   «Ставка з» її ставок, джерело «Перенесено з проєкту», «перенос з М1» (calc.migration_line_rules).
2. Ставки (workset.rate.rates_link_rules, migration=True): «На руки з» — «Перенос»; «Своя» — Ц ≠ правилу на «Ставка з», з причиною
   (ручна частка агента М1 — «було: частка агента N (до посередників)»; calc.migration_rate_vals); далі ланцюжок (правило, частки, бонус,
   рівні) — без автоматики А1: карта проєктів і замовлення не змінюються.
3. Параметр workset_rates.a6_mode не створюється: немає параметра — А6 вимкнено (off), вмикає інструмент штабу (--a6-mode)."""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.workset_rates.models import calc
from odoo.addons.workset_rates.models.workset_rate import RATE

_logger = logging.getLogger(__name__)

TABLE = 'workset_rates_m1_line_shares'


def saved_lines(cr):
    cr.execute("SELECT to_regclass(%s)", (TABLE,))
    if not cr.fetchone()[0]:
        return []
    cr.execute('SELECT line_id, share_line_found, share_we_found, min_share FROM %s' % TABLE)
    return [{'id': r[0], 'share_line_found': r[1] or 0.0, 'share_we_found': r[2] or 0.0, 'min_share': r[3] or 0.0} for r in cr.fetchall()]


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False})
    Rate = env[RATE].with_context(active_test=False)
    Rule = env['workset.rate.rule'].with_context(active_test=False)
    today = calc.iso(Rate._today())
    lines = saved_lines(cr)
    known = set(env['workset.line'].with_context(active_test=False).search([]).ids)
    lines = [x for x in lines if x['id'] in known]
    first = {}
    for r in Rate.search_read([('line_id', '!=', False)], ['line_id', 'date_from']):
        lid, day = r['line_id'][0], calc.iso(r['date_from'])
        if day and (lid not in first or day < first[lid]):
            first[lid] = day
    have = set(Rule.search([('customer_id', '=', False)]).line_id.ids)
    plan = calc.migration_line_rules(lines, first, have, today)
    if plan:
        Rule.create(plan)
    # ставки (у всіх після оновлення «Загальна ставка» порожня): «Своя» і її причина — до ланцюжка (обмеження «Своя без причини»),
    # «На руки з» — «Перенос», далі ланцюжок без зміни «Своєї»; карта проєктів не змінюється
    res = env[RATE].rates_link_rules(migration=True)
    cr.execute('DROP TABLE IF EXISTS %s' % TABLE)
    _logger.info('workset_rates %s → 19.0.2: правил лінії створено %d; ставок %d (до правила %d, своїх %d, ланцюжок змінено в %d); '
                 'a6_mode не задано (off)', version, len(plan), res['rates'], res['linked'], res['own'], res['chained'])
