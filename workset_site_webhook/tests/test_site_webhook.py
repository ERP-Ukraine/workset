# -*- coding: utf-8 -*-
"""Тести модуля: запускаються Odoo.sh на dev-збірці (і локально: odoo-bin -i workset_site_webhook --test-enable).
HTTP не ходить у мережу — requests.post підмінено."""
from unittest.mock import patch

import requests

from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

MOD = "odoo.addons.workset_site_webhook.models.hr_job"


@tagged("post_install", "-at_install")
class TestSiteWebhook(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.icp.set_param("workset_site_webhook.url", "http://hook.test/rebuild")
        cls.icp.set_param("workset_site_webhook.token", "secret-token")
        cls.icp.set_param("database.is_neutralized", "False")
        cls.job = cls.env["hr.job"].create({"name": "Тестова вакансія"})
        cls.env.cr.postcommit.clear()

    def setUp(self):
        super().setUp()
        self.env.cr.postcommit.clear()

    def _run(self, side_effect=None):
        """Виконати відкладені після-комітні виклики синхронно з підміненим requests.post."""
        with patch(MOD + ".ASYNC", False), patch(MOD + ".requests.post") as post:
            post.return_value.status_code = 202
            post.side_effect = side_effect
            self.env.cr.postcommit.run()
        return post

    def test_status_date_stamps(self):
        stamps = self.env["hr.job"]._site_stamps_for
        self.assertEqual(stamps("published", False, False, "2026-09-16"), {"x_published_at": "2026-09-16", "x_closed_at": False})
        self.assertEqual(stamps("published", "2026-09-01", False, "2026-09-16"), {})  # вже опубліковано — дату не зсуваємо
        self.assertEqual(stamps("published", "2026-06-01", "2026-08-01", "2026-09-16"), {"x_published_at": "2026-09-16", "x_closed_at": False})  # повторний набір
        self.assertEqual(stamps("done", "2026-09-01", False, "2026-09-16"), {"x_closed_at": "2026-09-16"})
        self.assertEqual(stamps("closed", "2026-09-01", "2026-09-10", "2026-09-16"), {})
        self.assertEqual(stamps("paused", "2026-09-01", False, "2026-09-16"), {})
        self.assertEqual(stamps(None, False, False, "2026-09-16"), {})
        self.assertFalse(self.env["hr.job"]._site_has_date_fields())  # у тестовій базі полів x_* немає — штампи не застосовуються

    def test_watched_fields_filter(self):
        is_watched = self.env["hr.job"]._site_webhook_is_watched
        self.assertTrue(is_watched({"x_publish": "published"}))
        self.assertTrue(is_watched({"name": "Бетоняр"}))
        self.assertTrue(is_watched({"active": False}))
        self.assertFalse(is_watched({"no_of_recruitment": 3}))
        self.assertFalse(is_watched({}))

    def test_write_fires_once_per_transaction(self):
        self.job.write({"name": "Бетоняр-монолітник"})
        self.job.write({"active": True})
        post = self._run()
        post.assert_called_once()
        url, kwargs = post.call_args[0][0], post.call_args[1]
        self.assertEqual(url, "http://hook.test/rebuild")
        self.assertEqual(kwargs["headers"]["x-hook-token"], "secret-token")
        self.assertEqual(kwargs["json"]["model"], "hr.job")
        self.assertEqual(kwargs["json"]["events"], ["write"])
        self.assertEqual(kwargs["json"]["ids"], [self.job.id])
        self.assertEqual(kwargs["json"]["db"], self.env.cr.dbname)

    def test_unwatched_field_is_silent(self):
        self.job.write({"no_of_recruitment": 2})
        self._run().assert_not_called()

    def test_create_and_unlink_fire(self):
        job = self.env["hr.job"].create({"name": "Нова позиція"})
        post = self._run()
        post.assert_called_once()
        self.assertEqual(post.call_args[1]["json"]["events"], ["create"])
        job.unlink()
        post = self._run()
        post.assert_called_once()
        self.assertEqual(post.call_args[1]["json"]["events"], ["unlink"])

    def test_without_url_nothing_is_sent(self):
        self.icp.set_param("workset_site_webhook.url", False)  # False видаляє параметр
        self.job.write({"name": "Без адреси"})
        self._run().assert_not_called()

    def test_neutralized_copy_is_silent(self):
        self.icp.set_param("database.is_neutralized", "True")
        self.job.write({"name": "Копія бази"})
        self._run().assert_not_called()

    @mute_logger(MOD)  # очікуване попередження «недоступний» не має засмічувати лог збірки
    def test_network_error_does_not_break_save(self):
        self.job.write({"name": "VPS недоступний"})
        post = self._run(side_effect=requests.ConnectionError("down"))
        post.assert_called_once()
        self.assertEqual(self.job.name, "VPS недоступний")
