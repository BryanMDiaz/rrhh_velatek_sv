/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
import { ListRenderer } from "@web/views/list/list_renderer";

class PrepayrollListRenderer extends ListRenderer {
    static rowsTemplate = "rrhh_velatek_sv.PrepayrollLines.Rows";

    createViewKey() {
        return `${super.createViewKey()},prepayroll_incidents`;
    }

    getColumnClass(column) {
        return `${super.getColumnClass(column)} ${column.attrs?.class || ""}`;
    }

    getActiveColumns(list) {
        const columns = super.getActiveColumns(list);
        if (list.evalContext.parent.payment_frequency !== "biweekly") {
            return columns;
        }
        return columns.map((column) =>
            column.name === "period_wage" ? { ...column, label: _t("Salario quincenal") } : column
        );
    }

    getCellTitle(column, record) {
        const details = record.data.incident_cell_details?.[column.name];
        if (details?.length && record.data[column.name]) {
            return details.join(" | ");
        }
        return super.getCellTitle(column, record);
    }

    getCellClass(column, record) {
        const cellClass = super.getCellClass(column, record);
        const sectionClasses = (column.attrs?.class || "").split(/\s+/);
        const isIncidentColumn = record.data.incident_cell_details?.[column.name]?.length ||
            column.name === "viaticos_amount" || (
            column.name !== "loan_deduction_amount" && sectionClasses.some((className) =>
                ["o_prepayroll_hours", "o_prepayroll_absences", "o_prepayroll_adjustments"].includes(className)
            )
        );
        const value = record.data[column.name];
        return isIncidentColumn && typeof value === "number" && value !== 0
            ? `${cellClass} o_prepayroll_incident_value`
            : cellClass;
    }

    get departmentGroups() {
        const groups = new Map();
        for (const record of this.props.list.records) {
            const [id] = record.data.department_id || [false];
            if (!groups.has(id)) {
                groups.set(id, { id, label: record.data.department_name || _t("Sin departamento"), records: [] });
            }
            groups.get(id).records.push(record);
        }
        return [...groups.values()];
    }
}

class PrepayrollLines extends X2ManyField {
    static components = { ...X2ManyField.components, ListRenderer: PrepayrollListRenderer };
}

registry.category("fields").add("velatek_prepayroll_lines", {
    ...x2ManyField,
    component: PrepayrollLines,
});
