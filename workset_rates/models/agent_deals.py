# -*- coding: utf-8 -*-
"""Адаптер моделей посередників і агентів людей — ЄДИНИЙ файл модуля, що знає їхні імена.

«Ставки» лише читають: посередник (агент по замовниках) — домовленості з замовниками; агент людей — учасники домовленостей «люди
агента»; Telegram контакту — partner_tg, для задачі «Переговорити».
Джерело — один шар (SOURCES → source): спершу модуль «Агенти» (workset_agents, моделі workset.agent.*, поля без x_), і лише якщо його
моделей у базі немає — ручні моделі DEAL / PEOPLE_DEAL / MEMBER (до їх зняття на копії). Обидва джерела описані картою «смисл → ім'я поля» (ключ 'f'),
решта коду працює лише зі смислами.
Моделі немає (модуль посередників не встановлено) або бракує полів — None: частка 0 і підпис «посередники не підключені»; кнопки
«Домовленість» у формі ставки не видно (deal_ids / deal_action), ім'я посередника у вікні правки — текстом, без посилання (deal_link).
Лінія домовленості рахується лише тоді, коли її поле вже веде на workset.line (до перейменування воно веде на стару модель).
Переїде модуль посередників на нові імена — змінюється лише цей файл."""
from . import calc

DEAL = 'x_agent_client_deal'
MEMBER = 'x_agent_people_member'
PEOPLE_DEAL = 'x_agent_people_deal'
TG = 'x_agent_tg_id'  # Telegram контакту — поле ручного модуля посередників на res.partner (є лише там, де модуль стоїть)
DEAL_ACTION = 'workset_hq.agents_act_cl_deals'  # дія «Домовленості (агенти по замовниках)» ручного модуля посередників

# Смисли полів, без яких джерело не читається (як і раніше: бракує — None)
NEED = ('rate', 'state', 'customer_ids', 'date_from', 'date_to')
NEED_MEMBER = ('deal_id', 'partner_id', 'date_from', 'date_to')
NEED_PEOPLE = ('rate', 'state', 'date_from', 'date_to')

# Джерела по черзі (calc.agent_source бере перше, чиї моделі є в базі). deal — домовленості з замовниками, people_deal / member — люди
# агентів; agent_model — модель агента з Telegram у полі tg (немає — Telegram у полі tg контакту res.partner); agent_contact — поле
# агента, що веде на контакт (workset.agent наслідує res.partner через partner_id; у ручних моделях агент — уже контакт); action —
# зовнішній id дії списку домовленостей (немає — шукаємо дію вікна за res_model). Стан «живих» — calc.LIVE_DEAL_STATES: у модуля
# «Агенти» 'zvirka' перейшло в поле mode (pay / zvirka — для частки годяться обидва), тож живі там — 'active'
SOURCES = (
    {'deal': 'workset.agent.deal.client', 'people_deal': 'workset.agent.deal.people', 'member': 'workset.agent.member',
     'agent_model': 'workset.agent', 'agent_contact': 'partner_id', 'tg': 'tg_id', 'action': None,
     'f': {'rate': 'rate', 'rate_full': 'rate_full', 'state': 'state', 'kind': 'kind', 'customer_ids': 'customer_ids', 'line_id': 'line_id',
           'date_from': 'date_from', 'date_to': 'date_to', 'agent_id': 'agent_id', 'deal_id': 'deal_id', 'partner_id': 'partner_id'}},
    {'deal': DEAL, 'people_deal': PEOPLE_DEAL, 'member': MEMBER,
     'agent_model': None, 'agent_contact': None, 'tg': TG, 'action': DEAL_ACTION,
     'f': {'rate': 'x_rate', 'rate_full': 'x_rate_full', 'state': 'x_state', 'kind': 'x_kind', 'customer_ids': 'x_customer_ids',
           'line_id': 'x_line_id', 'date_from': 'x_date_from', 'date_to': 'x_date_to', 'agent_id': 'x_agent_id', 'deal_id': 'x_deal_id',
           'partner_id': 'x_partner_id'}},
)
DEAL_KEYS = ('deal',)
PEOPLE_KEYS = ('member', 'people_deal')


def source(env, keys=DEAL_KEYS):
    """Джерело моделей посередників (рядок SOURCES): перше, чиї моделі keys є в базі; немає жодного — None."""
    present = sorted({s[k] for s in SOURCES for k in keys if s.get(k) and s[k] in env})
    i = calc.agent_source(SOURCES, present, list(keys))
    return None if i is None else SOURCES[i]


