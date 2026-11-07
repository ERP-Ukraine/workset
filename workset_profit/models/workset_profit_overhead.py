# -*- coding: utf-8 -*-
"""«ZUS і загальні місяця» (М3, круг 4): підсумок, який пише перерахунок місяця поруч із рядками (_recompute_month) — ZUS і PIT-4 за
місяцем роботи (рахунки польських фірм, calc.contrib_month), загальні фірм (рядки «Офіс» без аналітики рахунків ZUS / PIT-4), години
табеля, на які їх розділено (версія 1 — calc.overhead), і що не розподілено, бо годин немає; круг 5 — склад для вікна «Що входить»
(breakdown). Екрани читають його, а не шукають рахунки наново; звірка місяця порівнює з ним рядки до цента. Один запис на місяць; пише
лише код (права моделі), бачить «Перегляд»."""
from odoo import fields, models

OVERHEAD = 'workset.profit.overhead'


class WorksetProfitOverhead(models.Model):
    _name = OVERHEAD
    _description = 'ZUS і загальні місяця'
    _order = 'month desc, id desc'
    _rec_name = 'month'

    month = fields.Date(string='Місяць', required=True, index=True, readonly=True, help='Перше число місяця')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    zus = fields.Monetary(string='ZUS і PIT-4', currency_field='currency_id', readonly=True,
                          help='Рахунки ZUS і PIT-4 польських фірм за місяцем роботи (мітка «ММ/РРРР»; немає — місяць проводки), рядки '
                               'рахунку без податку й кредиторки, EUR; витрата — мінус')
    pit4 = fields.Monetary(string='З них PIT-4', currency_field='currency_id', readonly=True)
    office = fields.Monetary(string='Загальні фірм', currency_field='currency_id', readonly=True,
                             help='Рядки «Офіс» місяця (розмітка «офіс» і картки DKV з метою «офіс») без аналітики рахунків ZUS / PIT-4')
    office_dup = fields.Monetary(string='Рахунки ZUS / PIT-4 в «Офісі»', currency_field='currency_id', readonly=True,
                                 help='Аналітика рахунків ZUS / PIT-4, яку розмітка поклала в «Офіс»: у загальні не входить — уже в ZUS')
    zus_elsewhere = fields.Monetary(string='Рахунки ZUS / PIT-4 поза «Офісом»', currency_field='currency_id', readonly=True,
                                    help='Аналітика рахунків ZUS / PIT-4 у рядках ліній, «Не розподілено» чи «Власник» — можливий '
                                         'подвійний рахунок: розмітити «ігнор»')
    total = fields.Monetary(string='Разом ZUS і загальні', currency_field='currency_id', readonly=True)
    hours = fields.Float(string='Години табеля', readonly=True, digits=(16, 2),
                         help='Усі години місяця, які рахує модуль: лінії й «без лінії»')
    per_hour = fields.Float(string='ZUS і загальні на годину', readonly=True, digits=(16, 4))
    zus_rest = fields.Monetary(string='ZUS без годин', currency_field='currency_id', readonly=True,
                               help='Не розподілено: годин місяця немає')
    office_rest = fields.Monetary(string='Загальні без годин', currency_field='currency_id', readonly=True,
                                  help='Не розподілено: годин місяця немає')
    move_ids = fields.Many2many('account.move', 'workset_profit_overhead_move_rel', 'overhead_id', 'move_id', string='Рахунки ZUS і PIT-4',
                                readonly=True)
    unlabeled_ids = fields.Many2many('account.move', 'workset_profit_overhead_unlabeled_rel', 'overhead_id', 'move_id',
                                     string='Без мітки місяця', readonly=True,
                                     help='Рахунки без єдиної мітки «ММ/РРРР» — місяць за датою проводки')
    # круг 5: склад для вікна «Що входить у ZUS і загальні» (calc.overhead_breakdown) — екрани не шукають рахунки й не розкладають місяць
    # наново; місяць, перерахований до 19.0.4 (поля немає), вікно складає саме — повільніше
    breakdown = fields.Json(string='Склад ZUS і загальних', readonly=True,
                            help='Службове для вікна «Що входить»: вид і сума кожного рахунку ZUS / PIT-4 (EUR) і аналітика цих рахунків '
                                 'у рядках «Офіс» за статтями. Пише перерахунок')

    # Odoo 19: обмеження бази — models.Constraint (не _sql_constraints)
    _month_uniq = models.Constraint('UNIQUE(month)', 'ZUS і загальні цього місяця вже є.')
