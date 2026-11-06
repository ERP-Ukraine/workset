/** Полоса підсумків «Ставок» над списком і дошкою: «Працюють N · Сер. на руки · Сер. ціна · Сер. частка WorkSET · Сер. чиста частка ·
 *  Без частки N · Нижче мінімуму N · Тимчасових N» (останнє — лише коли > 0; числа М2 — avg_net, below_min, raises).
 *  Дані — один виклик workset.rate.rates_summary(домен) по поточному пошуку й лівій панелі (searchModel.domain), а не по завантажених рядках.
 *  Перечитується (з паузою 300 мс, щоб серія подій дала один виклик):
 *  - на подію «update» моделі виду (prop model — модель списку чи дошки): її шле Odoo після кожного завантаження (зміна пошуку, фільтра,
 *    панелі, сторінки) і після збереження запису — тобто й після правки ставки в списку;
 *  - коли батьківський рендерер перемальовує полосу з новим доменом (onWillUpdateProps). */
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { debounce } from "@web/core/utils/timing";
import { formatFloat } from "@web/views/fields/formatters";
import { listView } from "@web/views/list/list_view";
import { ListRenderer } from "@web/views/list/list_renderer";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

export class RatesSummary extends Component {
    static template = "workset_rates.RatesSummary";
    static props = { model: { type: Object, optional: true } };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ data: null });
        this.key = null;
        this.reload = debounce(() => this.load(true), 300);
        if (this.props.model && this.props.model.bus) {
            useBus(this.props.model.bus, "update", () => this.reload());
        }
        onWillStart(() => this.load(true));
        onWillUpdateProps(() => this.load(false));
    }

    domain() {
        return this.env.searchModel ? this.env.searchModel.domain : [];
    }

    async load(force) {
        const domain = this.domain();
        const key = JSON.stringify(domain);
        if (!force && key === this.key) {
            return;
        }
        this.key = key;
        this.state.data = await this.orm.call("workset.rate", "rates_summary", [domain]);
    }

    money(value) {
        return formatFloat(value || 0, { digits: [16, 2] });
    }
}

export class RatesListRenderer extends ListRenderer {
    static template = "workset_rates.ListRenderer";
    static components = { ...ListRenderer.components, RatesSummary };
}

export class RatesKanbanRenderer extends KanbanRenderer {
    static template = "workset_rates.KanbanRenderer";
    static components = { ...KanbanRenderer.components, RatesSummary };
}

registry.category("views").add("workset_rates_list", { ...listView, Renderer: RatesListRenderer });
registry.category("views").add("workset_rates_kanban", { ...kanbanView, Renderer: RatesKanbanRenderer });
