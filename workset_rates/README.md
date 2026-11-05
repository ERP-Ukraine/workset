# workset_rates — «Ставки»

Модуль Odoo 19.0 для репозитория Odoo.sh `ERP-Ukraine/workset`. Здесь только устройство (модели, поля, экраны, права, автоматика,
плановые); данные — линии, их заказчики, периметр фирм, состав редакторов, исполнитель звірки, пороги частки — пишет инструмент штаба
через API.

## Что делает
- **Ставка** `workset.rate` — «працівник × замовник × Ставка з», одна на все фирмы, с историей пары. При сохранении «Вартість години»
  (`project.sale.line.employee.map.cost` + флаг ручной правки) ставится в строки карты проектов этого заказчика во всех фирмах периметра
  (только строки в EUR); пара без действующей ставки — строки обратно на `hourly_cost` карточки. Автоматика — методы `create` / `write`
  (поля-триггеры) / `unlink`, не `base.automation`.
- **А2**: строку карты создали или сменили в ней работника или проект — действующая ставка пары ставится сама.
- **Плановые** (`data/ir_cron.xml`): «Ставки: щодня» — 22:15 UTC (00:15 по Киеву зимой): будущие ставки становятся действующими,
  доли посредников сверяются; «Ставки: звірка» — понедельник 06:00 UTC, создаётся **выключенной**: запись «Звірка ставок» с разделами
  и одна задача To Do исполнителю.
- **Средние**: деньги ставки хранятся с `aggregator='avg'` — в строке каждой группы списка среднее, в подвале — среднее по списку;
  над списком и доской — полоса итогов (`js_class` `workset_rates_list` / `workset_rates_kanban`, один вызов `rates_summary(domain)`);
  экран «Середні» — представление SQL `workset.rate.avg` (средние по тем, кто работает, в разрезах линия / заказчик / фирма / проект).
- **Левая панель**: «Робота» — все значения; «Лінія», «Замовник», «Фірми» — без пустых значений, линии и фирмы — по числу работающих
  (переопределение `search_panel_select_range` / `search_panel_select_multi_range`).
- **Посредники**: доля посредника ставки — цепочка (а) договорённости модели `x_agent_client_deal` (только почасовые `x_kind = hour`,
  состояния `zvirka`, `active`, по заказчику или линии) → (б) нет договорённости, а у линии есть агент и доля — доля линии → (в) введённое
  вручную. Дата поиска: действующая и будущая — сегодня (будущая — её «Ставка з»), прошлая — «Ставка до». Ночная «щодня» пишет долю,
  только если число изменилось. Имена модели посредников — только в `models/agent_deals.py`. В звірке — раздел «Лінія з посередником,
  домовленості немає» (только когда модель договорённостей в базе есть).
- **Договор**: «У договорі» (`hr.version.hourly_wage`) есть только с `hr_payroll`; без него договор не сверяется (`contract_ok` = True,
  во вкладке «Контроль» — «модуля зарплат немає»).
- **Права**: привилегия «Ставки» в разделе кадров формы пользователя, группа «Редактор» — правка ставок и линий; чтение — кадры,
  бухгалтерия, администраторы проектов; удаление — только системный администратор; звірку создаёт только код (редактор ставит «Розібрано»).

## Установка
1. Папку модуля — в репозиторий Odoo.sh (ветка копии → проверка сборки и тестов → `main`); помощник штаба `tools/odoo_sh_push.js`.
2. `node tools/odoo_workset_rates.js --target copy --install --apply` — обновить список модулей и установить (обновить) модуль.
3. `node tools/odoo_workset_rates.js --target copy --seed --apply [--editors <id,…>]` — линии и заказчики, параметры.
4. Перенос ставок — `node tools/odoo_rates_migrate.js` (уже на новые имена); затем `--check-cron on`.
5. `--verify` — модуль, виды, меню, права, плановые.

## Системные параметры (пишет инструмент штаба)
| Параметр | Смысл |
|---|---|
| `workset_rates.out_of_perimeter_company_ids` | id фирм вне периметра рекрутинга через запятую: их строки карты автоматика не трогает |
| `workset_rates.check_assignee_uid` | пользователь, которому идёт задача звірки (нет — администратор) |
| `workset_rates.work_ref` | опорная дата «Працює» — действует только на нейтрализованной копии |
| `workset_rates.min_share_default`, `workset_rates.good_share_default` | пороги частки WorkSET, если в линии не заданы |

