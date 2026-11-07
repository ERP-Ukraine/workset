/** «Прибутковість ліній» (М3, круг 2): три екрани за макетами A, B, C — client actions workset_profit.overview («Огляд»),
 *  workset_profit.line_card (картка лінії) і workset_profit.matrix («Лінії × місяці»).
 *  Кожен екран — ОДИН виклик сервера на workset.profit.month (profit_overview / profit_line_card / profit_matrix): числа, кольори тривог,
 *  ряди графіків, вісь без викидів, порядок плиток, геометрію водоспаду й описи списків для кліку рахує сервер (models/calc.py), тут —
 *  лише показ і переходи. Графіки — Chart.js із бандла web (web.chartjs_lib); кольори графіків — явні (PALETTE, круг 3а: CSS-змінних
 *  Bootstrap Odoo 19 не задає, Chart.js малював своєю палітрою): сірі ряди, бренд — той самий тон, що смуга «чисто» (клас scss), мінус —
 *  червоний; світла чи темна тема — за яскравістю фону. Переходи: плитка
 *  «Огляду», смуга «Куди йде частка», «Відкрити рядки» з клітинки таблиці й кнопка рядка списку «Рядки розрахунку» — картка лінії
 *  (лінія й місяць — у params дії; після перезавантаження сторінки — останні з localStorage користувача). Круг 3в: кожне число відкриває
 *  свої записи — опис 'open' приходить із сервера в тій самій відповіді (openList), точка чи стовпець графіка — місяць (chartPick). */
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
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

/** € на годину: 9.78 → «9,78»; годин зі ставкою немає (null) — «—», не «0,00». */
export function perHour(value) {
    if (value === null || value === undefined) {
        return "—";
    }
    return formatFloat(value, { digits: [16, 2] }).replace(/^-/, "−");
}

/** Є число на годину (одиницю «€ год» показувати лише поруч із числом). */
export function hasPh(value) {
    return value !== null && value !== undefined;
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

/** Ціле число з пробілами тисяч: 9721.72 → «9 722» (години на екрані — цілі). */
export function whole(value) {
    return euros(value);
}

/** Кольори графіків (круг 3а): ряди — нейтральний сірий, що читається на обох темах; бренд — запасний тон смуги «чисто» ($primary
 *  світлої теми; справжній береться з класу смуги — brandOf); мінус — червоний Bootstrap. Текст і сітка — під фон теми. */
export const PALETTE = {
    light: { text: "#4a5058", grid: "#e2e4e8", zero: "#8a8f98", line: "#9aa0ab", brand: "#6b3e66", danger: "#dc3545" },
    dark: { text: "#c3c7ce", grid: "#3a3f48", zero: "#8a8f98", line: "#9aa0ab", brand: "#6b3e66", danger: "#dc3545" },
};

/** «rgb(1, 2, 3)» / «rgba(…)» → [r, g, b]; прозоре чи не колір — null. */
export function rgbOf(css) {
    const m = String(css || "").match(/rgba?\(([^)]+)\)/);
    if (!m) {
        return null;
    }
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.slice(0, 3).some((x) => Number.isNaN(x)) || (p.length > 3 && p[3] === 0)) {
        return null;
    }
    return p.slice(0, 3);
}

/** Темний фон: відносна яскравість < 0,5. */
export function isDark(rgb) {
    return Boolean(rgb) && (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255 < 0.5;
}

/** Фон під елементом: перший непрозорий від el угору (немає — білий). */
function backdrop(el) {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
        const c = rgbOf(getComputedStyle(n).backgroundColor);
        if (c) {
            return c;
        }
    }
    return [255, 255, 255];
}

/** Тон бренду — фон класу смуги «чисто» (scss: $primary свого бандла), щоб лінія й стовпці були того ж кольору, що смуги «Куди йде
 *  частка»; клас не дав кольору — запасний. */
