# -*- coding: utf-8 -*-
from odoo import api, models

from .workset_rate import RATE, cust_of


class ProjectSaleLineEmployeeMap(models.Model):
    """А2 «Працівник у проєкті»: рядок карти створено або змінено працівника чи проєкт → чинна ставка пари «робочий контакт картки ×
    замовник проєкту» в «Вартість години» з прапорцем ручної правки. Ставки немає — нічого не пишемо (рядок потрапить у «Звірку»).
    Рядок перенесли до пари без ставки: якщо в ньому стояла чинна ставка колишньої пари (те саме число з прапорцем) — назад на hourly_cost.
    Рядок правили вручну в тому самому записі (cost, прапорець) або число інше — не чіпаємо. Зміну замовника в самому проєкті автоматика
    не бачить (тригер — на рядку карти): такі рядки видно у «Звірці» як «вартість з ручною правкою»."""
    _inherit = 'project.sale.line.employee.map'

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._workset_rates_sync({}, set())
        return recs

    def write(self, vals):
        if 'employee_id' not in vals and 'project_id' not in vals:
            return super().write(vals)
        old = {m.id: {'employee_id': m.employee_id.id, 'project_id': m.project_id.id} for m in self}
        res = super().write(vals)
        self._workset_rates_sync(old, set(vals))
        return res

    def _workset_rates_sync(self, old, fnames):
        Rate = self.env[RATE]
        today = Rate._today()
        done = []
        for m in self:
            line = m.sudo()
            person = line.employee_id.work_contact_id.id
            cust = cust_of(line.project_id.partner_id)
            o = old.get(m.id)
            if o and line.is_cost_changed and 'cost' not in fnames and 'is_cost_changed' not in fnames and not (person and cust and Rate._pair_rates(person, cust)):
                was_e = self.env['hr.employee'].sudo().with_context(active_test=False).browse(o['employee_id'])
                was_p = self.env['project.project'].sudo().with_context(active_test=False).browse(o['project_id'])
                was_person = was_e.work_contact_id.id
                was_cust = cust_of(was_p.partner_id)
                if was_person and was_cust:
                    Rate._revert_lines(line, [r.wage for r in Rate._stored_current(was_person, was_cust)])
            if not person or not cust or (person, cust) in done:
                continue
            done.append((person, cust))
            if Rate._pair_rates(person, cust):
                Rate._sync_pair(person, cust, today, 'map', True)
