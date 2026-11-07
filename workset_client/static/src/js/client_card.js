/** «Умови клієнта» (М3, К1): екран замовника — client action workset_client.card; рядок списку «Умови клієнтів» відкриває його ж
 *  (js_class workset_client_list), а не форму контакту.
 *  Екран — ОДИН виклик сервера res.partner.workset_client_card (замовник, період): числа, вибір правил («замовник точніший за лінію»),
 *  жовте «не задано» й описи списків для кліку рахує сервер (models/client_screen.py, models/calc.py), тут — лише показ і переходи. Стиль
 *  — як екрани «Прибутковості»: підпис зверху, крупне число, «€ год» дрібно сірим; колір — лише бренд (частка) і тривоги (жовте
 *  «не задано», червоне — нижче нуля); класи й змінні теми Bootstrap — світла й темна теми. Замовник і період — у params дії; після
 *  перезавантаження сторінки — останні з localStorage користувача. Кожне число й рядок — кнопка (role, tabindex, Enter / пробіл). */
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { formatFloat } from "@web/views/fields/formatters";
import { Layout } from "@web/search/layout";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { Component, onWillStart, useState } from "@odoo/owl";

const PARTNER = "res.partner";
const CARD = "workset_client.card";
const PROFIT_CARD = "workset_profit.line_card";
const TERMS = "workset.client.terms";
const STORE = "workset_client.card.";
const PERIODS = ["month", "quarter", "year"];

/** € на годину: 26 → «26,00», −6.24 → «−6,24»; немає (null) — «—», не «0,00». */
export function money(value) {
    if (value === null || value === undefined) {
        return "—";
    }
    return formatFloat(value, { digits: [16, 2] }).replace(/^-/, "−");
}

/** Число без зайвих нулів: 12 → «12», 0.5 → «0,5». */
export function plain(value) {
    return formatFloat(value || 0, { digits: [16, 2], trailingZeros: false }).replace(/^-/, "−");
}

/** Діапазон діючих ставок {min, max}: однакові — одне число «26,00», різні — «12–14»; ставок немає — «—». */
export function range(r) {
    if (!r) {
        return "—";
    }
    return r.min === r.max ? money(r.min) : `${plain(r.min)}–${plain(r.max)}`;
}

/** Сума за період без копійок: 20667.4 → «20 667». */
export function euros(value) {
    return formatFloat(Math.round(value || 0), { digits: [16, 0] }).replace(/^-/, "−");
}

/** Збережені замовник, період і місяць → чисті: невідоме — за замовчуванням. */
export function cleanSaved(saved) {
    const out = { partner_id: false, period: "month", month: false };
    if (saved && typeof saved === "object") {
        if (Number.isInteger(saved.partner_id) && saved.partner_id > 0) {
            out.partner_id = saved.partner_id;
        }
        if (PERIODS.includes(saved.period)) {
            out.period = saved.period;
        }
        if (typeof saved.month === "string" && /^\d{4}-\d{2}-\d{2}$/.test(saved.month)) {
            out.month = saved.month;
        }
    }
    return out;
}

