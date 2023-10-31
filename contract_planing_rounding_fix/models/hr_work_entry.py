from odoo import models


class HrWorkEntry(models.Model):
    _inherit = 'hr.work.entry'


    def _get_planning_duration(self, date_start, date_stop):
        self.ensure_one()
        date_start = date_start or self.date_start
        date_stop = date_stop or self.date_stop

        if (self.planning_slot_id.start_datetime == date_start and
                self.planning_slot_id.end_datetime == date_stop):
            return self.planning_slot_id.allocated_hours
        else:
            new_slot = self.env['planning.slot'].new({
                **self.planning_slot_id.read(['employee_id', 'company_id', 'allocated_hours', 'allocated_percentage'])[0],
                **{
                    'start_datetime': date_start,
                    'end_datetime': date_stop,
                },
            })
            return new_slot.allocated_hours
