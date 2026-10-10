# workset_site_webhook

Модуль Odoo для репозитория Odoo.sh `ERP-Ukraine/workset` (ветка `main`, 16.0; совместим с 19.0 — версия в манифесте без серии).

**Что делает.** После коммита создания / изменения / удаления позиции `hr.job` (вакансии сайта) отправляет
`POST` на сервис пересборки сайта на VPS (`workset-site-watch`, `POST /rebuild`, заголовок `x-hook-token`).
Один вызов на транзакцию, в фоновом потоке, ошибки — только в лог Odoo. Сервис сам перечитывает вакансии через API,
тело запроса (`model`, `db`, `events`, `ids`) нужно только для журнала на VPS.

**Настройка.** Два системных параметра (Налаштування → Технічні → Системні параметри) — их пишет скрипт штаба
`node tools/odoo_jobs_setup.js --apply` из переменных `SITE_HOOK_URL` и `SITE_HOOK_SECRET`:

| Параметр | Значение |
|---|---|
| `workset_site_webhook.url` | `http://2.28.64.19:8787/rebuild` (пусто = выключено) |
| `workset_site_webhook.token` | секрет `SITE_HOOK_SECRET` |

Нейтрализованные копии базы (staging / dev на Odoo.sh, параметр `database.is_neutralized`) вебхук не отправляют.

**Тесты.** `tests/test_site_webhook.py` — Odoo.sh запускает их сам на dev-сборке ветки; в сеть не ходят.

**Установка.** Папку модуля загрузить в репозиторий (ветка dev → проверка сборки → `main`), затем
`node tools/odoo_jobs_setup.js --apply --install-webhook` (обновляет список приложений, ставит модуль, пишет параметры).