export class ClientCard extends Component {
    static template = "workset_client.Card";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ data: null, failed: false });
        this.serial = 0;
        this.money = money;
        this.plain = plain;
        this.range = range;
        this.euros = euros;
        this.storeKey = STORE + user.userId;
        const params = (this.props.action && this.props.action.params) || {};
        const saved = this.restore();
        // інший замовник у params — період береться за замовчуванням (останній закритий місяць), а не з попереднього замовника
        const same = !params.partner_id || params.partner_id === saved.partner_id;
        this.partnerId = params.partner_id || saved.partner_id;
        this.period = params.period || (same ? saved.period : "month");
        this.month = params.month || (same ? saved.month : false);
        onWillStart(() => this.load());
    }

    get data() {
        return this.state.data;
    }

    restore() {
        try {
            return cleanSaved(JSON.parse(browser.localStorage.getItem(this.storeKey) || "null"));
        } catch {
            return cleanSaved(null);
        }
    }

    save() {
        // приватний режим / заповнене сховище — після перезавантаження просто «оберіть клієнта», сторінка працює далі
        try {
            browser.localStorage.setItem(this.storeKey, JSON.stringify({ partner_id: this.partnerId, period: this.period, month: this.month }));
        } catch {
            // нічого
        }
    }

    /** Виклик сервера; відповідь, що запізнилась, відкидається. Помилка при першому показі — як у Odoo (вікно помилки); при зміні
     *  періоду — повідомлення, на екрані лишаються попередні дані з позначкою (state.failed) → null. */
    async fetch(args) {
        const serial = ++this.serial;
        let data;
        try {
            data = await this.orm.call(PARTNER, "workset_client_card", args);
        } catch (error) {
            if (!this.state.data) {
                throw error;
            }
            if (serial === this.serial) {
                this.keep(this.state.data);
                this.state.failed = true;
                this.notification.add(_t("Не вдалося оновити — на екрані попередні дані"), { type: "danger" });
            }
            return null;
        }
        if (serial !== this.serial) {
            return null;
        }
        this.state.failed = false;
        return data;
    }

    async load() {
        const data = await this.fetch([this.partnerId || false, this.period, this.month || false]);
        if (data) {
            this.keep(data);
            this.state.data = data;
            this.save();
            this.setName(data);
        }
    }

    /** Вибір екрана — з показаних даних (після помилки наступний запит іде від них). */
    keep(data) {
        this.partnerId = data.partner ? data.partner.id : false;
        this.period = data.period.kind;
        this.month = data.period.value;
    }

    /** Ім'я дії — назва замовника (хлібна крихта й вкладка після F5 чи прямого URL), як картка лінії «Прибутковості». */
    setName(data) {
        const config = this.env.config;
        if (config && typeof config.setDisplayName === "function") {
            config.setDisplayName(data.partner ? data.partner.name : _t("Оберіть клієнта у списку «Умови клієнтів»"));
        }
    }

    setPeriod(kind) {
        if (kind !== this.period) {
            this.period = kind;
            this.load();
        }
    }

    go(value) {
        if (value) {
            this.month = value;
            this.load();
        }
    }

    /** Список за описом сервера: модель, заголовок, домен, контекст (без «Створити»); є res_id — форма одного запису. */
    openList(spec) {
        if (!spec) {
            return;
        }
        const form = Boolean(spec.res_id);
        this.action.doAction({
            type: "ir.actions.act_window",
            name: spec.title,
            res_model: spec.model,
            views: form
                ? [[false, "form"]]
                : [
                      [false, "list"],
                      [false, "form"],
                  ],
            ...(form ? { res_id: spec.res_id } : {}),
            domain: spec.domain,
            context: spec.context || {},
            target: "current",
        });
    }

    /** Вигляд «клікабельно» (рука, підкреслення при наведенні) — лише в елемента з описом; без опису — як текст. */
    clickCls(spec) {
        return spec ? "o_workset_client_click" : "";
    }

    hint(spec) {
        return spec ? _t("Відкрити записи") : false;
    }

    role(spec) {
        return spec ? "button" : false;
    }

    tab(spec) {
        return spec ? "0" : false;
    }

    keyOpen(ev, spec) {
        if (spec && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            ev.stopPropagation();
            this.openList(spec);
        }
    }

    /** «Картка лінії ›» «Прибутковості» — лише коли сервер дав посилання (група «Прибутковість: Перегляд»). */
    openLineCard() {
        const p = this.data.profit;
        if (p && p.link) {
            this.action.doAction({ type: "ir.actions.client", tag: PROFIT_CARD, name: p.line.name, params: { line_id: p.link.line_id, month: p.link.month } });
        }
    }

    openClients() {
        this.action.doAction("workset_client.action_clients");
    }

    /** «Позначити перевіреним» (редактор): сьогодні й поточний користувач — на сервері; потім екран наново. */
    async markChecked() {
        await this.orm.call(PARTNER, "workset_client_mark_checked", [[this.partnerId]]);
        await this.load();
    }

    /** «Заповнити» (редактор): вікно «Договір і оплата» з поточними значеннями; закрили — екран наново. */
    fill() {
        this.action.doAction(
            {
                type: "ir.actions.act_window",
                name: _t("Договір і оплата"),
                res_model: TERMS,
                views: [[false, "form"]],
                target: "new",
                context: { default_partner_id: this.partnerId },
            },
            { onClose: () => this.load() }
        );
    }
}

registry.category("actions").add(CARD, ClientCard);

/** Список «Умови клієнтів»: рядок — екран «Умови клієнта» цього замовника (форма контакту — через смарт-кнопку чи назву на екрані). */
export class ClientListController extends ListController {
    setup() {
        super.setup();
        this.cardAction = useService("action");
    }

    async openRecord(record) {
        return this.cardAction.doAction({ type: "ir.actions.client", tag: CARD, name: record.data.name || "", params: { partner_id: record.resId } });
    }
}

registry.category("views").add("workset_client_list", { ...listView, Controller: ClientListController });
