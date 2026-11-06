/** «Огляд ставок» (рівень B, розділ 6б): client action workset_rates.overview — перший пункт меню «Ставки».
 *  Дані — ОДИН виклик workset.rate.rates_overview(домен фільтра, місяців, only_working): плитки, картки ліній, ряди графіків, тривоги,
 *  списки ліній і замовників для фільтра, підписи рядів мовою користувача. Фільтр (лінії, замовник, період, «тільки працюють») — у
 *  localStorage браузера окремо для кожного користувача. Графіки — Chart.js, який уже вантажить web (бандл web.chartjs_lib), кольори —
 *  штатна палітра Odoo й змінні Bootstrap теми (світла й темна). Клік по числу смуги, «пігулці», картці лінії, стовпцю розподілу, сектору
 *  «Хто знайшов» — штатний список за описом, який сервер прислав поруч із числом (open: дія, заголовок, домен, фільтри пошуку; openList
 *  лише виконує його) — число на елементі = число рядків списку; клік по тривозі — список або картка. */
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { getColor, hexToRGBA } from "@web/core/colors/colors";
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
const ALL = "workset_rates.action_rates_all";
// «Працюють без ставки» — останні табелі пар без діючої ставки (штатний список табелів)
const ALERT_ACTIONS = { "workset.rate": ALL, "workset.rate.raise": "workset_rates.action_raises", "account.analytic.line": "hr_timesheet.timesheet_action_all" };

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

    /** Середні смуги пораховано по всіх діючих ставках (не лише по тих, хто працює) — коли «Тільки працюють» вимкнено. */
    allRates(key) {
        return !this.filter.working && ["avg", "below_min", "raises"].includes(key);
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

    /** Сума без зайвих нулів: 3 → «3», 1,5 → «1,5». */
    plain(value) {
        return formatFloat(value || 0, { digits: [16, 2], trailingZeros: false });
    }

    /** Посередники картки лінії одним рядком: «Ім'я −сума €»; суми різні — сума більшості ставок і замовники з іншою сумою
     *  («Ім'я −А € · Замовник −Б €»), а замовників сервер не знає — діапазон «Ім'я −Б…А €». Порожньо, якщо посередників немає. */
    agentText(line) {
        return (line.agents || [])
            .map((a) => {
                let sum;
                if (a.min === a.max) {
                    sum = `−${this.plain(a.max)} €`;
                } else if (a.except && a.except.length) {
                    sum = [`−${this.plain(a.main)} €`, ...a.except.map((e) => `${e.customer} −${this.plain(e.amount)} €`)].join(" · ");
                } else {
                    sum = `−${this.plain(a.min)}…${this.plain(a.max)} €`;
                }
                return a.name ? `${a.name} ${sum}` : sum;
            })
            .join(", ");
    }

    /** Фонд працівників картки лінії: «Фонд −А €»; суми різні (повна / неповна ставка) — «Фонд −Б…А €». */
    fundText(line) {
        const f = line.fund;
        if (!f) {
            return "";
        }
        const sum = f.min === f.max ? `−${this.plain(f.max)} €` : `−${this.plain(f.min)}…${this.plain(f.max)} €`;
        return `${this.data.labels.fund} ${sum}`;
    }

    /** Підпис із сервера з підстановкою %s по черзі: підпис("Частка %s", "5–6") */
    fill(text, ...args) {
        let i = 0;
        return String(text).replace(/%s/g, () => String(args[i++] ?? ""));
    }

    // ——— переходи ———
    /** Єдиний вхід у список ставок: опис від сервера (rates_overview → open) — дія, заголовок вікна, домен (фільтр сторінки вже в ньому),
     *  фільтри пошуку й панель зліва. Тут нічого не додається: число на елементі пораховано по тих самих ставках. */
    async openList(spec) {
        if (!spec) {
            return;
        }
        const act = await this.action.loadAction(spec.action);
        this.action.doAction({ ...act, name: spec.title, domain: spec.domain, context: spec.context });
    }

    openTile(key) {
        return this.openList(this.data.open.tiles[key]);
    }

    /** Стовпець «Розподілу частки»: ставки з часткою стовпця. */
    openBucket(index) {
        const b = this.data.buckets[index];
        return this.openList(b && b.open);
    }

    /** Сектор «Хто знайшов»: ми / лінія / не вказано. */
    openFound(kind) {
        return this.openList(this.data.open.found[kind]);
    }

    /** Картка лінії → ставки, з яких пораховано картку (лінія вибрана в панелі зліва). */
    openLine(line) {
        return this.openList(line.open);
    }

    /** Заголовок тривоги — список; link === false (сервер: немає права читати ці записи, напр. табелі) — не посилання. */
    async openAlert(alert) {
        if (alert.link === false) {
            return;
        }
        const action = await this.action.loadAction(ALERT_ACTIONS[alert.model] || ALL);
        this.action.doAction({ ...action, domain: [["id", "in", alert.ids]], context: {} });
    }

    /** Рядок тривоги: запис; у «Працюють без ставки» — нова ставка цієї пари (форма з працівником і замовником); click === false — без кліку. */
    openRow(alert, row) {
        if (row.click === false) {
            return;
        }
        return row.new ? this.openRecord("workset.rate", false, row.new) : this.openRecord(alert.model, row.id);
    }

    openRecord(model, id, context = {}) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]], target: "current", context });
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
        // клік по елементу графіка відкриває список, курсор-вказівник — лише над елементом
        const clickable = (open) => ({
            onClick: (_ev, elements) => {
                if (elements.length) {
                    open(elements[0].index);
                }
            },
            onHover: (ev, elements) => {
                const el = ev.native && ev.native.target;
                if (el) {
                    el.style.cursor = elements.length ? "pointer" : "default";
                }
            },
        });
        this.charts.push(
            new Chart(this.bucketsRef.el, {
                type: "bar",
                data: {
                    labels: d.buckets.map((b) => b.label),
                    datasets: [{ label: L.people, data: d.buckets.map((b) => b.count), backgroundColor: primary, borderRadius: 3 }],
                },
                options: {
                    ...base,
                    ...clickable((i) => this.openBucket(i)),
                    plugins: { legend: { display: false } },
                    scales: { x: axis, y: { ...axis, beginAtZero: true, ticks: { color: text, precision: 0 } } },
                },
            })
        );
        // «Хто знайшов»: «Не вказано» — нейтральний сірий (змінна теми), щоб не збігатися з «Знайшли ми»; поле порожнє в усіх — підпис у центрі,
        // вписаний у дірку кільця (measureText: найширший рядок і висота блоку — не далі за 0,9 внутрішнього радіуса від центру)
        const kinds = ["us", "line", "none"];
        const total = d.found.us + d.found.line + d.found.none;
        const empty = total > 0 && d.found.us + d.found.line === 0;
        const neutral = css.getPropertyValue("--bs-gray-500").trim() || grid || color(5);
        const centerText = {
            id: "wsFoundEmpty",
            afterDraw: (chart) => {
                if (!empty) {
                    return;
                }
                const a = chart.chartArea;
                const arc = chart.getDatasetMeta(0).data[0];
                const cx = arc ? arc.x : (a.left + a.right) / 2;
                const cy = arc ? arc.y : (a.top + a.bottom) / 2;
                const inner = arc && arc.innerRadius ? arc.innerRadius : Math.min(a.width, a.height) * 0.3;
                const ctx = chart.ctx;
                const rows = this.fill(L.found_empty, total, total).split("\n");
                const font = (px) => `600 ${px}px ${css.fontFamily}`;
                let size = inner / 3;
                ctx.save();
                ctx.font = font(size);
                const wide = Math.max(...rows.map((row) => ctx.measureText(row).width));
                const tall = rows.length * size * 1.3;
                size = Math.max(9, size * Math.min(1, (inner * 0.9) / Math.hypot(wide / 2, tall / 2)));
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillStyle = text || neutral;
                ctx.font = font(size);
                rows.forEach((row, i) => ctx.fillText(row, cx, cy + (i - (rows.length - 1) / 2) * size * 1.3));
                ctx.restore();
            },
        };
        this.charts.push(
            new Chart(this.foundRef.el, {
                type: "doughnut",
                data: {
                    labels: [L.found_us, L.found_line, L.found_none],
                    datasets: [{ data: [d.found.us, d.found.line, d.found.none], backgroundColor: [color(0), color(1), neutral], borderWidth: 0 }],
                },
                options: { ...base, ...clickable((i) => this.openFound(kinds[i])), cutout: "60%" },
                plugins: [centerText],
            })
        );
    }
}

registry.category("actions").add("workset_rates.overview", RatesOverview);
