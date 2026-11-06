/** «Огляд ставок» (рівень B, розділ 6б): client action workset_rates.overview — перший пункт меню «Ставки».
 *  Дані — ОДИН виклик workset.rate.rates_overview(домен фільтра, місяців, only_working): плитки, картки ліній, ряди графіків, тривоги,
 *  списки ліній і замовників для фільтра, підписи рядів мовою користувача. Фільтр (лінії, замовник, період, «тільки працюють») — у
 *  localStorage браузера окремо для кожного користувача. Графіки — Chart.js, який уже вантажить web (бандл web.chartjs_lib), кольори —
 *  штатна палітра Odoo й змінні Bootstrap теми (світла й темна). Клік по плитці, картці чи тривозі — штатний список або картка. */
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { getColor, hexToRGBA } from "@web/core/colors/colors";
import { Domain } from "@web/core/domain";
import { user } from "@web/core/user";
import { formatFloat } from "@web/views/fields/formatters";
import { Layout } from "@web/search/layout";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { CheckboxItem } from "@web/core/dropdown/checkbox_item";
import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";

const RATE = "workset.rate";
const MONTHS = [6, 12, 24];
const STORE = "workset_rates.overview.";
const CURRENT = "workset_rates.action_rates_current";
const ALL = "workset_rates.action_rates_all";
// плитка → дія й фільтри пошуку «Ставок» (search_default_* — знімні фасети) поверх фільтра сторінки; working — завжди «Працюють»,
// решта — «Працюють» лише коли ввімкнено «Тільки працюють» (ті самі ставки, з яких пораховано число)
const TILES = {
    working: { action: CURRENT, ctx: { search_default_current: 1 }, working: true },
    avg: { action: CURRENT, ctx: { search_default_current: 1 } },
    below_min: { action: CURRENT, ctx: { search_default_current: 1, search_default_share_low: 1 } },
    raises: { action: CURRENT, ctx: { search_default_current: 1, search_default_has_raise: 1 } },
    contract_diff: { action: CURRENT, ctx: { search_default_contract_diff: 1 } },
    no_line: { action: ALL, ctx: { search_default_current: 1 }, domain: [["line_id", "=", false]] },
    to_check: { action: ALL, ctx: { search_default_current: 1, search_default_to_check: 1 } },
};
const ALERT_ACTIONS = { "workset.rate": ALL, "workset.rate.raise": "workset_rates.action_raises" };

export function defaultFilter() {
    return { lines: [], customer: 0, months: 12, working: true };
}

/** Збережений фільтр → чистий: невідоме й зіпсоване — за замовчуванням. */
export function cleanFilter(saved) {
    const out = defaultFilter();
    if (!saved || typeof saved !== "object") {
        return out;
    }
    if (Array.isArray(saved.lines)) {
        out.lines = saved.lines.filter((id) => Number.isInteger(id) && id > 0);
    }
    if (Number.isInteger(saved.customer) && saved.customer > 0) {
        out.customer = saved.customer;
    }
    if (MONTHS.includes(saved.months)) {
        out.months = saved.months;
    }
    if (typeof saved.working === "boolean") {
        out.working = saved.working;
    }
    return out;
}

