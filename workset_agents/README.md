# workset_agents — «Агенти»

Модуль Odoo 19.0 для репозитория Odoo.sh `ERP-Ukraine/workset`. Здесь только устройство (модели, поля, экраны, права, автоматика,
плановое); факты — фирмы выплат, периметр, журналы, аналитика, тип активности, учётные записи владельца и сотрудников — системные
параметры `workset_agents.*`, их пишет инструмент штаба `tools/odoo_workset_agents.js --seed`. Нет параметра — ясная ошибка
«параметр … не заданий» (в ночи — строка журнала «Помилка», ничего не пишется).

Заменяет ручную сборку «Агенти» (11 моделей `x_agent_*`, серверные действия safe_eval, ставит `tools/odoo_agents_setup.js`, последняя
версия — ветка `wip/druzya8-agents-vid`). Поведение денег — один в один с `tools/odoo_agents_calc/*.py` той ветки.

## Что делает (круг М1)
- **Ночь** `workset.agent.run._cron_night()` (плановое `cron_night`, ~00:30 UTC, создаётся **выключенным**) — точки сохранения в прежнем
  порядке: (1) начисления «€ за годину» обоих разделов и правки статусов / дат / сторожа, разовые; (2) сторно; (2б) «Виплачено» по счёту
  поставщика фирмы official и по строке журнала WS фирмы personal; (3) решения и подготовка выплат; (4) автозатвердження — только при
  `workset_agents.auto_approve = 1` и только на копии. Каждая точка — `savepoint`; сбой одной не откатывает другие — журнал «Частково».
  Расчёт целиком до первой записи: ошибка чтения или расчёта — журнал «Помилка», ничего не записано. Прод (`is_copy` ≠ 1) без
  `night_prod_ok = 1` — журнал «ніч не дозволена», выход.
- `run_now(dry=True)` — ночь вхолостую: ничего не пишет (ни начислений, ни журнала), возвращает план; `run_now(dry=False)` — как ночь.
- Чистые функции — `models/calc.py` (даты выплат, календарь банков Польши, часы счёта, начисления, сторно, сторож) и `models/payplan.py`
  («Виплачено», помощники плана); без `env`, их проверяет node-тест `tools/tests/odoo_workset_agents_module.test.js` через uv.
- Кнопки: «Сторно» (начисление), «Затвердити», «Як пропонує штаб», «Зупинити» (решение, только группа «Власник»), окно «Позначити
  виплаченим вручну» (бухгалтерия и «Власник»).
- Шаги (3)–(4) (круг 2, 07.11): `workset.agent.decision._night_pay_step` → `payplan.pay_plan(_load_pay)` — пропущенные решения, перенос
  «До виплати», подготовка затверджених (выплата; official — черновик счёта поставщика с удержаниями и `payment_reference` = номер выплаты;
  personal — запись «з особистого рахунку»; custom — «оформити окремо»; начисления — «Закрито рішенням»; список офису — активность),
  пересборка черновиков, новое решение D−3 на раздел; `_night_auto_step` → `payplan.auto_plan` — автозатвердження при `_auto_allowed()`,
  иначе тревога «ask» владельцу на каждый черновик к выплате (Р-2), Р-4 — разовые / не ночные начисления, договорённость изменена после выплаты.
- Поля штаба на `mail.activity` знает только `models/office_bridge.py` (в проверке публичности — `allow_x` этого файла); полей нет —
  активность без них и примечание в журнале.

