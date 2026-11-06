/** «Прибутковість ліній» (М3, круг 2): три екрани за макетами A, B, C — client actions workset_profit.overview («Огляд»),
 *  workset_profit.line_card (картка лінії) і workset_profit.matrix («Лінії × місяці»).
 *  Кожен екран — ОДИН виклик сервера на workset.profit.month (profit_overview / profit_line_card / profit_matrix): числа, кольори тривог,
 *  ряди графіків, геометрію водоспаду й описи списків для кліку рахує сервер (models/calc.py), тут — лише показ і переходи. Графіки —
 *  Chart.js із бандла web (web.chartjs_lib), кольори — змінні Bootstrap теми (світла й темна), своїх кольорів немає. Переходи: плитка
 *  «Огляду», смуга «Куди йде частка», «Відкрити рядки» з клітинки таблиці й кнопка рядка списку «Рядки розрахунку» — картка лінії
 *  (лінія й місяць — у params дії; після перезавантаження сторінки — останні з localStorage користувача). */
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { formatFloat } from "@web/views/fields/formatters";
import { usePopover } from "@web/core/popover/popover_hook";
import { Layout } from "@web/search/layout";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";

const MONTH = "workset.profit.month";
const CARD = "workset_profit.line_card";
const STORE = "workset_profit.card.";

/** Сума в € без копійок: 20667 → «20 667», −438 → «−438» (мінус — типографський). */
export function euros(value) {
    return formatFloat(Math.round(value || 0), { digits: [16, 0] }).replace(/^-/, "−");
}

/** € на годину: 9.78 → «9,78». */
export function perHour(value) {
    return formatFloat(value || 0, { digits: [16, 2] }).replace(/^-/, "−");
}

/** Число без зайвих нулів: 0.4 → «0,4», 2113 → «2 113». */
export function plain(value) {
    return formatFloat(value || 0, { digits: [16, 2], trailingZeros: false }).replace(/^-/, "−");
}

/** «вересень» → «Вересень». */
export function cap(text) {
    const s = String(text || "");
    return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Збережені лінія й місяць картки → чисті: невідоме — порожньо. */
export function cleanCard(saved) {
    const out = { line_id: false, month: false };
    if (saved && typeof saved === "object") {
        if (Number.isInteger(saved.line_id) && saved.line_id > 0) {
            out.line_id = saved.line_id;
        }
        if (typeof saved.month === "string" && /^\d{4}-\d{2}-\d{2}$/.test(saved.month)) {
            out.month = saved.month;
        }
    }
    return out;
}

/** Колір теми з CSS-змінної (немає — запасний). */
function themeColor(el, name, fallback) {
    return (el && getComputedStyle(el).getPropertyValue(name).trim()) || fallback;
}

/** Спільне трьох екранів: формат чисел, відкрити список рядків за описом сервера, відкрити картку лінії. */
class ProfitScreen extends Component {
    static components = { Layout, Dropdown, DropdownItem };
    static props = ["*"];
    static usesChart = true;

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ data: null });
        this.serial = 0;
        this.charts = [];
        this.euros = euros;
        this.perHour = perHour;
        this.plain = plain;
        this.cap = cap;
        this.chartRef = useRef("chart");
        onWillStart(async () => {
            if (this.constructor.usesChart) {
                await loadBundle("web.chartjs_lib");
            }
            await this.load();
        });
        useEffect(
            () => {
                this.renderChart();
                return () => this.destroyCharts();
            },
            () => [this.state.data]
        );
    }

    get data() {
        return this.state.data;
    }

    /** Виклик сервера; відповідь, що запізнилась (користувач уже вибрав інше), відкидається. */
    async fetch(method, args) {
        const serial = ++this.serial;
        const data = await this.orm.call(MONTH, method, args);
        return serial === this.serial ? data : null;
    }

    /** Список за описом сервера: модель, заголовок, домен — число на елементі пораховано по тих самих записах. */
    openList(spec) {
        if (!spec) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: spec.title,
            res_model: spec.model,
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: spec.domain,
            context: {},
            target: "current",
        });
    }

    /** Картка лінії (екран B) за лінією й місяцем. */
    openCard(lineId, month, name) {
        this.action.doAction({ type: "ir.actions.client", tag: CARD, name: name || "", params: { line_id: lineId, month } });
    }

    destroyCharts() {
        for (const chart of this.charts) {
            chart.destroy();
        }
        this.charts = [];
    }

    renderChart() {}

    /** Основа графіків: кольори тексту й сітки — з теми, без анімації, легенди немає. */
    chartBase() {
        const el = this.chartRef.el;
        const text = themeColor(el, "--bs-body-color", undefined);
        const grid = themeColor(el, "--bs-border-color", undefined);
        return {
            text,
            primary: themeColor(el, "--bs-primary", text),
            muted: themeColor(el, "--bs-gray-400", grid),
            gray: themeColor(el, "--bs-gray-500", grid),
            font: getComputedStyle(el).fontFamily,
            axis: { ticks: { color: text }, grid: { color: grid } },
            options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } } },
        };
    }
}

