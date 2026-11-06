# -*- coding: utf-8 -*-
"""Перенос 19.0.1 → 19.0.2, до оновлення моделей: частки М1 ліній («Знайшла лінія», «Знайшли ми», «Мінімум частки») у 19.0.2 стають
обчислюваними з «Загальної ставки» лінії — їхні значення зберігаються в тимчасову таблицю (post-migrate створить з них правила лінії
й видалить таблицю). Свіжа установка 19.0.2 сюди не заходить (Odoo запускає міграції лише при оновленні)."""
import logging

_logger = logging.getLogger(__name__)

TABLE = 'workset_rates_m1_line_shares'
COLUMNS = ('share_line_found', 'share_we_found', 'min_share')


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'workset_line' AND column_name IN %s", (COLUMNS,))
    have = {row[0] for row in cr.fetchall()}
    cr.execute('DROP TABLE IF EXISTS %s' % TABLE)
    cols = ', '.join('%s AS %s' % (c, c) if c in have else '0.0 AS %s' % c for c in COLUMNS)
    cr.execute('CREATE TABLE %s AS SELECT id AS line_id, %s FROM workset_line' % (TABLE, cols))
    cr.execute('SELECT count(*) FROM %s' % TABLE)
    _logger.info('workset_rates %s → 19.0.2: частки ліній М1 збережено (%d ліній; стовпці: %s)', version, cr.fetchone()[0], ', '.join(sorted(have)) or '—')