## Системные параметры (пишет `--seed`; модуль только читает)
| Параметр | Смысл | Обязателен |
|---|---|---|
| `workset_agents.company_official_id` | фирма официальных выплат (`official_1`) | ночь |
| `workset_agents.company_personal_id` | фирма личных переводов (`personal_36`), в ней журналы WS | ночь |
| `workset_agents.perimeter_company_ids` | фирмы периметра счетов заказчиков, через запятую | ночь |
| `workset_agents.ws_journal_codes` | коды банковских журналов WS фирмы personal, через запятую | «Виплачено» |
| `workset_agents.withhold_analytic_id` | аналитика удержания 10 % (примечание «утримання знайдено / ні») | «Виплачено» |
| `workset_agents.bill_journal_code` | журнал счетов поставщика фирмы official (кода нет — единственный журнал закупок фирмы, иначе строка «На перевірці») | нет: строка решения «На перевірці» |
| `workset_agents.analytic_agents_id` | аналитика «Агенты» строк счёта поставщика | нет: «На перевірці» `bill_setup` |
| `workset_agents.fee_account_code` | код счёта расходов вознаграждения в фирме official (было `PAY.fee_account`) | нет: «На перевірці» `bill_setup` |
| `workset_agents.tax_account_code` | код счёта налога удержания по п. 7.2 там же (было `PAY.tax_account`) | нет: «На перевірці» `bill_setup` |
| `workset_agents.envelope_prefix` | начало имени конверта («ДОЛЯ | ») — пока не читается (конверт — поле агента `envelope_id`) | нет |
| `workset_agents.office_activity_type_id` | тип активности «Бот «Офіс»» (список переводов офису) | нет: список не ставится, примечание в журнале |
| `workset_agents.office_to` | адресат списка в боте «Офіс» (имя раздела office-desk; было `PAY.office_to`) | нет: пусто |
| `workset_agents.owner_uid` | учётка владельца: от его имени автозатвердження, ему тревоги «ask» | нет: тревоги не ставятся, примечание |
| `workset_agents.owner_to` | подпись владельца в активностях штаба (было `PAY.owner_to`) | нет: пусто |
| `workset_agents.olya_uid` | учётка бухгалтерии — пока не читается | нет |
| `workset_agents.card_year` | год сумм карточки агента; пусто — текущий год по Киеву | нет |
| `workset_agents.is_copy` | `1` — база не прод (копия) | нет: пусто = прод |
| `workset_agents.night_prod_ok` | `1` — ночь с записью на проде разрешена владельцем | нет: пусто = нельзя |
| `workset_agents.auto_approve` | `0` / `1`, только копия | нет: пусто = `0` |
| `workset_agents.probe_today` | «сегодня» ночи, только при `is_copy = 1` | нет |
| `workset_agents.agent_tag_id` | метка контакта «Агент» (ставится при создании агента) | нет: пусто — без метки |

`withhold_analytic_id` и `agent_tag_id` — новые против списка брифа (их нет в брифе, но нынешний код ими пользуется: `PAY.wh_analytic`,
метка «Агент» контакта).

## Права
Привилегия «Агенти» (раздел кадров формы пользователя), группы: `group_viewer` «Перегляд» (подразумевается у `hr.group_hr_user` и
`account.group_account_user`), `group_editor` «Редактор», `group_owner` «Власник» (состав ставит `--seed`: ровно учётка владельца).
Строки `ir.model.access.csv` — по `PERMS` ручной сборки один в один (кадровик = `hr.group_hr_user`, адм. кадров = `hr.group_hr_manager`,
бухгалтер = `account.group_account_user`, власник = `group_owner`), плюс чтение для `group_viewer` и правка агентов, договорённостей,
людей для `group_editor`. Правило записей `res.partner.bank` — глобальное: счета контактов-агентов видят только бухгалтерия и
администраторы кадров. Журнал — только чтение, пишет код через `sudo()`.

## Таблица имён: ручная сборка → модуль
Правило: модель — по таблице ниже; поле `x_<имя>` → `<имя>`; ссылка на агента `x_agent_id → res.partner` → `agent_id → workset.agent`
(единственное плановое изменение смысла). Поле `legacy_ref` (Char, индекс, скрыто) — на каждой модели модуля: «`x_agent_accrual,123`» —
для идемпотентного переноса с копии; на проде пусто. Чаттер при переносе не переносится.