// ——— A «Огляд» ———
export class ProfitOverview extends ProfitScreen {
    static template = "workset_profit.Overview";

    setup() {
        const params = (this.props.action && this.props.action.params) || {};
        this.month = params.month || false;
        super.setup();
    }

    async load() {
        const data = await this.fetch("profit_overview", [this.month]);
        if (data) {
            this.month = data.month.value;
            this.state.data = data;
        }
    }

    setMonth(value) {
        this.month = value;
        this.load();
    }

    alertTone(alert) {
        return alert.tone === "danger" ? "bg-danger" : alert.tone === "warning" ? "bg-warning" : "";
    }

    /** Чип: список рядків; «Звірка» — її текст повідомленням (розбіжність — не зникає сама). */
    onAlert(alert) {
        if (alert.key === "check") {
            this.notification.add(alert.text, { type: alert.state === "ok" ? "success" : "warning", sticky: alert.state !== "ok" });
            return;
        }
        this.openList(alert.open);
    }

    openLine(line) {
        this.openCard(line.id, this.data.month.value, line.name);
    }

    get hasSeries() {
        const s = this.data && this.data.series;
        return Boolean(s && s.total.some((v) => v !== null));
    }

    renderChart() {
        this.destroyCharts();
        const d = this.state.data;
        if (!d || !window.Chart || !this.chartRef.el || !this.hasSeries) {
            return;
        }
        const c = this.chartBase();
        // кожна лінія — тонко сірим, усі лінії разом — жирно кольором бренду (макет A)
        const datasets = d.series.lines.map((l) => ({
            label: l.name,
            data: l.values,
            borderColor: c.muted,
            borderWidth: 1,
            pointRadius: 0,
            tension: 0.3,
        }));
        datasets.push({ label: d.series.total_label, data: d.series.total, borderColor: c.primary, borderWidth: 3, pointRadius: 2, tension: 0.3 });
        this.charts.push(
            new Chart(this.chartRef.el, {
                type: "line",
                data: { labels: d.series.months.map((m) => m.short), datasets },
                options: {
                    ...c.options,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label || ""}: ${perHour(ctx.parsed.y)}` } } },
                    scales: { x: c.axis, y: c.axis },
                },
            })
        );
    }
}

// ——— B «Картка лінії» ———
export class ProfitLineCard extends ProfitScreen {
    static template = "workset_profit.LineCard";

    setup() {
        this.storeKey = STORE + user.userId;
        const params = (this.props.action && this.props.action.params) || {};
        const saved = this.restore();
        this.lineId = params.line_id || saved.line_id;
        this.month = params.month || saved.month;
        super.setup();
    }

    restore() {
        try {
            return cleanCard(JSON.parse(browser.localStorage.getItem(this.storeKey) || "null"));
        } catch {
            return cleanCard(null);
        }
    }

    save() {
        // приватний режим / заповнене сховище — після перезавантаження картка відкриється з першої лінії, сторінка працює далі
        try {
            browser.localStorage.setItem(this.storeKey, JSON.stringify({ line_id: this.lineId, month: this.month }));
        } catch {
            // нічого
        }
    }

    async load() {
        const data = await this.fetch("profit_line_card", [this.lineId || false, this.month || false]);
        if (data) {
            this.month = data.month.value;
            this.lineId = data.line ? data.line.id : false;
            this.state.data = data;
            this.save();
        }
    }

    go(month) {
        if (month) {
            this.month = month.value;
            this.load();
        }
    }

    barStyle(b) {
        return `bottom: ${b.bottom}%; height: ${Math.max(b.height, 0.6)}%;`;
    }

    /** Сума над стовпцем; витрата — під ним (як у макеті B). */
    valueStyle(b) {
        return b.dir === "down" ? `top: ${100 - b.bottom}%;` : `bottom: ${b.bottom + b.height}%;`;
    }

    barClass(b) {
        if (b.key === "net") {
            return b.amount < 0 ? "bg-danger" : "o_workset_profit_fall_net";
        }
        return `o_workset_profit_fall_${b.dir}`;
    }

    renderChart() {
        this.destroyCharts();
        const d = this.state.data;
        if (!d || !d.series || !window.Chart || !this.chartRef.el || !d.series.net.some((v) => v !== null)) {
            return;
        }
        const c = this.chartBase();
        // «Чисто по місяцях»: стовпці — чисто €/год, тонка лінія — частка; число над стовпцем (макет B)
        const values = {
            id: "wsProfitValues",
            afterDatasetsDraw: (chart) => {
                const meta = chart.getDatasetMeta(0);
                const ctx = chart.ctx;
                ctx.save();
                ctx.font = `600 11px ${c.font}`;
                ctx.fillStyle = c.text;
                ctx.textAlign = "center";
                meta.data.forEach((bar, i) => {
                    const v = chart.data.datasets[0].data[i];
                    if (v !== null && v !== undefined) {
                        ctx.fillText(plain(Math.round(v * 10) / 10), bar.x, v >= 0 ? bar.y - 4 : bar.y + 12);
                    }
                });
                ctx.restore();
            },
        };
        this.charts.push(
            new Chart(this.chartRef.el, {
                type: "bar",
                data: {
                    labels: d.series.months.map((m) => m.short),
                    datasets: [
                        { label: d.series.net_label, data: d.series.net, backgroundColor: c.primary, borderRadius: 3, order: 2 },
                        { type: "line", label: d.series.share_label, data: d.series.share, borderColor: c.gray, borderWidth: 1, pointRadius: 0, tension: 0.3, order: 1 },
                    ],
                },
                options: {
                    ...c.options,
                    layout: { padding: { top: 16 } },
                    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label || ""}: ${perHour(ctx.parsed.y)}` } } },
                    scales: { x: c.axis, y: { ...c.axis, beginAtZero: true } },
                },
                plugins: [values],
            })
        );
    }
}