function brandOf(root, fallback) {
    if (!root) {
        return fallback;
    }
    const probe = document.createElement("span");
    probe.className = "o_workset_profit_part_net d-none";
    root.appendChild(probe);
    const rgb = rgbOf(getComputedStyle(probe).backgroundColor);
    probe.remove();
    return rgb ? `rgb(${rgb.join(", ")})` : fallback;
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
        this.state = useState({ data: null, failed: false });
        this.serial = 0;
        this.charts = [];
        this.euros = euros;
        this.perHour = perHour;
        this.hasPh = hasPh;
        this.plain = plain;
        this.whole = whole;
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

    /** Виклик сервера; відповідь, що запізнилась (користувач уже вибрав інше), відкидається. Помилка при першому показі — як у Odoo
     *  (вікно помилки, екран не відкривається); при зміні місяця чи лінії — стандартне повідомлення, на екрані лишаються попередні дані з
     *  позначкою (state.failed) → null. */
    async fetch(method, args) {
        const serial = ++this.serial;
        let data;
        try {
            data = await this.orm.call(MONTH, method, args);
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

    /** Після помилки — вибір екрана (місяць, лінія, період) назад до показаних даних, щоб наступний запит ішов від них. */
    keep() {}

    /** Список за описом сервера: модель, заголовок, домен, контекст (без «Створити») — число на елементі пораховано по тих самих
     *  записах; є res_id — форма одного запису (правило «Хто платить», «Звірка місяця»). */
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

    /** Круг 3в: вигляд «клікабельно» (рука, легке підкреслення при наведенні) — лише в елемента з описом списку; без опису — як текст. */
    clickCls(spec) {
        return spec ? "o_workset_profit_click" : "";
    }

    /** Підказка «Відкрити записи» — лише в елемента, що їх відкриває. */
    hint(spec) {
        return spec ? _t("Відкрити записи") : false;
    }

    /** Клік і наведення на графіку (круг 3в): номер місяця найближчої точки чи стовпця (Chart.js getElementsAtEventForMode, режим «index»
     *  без точного влучання — як підказка) → onPick(i); над полем графіка — рука. */
    chartPick(onPick) {
        const at = (chart, ev) => {
            const els = chart.getElementsAtEventForMode(ev, "index", { intersect: false }, true);
            return els.length ? els[0].index : null;
        };
        return {
            onClick: (ev, _els, chart) => {
                const i = at(chart, ev);
                if (i !== null) {
                    onPick(i);
                }
            },
            onHover: (ev, _els, chart) => {
                chart.canvas.style.cursor = at(chart, ev) === null ? "" : "pointer";
            },
        };
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

    /** Основа графіків: явні кольори (PALETTE за яскравістю фону, бренд — з класу смуги «чисто»), лінія нуля виразніша за сітку, без
     *  анімації; підказка — найближча точка без точного влучання (тонкі лінії без крапок). */
    chartBase() {
        const el = this.chartRef.el;
        const bg = backdrop(el);
        const pal = PALETTE[isDark(bg) ? "dark" : "light"];
        const c = { ...pal, brand: brandOf(el.closest(".o_workset_profit"), pal.brand), font: getComputedStyle(el).fontFamily };
        // підкладка під підпис на графіку — фон картки напівпрозоро (круг 3б: підпис викиду не зливається з лініями)
        c.veil = `rgba(${bg.join(", ")}, 0.85)`;
        // розмір підписів осей Chart.js (за замовчуванням 12 px) — від нього міряються власні підписи
        c.tick = (window.Chart && Chart.defaults.font && Chart.defaults.font.size) || 12;
        c.axis = { ticks: { color: c.text }, grid: { color: c.grid } };
        c.yAxis = { ticks: { color: c.text }, grid: { color: (ctx) => (ctx.tick && ctx.tick.value === 0 ? c.zero : c.grid) } };
        c.options = { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "nearest", intersect: false } };
        return c;
    }

    /** Легенда з готових пунктів [{text, color, width}] (клік нічого не ховає): ряди з однаковим кольором — одним пунктом. */
    legend(c, items) {
        return {
            display: true,
            position: "bottom",
            onClick: () => {},
            labels: {
                color: c.text,
                boxWidth: 14,
                boxHeight: 3,
                generateLabels: () =>
                    items.map((x) => ({ text: x.text, fillStyle: x.color, strokeStyle: x.color, lineWidth: x.width || 0, fontColor: c.text, hidden: false })),
            },
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

    keep(data) {
        this.month = data.month.value;
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

    /** Чип «Власники»: кожна стаття окремо — «AA −100 · BB −200» (умовні коди; R05 власника: «по кожному»). */
    ownerParts(alert) {
        return (alert.parts || []).map((p) => `${p.code} ${euros(p.amount)}`).join(" · ");
    }

    /** «Куди йде частка»: пункти легенди — лише частини з помітним сегментом хоч в одній лінії (сервер: calc.split_legend, круг 3б). */
    get splitKeys() {
        return new Set((this.data && this.data.split_keys) || []);
    }

    /** Частка «чисто» в частці лінії, ціле число %: смуги без «чисто» (чисто ≤ 0) — 0. */
    netPct(line) {
        const p = line.split.find((x) => x.key === "net");
        return p ? Math.round(p.pct) : 0;
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
        const s = d.series;
        const ax = s.axis;
        // викид (calc.chart_axis) — біля краю осі, справжнє число — у підказці й підписі
        const clamp = (v) => (v === null || v === undefined ? null : Math.min(ax.max, Math.max(ax.min, v)));
        // кожна лінія — тонко сірим, усі лінії разом — жирно кольором бренду, зверху (макет A); без згладжування
        const datasets = s.lines.map((l) => ({
            label: l.name,
            data: l.values.map(clamp),
            raw: l.values,
            borderColor: c.line,
            backgroundColor: c.line,
            borderWidth: 1,
            pointRadius: 0,
            pointHitRadius: 6,
            tension: 0,
            order: 1,
        }));
        datasets.push({
            label: s.total_label,
            data: s.total.map(clamp),
            raw: s.total,
            borderColor: c.brand,
            backgroundColor: c.brand,
            borderWidth: 4,
            pointRadius: 2,
            pointHitRadius: 6,
            tension: 0,
            order: 0,
        });
        // підпис викиду (круг 3б): шрифт підписів осі + 1 px, на напівпрозорій підкладці, усередині поля графіка — від верхнього краю
        // вниз, від нижнього вгору (не наїжджає на підписи місяців під віссю), біля правого краю — ліворуч від точки
        const outliers = {
            id: "wsProfitOutliers",
            afterDatasetsDraw: (chart) => {
                const ctx = chart.ctx;
                const area = chart.chartArea;
                const size = c.tick + 1;
                const h = size + 6;
                ctx.save();
                ctx.font = `${size}px ${c.font}`;
                ctx.textAlign = "left";
                ctx.textBaseline = "middle";
                for (const o of ax.out) {
                    const pt = chart.getDatasetMeta(o.s).data[o.i];
                    if (!pt) {
                        continue;
                    }
                    const up = o.value > ax.max;
                    const color = o.value < 0 ? c.danger : c.text;
                    ctx.fillStyle = color;
                    ctx.beginPath();
                    ctx.arc(pt.x, pt.y, 3, 0, 2 * Math.PI);
                    ctx.fill();
                    const w = ctx.measureText(o.text).width;
                    const x = Math.max(area.left + 3, pt.x + 8 + w > area.right ? pt.x - 8 - w : pt.x + 8);
                    const y = Math.min(area.bottom - h / 2 - 3, Math.max(area.top + h / 2 + 3, up ? pt.y + 8 + h / 2 : pt.y - 8 - h / 2));
                    ctx.fillStyle = c.veil;
                    ctx.fillRect(x - 4, y - h / 2, w + 8, h);
                    ctx.fillStyle = color;
                    ctx.fillText(o.text, x, y);
                }
                ctx.restore();
            },
        };
        this.charts.push(
            new Chart(this.chartRef.el, {
                type: "line",
                // підписи осі — сервер (calc.month_axis): з роком, коли ряд зачіпає більше одного року; лише закриті місяці
                data: { labels: s.labels, datasets },
                options: {
                    ...c.options,
                    // клік по місяцю — «Огляд» цього місяця (як вибір місяця вгорі)
                    ...this.chartPick((i) => s.months[i] && this.setMonth(s.months[i].value)),
                    plugins: {
                        legend: this.legend(c, [
                            { text: s.total_label, color: c.brand, width: 4 },
                            { text: s.each_label, color: c.line, width: 1 },
                        ]),
                        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label || ""}: ${perHour(ctx.dataset.raw[ctx.dataIndex])}` } },
                    },
                    scales: { x: c.axis, y: { ...c.yAxis, min: ax.min, max: ax.max } },
                },
                plugins: [outliers],
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

    keep(data) {
        this.month = data.month.value;
        this.lineId = data.line ? data.line.id : false;
    }

    go(month) {
        if (month) {
            this.month = month.value;
            this.load();
        }
    }

    /** Тонкий крок (calc.waterfall: < 2 % частки, круг 3б) — не менше 4 px, щоб було видно; підпис — той самий. */
    barStyle(b) {
        return `bottom: ${b.bottom}%; height: ${Math.max(b.height, 0.6)}%;` + (b.thin ? " min-height: 4px;" : "");
    }

    /** Підпис суми в одному місці в усіх стовпців: плюс — над верхом, мінус — під низом (і «Чисто» нижче нуля теж). */
    valueStyle(b) {
        return b.amount < 0 ? `top: ${100 - b.bottom}%;` : `bottom: ${b.bottom + b.height}%;`;
    }

    /** Пунктир від підсумку після кроку до наступного стовпця (останній — без нього). */
    linkStyle(b) {
        return `bottom: ${b.end}%;`;
    }

    /** Сума кроку: повернення (плюс між часткою й «чисто») — зі знаком «+». */
    stepAmount(b) {
        return b.dir === "up" ? `+${euros(b.amount)}` : euros(b.amount);
    }

    /** Частка й повернення — один колір; витрата — темніше; «Чисто» — бренд, нижче нуля — червоне. */
    barClass(b) {
        if (b.key === "net") {
            return b.amount < 0 ? "bg-danger" : "o_workset_profit_fall_net";
        }
        return b.dir === "down" ? "o_workset_profit_fall_down" : "o_workset_profit_fall_total";
    }

    /** «Рядки за місяць»: у «чисто» (і табелі) — основний список; буфер житла, гроші лінії, фонд — окремою групою нижче. */
    get mainRows() {
        return this.data.rows.filter((r) => r.chain || r.timesheets);
    }

    get asideRows() {
        return this.data.rows.filter((r) => !r.chain && !r.timesheets);
    }

    renderChart() {
        this.destroyCharts();
        const d = this.state.data;
        if (!d || !d.series || !window.Chart || !this.chartRef.el || !d.series.net.some((v) => v !== null)) {
            return;
        }
        const c = this.chartBase();
        // «Чисто по місяцях» (лише місяці з даними лінії): стовпці — чисто €/год кольором бренду, мінус — червоним; тонка сіра лінія —
        // частка; число — поза стовпцем (над плюсом, під мінусом з відступом — не на краю стовпця, круг 3б), місце під нього — запас осі (grace)
        const values = {
            id: "wsProfitValues",
            afterDatasetsDraw: (chart) => {
                const meta = chart.getDatasetMeta(0);
                const ctx = chart.ctx;
                ctx.save();
                ctx.font = `600 12px ${c.font}`;
                ctx.textAlign = "center";
                meta.data.forEach((bar, i) => {
                    const v = chart.data.datasets[0].data[i];
                    if (v !== null && v !== undefined) {
                        ctx.fillStyle = v < 0 ? c.danger : c.text;
                        ctx.textBaseline = v < 0 ? "top" : "bottom";
                        ctx.fillText(plain(Math.round(v * 10) / 10), bar.x, v < 0 ? bar.y + 6 : bar.y - 3);
                    }
                });
                ctx.restore();
            },
        };
        const net = d.series.net;
        this.charts.push(
            new Chart(this.chartRef.el, {
                type: "bar",
                data: {
                    labels: d.series.labels,
                    datasets: [
                        {
                            label: d.series.net_label,
                            data: net,
                            backgroundColor: net.map((v) => (v !== null && v < 0 ? c.danger : c.brand)),
                            borderRadius: 3,
                            maxBarThickness: 40,
                            order: 2,
                        },
                        {
                            type: "line",
                            label: d.series.share_label,
                            data: d.series.share,
                            borderColor: c.line,
                            backgroundColor: c.line,
                            borderWidth: 1,
                            pointRadius: 0,
                            pointHitRadius: 6,
                            tension: 0,
                            order: 1,
                        },
                    ],
                },
                options: {
                    ...c.options,
                    // клік по стовпцю — картка лінії на цей місяць (як стрілки)
                    ...this.chartPick((i) => this.go(d.series.months[i])),
                    interaction: { mode: "index", intersect: false },
                    layout: { padding: { top: 8, bottom: 4 } },
                    plugins: {
                        legend: this.legend(c, [
                            { text: d.series.net_label, color: c.brand },
                            { text: d.series.share_label, color: c.line, width: 1 },
                        ]),
                        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label || ""}: ${perHour(ctx.parsed.y)}` } },
                    },
                    scales: { x: c.axis, y: { ...c.yAxis, beginAtZero: true, grace: "12%" } },
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

    keep(data) {
        this.from = data.period.from;
        this.to = data.period.to;
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

    /** Заливка — лише червоне (чисто < 0) і жовте (падіння); клітинка без годин зі ставкою ('neg_text') — без заливки, червоним текстом. */
    cellClass(cell) {
        return cell.tone === "neg" ? "text-bg-danger" : cell.tone === "drop" ? "text-bg-warning" : cell.tone === "neg_text" ? "text-danger" : "";
    }

    /** Значення клітинки в обраній одиниці (одиниця — у шапці таблиці): «€ год» — чисто на годину, «€ за місяць» — чисто місяця. Годин зі
     *  ставкою немає: «—», а чисто місяця нижче нуля — сума за місяць з одиницею «€/міс», дрібно червоним. */
    cellValue(cell) {
        if (this.view.unit === "hour") {
            if (hasPh(cell.net_ph)) {
                return perHour(cell.net_ph);
            }
            return cell.tone === "neg_text" ? `${euros(cell.net)} ${this.data.labels.month}` : "—";
        }
        return euros(cell.net);
    }

    /** Дрібний шрифт — лише сума за місяць у режимі «€ год» (клітинка без годин зі ставкою). */
    cellSmall(cell) {
        return this.view.unit === "hour" && cell.tone === "neg_text";
    }

    /** Крок ланцюжка з одиницею; годин зі ставкою немає — лише «—». */
    stepValue(step) {
        if (this.view.unit === "hour") {
            return hasPh(step.ph) ? `${perHour(step.ph)} ${this.data.labels.hour}` : "—";
        }
        return `${euros(step.amount)} ${this.data.labels.month}`;
    }

    onCell(ev, line, cell) {
        if (!cell.has) {
            return;
        }
        const month = this.data.months.find((m) => m.value === cell.month);
        this.popover.open(ev.currentTarget, {
            // назва місяця з роком: з 2027 у таблиці можуть бути два січні
            title: `${line.name}, ${month ? month.title : ""}`,
            steps: cell.chain,
            format: (s) => this.stepValue(s),
            onOpen: () => this.openCard(line.id, cell.month, line.name),
        });
    }
}

registry.category("actions").add("workset_profit.overview", ProfitOverview);
registry.category("actions").add("workset_profit.line_card", ProfitLineCard);
registry.category("actions").add("workset_profit.matrix", ProfitMatrix);