### Модели и внешние id
| Было | Стало |
|---|---|
| поля `x_agent_*` на `res.partner` + метка «Агент» | `workset.agent` (`_inherits` `res.partner` через `partner_id`, `mail.thread`, `mail.activity.mixin`; один агент на контакт) |
| `x_agent_people_deal` | `workset.agent.deal.people` |
| `x_agent_people_member` | `workset.agent.member` |
| `x_agent_client_deal` | `workset.agent.deal.client` |
| `x_agent_accrual` | `workset.agent.accrual` |
| `x_agent_payout` | `workset.agent.payout` |
| `x_agent_pay_decision` | `workset.agent.decision` |
| `x_agent_pay_line` | `workset.agent.decision.line` |
| `x_agent_run` | `workset.agent.run` |
| `x_agent_tax_profile` | `workset.agent.tax` |
| `x_agent_zvirka` | `workset.agent.zvirka` |
| `x_agent_paid_wizard` | `workset.agent.paid.wizard` (`TransientModel`) |
| `workset_hq.agents_group_owner` | `workset_agents.group_owner` (+ `group_editor`, `group_viewer`) |
| `workset_hq.agents_cron_night` + `workset_hq.agents_srv_night` | `workset_agents.cron_night` → `workset.agent.run._cron_night()` |
| серверные действия «Сторно», «Затвердити», «Як пропонує штаб», «Зупинити», «Позначити виплаченим вручну» | методы `action_storno`, `action_approve`, `action_apply_advice`, `action_stop`, `workset.agent.paid.wizard.action_confirm` |
| правило «Штаб: Агенти — банківські рахунки агентів …» | `workset_agents.rule_partner_bank_agents` |
| меню и действия `workset_hq.agents_*` | `workset_agents.menu_root` («Агенти»), `action_*`, `view_*` |

### Поля: исключения из правила (всё остальное — `x_<имя>` → `<имя>`)
| Модель | Было | Стало | Почему |
|---|---|---|---|
| все модели модуля | `x_agent_id` (`res.partner`) | `agent_id` (`workset.agent`) | плановая смена смысла: агент — своя модель |
| `res.partner` → `workset.agent` | `x_agent` (метка-булево) | `res.partner.is_agent` (вычисляемое хранимое: есть запись `workset.agent`) | признак остаётся на контакте для правила `res.partner.bank` и фильтров; имя `agent` на контакте читалось бы как ссылка |
| `res.partner` → `workset.agent` | `x_agent_tg_id`, `x_agent_code`, `x_agent_form_state`, `x_agent_envelope_id` | `tg_id`, `code`, `form_state`, `envelope_id` | поля агента переезжают с контакта: префикс `x_agent_` снят целиком |
| `res.partner` → `workset.agent` | `x_agent_pd_ids`, `x_agent_cd_ids`, `x_agent_member_ids` | `pd_ids`, `cd_ids`, `member_ids` | то же |
| `res.partner` → `workset.agent` | `x_agent_kind` | `kind` | то же |
| `res.partner` → `workset.agent` | `x_agent_mode` («Стан агента») | `state` | имя брифа: стан своей модели |
| `res.partner` → `workset.agent` | `x_agent_pd_n`, `x_agent_cd_n`, `x_agent_people_n`, `x_agent_cust_n`, `x_agent_bank_n` | `pd_n`, `cd_n`, `people_n`, `cust_n`, `bank_n` | префикс снят |
| `res.partner` → `workset.agent` | `x_agent_calc_year`, `x_agent_env_year`, `x_agent_diff_year`, `x_agent_next_pay`, `x_agent_is_test` | `calc_year`, `env_year`, `diff_year`, `next_pay`, `is_test` | префикс снят; `is_test` — хранимое, вычисляется по коду / имени «ТЕСТ-ШТАБ», можно ставить вручную |
| `workset.agent` | — | `deal_count`, `accrual_n`, `payout_n`, `next_pay_day`, `due_next`, `paid_year`, `diff_pct`, `accrual_ids`, `payout_ids`, `zvirka_ids`, `tax_ids` | новые (бриф: карточка агента, stat-кнопки) |
| `x_agent_tax_profile` | `x_partner_id` (контакт агента) | `agent_id` (`workset.agent`, один профиль на агента) | бриф: профиль — у агента |
| `hr.employee` | `x_agent_brought_id` (`res.partner`) | `agent_id` (`workset.agent`) + `agent_deal_id` (`workset.agent.deal.people`) | бриф: «Привів агент» |
| `x_agent_run` | `x_name`, `x_at`, `x_result`, `x_summary`, `x_created`, `x_changed`, `x_review`, `x_seconds`, `x_before`, `x_kind` | `name`, `at`, `result`, `summary`, `created`, `changed`, `review`, `seconds`, `before`, `kind` | по правилу (бриф называл `date`, `text`, `created_count` … — оставлено правило, по нему переносит Б) |
| `x_agent_zvirka` | — | `diff_pct` (новое вычисляемое) | бриф: колонка «%» |
| `x_agent_people_member` | `x_agent_id` (вычисляемое хранимое) | `agent_id` — `related` от договорённости (хранимое) | то же значение, проще устройство |
| `x_agent_client_deal` | `x_customer_ids` (таблица `x_agent_client_deal_partner_rel`) | `customer_ids` (таблица `workset_agent_deal_client_partner_rel`, столбцы `deal_id`, `partner_id`) | таблица связи — своя |
| `x_agent_paid_wizard` | `x_payout_ids` | `payout_ids` (таблица `workset_agent_paid_wizard_payout_rel`) | таблица связи — своя |
| `x_agent_pay_line` | `x_overdue_move_ids` | `overdue_move_ids` (таблица `workset_agent_decision_line_overdue_rel`) | таблица связи — своя |

