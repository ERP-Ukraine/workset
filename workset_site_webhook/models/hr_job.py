# -*- coding: utf-8 -*-
"""Вебхук сайту: після коміту змін у hr.job надсилає POST на сервіс пересборки сайту. Плюс штампи дат публікації.

Як працює:
  create / write (поля картки вакансії або name, active, company_id) / unlink → подія запам'ятовується
  у cr.postcommit.data → після коміту транзакції один HTTP-виклик у фоновому потоці.
  Сервіс на VPS сам перечитує вакансії з Odoo через API, тому тіло запиту — лише для логу.
  Зміна статусу x_publish (поле створює tools/odoo_jobs_setup.js): published → x_published_at = сьогодні
  (перша публікація або повторна після закриття, x_closed_at очищається); done / closed → x_closed_at = сьогодні.

Параметри (Налаштування → Технічні → Системні параметри):
  workset_site_webhook.url    — адреса, напр. http://2.28.64.19:8787/rebuild (порожньо = вимкнено)
  workset_site_webhook.token  — секрет, іде в заголовку x-hook-token
Нейтралізовані копії бази (staging / dev на Odoo.sh, параметр database.is_neutralized) вебхук не шлють.
"""
import logging
import threading

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

PARAM_URL = "workset_site_webhook.url"
PARAM_TOKEN = "workset_site_webhook.token"
WATCHED_FIELDS = {"name", "active", "company_id"}  # плюс будь-яке поле x_* (картка вакансії сайту)
TIMEOUT = (3, 10)  # секунд: з'єднання, відповідь
ASYNC = True  # тести вимикають, щоб виклик ішов синхронно


def _post(url, token, payload):
    """HTTP-виклик. Будь-яка помилка — лише в лог: збереження в Odoo вже відбулося і не має ламатися."""
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"x-hook-token": token or "", "user-agent": "odoo-workset-site-webhook/1.0"},
            timeout=TIMEOUT,
        )
        if resp.status_code in (200, 202):
            _logger.info("workset_site_webhook: %s -> HTTP %s %s", url, resp.status_code, payload.get("events"))
        else:
            _logger.warning("workset_site_webhook: %s -> HTTP %s", url, resp.status_code)
    except Exception as exc:  # noqa: BLE001 — мережеві помилки не мають впливати на користувача
        _logger.warning("workset_site_webhook: %s недоступний: %s", url, exc)


class HrJob(models.Model):
    _inherit = "hr.job"

    @api.model_create_multi
    def create(self, vals_list):
        if self._site_has_date_fields():
            today = fields.Date.context_today(self)
            vals_list = [dict(v, **self._site_stamps_for(v.get("x_publish"), v.get("x_published_at"), v.get("x_closed_at"), today)) for v in vals_list]
        jobs = super().create(vals_list)
        jobs._site_webhook_schedule("create")
        return jobs

    def write(self, vals):
        res = super().write(vals)
        if vals.get("x_publish") and self._site_has_date_fields():
            today = fields.Date.context_today(self)
            for job in self:
                stamps = self._site_stamps_for(vals["x_publish"], job.x_published_at, job.x_closed_at, today)
                if stamps:
                    job.write(stamps)  # без x_publish — повторного штампування немає, вебхук дедуплікується
        if self._site_webhook_is_watched(vals):
            self._site_webhook_schedule("write")
        return res

    def unlink(self):
        self._site_webhook_schedule("unlink")
        return super().unlink()

    # --- штампи дат -------------------------------------------------------------------------------------------------

    def _site_has_date_fields(self):
        return "x_publish" in self._fields and "x_published_at" in self._fields and "x_closed_at" in self._fields

    @staticmethod
    def _site_stamps_for(status, published_at, closed_at, today):
        """Які дати проставити при переході в статус: {} — нічого."""
        if status == "published" and (not published_at or closed_at):
            return {"x_published_at": today, "x_closed_at": False}
        if status in ("done", "closed") and not closed_at:
            return {"x_closed_at": today}
        return {}

    # --- вебхук ------------------------------------------------------------------------------------------------------

    @staticmethod
    def _site_webhook_is_watched(vals):
        """Чи зачіпає запис те, що бачить сайт: поля x_* картки вакансії, назву, архівацію, компанію."""
        return any(key in WATCHED_FIELDS or key.startswith("x_") for key in vals)

    def _site_webhook_schedule(self, event):
        """Запам'ятати подію; один виклик на транзакцію, після коміту."""
        icp = self.env["ir.config_parameter"].sudo()
        url = (icp.get_param(PARAM_URL) or "").strip()
        if not url:
            return
        if str(icp.get_param("database.is_neutralized") or "").strip().lower() in ("true", "1"):
            return
        data = self.env.cr.postcommit.data
        pending = data.get("workset_site_webhook")
        if pending is None:
            pending = data["workset_site_webhook"] = {
                "url": url,
                "token": icp.get_param(PARAM_TOKEN) or "",
                "db": self.env.cr.dbname,
                "events": [],
                "ids": [],
            }
            self.env.cr.postcommit.add(lambda: self._site_webhook_fire(pending))
        if event not in pending["events"]:
            pending["events"].append(event)
        pending["ids"].extend(i for i in self.ids if i not in pending["ids"])

    @staticmethod
    def _site_webhook_fire(pending):
        """Після коміту: без ORM, лише HTTP у фоновому потоці (або синхронно в тестах)."""
        payload = {"model": "hr.job", "db": pending["db"], "events": pending["events"], "ids": pending["ids"]}
        args = (pending["url"], pending["token"], payload)
        if ASYNC:
            threading.Thread(target=_post, args=args, name="workset_site_webhook", daemon=True).start()
        else:
            _post(*args)
