# -*- coding: utf-8 -*-
"""Адаптер домовленостей з посередниками (модель x_agent_client_deal модуля посередників) — ЄДИНИЙ файл модуля, що знає її імена.

Моделі немає (модуль посередників не встановлено) — None: частка посередника береться з лінії або лишається введеною (calc.agent_choice).
Лінія домовленості рахується лише тоді, коли її поле вже веде на workset.line (до перейменування воно веде на стару модель)."""
from . import calc

DEAL = 'x_agent_client_deal'
NEED = ('x_rate', 'x_state', 'x_customer_ids', 'x_date_from', 'x_date_to')


def read_deals(env):
    """→ [{'id', 'rate', 'state', 'kind', 'customer_ids', 'line_id', 'date_from', 'date_to'}] діючих домовленостей або None."""
    if DEAL not in env:
        return None
    Deal = env[DEAL].sudo()
    if any(f not in Deal._fields for f in NEED):
        return None
    by_line = 'x_line_id' in Deal._fields and Deal._fields['x_line_id'].comodel_name == 'workset.line'
    has_kind = 'x_kind' in Deal._fields
    out = []
    for d in Deal.search([('x_state', 'in', list(calc.LIVE_DEAL_STATES))]):
        out.append({'id': d.id, 'rate': d.x_rate, 'state': d.x_state, 'kind': d.x_kind if has_kind else 'hour',
                    'customer_ids': d.x_customer_ids.ids, 'line_id': d.x_line_id.id if by_line else 0,
                    'date_from': calc.iso(d.x_date_from), 'date_to': calc.iso(d.x_date_to)})
    return out
