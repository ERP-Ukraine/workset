/** «З ким говорити» майстра «Переговорити» (workset.rate.talk, слово власника 06.11 «переговорити: значок и с кем»): штатний перемикач
 *  (radio) лише з тими варіантами, для яких у ставки є людина. Список доступних ролей сервер кладе в нехраниме поле форми who_options
 *  («worker,agent,…»); вибір серед них сервер перевіряє ще раз (action_create). Поля немає або воно порожнє — усі варіанти, як у штатного. */
import { registry } from "@web/core/registry";
import { RadioField, radioField } from "@web/views/fields/radio/radio_field";

export class TalkWhoField extends RadioField {
    get items() {
        const all = super.items || [];
        const allowed = String(this.props.record.data.who_options || "").split(",").filter(Boolean);
        return allowed.length ? all.filter(([value]) => allowed.includes(value)) : all;
    }
}

registry.category("fields").add("workset_rates_talk_who", { ...radioField, component: TalkWhoField });
