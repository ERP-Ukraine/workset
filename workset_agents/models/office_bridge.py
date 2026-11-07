# -*- coding: utf-8 -*-
"""Адаптер полів штабу на mail.activity — ЄДИНИЙ файл модуля, що знає їх імена (у перевірці публічності — allow_x цього файлу).

Поля штабу (ключ, текст, адресат, кнопки, «гроші», «надіслано») ставить модуль «Приведи друга» штабу; ними сторож доставляє активності в бот
«Офіс». Немає полів на базі — активність створюється без них (видно в Odoo, у бот не піде) і повертається примітка для журналу ночі."""
F_KEY = 'x_hq_key'
F_TEXT = 'x_hq_text'
F_TO = 'x_office_to'
F_BUTTONS = 'x_office_buttons'
F_MONEY = 'x_office_money'
F_SENT = 'x_office_sent'


def ready(env):
    return F_KEY in env['mail.activity']._fields


def keys_like(env, prefixes):
    """Ключі активностей штабу з цими префіксами (і архівних) — ніч не ставить другу таку саму."""
    if not ready(env):
        return []
    Act = env['mail.activity'].sudo().with_context(active_test=False)
    out = []
    for pre in prefixes:
        for r in Act.search_read([(F_KEY, '=like', pre + '%')], [F_KEY]):
            if r.get(F_KEY):
                out.append(r[F_KEY])
    return out


def create(env, vals, hq, quick=True):
    """Активність зі стандартними полями vals і полями штабу hq {key, text, to, buttons, money} → (запис, примітка або '')."""
    Act = env['mail.activity'].sudo()
    if quick:
        Act = Act.with_context(mail_activity_quick_update=True)
    v = dict(vals)
    note = ''
    fields_ = Act._fields
    if F_KEY in fields_:
        v[F_KEY] = hq.get('key') or False
        if F_TEXT in fields_:
            v[F_TEXT] = hq.get('text') or False
        if F_TO in fields_:
            v[F_TO] = hq.get('to') or False
        if F_BUTTONS in fields_:
            v[F_BUTTONS] = hq.get('buttons') or False
        if F_MONEY in fields_:
            v[F_MONEY] = bool(hq.get('money'))
    else:
        note = 'полів штабу на активностях немає — активність %s створено без них (у бот «Офіс» не піде)' % (hq.get('key') or '—')
    return Act.create(v), note


def find(env, key):
    """Активності штабу з точним ключем (і архівні)."""
    if not ready(env) or not key:
        return env['mail.activity'].browse()
    return env['mail.activity'].sudo().with_context(active_test=False).search([(F_KEY, '=', key)])


def sent(acts):
    """Чи пішла хоч одна з активностей у бот «Офіс» (поля «надіслано» немає — вважаємо, що ні)."""
    if F_SENT not in acts._fields:
        return False
    return any(a[F_SENT] for a in acts)