// ——— C «Лінії × місяці» ———
/** Спливаюче вікно клітинки: ланцюжок місяця й «Відкрити рядки» (картка лінії). */
export class ChainPopover extends Component {
    static template = "workset_profit.ChainPopover";
    static props = ["*"];

    open() {
        this.props.close();
        this.props.onOpen();
    }
}

export class ProfitMatrix extends ProfitScreen {
    static template = "workset_profit.Matrix";
    static usesChart = false;

    setup() {
        this.from = false;
        this.to = false;
        this.view = useState({ unit: "hour" });
        this.popover = usePopover(ChainPopover, { position: "bottom" });
        super.setup();
    }

    async load() {
        const data = await this.fetch("profit_matrix", [this.from, this.to]);
        if (data) {
            this.from = data.period.from;
            this.to = data.period.to;
            this.state.data = data;
        }
    }

    setFrom(value) {
        this.from = value;
        this.load();
    }

    setTo(value) {
        this.to = value;
        this.load();
    }

    setUnit(unit) {
        this.view.unit = unit;
    }

    cellClass(cell) {
        return cell.tone === "neg" ? "text-bg-danger" : cell.tone === "drop" ? "text-bg-warning" : "";
    }

    /** Значення клітинки в обраній одиниці: «€ год» — чисто на годину, «€ за місяць» — чисто місяця. */
    cellValue(cell) {
        if (this.view.unit === "hour") {
            return cell.net_ph === null || cell.net_ph === undefined ? "—" : perHour(cell.net_ph);
        }
        return euros(cell.net);
    }

    stepValue(step) {
        return this.view.unit === "hour" ? perHour(step.ph) : euros(step.amount);
    }

    onCell(ev, line, cell) {
        if (!cell.has) {
            return;
        }
        const month = this.data.months.find((m) => m.value === cell.month);
        this.popover.open(ev.currentTarget, {
            title: `${line.name}, ${month ? month.name : ""}`,
            steps: cell.chain,
            unit: this.view.unit === "hour" ? this.data.labels.hour : this.data.labels.month,
            format: (s) => this.stepValue(s),
            onOpen: () => this.openCard(line.id, cell.month, line.name),
        });
    }
}

registry.category("actions").add("workset_profit.overview", ProfitOverview);
registry.category("actions").add("workset_profit.line_card", ProfitLineCard);
registry.category("actions").add("workset_profit.matrix", ProfitMatrix);