Имена брифа, которые расходятся с правилом и **не** взяты (взято правило): начисление `deal_people_id` / `deal_client_id` / `invoice_id` /
`pay_day` / `storno_of_id` / `decision_line_id` → `people_deal_id` / `client_deal_id` / `move_id` / `pay_date` / `storno_id` / `pay_line_id`;
выплата `tax10` → `wh10`, `decision_line_id` → `pay_line_id`; звірка `odoo_amount` / `envelope_amount` / `state` → `calc` / `envelope` /
`state_badge`; налоги `status` / `country_id` → `tax_status` / `tax_country`.

### Поля по моделям (полный список, после правила)
- `workset.agent.deal.people`: `name`, `agent_id`, `kind`, `date_from`, `date_to`, `company_id`, `pay_path`, `currency_id`, `rate`, `withhold_pct`,
  `withhold_analytic_id`, `terms_version`, `terms_source`, `owner_ok`, `state`, `note`, `member_ids`, `mode`, `oneoff_amount`, `people_n`,
  `accrual_ids`, `accrual_live_ids`, `acc_sum`, `env_sum`, `zv_diff_sum`, `legacy_ref`.
- `workset.agent.member`: `name`, `deal_id`, `partner_id`, `date_from`, `date_to`, `agent_id` (хранимое из договорённости), `legacy_ref`.
- `workset.agent.deal.client`: `name`, `agent_id`, `customer_ids`, `line_id`, `kind`, `currency_id`, `rate`, `rate_full`, `withhold_pct`,
  `withhold_analytic_id`, `date_from`, `date_to`, `company_id`, `pay_path`, `fixed_advance`, `addendum`, `terms_source`, `owner_ok`, `state`,
  `note`, `mode`, `cust_n`, `accrual_ids`, `accrual_live_ids`, `acc_sum`, `env_sum`, `zv_diff_sum`, `legacy_ref`.
- `workset.agent.accrual`: `name`, `section`, `people_deal_id`, `client_deal_id`, `agent_id`, `kind`, `person_id`, `customer_id`, `move_id`, `hours`,
  `rate`, `currency_id`, `amount`, `earned_date`, `pay_date`, `work_from`, `work_to`, `fund`, `company_id`, `line_id`, `state`, `guard`,
  `guard_note`, `key` (уникальный), `storno_id`, `note`, `payout_id`, `pay_line_id`, `legacy_ref`.
- `workset.agent.payout`: `name`, `agent_id`, `company_id`, `section`, `pay_path`, `pay_day`, `currency_id`, `gross`, `tax20`, `wh10`, `net`,
  `move_id`, `state`, `paid_date`, `paid_manual`, `paid_note`, `note`, `move_line_id`, `paid_via`, `paid_amount`, `work_month`, `paid_by`,
  `guard`, `guard_note`, `bank_ref`, `iban`, `purpose`, `accrual_ids`, `decision_id`, `pay_line_id`, `legacy_ref`.
- `workset.agent.run`: `name`, `at`, `result`, `summary`, `created`, `changed`, `review`, `seconds`, `before`, `kind`, `legacy_ref`.
- `workset.agent.paid.wizard`: `name`, `payout_ids`, `date`, `note`, `amount`, `source`.
- `workset.agent.tax`: `name`, `agent_id`, `tax_status`, `tax_country`, `cert_until`, `tax_number`, `note`, `legacy_ref`.
- `workset.agent.decision`: `name`, `pay_day`, `section`, `state`, `currency_id`, `approved_by`, `approved_at`, `prepared_at`, `guard_note`, `auto`,
  `auto_note`, `note`, `pay_line_ids`, `payable_line_ids`, `zvirka_line_ids`, `payout_ids`, `available_total`, `pay_total`, `net_total`,
  `buffer_total`, `hq_advice`, `zv_more_total`, `zv_less_total`, `zv_diff_total`, `zv_missing_total`, `overdue_total`, `cut_total`,
  `zvirka_total`, `line_count`, `legacy_ref`.