def deal_model(env):
    """Модель домовленостей з замовниками в цій базі; джерела немає — ім'я ручної моделі (для дії кнопки, коли домовленості вже знайдено)."""
    src = source(env)
    return src['deal'] if src else DEAL


def _to_line(Model, name):
    """Поле лінії домовленості є і веде на workset.line (до перейменування воно вело на стару модель — тоді лінію не враховуємо)."""
    return bool(name) and name in Model._fields and Model._fields[name].comodel_name == 'workset.line'


def _contact(src, agent):
    """Контакт res.partner агента домовленості: у модуля «Агенти» — поле agent_contact агента, у ручних моделей агент — уже контакт."""
    if not agent:
        return agent
    name = src.get('agent_contact')
    return agent[name] if name and name in agent._fields else agent


def read_deals(env):
    """→ [{'id', 'rate', 'rate_full', 'state', 'kind', 'customer_ids', 'line_id', 'date_from', 'date_to', 'agent', 'agent_id'}] діючих
    домовленостей або None. kind — вид домовленості: 'hour' (посередник, €/год), 'fixed', 'worker_fund' (фонд працівників —
    calc.worker_fund_for); поля виду немає — 'hour'. rate_full — сума фонду на повну ставку зайнятості; поля немає — None (тоді
    для всіх rate). agent / agent_id — ім'я й контакт res.partner посередника (вікно правки «Посередник», «Переговорити»); поля немає — '' і 0."""
    src = source(env)
    if not src:
        return None
    f = src['f']
    Deal = env[src['deal']].sudo()
    if calc.agent_missing(f, NEED, list(Deal._fields)):
        return None
    by_line = _to_line(Deal, f['line_id'])
    has_kind = f['kind'] in Deal._fields
    has_agent = f['agent_id'] in Deal._fields
    has_full = f['rate_full'] in Deal._fields
    out = []
    for d in Deal.search([(f['state'], 'in', list(calc.LIVE_DEAL_STATES))]):
        agent = d[f['agent_id']] if has_agent else None
        contact = _contact(src, agent)
        out.append({'id': d.id, 'rate': d[f['rate']], 'rate_full': d[f['rate_full']] if has_full else None, 'state': d[f['state']],
                    'kind': d[f['kind']] if has_kind else 'hour', 'customer_ids': d[f['customer_ids']].ids,
                    'line_id': d[f['line_id']].id if by_line else 0,
                    'date_from': calc.iso(d[f['date_from']]), 'date_to': calc.iso(d[f['date_to']]),
                    'agent': (agent.name or '') if agent else '', 'agent_id': contact.id if contact else 0})
    return out


def partner_tg(partner):
    """Telegram контакту для задачі «Переговорити»: у модуля «Агенти» — поле tg агента цього контакту, у ручних моделей — поле контакту;
    поля немає або воно порожнє — ''. Лише це поле, під sudo."""
    if not partner:
        return ''
    src = source(partner.env, DEAL_KEYS) or source(partner.env, PEOPLE_KEYS)
    if src and src.get('agent_model') and src['agent_model'] in partner.env:
        Agent = partner.env[src['agent_model']].sudo()
        if src['tg'] not in Agent._fields or src['agent_contact'] not in Agent._fields:
            return ''
        value = Agent.search([(src['agent_contact'], '=', partner.id)], limit=1)[src['tg']]
    else:
        if TG not in partner._fields:
            return ''
        value = partner.sudo()[TG]
    return str(value).strip() if value else ''


def _deal_xmlid(env, src):
    """Зовнішній id дії списку домовленостей джерела: свій id джерела (ручні моделі), якщо дія є й веде на модель домовленостей; інакше —
    перша дія вікна з res_model = модель домовленостей без прив'язки до меню «Дія» (ім'я дії модуля «Агенти» модулю «Ставки» невідоме);
    дії з зовнішнім id немає — None (ім'я тоді текстом)."""
    model = src['deal']
    if src.get('action'):
        act = env.ref(src['action'], raise_if_not_found=False)
        if act and act._name == 'ir.actions.act_window' and act.res_model == model:
            return src['action']
    for act in env['ir.actions.act_window'].sudo().search([('res_model', '=', model), ('binding_model_id', '=', False)], order='id'):
        xid = act.get_external_id().get(act.id)
        if xid:
            return xid
    return None


