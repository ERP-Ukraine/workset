# -*- coding: utf-8 -*-
"""Перенос 19.0.2 → 19.0.3, після оновлення моделей: нові хранимі поля ставки «Фонд працівників» (worker_fund_share) і «Оренда авто»
(car_rent_share) з'явились порожніми, а «Частка WorkSET» і «Після бонусів офісу» тепер рахуються вже без них — діючі й майбутні ставки
перераховують ланцюжок (той самий, що щоночі: full=False — ціни й правило ставки не змінюються, лише посередник, фонд, оренда, бонус, частки,
рівні). Минулі ставки не чіпаються (історія). Свіжа установка сюди не заходить."""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.workset_rates.models.workset_rate import RATE

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    rates = env[RATE].sudo().search([('state', 'in', ['current', 'future'])])
    changed = rates._chain(full=False)
    _logger.info('workset_rates %s → 19.0.3: ланцюжок діючих і майбутніх ставок — %d, змінено %d (фонд працівників, оренда авто, частки)',
                 version, len(rates), changed)