- `workset.agent.decision.line`: `name`, `decision_id`, `agent_id`, `section`, `pay_path`, `company_id`, `currency_id`, `state`, `key`, `accrued`,
  `review_amount`, `buffer_in`, `buffer_from_id`, `available`, `wh_pct`, `tax_pct`, `wh_note`, `proposal`, `proposal_note`, `pay_now`, `pay_wh`,
  `pay_tax`, `pay_net`, `unpaid`, `seen_at`, `owner_edit`, `night_pay`, `buffer_out`, `cust_paid`, `cust_unpaid`, `cust_overdue`, `cust_text`,
  `overdue_move_ids`, `zv_period`, `zv_env`, `zv_diff`, `zv_note`, `zv_missing`, `advice`, `advice_text`, `guard`, `guard_note`, `payout_id`,
  `accrual_ids`, `legacy_ref`.
- `workset.agent.zvirka`: `name`, `agent_id`, `section`, `envelope_id`, `month`, `currency_id`, `calc`, `envelope`, `diff`, `diff_pct`, `lines`,
  `matched`, `source`, `calc_note`, `note`, `state_badge`, `month_label`, `key`, `checked_at`, `legacy_ref`.
- `workset.agent`: `partner_id`, `code`, `tg_id`, `form_state`, `envelope_id`, `pd_ids`, `cd_ids`, `member_ids`, `kind`, `state`, `pd_n`, `cd_n`,
  `deal_count`, `people_n`, `cust_n`, `bank_n`, `accrual_n`, `payout_n`, `card_year`, `calc_year`, `env_year`, `diff_year`, `diff_pct`, `next_pay`, `next_pay_day`, `due_next`,
  `paid_year`, `is_test`, `accrual_ids`, `payout_ids`, `zvirka_ids`, `tax_ids`, `legacy_ref`; имя, телефон, почта, банки — поля контакта
  (`_inherits`).
- `hr.employee`: `agent_id`, `agent_deal_id` (только показ).
- `res.partner`: `agent_ids`, `is_agent`.

Значения списков (ключи) — прежние: `pay_path` `personal_36` / `official_1` / `custom`, `mode` `pay` / `zvirka`, `state` договорённости
`draft` / `zvirka` / `active` / `closed`, вид клиента `hour` / `fixed` / `worker_fund` и т. д.

## Известные следствия переноса копии (`--migrate`)
- У перенесённых записей `create_uid` / `write_uid` / `create_date` — учётка ключа и время переноса: проверка Р-4 автозатвердження
  («начисление создано или изменено не ночью», «договорённость изменена после последней выплаты») на копии теряет историю до переноса.
  Касается только автозатвердження копии; на проде переноса нет.
- Перенесённые решения получают новые id: ключ тревоги владельцу содержит id решения — по уже спрошенному решению возможна повторная тревога.
- Ключи начислений, строк решений и звірки содержат id (договорённость, счёт, агент): перенос обязан переписать их по картам id, иначе первая
  ночь даст «На перевірці» `no_key` и пересборку строк (Б-1 ревью М1 — инструмент Б).
- Список офису ставится на OdooBot (как в ручной сборке): доставку в бот «Офіс» делает рутина office-desk; параметр `yulia_uid` больше не
  читается.
- Кнопки владельца «Затвердити» / «Зупинити» сразу запускают расчёт и на проде (замок `night_prod_ok` — только у планового и ручного
  `run_now`).

## Установка
1. Папку модуля — в репозиторий Odoo.sh (`node tools/odoo_sh_push.js --module workset_agents …`, делает coordinator).
2. `node tools/odoo_workset_agents.js --target copy --install --apply`, затем `--seed --apply`, `--migrate --apply`, `--verify`.
3. Ночь — `--night on` (на проде — только по слову владельца и параметру `night_prod_ok`).

## Тесты
- Odoo: `tests/test_calc.py`, `tests/test_night.py` (`TransactionCase`, `post_install`, `-at_install`) — dev-сборка Odoo.sh.
- Без Odoo: `node --test tools/tests/odoo_workset_agents_module.test.js` (чистые функции через uv, проверка модуля `tools/odoo_addon_check.py`).
