# -*- coding: utf-8 -*-
"""Адаптер моделей посередників і агентів людей (ручні моделі модуля посередників) — ЄДИНИЙ файл модуля, що знає їхні імена.

«Ставки» лише читають: посередник (агент по замовниках) — домовленості DEAL; агент людей — учасники MEMBER домовленостей PEOPLE_DEAL;
Telegram контакту (поле модуля посередників на res.partner) — partner_tg, для задачі «Переговорити».
Моделі немає (модуль посередників не встановлено) або бракує полів — None: частка 0 і підпис «посередники не підключені»; кнопки
«Домовленість» у формі ставки не видно (deal_ids / deal_action), ім'я посередника у вікні правки — текстом, без посилання (deal_link).
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
    """→ [{'id', 'rate', 'rate_full', 'state', 'kind', 'customer_ids', 'line_id', 'date_from', 'date_to', 'agent', 'agent_id'}] діючих
    домовленостей або None. kind — вид домовленості: 'hour' (посередник, €/год), 'fixed', 'worker_fund' (фонд працівників —
    calc.worker_fund_for); поля виду немає — 'hour'. rate_full — сума фонду на повну ставку зайнятості (x_rate_full); поля немає — None (тоді
    для всіх rate). agent / agent_id — ім'я й контакт посередника (вікно правки «Посередник», «Переговорити»); поля немає — '' і 0."""
    if DEAL not in env:
        return None
    Deal = env[DEAL].sudo()
    if any(f not in Deal._fields for f in NEED):
        return None
    by_line = 'x_line_id' in Deal._fields and Deal._fields['x_line_id'].comodel_name == 'workset.line'
    has_kind = 'x_kind' in Deal._fields
    has_agent = 'x_agent_id' in Deal._fields
    has_full = 'x_rate_full' in Deal._fields
    out = []
    for d in Deal.search([('x_state', 'in', list(calc.LIVE_DEAL_STATES))]):
        out.append({'id': d.id, 'rate': d.x_rate, 'rate_full': d.x_rate_full if has_full else None, 'state': d.x_state,
                    'kind': d.x_kind if has_kind else 'hour', 'customer_ids': d.x_customer_ids.ids, 'line_id': d.x_line_id.id if by_line else 0,
                    'date_from': calc.iso(d.x_date_from), 'date_to': calc.iso(d.x_date_to),
                    'agent': (d.x_agent_id.name or '') if has_agent else '', 'agent_id': d.x_agent_id.id if has_agent else 0})
    return out


TG = 'x_agent_tg_id'  # Telegram контакту — поле модуля посередників на res.partner (є лише там, де модуль стоїть)


def partner_tg(partner):
    """Telegram контакту для задачі «Переговорити»: поле модуля посередників; поля немає або воно порожнє — ''. Лише це поле, під sudo."""
    if not partner or TG not in partner._fields:
        return ''
    value = partner.sudo()[TG]
    return str(value).strip() if value else ''


DEAL_ACTION = 'workset_hq.agents_act_cl_deals'  # дія «Домовленості (агенти по замовниках)» модуля посередників (є лише там, де він стоїть)


def deal_link(env, ids, title):
    """Ім'я посередника посиланням (вікно правки «Посередник», слово власника 06.11 «за кем прикреплено, непонятно»): дія модуля
    посередників DEAL_ACTION на домовленості ids, з яких сума ставки (calc.agent_who → 'ids'); одна — картка, кілька — список. Від імені
    користувача: бачить лише свої. Моделі чи дії немає, прав немає або жодної не видно — None (ім'я тоді текстом)."""
    if not ids or DEAL not in env or not env[DEAL].has_access('read'):
        return None
    act = env.ref(DEAL_ACTION, raise_if_not_found=False)
    if not act or act._name != 'ir.actions.act_window' or act.res_model != DEAL:
        return None
    seen = env[DEAL].search([('id', 'in', list(ids))]).ids
    if not seen:
        return None
    out = env['ir.actions.actions']._for_xml_id(DEAL_ACTION)
    out.update({'name': title, 'target': 'current'})
    if len(seen) == 1:
        forms = [v for v in out.get('views') or [] if v[1] == 'form']
        out.update({'res_id': seen[0], 'view_mode': 'form', 'views': forms[:1] or [[False, 'form']]})
    else:
        out['domain'] = [('id', 'in', seen)]
    return out


def deal_writable(env):
    """Користувач може правити домовленості посередників (майстер правки «Посередник» → «для всієї домовленості»): модель є і право запису є."""
    return DEAL in env and env[DEAL].has_access('write')


def deal_ids(env, cust_id, line_id):
    """Домовленості посередника для кнопки «Домовленість» форми ставки — за тим самим правилом, що частка (calc.agent_share_for), але
    будь-якого стану й дат: замовник ставки (комерційний) серед замовників домовленості; домовленість без замовників — коли її лінія — лінія
    ставки. Від імені користувача (його права й правила доступу). → [id] або None — моделі немає, бракує полів або користувач її не читає."""
    if DEAL not in env or not cust_id:
        return None
    Deal = env[DEAL]
    if 'x_customer_ids' not in Deal._fields or not Deal.has_access('read'):
        return None
    dom = [('x_customer_ids', 'in', [cust_id])]
    if line_id and 'x_line_id' in Deal._fields and Deal._fields['x_line_id'].comodel_name == 'workset.line':
        dom = ['|'] + dom + ['&', ('x_customer_ids', '=', False), ('x_line_id', '=', line_id)]
    return Deal.search(dom).ids


def deal_action(env, cust_id, line_id, title):
    """Дія кнопки «Домовленість»: одна домовленість — її картка, кілька — список; немає (або моделі немає) — None."""
    ids = deal_ids(env, cust_id, line_id)
    if not ids:
        return None
    act = {'type': 'ir.actions.act_window', 'name': title, 'res_model': DEAL, 'target': 'current'}
    if len(ids) == 1:
        act.update({'res_id': ids[0], 'view_mode': 'form', 'views': [[False, 'form']]})
    else:
        act.update({'domain': [('id', 'in', ids)], 'view_mode': 'list,form', 'views': [[False, 'list'], [False, 'form']]})
    return act


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
