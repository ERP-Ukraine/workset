# -*- coding: utf-8 -*-
"""Імена людей без Odoo: кириличні двійники латиниці, транслітерація за паспортними схемами, збіг імен із запасом на написання.
Файл не імпортує odoo — його перевіряє стенд tools/odoo_addon_calc.py (тест tools/tests/odoo_dispatch_module.test.js)."""
import itertools
import re

# Кирилиця, схожа на латиницю (і навпаки): слово з обох абеток приводиться до тієї, якої в ньому більше (при рівності — кирилиця)
CYR2LAT = {
    'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X', 'У': 'Y', 'І': 'I',
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x', 'у': 'y', 'і': 'i',
}
LAT2CYR = {v: k for k, v in CYR2LAT.items() if k != 'У' and k != 'у'}
LAT2CYR.update({'Y': 'У', 'y': 'у'})
RE_LAT = re.compile(r'[A-Za-z]')
RE_CYR = re.compile(r'[Ѐ-ӿ]')
RE_WORD = re.compile(r'[A-Za-zЀ-ӿ]+')

SCHEMES = ('ua', 'by', 'ru')

# Спільна частина: КМУ 2010 (Україна)
_UA = {
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'H', 'Ґ': 'G', 'Д': 'D', 'Е': 'E', 'Є': 'IE', 'Ж': 'ZH', 'З': 'Z', 'И': 'Y', 'І': 'I', 'Ї': 'I',
    'Й': 'I', 'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F', 'Х': 'KH',
    'Ц': 'TS', 'Ч': 'CH', 'Ш': 'SH', 'Щ': 'SHCH', 'Ю': 'IU', 'Я': 'IA', 'Ь': '', 'Ъ': '', 'Ы': 'Y', 'Э': 'E', 'Ё': 'IO',
    "'": '', '’': '', 'ʼ': '', '`': '',
}
_UA_INITIAL = {'Є': 'YE', 'Ї': 'YI', 'Й': 'Y', 'Ю': 'YU', 'Я': 'YA'}
# Білоруська паспортна схема (SIARHEI, ALIAKSANDR, IVANOU)
_BY = dict(_UA, **{'Е': 'E', 'Ё': 'YO', 'И': 'I', 'Ў': 'U', 'Ы': 'Y', 'Э': 'E'})
_BY_INITIAL = {'Й': 'Y', 'Ё': 'YO'}
# Російська (ICAO-подібна): Г — G
_RU = dict(_UA, **{'Г': 'G', 'Е': 'E', 'Ё': 'E', 'И': 'I', 'Ы': 'Y', 'Э': 'E'})
_RU_INITIAL = {'Й': 'I'}
_TABLES = {'ua': (_UA, _UA_INITIAL), 'by': (_BY, _BY_INITIAL), 'ru': (_RU, _RU_INITIAL)}
_VOWELS = 'АЕЁИОУЫЭЮЯІЎ'


def fold_mixed(text):
    """Слова з двох абеток → до тієї, якої в слові більше (при рівності — кирилиця): латинське слово з однією кириличною літерою-двійником → чисто латинське."""
    def fix(m):
        w = m.group(0)
        lat = len(RE_LAT.findall(w))
        cyr = len(RE_CYR.findall(w))
        if not lat or not cyr:
            return w
        if lat > cyr:
            return ''.join(CYR2LAT.get(c, c) for c in w)
        return ''.join(LAT2CYR.get(c, c) for c in w)
    return RE_WORD.sub(fix, text or '')


def has_cyrillic(text):
    return bool(RE_CYR.search(text or ''))


