# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import AccessError

from . import calc

# що людина (не адміністратор) може змінити в записі звірки: позначку «Розібрано»; службові поля чату й задач пише сам Odoo
USER_FIELDS = frozenset(['done'])


class WorksetRateCheck(models.Model):
    """Звірка ставок — запис щопонеділка (А4) зі списком розбіжностей по розділах і однією задачею To Do. Створює лише код."""
    _name = 'workset.rate.check'
    _description = 'Звірка ставок'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Звірка', readonly=True)
    date = fields.Date(string='Дата', readonly=True, index=True)
    issue_count = fields.Integer(string='Розбіжностей', readonly=True)
    body = fields.Html(string='Що перевірити', readonly=True, sanitize=True)
    done = fields.Boolean(string='Розібрано')

    def write(self, vals):
        # звірку пише код (sudo); редактор — лише «Розібрано»
        if not (self.env.su or self.env.user.has_group('base.group_system')):
            extra = [f for f in vals if f not in USER_FIELDS and not f.startswith(('message_', 'activity_'))]
            if extra:
                raise AccessError(calc.t('check_only_done', calc.lang_of(self.env.lang)) % ', '.join(sorted(extra)))
        return super().write(vals)