def deal_link(env, ids, title):
    """Ім'я посередника посиланням (вікно правки «Посередник», слово власника 06.11 «за кем прикреплено, непонятно»): дія модуля
    посередників на домовленості ids, з яких сума ставки (calc.agent_who → 'ids'); одна — картка, кілька — список. Від імені
    користувача: бачить лише свої. Моделі чи дії немає, прав немає або жодної не видно — None (ім'я тоді текстом)."""
    src = source(env)
    if not ids or not src or not env[src['deal']].has_access('read'):
        return None
    xid = _deal_xmlid(env, src)
    if not xid:
        return None
    seen = env[src['deal']].search([('id', 'in', list(ids))]).ids
    if not seen:
        return None
    out = env['ir.actions.actions']._for_xml_id(xid)
    out.update({'name': title, 'target': 'current'})
    if len(seen) == 1:
        forms = [v for v in out.get('views') or [] if v[1] == 'form']
        out.update({'res_id': seen[0], 'view_mode': 'form', 'views': forms[:1] or [[False, 'form']]})
    else:
        out['domain'] = [('id', 'in', seen)]
    return out


def deal_writable(env):
    """Користувач може правити домовленості посередників (майстер правки «Посередник» → «для всієї домовленості»): модель є і право запису є."""
    src = source(env)
    return bool(src) and env[src['deal']].has_access('write')


def deal_ids(env, cust_id, line_id):
    """Домовленості посередника для кнопки «Домовленість» форми ставки — за тим самим правилом, що частка (calc.agent_share_for), але
    будь-якого стану й дат: замовник ставки (комерційний) серед замовників домовленості; домовленість без замовників — коли її лінія — лінія
    ставки. Від імені користувача (його права й правила доступу). → [id] або None — моделі немає, бракує полів або користувач її не читає."""
    src = source(env)
    if not src or not cust_id:
        return None
    f = src['f']
    Deal = env[src['deal']]
    if f['customer_ids'] not in Deal._fields or not Deal.has_access('read'):
        return None
    dom = [(f['customer_ids'], 'in', [cust_id])]
    if line_id and _to_line(Deal, f['line_id']):
        dom = ['|'] + dom + ['&', (f['customer_ids'], '=', False), (f['line_id'], '=', line_id)]
    return Deal.search(dom).ids


def deal_action(env, cust_id, line_id, title):
    """Дія кнопки «Домовленість»: одна домовленість — її картка, кілька — список; немає (або моделі немає) — None."""
    ids = deal_ids(env, cust_id, line_id)
    if not ids:
        return None
    act = {'type': 'ir.actions.act_window', 'name': title, 'res_model': deal_model(env), 'target': 'current'}
    if len(ids) == 1:
        act.update({'res_id': ids[0], 'view_mode': 'form', 'views': [[False, 'form']]})
    else:
        act.update({'domain': [('id', 'in', ids)], 'view_mode': 'list,form', 'views': [[False, 'list'], [False, 'form']]})
    return act


def read_people(env):
    """→ [{'id', 'partner_id', 'rate', 'state', 'kind', 'date_from', 'date_to', 'deal_from', 'deal_to'}] учасників діючих домовленостей
    агентів людей або None (моделей немає)."""
    src = source(env, PEOPLE_KEYS)
    if not src:
        return None
    f = src['f']
    Member = env[src['member']].sudo()
    Deal = env[src['people_deal']].sudo()
    if calc.agent_missing(f, NEED_MEMBER, list(Member._fields)) or calc.agent_missing(f, NEED_PEOPLE, list(Deal._fields)):
        return None
    has_kind = f['kind'] in Deal._fields
    out = []
    for m in Member.search([(f['deal_id'] + '.' + f['state'], 'in', list(calc.LIVE_DEAL_STATES))]):
        d = m[f['deal_id']]
        out.append({'id': m.id, 'partner_id': m[f['partner_id']].id, 'rate': d[f['rate']], 'state': d[f['state']],
                    'kind': d[f['kind']] if has_kind else 'hour',
                    'date_from': calc.iso(m[f['date_from']]), 'date_to': calc.iso(m[f['date_to']]),
                    'deal_from': calc.iso(d[f['date_from']]), 'deal_to': calc.iso(d[f['date_to']])})
    return out