export class RatesOverview extends Component {
    static template = "workset_rates.RatesOverview";
    static components = { Layout, Dropdown, DropdownItem, CheckboxItem };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.months = MONTHS;
        this.storeKey = STORE + user.userId;
        this.filter = useState(this.restore());
        this.state = useState({ data: null, byLine: false });
        this.serial = 0;
        this.charts = [];
        this.historyRef = useRef("history");
        this.bucketsRef = useRef("buckets");
        this.foundRef = useRef("found");
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(
            () => {
                this.renderCharts();
                return () => this.destroyCharts();
            },
            () => [this.state.data, this.state.byLine]
        );
    }

    // ——— фільтр ———
    restore() {
        try {
            return cleanFilter(JSON.parse(browser.localStorage.getItem(this.storeKey) || "null"));
        } catch {
            return defaultFilter();
        }
    }

    save() {
        // приватний режим / заповнене сховище — фільтр просто не запам'ятається, сторінка працює далі
        try {
            browser.localStorage.setItem(this.storeKey, JSON.stringify({ ...this.filter, lines: [...this.filter.lines] }));
        } catch {
            // нічого
        }
    }

    domain() {
        const d = [];
        if (this.filter.lines.length) {
            d.push(["line_id", "in", [...this.filter.lines]]);
        }
        if (this.filter.customer) {
            d.push(["customer_id", "=", this.filter.customer]);
        }
        return d;
    }

    async load() {
        const serial = ++this.serial;
        const data = await this.orm.call(RATE, "rates_overview", [this.domain(), this.filter.months], { only_working: this.filter.working });
        if (serial !== this.serial) {
            return;
        }
        // збережені лінії, яких уже немає (архів), — з фільтра геть і ще раз: інакше сторінка порожня з «Лінії: 1»
        // так само замовник без діючих ставок (його немає у списку вибору)
        const known = new Set(data.options.lines.map((l) => l.id));
        const keep = this.filter.lines.filter((id) => known.has(id));
        const customerGone = this.filter.customer && !data.options.customers.some((c) => c.id === this.filter.customer);
        if (keep.length !== this.filter.lines.length || customerGone) {
            this.filter.lines.splice(0, this.filter.lines.length, ...keep);
            if (customerGone) {
                this.filter.customer = 0;
            }
            this.save();
            return this.load();
        }
        this.state.data = data;
    }

    async changed() {
        this.save();
        await this.load();
    }

    toggleLine(id) {
        const i = this.filter.lines.indexOf(id);
        if (i >= 0) {
            this.filter.lines.splice(i, 1);
        } else {
            this.filter.lines.push(id);
        }
        this.changed();
    }

    allLines() {
        this.filter.lines.splice(0);
        this.changed();
    }

    onCustomer(ev) {
        this.filter.customer = parseInt(ev.target.value, 10) || 0;
        this.changed();
    }

    setMonths(m) {
        this.filter.months = m;
        this.changed();
    }

    toggleWorking() {
        this.filter.working = !this.filter.working;
        this.changed();
    }

    setByLine(value) {
        this.state.byLine = value;
    }

    // ——— показ ———
    get data() {
        return this.state.data;
    }

    get options() {
        return (this.state.data && this.state.data.options) || { lines: [], customers: [] };
    }

    get hasAlerts() {
        const d = this.state.data;
        return Boolean(d && (d.check || d.alerts.some((a) => a.count)));
    }

    /** Плитка пораховано по всіх діючих ставках (не лише по тих, хто працює) — коли «Тільки працюють» вимкнено. */
    allRates(key) {
        return !this.filter.working && ["avg", "below_min", "raises"].includes(key);
    }

    /** Колір плитки середньої частки за порогами (сервер: share_tone / net_tone) — лише класи тексту Bootstrap. */
    toneClass(tone) {
        return { low: "text-danger", good: "text-success" }[tone] || "";
    }

    money(value) {
        return formatFloat(value || 0, { digits: [16, 2] });
    }

    monthLabel(day) {
        return luxon.DateTime.fromISO(day).toFormat("LLL yy");
    }

    dateLabel(day) {
        return day ? luxon.DateTime.fromISO(day).toFormat("dd.MM") : "";
    }

    // ——— переходи ———
    async openTile(key) {
        const t = TILES[key] || TILES.working;
        const action = await this.action.loadAction(t.action);
        const context = { ...t.ctx };
        if (t.working || this.filter.working) {
            context.searchpanel_default_work_group = "g1_work";
        }
        this.action.doAction({ ...action, domain: Domain.and([this.domain(), t.domain || []]).toList(), context });
    }

    async openLine(line) {
        const action = await this.action.loadAction(CURRENT);
        const context = { search_default_current: 1, searchpanel_default_line_id: line.id };
        if (this.filter.working) {
            context.searchpanel_default_work_group = "g1_work";
        }
        const domain = this.filter.customer ? [["customer_id", "=", this.filter.customer]] : [];
        this.action.doAction({ ...action, domain, context });
    }

    async openAlert(alert) {
        const action = await this.action.loadAction(ALERT_ACTIONS[alert.model] || ALL);
        this.action.doAction({ ...action, domain: [["id", "in", alert.ids]], context: {} });
    }

    openRecord(model, id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]], target: "current" });
    }

    openCheck() {
        this.openRecord("workset.rate.check", this.state.data.check.id);
    }

    // ——— графіки ———
    destroyCharts() {
        for (const chart of this.charts) {
            chart.destroy();
        }
        this.charts = [];
    }

    renderCharts() {
        this.destroyCharts();
        const d = this.state.data;
        if (!d || !window.Chart || !this.historyRef.el) {
            return;
        }
        const scheme = cookie.get("color_scheme");
        const color = (i) => getColor(i, scheme, "odoo");
        const css = getComputedStyle(this.historyRef.el);
        const text = css.getPropertyValue("--bs-body-color").trim() || undefined;
        const grid = css.getPropertyValue("--bs-border-color").trim() || undefined;
        const primary = css.getPropertyValue("--bs-primary").trim() || color(0);
        const axis = { ticks: { color: text }, grid: { color: grid } };
        const base = {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { position: "bottom", labels: { color: text, boxWidth: 12 } } },
        };
        const line = (i) => ({ borderColor: color(i), backgroundColor: hexToRGBA(color(i), 0.2), tension: 0.3, pointRadius: 2 });
        const labels = d.series.days.map((day) => this.monthLabel(day));
        const L = d.labels;
        let datasets;
        if (this.state.byLine) {
            datasets = d.series.by_line.map((l, i) => ({ label: l.name, data: l.share, ...line(i) }));
        } else {
            const total = d.series.total;
            datasets = [
                { label: L.wage, data: total.map((p) => p.avg_wage), ...line(0) },
                { label: L.price, data: total.map((p) => p.avg_price), ...line(1) },
                { label: L.share, data: total.map((p) => p.avg_share), ...line(2) },
                { label: L.net, data: total.map((p) => p.avg_net), ...line(3) },
                { label: L.count, data: total.map((p) => p.count), yAxisID: "y1", borderDash: [4, 4], ...line(4) },
            ];
        }
        this.charts.push(
            new Chart(this.historyRef.el, {
                type: "line",
                data: { labels, datasets },
                options: {
                    ...base,
                    scales: {
                        x: axis,
                        y: { ...axis, beginAtZero: true },
                        y1: { ...axis, beginAtZero: true, position: "right", display: !this.state.byLine, grid: { drawOnChartArea: false } },
                    },
                },
            })
        );
        this.charts.push(
            new Chart(this.bucketsRef.el, {
                type: "bar",
                data: {
                    labels: d.buckets.map((b) => b.label),
                    datasets: [{ label: L.people, data: d.buckets.map((b) => b.count), backgroundColor: primary, borderRadius: 3 }],
                },
                options: { ...base, plugins: { legend: { display: false } }, scales: { x: axis, y: { ...axis, beginAtZero: true, ticks: { color: text, precision: 0 } } } },
            })
        );
        this.charts.push(
            new Chart(this.foundRef.el, {
                type: "doughnut",
                data: {
                    labels: [L.found_us, L.found_line, L.found_none],
                    datasets: [{ data: [d.found.us, d.found.line, d.found.none], backgroundColor: [color(0), color(1), color(5)], borderWidth: 0 }],
                },
                options: { ...base, cutout: "60%" },
            })
        );
    }
}

registry.category("actions").add("workset_rates.overview", RatesOverview);
