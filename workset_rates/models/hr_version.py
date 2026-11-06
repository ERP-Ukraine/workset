# -*- coding: utf-8 -*-
"""Версія договору (hr.version, Odoo 19) — лише додавання: позначка «Тимчасово (не база)», «Повна ставка» (сума фонду працівників за
домовленістю — workset.rate._full_rate_on; зміна одразу перераховує ставки людини) і тригер А6 «договір → база».

А6 (розділ 5 спеціфікації М2): версію створили або змінили поля A6_TRIGGERS — ставки пар, де ця картка — картка договору, отримують нову
«На руки» (workset.rate._contract_to_base); ставка, створена з цієї версії, іде за нею (позначили «Тимчасово», архівували, перенесли дату —
ставку в архів). У саму версію модуль нічого не пише, крім своєї позначки (її ставить людина).
Вимикач — параметр workset_rates.a6_mode (calc.a6_mode): немає — «off», А6 нічого не робить; «dry» — збереження договору нічого не робить,
ніч лише рахує; «on» — діє. Помилка перевірки ставки під час А6 не губиться: договір не зберігається з поясненням (calc a6_error).
Погодинна ставка договору (hourly_wage, wage_type) — поля модуля зарплат (Enterprise): читаються м'яко (workset.rate._contract_of),
без нього А6 нічого не робить. Картку редагують у формі працівника: Odoo 19 пише поля версії через неї (hr.employee._inherits),
тож create / write тут бачать і правку у формі працівника, і нову версію з дати. Позначка — і у формі працівника (views/hr_version_views.xml),
і в кожній формі самої версії (_get_view: ідентифікаторів видів версії модуль не знає — рядок додається в перший sheet)."""
from lxml import etree

from odoo import api, fields, models
from odoo.exceptions import UserError

from . import calc

# поля версії, правка яких запускає А6 (поля модуля зарплат може не бути — тоді їх і немає серед змінених)
A6_TRIGGERS = frozenset(['hourly_wage', 'wage_type', 'date_version', 'active', 'workset_temporary'])
TEMP_FIELD = 'workset_temporary'
FULL_FIELD = 'workset_full_rate'


def add_temporary_row(arch):
    """Форма версії договору: рядок «Тимчасово (не база)» — у перший sheet перед вкладками (немає вкладок — у кінець sheet; немає sheet —
    у кінець форми). «Повна ставка» — одразу після погодинної ставки договору (hourly_wage, модуль зарплат), а її у формі немає — у той
    самий рядок. Поле вже є у формі — нічого. Пошук — лише у самій формі версії, не у вкладених формах полів x2many (інша модель:
    рядок там не відкрив би форму). → arch (той самий вузол)."""
    own = ".//field[@name='%s'][not(ancestor::field)]"
    if not arch.xpath(own % TEMP_FIELD):
        row = etree.Element('group', {'name': 'workset_rates_temporary'})
        etree.SubElement(row, 'field', {'name': TEMP_FIELD})
        sheets = arch.xpath('./sheet')
        parent = sheets[0] if sheets else arch
        book = parent.xpath('./notebook')
        if book:
            book[0].addprevious(row)
        else:
            parent.append(row)
    if not arch.xpath(own % FULL_FIELD):
        full = etree.Element('field', {'name': FULL_FIELD})
        # місце — після рядка групи, у якому стоїть ставка договору (поле може бути всередині div-рядка з підписом «за годину»)
        spot = None
        for node in arch.xpath(own % 'hourly_wage')[:1]:
            while node is not None and node.getparent() is not None and node.getparent().tag != 'group':
                node = node.getparent()
            if node is not None and node.getparent() is not None:
                spot = node
        if spot is not None:
            spot.addnext(full)
        else:
            rows = arch.xpath(".//group[@name='workset_rates_temporary'][not(ancestor::field)]")
            if rows:
                rows[0].append(full)
    return arch


class HrVersion(models.Model):
    _inherit = 'hr.version'

    # groups: поле версії через _inherits стає й полем hr.employee — без groups тест hr test_employee_fields_groups падає
    workset_temporary = fields.Boolean(string='Тимчасово (не база)', copy=False, tracking=True, groups='hr.group_hr_user',
                                       help='Позначте, якщо підвищення тимчасове: ставка не зміниться, доплату оформіть у «Ставки → Тимчасові зміни»')
    # повна ставка зайнятості (податки вищі): фонд працівників за домовленістю — своя сума «на повну ставку»; нова версія з дати її копіює
    workset_full_rate = fields.Boolean(string='Повна ставка', tracking=True, groups='hr.group_hr_user',
                                       help='Повна ставка зайнятості за договором. Порожньо — неповна. Від неї залежить сума фонду працівників у ставках '
                                            '(домовленість «фонд працівників» із сумою на повну ставку).')

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        # нова версія (і «нова з дати» копією поточної) — завжди: ставка договору в ній може бути скопійована, а не передана
        recs._workset_rates_a6()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if A6_TRIGGERS & set(vals):
            self._workset_rates_a6()
        if FULL_FIELD in vals:
            self._workset_rates_full_rate()
        return res

    def _workset_rates_full_rate(self):
        """«Повна ставка» змінилась — діючі й майбутні ставки людей цих версій перераховують ланцюжок одразу (фонд працівників і частка),
        не чекаючи ночі. «Без автоматики» (workset_rates_no_sync) — нічого."""
        Rate = self.env['workset.rate']
        if Rate._no_sync():
            return 0
        people = self.sudo().employee_id.work_contact_id.ids
        if not people:
            return 0
        return Rate.sudo().search([('person_id', 'in', people), ('state', 'in', ['current', 'future'])])._chain(full=False)

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form':
            arch = add_temporary_row(arch)
        return arch, view

    def _workset_rates_a6(self):
        Rate = self.env['workset.rate']
        if Rate._no_sync() or not Rate._has_contract() or Rate._a6_mode() != 'on':
            return {}
        try:
            return Rate._contract_to_base(self)
        except UserError as e:
            # перевірка ставки (дубль на дату тощо) відкочує збереження договору — людині пояснення, а не текст про ставки без контексту
            text = e.args[0] if e.args else str(e)
            raise UserError(calc.t('a6_error', calc.lang_of(self.env.lang)) % text) from e