def translit_word(word, scheme='ua'):
    """Слово кирилицею → латиниця великими за схемою ua / by / ru; латинські літери залишаються."""
    table, initial = _TABLES.get(scheme, _TABLES['ua'])
    w = (word or '').upper()
    if scheme != 'by':
        w = w.replace('ЗГ', 'ZGH') if scheme == 'ua' else w
    out = []
    for i, ch in enumerate(w):
        if ch == 'Z' and w[i:i + 3] == 'ZGH':
            out.append(ch)
            continue
        if i == 0 and ch in initial:
            out.append(initial[ch])
            continue
        if scheme == 'by' and ch == 'В' and i == len(w) - 1 and i > 0 and w[i - 1] in _VOWELS:
            out.append('U')
            continue
        out.append(table.get(ch, ch))
    return ''.join(out)


def translit(text, scheme='ua'):
    return ' '.join(translit_word(w, scheme) for w in (text or '').split())


def scheme_for_country(code):
    """Країна громадянства → схема: Україна — КМУ 2010, Білорусь — білоруська паспортна, решта — українська."""
    return 'by' if (code or '').upper() == 'BY' else 'ua'


def clean_name(text):
    """Пробіли схлопнуті, великі літери, двійники кирилиці в латиниці виправлені; метка « | XX» відрізана."""
    t = (text or '').split('|')[0]
    t = fold_mixed(t.replace(' ', ' '))
    return re.sub(r'\s+', ' ', t).strip().upper()


def latin_name(last, first, code=''):
    """(прізвище, ім'я, код країни) → («ПРІЗВИЩЕ ІМ'Я» латиницею великими, чи була транслітерація). Ім'я в Pipedrive зазвичай уже латиницею."""
    raw = '%s %s' % (last or '', first or '')
    fixed = clean_name(raw)
    if not has_cyrillic(fixed):
        return fixed, False
    return clean_name(translit(fixed, scheme_for_country(code))), True


def with_suffix(name, suffix):
    """«ПРІЗВИЩЕ ІМ'Я» + «PA» → «ПРІЗВИЩЕ ІМ'Я | PA» (один пробіл, великі)."""
    n = clean_name(name)
    s = re.sub(r'\s+', '', suffix or '').upper()
    return '%s | %s' % (n, s) if s else n


# ——— збіг імен ———
def canon(word):
    """Латинське слово → груба форма написань одного звучання (TAMDZIN≈TAMDIN, SIARHEI≈SERGEI≈SERHII, IVANOU≈IVANOV)."""
    s = re.sub(r'[^A-Z]', '', (word or '').upper())
    s = re.sub(r'[AO]U$', 'OV', s)
    s = re.sub(r'EU$', 'EV', s)
    s = s.replace('Y', 'I').replace('J', 'I').replace('W', 'V').replace('G', 'H')
    s = s.replace('KH', 'H').replace('ZH', 'Z')
    s = re.sub(r'I([AUE])', r'\1', s)
    s = re.sub(r'(.)\1+', r'\1', s)
    s = re.sub(r'^H(?=[AEIOU])', '', s)
    return s


