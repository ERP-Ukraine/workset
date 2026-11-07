# -*- coding: utf-8 -*-
"""Чисті функції «Виїздів» без Odoo: код вакансії з підпису, країна з підпису громадянства, дата, телефони, рішення.
Файл не імпортує odoo — його перевіряє стенд tools/odoo_addon_calc.py (тест tools/tests/odoo_dispatch_module.test.js)."""
import re

from . import names

RE_VACANCY = re.compile(r'\bWS-[A-Z]{2}-\d{4}-\d{2}(?!\d)', re.I)

# Підпис громадянства (регістр, пробіли, емодзі не важливі; російська й українська) → код країни res.country
COUNTRIES = {
    'украина': 'UA', 'україна': 'UA', 'беларусь': 'BY', 'білорусь': 'BY', 'белоруссия': 'BY', 'білорусія': 'BY',
    'молдавия': 'MD', 'молдова': 'MD', 'грузия': 'GE', 'грузія': 'GE', 'казахстан': 'KZ', 'армения': 'AM', 'вірменія': 'AM',
    'узбекистан': 'UZ', 'таджикистан': 'TJ', 'киргизия': 'KG', 'кыргызстан': 'KG', 'киргизстан': 'KG', 'туркменистан': 'TM',
    'сербия': 'RS', 'сербія': 'RS', 'пакистан': 'PK', 'польша': 'PL', 'польща': 'PL', 'литва': 'LT', 'латвия': 'LV', 'латвія': 'LV',
    'эстония': 'EE', 'естонія': 'EE', 'румыния': 'RO', 'румунія': 'RO', 'словакия': 'SK', 'словаччина': 'SK', 'чехия': 'CZ',
    'чехія': 'CZ', 'венгрия': 'HU', 'угорщина': 'HU', 'германия': 'DE', 'німеччина': 'DE', 'турция': 'TR', 'туреччина': 'TR',
}

DECISIONS = ('created', 'exists', 'strong', 'weak', 'no_job', 'skipped')


def vacancy_code(label):
    """Підпис «Вакансія» → код WS-… (порожньо, якщо коду немає)."""
    m = RE_VACANCY.search(label or '')
    return m.group(0).upper() if m else ''


def country_code(label):
    """Підпис громадянства → код країни; «Громадянин ЄС», «Інші країни» і невідоме → порожньо."""
    t = re.sub(r'[^\w\s]', ' ', (label or '').lower().replace('ё', 'е'))
    t = re.sub(r'\s+', ' ', t).strip()
    return COUNTRIES.get(t, '')


def first_date(value):
    """'2026-11-09' (або з часом) → '2026-11-09'; інакше порожньо."""
    m = re.match(r'^(\d{4}-\d{2}-\d{2})', str(value or '').strip())
    return m.group(1) if m else ''


def phone_list(phone, phones=None):
    """Основний телефон і додаткові → список без порожніх і повторів, основний першим."""
    out = []
    for p in [phone] + list(phones or []):
        t = re.sub(r'\s+', ' ', str(p or '')).strip()
        if t and t not in out:
            out.append(t)
    return out


def shared_phones(rows):
    """rows — [(phone9, ім'я)] → список телефонів, записаних щонайменше у двох різних імен (спільні, збігом не вважаються)."""
    seen = {}
    for p9, name in rows:
        if p9:
            seen.setdefault(p9, set()).add(names.clean_name(name).lower())
    return sorted(p for p, ns in seen.items() if len(ns) >= 2)


def classify(person_id, primary9, extra9, name, employees, shared):
    """Повтор серед карток: employees — [{'id', 'name', 'person_id', 'phones': {phone9}}].
    → (сильні id, слабкі id).
    Сильний: той самий контакт Pipedrive, або ОСНОВНИЙ телефон контакту (не спільний) і ім'я хоч слабко збігається.
    Слабкий (перевірити дубль): лише ім'я; основний телефон, але ім'я інше; додатковий телефон контакту (хоч яке ім'я)."""
    forms = names.name_forms(name)
    strong, weak = [], []
    for e in employees:
        phones = e.get('phones', ())
        by_person = bool(person_id) and e.get('person_id') == person_id
        by_primary = bool(primary9) and primary9 in phones and primary9 not in shared
        by_extra = any(p in phones and p not in shared for p in extra9)
        by_name = names.names_match(forms, names.name_forms(e['name']))
        if by_person or (by_primary and by_name):
            strong.append(e['id'])
        elif by_primary or by_extra or by_name:
            weak.append(e['id'])
    return strong, weak
