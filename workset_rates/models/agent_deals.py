# -*- coding: utf-8 -*-
"""Адаптер моделей посередників і агентів людей (ручні моделі модуля посередників) — ЄДИНИЙ файл модуля, що знає їхні імена.

«Ставки» лише читають: посередник (агент по замовниках) — домовленості DEAL; агент людей — учасники MEMBER домовленостей PEOPLE_DEAL.
Моделі немає (модуль посередників не встановлено) або бракує полів — None: частка 0 і підпис «посередники не підключені».
Лінія домовленості рахується лише тоді, коли її поле вже веде на workset.line (до перейменування воно веде на стару модель).
Переїде модуль посередників у свій код — змінюється лише цей файл."""
from . import calc

DEAL = 'x_agent_client_deal'
NEED = ('x_rate', 'x_state', 'x_customer_ids', 'x_date_from', 'x_date_to')
MEMBER = 'x_agent_people_member'
PEOPLE_DEAL = 'x_agent_people_deal'
NEED_MEMBER = ('x_deal_id', 'x_partner_id', 'x_date_from', 'x_date_to')
NEED_PEOPLE = ('x_rate', 'x_state', 'x_date_from', 'x_date_to')


def read_deals(env):
    """→ [{'id', 'rate', 'state', 'kind', 'customer_ids', 'line_id', 'date_from', 'date_to', 'agent'}] діючих домовленостей або None."""
    if DEAL not in env:
        return None
    Deal = env[DEAL].sudo()
    if any(f not in Deal._fields for f in NEED):
        return None
    by_line = 'x_line_id' in Deal._fields and Deal._fields['x_line_id'].comodel_name == 'workset.line'
    has_kind = 'x_kind' in Deal._fields
    has_agent = 'x_agent_id' in Deal._fields
    out = []
    for d in Deal.search([('x_state', 'in', list(calc.LIVE_DEAL_STATES))]):
        out.append({'id': d.id, 'rate': d.x_rate, 'state': d.x_state, 'kind': d.x_kind if has_kind else 'hour',
                    'customer_ids': d.x_customer_ids.ids, 'line_id': d.x_line_id.id if by_line else 0,
                    'date_from': calc.iso(d.x_date_from), 'date_to': calc.iso(d.x_date_to),
                    'agent': (d.x_agent_id.name or '') if has_agent else ''})
    return out


def read_people(env):
    """→ [{'id', 'partner_id', 'rate', 'state', 'kind', 'date_from', 'date_to', 'deal_from', 'deal_to'}] учасників діючих домовленостей
    агентів людей або None (моделей немає)."""
    if MEMBER not in env or PEOPLE_DEAL not in env:
        return None
    Member = env[MEMBER].sudo()
    Deal = env[PEOPLE_DEAL].sudo()
    if any(f not in Member._fields for f in NEED_MEMBER) or any(f not in Deal._fields for f in NEED_PEOPLE):
        return None
    has_kind = 'x_kind' in Deal._fields
    out = []
    for m in Member.search([('x_deal_id.x_state', 'in', list(calc.LIVE_DEAL_STATES))]):
        d = m.x_deal_id
        out.append({'id': m.id, 'partner_id': m.x_partner_id.id, 'rate': d.x_rate, 'state': d.x_state, 'kind': d.x_kind if has_kind else 'hour',
                    'date_from': calc.iso(m.x_date_from), 'date_to': calc.iso(m.x_date_to),
                    'deal_from': calc.iso(d.x_date_from), 'deal_to': calc.iso(d.x_date_to)})
    return out