def distance(a, b):
    """Відстань правки (Левенштейн)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def close(a, b):
    """Дві канонічні форми: спільна перша літера; до 3 літер — точно, 4 — відстань ≤ 1, довші — ≤ 2."""
    if not a or not b or a[0] != b[0]:
        return False
    longest = max(len(a), len(b))
    limit = 0 if longest <= 3 else 1 if longest == 4 else 2
    return distance(a, b) <= limit


# Рівнозначні імена в різних мовах і написаннях (укр. / рос. / біл. / латиниця): перша літера в них різна, тому лише відстанню правки їх не зловити.
# Таблиця для ІМЕН; для прізвищ лишається правило спільної першої літери
NAME_GROUPS = (
    ('OLEKSANDR', 'OLEXANDR', 'ALEKSANDR', 'ALIAKSANDR', 'ALEXANDER', 'ALEXANDR', 'OLEKSANDER'),
    ('OLEKSII', 'OLEXII', 'OLEKSIY', 'ALEKSEI', 'ALEKSEY', 'ALEXEY', 'ALIAKSEI'),
    ('MYKOLA', 'MYKOLAI', 'NIKOLAI', 'NIKOLAY', 'MIKALAI', 'MIKOLA', 'MYKOLAY'),
    ('MYKHAILO', 'MIKHAIL', 'MICHAIL', 'MIHAIL', 'MIKHAYLO', 'MICHAEL', 'MIHAIL'),
    ('VOLODYMYR', 'VLADIMIR', 'ULADZIMIR', 'VLADIMER', 'VOLODIMIR'),
    ('HENADII', 'GENNADII', 'HENNADII', 'GENNADY', 'HENADZ', 'GENADII', 'GENNADIY'),
    ('YURII', 'IURII', 'JURY', 'YURI', 'YURIY', 'IURI', 'YURIJ'),
    ('SERHII', 'SERGEI', 'SERGEY', 'SIARHEI', 'SERHIY', 'SERGII'),
    ('ANDRII', 'ANDREI', 'ANDREY', 'ANDRIY', 'ANDREJ'),
    ('DMYTRO', 'DMITRY', 'DMITRII', 'DZMITRY', 'DMITRIJ', 'DMYTRII'),
    ('IHOR', 'IGOR', 'IAHOR', 'IGAR'),
    ('OLEH', 'OLEG', 'ALEH', 'OLEHA'),
    ('PAVLO', 'PAVEL', 'PAUL', 'PAVLA', 'PIAVEL'),
    ('VASYL', 'VASILII', 'VASILY', 'VASIL', 'VASILIJ'),
    ('YEVHEN', 'EVGENII', 'EVGENY', 'YAUHEN', 'IEVHEN', 'EUGENE'),
    ('BOHDAN', 'BOGDAN', 'BAHDAN'),
    ('MAKSYM', 'MAKSIM', 'MAXIM', 'MAKSIMA'),
    ('DENYS', 'DENIS', 'DZIANIS'),
    ('ARTEM', 'ARTYOM', 'ARTIOM', 'ARTSEM'),
    ('KOSTIANTYN', 'KONSTANTIN', 'KANSTANTSIN', 'KOSTYANTYN'),
)


def _group_index():
    out = {}
    for i, group in enumerate(NAME_GROUPS):
        for spelling in group:
            out[canon(spelling)] = i
    return out


_GROUPS = None


def word_forms(word):
    """Слово → множина канонічних форм (кирилиця — за всіма трьома схемами) і міток таблиці імен «n:<номер групи>»."""
    global _GROUPS
    if _GROUPS is None:
        _GROUPS = _group_index()
    if has_cyrillic(word):
        forms = {canon(translit_word(word, s)) for s in SCHEMES} - {''}
    else:
        forms = {canon(word)} - {''}
    return forms | {'n:%d' % _GROUPS[f] for f in forms if f in _GROUPS}


def name_words(name):
    return [w for w in re.sub(r"[.'’ʼ`]", '', clean_name(name)).replace('-', ' ').split() if w]


def name_forms(name):
    return [word_forms(w) for w in name_words(name)]


def forms_match(a, b):
    tags = {x for x in a if x.startswith('n:')} & {x for x in b if x.startswith('n:')}
    if tags:
        return True
    return any(close(x, y) for x in a if not x.startswith('n:') for y in b if not y.startswith('n:'))


def names_match(a, b):
    """Слабий збіг імен: кожне слово коротшого імені (не менше двох слів) збігається з окремим словом довшого, порядок слів будь-який.
    Це кандидат на підтвердження людиною, а не доказ однієї людини."""
    fa, fb = (a, b) if isinstance(a, list) else (name_forms(a), name_forms(b))
    short, long_ = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
    if len(short) < 2:
        return False
    for perm in itertools.permutations(range(len(long_)), len(short)):
        if all(forms_match(short[i], long_[j]) for i, j in enumerate(perm)):
            return True
    return False


def phone9(phone):
    """Останні 9 цифр номера (менше 9 цифр — порожньо)."""
    d = re.sub(r'\D', '', str(phone or ''))
    return d[-9:] if len(d) >= 9 else ''