## Таблица имён: ручные модели «Ставок» (копия 19) → модуль
По ней переписываются читатели: инструмент переноса `tools/odoo_rates_migrate.js` (таблица в коде — `tools/lib/workset_rates_names.js`),
модуль посредников (`x_agent_client_deal.x_line_id` → `workset.line`).

| Было | Стало |
|---|---|
| модель `x_workset_line` | `workset.line` |
| модель `x_workset_rate` | `workset.rate` |
| модель `x_workset_rate_check` | `workset.rate.check` |
| модель `x_workset_rate_avg` (таблица, писало действие) | `workset.rate.avg` (представление SQL, только чтение) |
| `x_name`, `x_code`, `x_plan_ids`, `x_agent_id`, `x_share_line_found`, `x_share_we_found`, `x_agent_share`, `x_customer_ids`, `x_rule_note`, `x_active`, `x_sequence`, `x_min_share`, `x_good_share`, `x_rate_count`, `x_work_count`, `x_avg_wage`, `x_avg_price`, `x_avg_share` (линия) | то же без `x_` |
| `x_rank` (линия) | убрано: порядок в панели считает `search_panel_select_range` |
| `x_name`, `x_person_id`, `x_customer_id`, `x_line_id`, `x_found_by`, `x_currency_id`, `x_wage`, `x_agent_share`, `x_price`, `x_our_share`, `x_rule_share`, `x_rule_ok`, `x_rule_text`, `x_date_from`, `x_date_to`, `x_state`, `x_last_hours`, `x_work`, `x_work_group`, `x_company_ids`, `x_project_ids`, `x_firms_text`, `x_projects_text`, `x_source`, `x_source_note`, `x_note`, `x_contract_wage`, `x_contract_company_id`, `x_contract_ok`, `x_map_count`, `x_map_mismatch`, `x_order_price_ok`, `x_check_reason`, `x_control_html`, `x_control_sig`, `x_active`, `x_employee_ids`, `x_employee_summary`, `x_pair_rate_ids`, `x_others_count`, `x_others_note`, `x_person_rate_ids`, `x_share_level`, `x_issues`, `x_line_agent_id`, `x_subtitle`, `x_formula` (ставка) | то же без `x_` |
| `x_check` (ставка) | `to_check` |
| — | `agent_note` (ставка): откуда доля посредника |
| `x_wage_v`, `x_price_v`, `x_share_v`, `x_agent_v` | убраны: деньги хранятся с усреднением |
| `x_name`, `x_date`, `x_issue_count`, `x_body`, `x_done` (звірка) | то же без `x_` |
| `x_dim`, `x_key`, `x_work_count`, `x_avg_wage`, `x_avg_price`, `x_avg_share`, `x_total`, `x_zero_share`, `x_note` («Середні») | то же без `x_`; `x_name` → вычисляемое `name` |
| `x_workset_rate_count` на `res.partner`, `hr.employee`, `project.project` | `workset_rate_count` |
| параметр `workset_hq.rates_work_ref` | `workset_rates.work_ref` |
| внешние id `workset_hq.rates_*` | `workset_rates.*` (`group_editor`, `view_rate_form`, `cron_daily`, `cron_check`, …) |

## Тесты
`tests/test_workset_rates.py` — `TransactionCase`, теги `post_install`, `-at_install`; Odoo.sh запускает их на dev-сборке ветки.
Без Odoo — проверка с параметрами (без них код 1: чужие `x_`-имена адаптера разрешаются только параметром):
`node tools/odoo_sh_push.js --module workset_rates --print-check-params > p.json`, затем
`uv run --python 3.13 python -I tools/odoo_addon_check.py tools/odoo_addons/workset_rates < p.json`; и
`node --test tools/tests/odoo_workset_rates_module.test.js` (чистые функции `models/calc.py`, перевод, проверка публичности).

## Перевод
Подписи — украинские в исходнике; `i18n/ru.po` собирается `tools/odoo_addon_po.py` из словаря `tools/lib/workset_rates_ru.json`
(не править руками).
