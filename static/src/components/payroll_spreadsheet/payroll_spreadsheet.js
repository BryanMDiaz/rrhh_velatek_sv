/** @odoo-module **/

import { Component, useState, onWillStart, onWillUpdateProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class PayrollSpreadsheetWidget extends Component {
    static template = "rrhh_velatek_sv.PayrollSpreadsheetWidget";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            companyName: "",
            periodTitle: "",
            currencySymbol: "$",
            isDraft: true,
            preparedBy: "",
            reviewedBy: "",
            approvedBy: "",
            payrollType: "regular",
            departments: [],
            grandTotals: {},
            isLoading: true,
        });

        onWillStart(async () => {
            this.layout = await this.orm.call("hr.payroll", "get_spreadsheet_layout", []);
            await this.loadData();
        });

        onWillUpdateProps(async (nextProps) => {
            await this.loadData(nextProps);
        });
    }

    get activeConfig() {
        const type = this.state.payrollType || "regular";
        return this.layout.columns[type] || this.layout.columns.regular;
    }

    isColVisible(colKey) {
        const cfg = this.activeConfig;
        return (
            cfg.base.includes(colKey) ||
            cfg.devengos.includes(colKey) ||
            cfg.descuentos.includes(colKey) ||
            cfg.pago.includes(colKey)
        );
    }

    get baseColspan() {
        return this.activeConfig.base.length;
    }

    get devengosColspan() {
        return this.activeConfig.devengos.length;
    }

    get descuentosColspan() {
        return this.activeConfig.descuentos.length;
    }

    get pagoColspan() {
        return this.activeConfig.pago.length;
    }

    get totalTableColspan() {
        return 2 + this.baseColspan + this.devengosColspan + this.descuentosColspan + this.pagoColspan;
    }

    isLastInGroup(groupName, colKey) {
        const cols = this.activeConfig[groupName] || [];
        return cols[cols.length - 1] === colKey;
    }

    getHeaderLabel(key) {
        const type = this.state.payrollType || "regular";
        return this.layout.overrides[type]?.[key] || this.layout.labels[key] || "";
    }

    async loadData(nextProps = null) {
        const record = nextProps ? nextProps.record : this.props.record;
        const payrollId = record.resId;

        this.state.payrollType = (record.data && record.data.payroll_type) ? record.data.payroll_type : "regular";

        if (!payrollId) {
            this.state.companyName = record.data.company_id ? record.data.company_id[1] : "";
            this.state.periodTitle = record.data.period_title || "PLANILLA DE SUELDOS";
            this.state.isDraft = (record.data.state === "draft");
            this.setSignatures(record.data);
            this.state.departments = [];
            this.state.grandTotals = this.getEmptyTotals();
            this.state.isLoading = false;
            return;
        }

        try {
            // Read payroll record metadata
            const [payroll] = await this.orm.read("hr.payroll", [payrollId], [
                "name", "company_id", "period_title", "state", "payroll_type",
                "prepared_by_id", "reviewed_by_id", "approved_by_id",
                "prepared_employee_id", "reviewed_employee_id", "approved_employee_id"
            ]);

            this.state.companyName = record.data.company_id ? record.data.company_id[1] : (payroll.company_id ? payroll.company_id[1] : "");
            this.state.periodTitle = record.data.period_title || payroll.period_title || "PLANILLA DE SUELDOS";
            this.state.isDraft = (record.data.state === "draft" || record.data.state === "computed" || payroll.state === "draft" || payroll.state === "computed");
            this.setSignatures(record.data, payroll);
            this.state.payrollType = (record.data && record.data.payroll_type) || (payroll ? payroll.payroll_type : "regular") || "regular";

            // Search read all lines for this payroll
            const fields = [
                "sequence", "employee_id", "department_id", "base_wage", "viaticos_amount",
                "worked_days", "extra_days", "holiday_days", "period_wage", "daily_salary",
                "production_amount", "holiday_amount", "vacation_amount", "overtime_amount",
                "night_overtime_amount", "night_work_amount", "weekly_rest_work_amount", "bonus_amount", "commission_amount",
                "other_income", "total_earned", "isss_employee_amount", "afp_employee_amount",
                "income_tax_amount", "loan_deduction_amount", "other_withholdings_amount", "deduction_amount", "absence_amount",
                "total_deductions", "salary_advance_amount", "payment_amount", "outside_payment_amount", "total_to_settle"
            ];

            const lines = await this.orm.searchRead(
                "hr.payroll.line",
                [["payroll_id", "=", payrollId]],
                fields,
                { order: "department_id, sequence, id" }
            );

            this.processLines(lines);
        } catch (e) {
            console.error("Error loading payroll spreadsheet data:", e);
        } finally {
            this.state.isLoading = false;
        }
    }

    setSignatures(form, saved = {}) {
        for (const role of ["prepared", "reviewed", "approved"]) {
            const employeeField = `${role}_employee_id`;
            const userField = `${role}_by_id`;
            const employee = form[employeeField] ?? saved[employeeField];
            this.state[`${role}By`] = (employee || form[userField] || saved[userField])?.[1] || "";
        }
    }

    getEmptyTotals() {
        return {
            base_wage: 0, viaticos_amount: 0, worked_days: 0, extra_days: 0, holiday_days: 0,
            period_wage: 0, production_amount: 0, holiday_amount: 0, vacation_amount: 0,
            overtime_amount: 0, night_overtime_amount: 0, night_work_amount: 0, weekly_rest_work_amount: 0,
            bonus_amount: 0, commission_amount: 0, other_income: 0, total_earned: 0,
            isss_employee_amount: 0, afp_employee_amount: 0, income_tax_amount: 0,
            loan_deduction_amount: 0, other_withholdings_amount: 0, other_deductions: 0, total_deductions: 0,
            salary_advance_amount: 0, payment_amount: 0, outside_payment_amount: 0, total_to_settle: 0,
        };
    }

    processLines(lines) {
        const deptMap = {};
        const grand = this.getEmptyTotals();

        let globalIndex = 1;
        lines.forEach((line) => {
            const deptName = line.department_id ? line.department_id[1] : "SIN DEPARTAMENTO";
            if (!deptMap[deptName]) {
                deptMap[deptName] = {
                    name: deptName,
                    lines: [],
                    totals: this.getEmptyTotals(),
                };
            }

            const otherDed = (line.deduction_amount || 0) + (line.absence_amount || 0);

            const processedLine = {
                id: line.id,
                displayIndex: globalIndex++,
                sequence: line.sequence || globalIndex,
                employeeName: line.employee_id ? line.employee_id[1] : "",
                base_wage: line.base_wage || 0,
                viaticos_amount: line.viaticos_amount || 0,
                worked_days: line.worked_days || 0,
                extra_days: line.extra_days || 0,
                holiday_days: line.holiday_days || 0,
                period_wage: line.period_wage || 0,
                daily_salary: line.daily_salary || 0,
                production_amount: line.production_amount || 0,
                holiday_amount: line.holiday_amount || 0,
                vacation_amount: line.vacation_amount || 0,
                overtime_amount: line.overtime_amount || 0,
                night_overtime_amount: line.night_overtime_amount || 0,
                night_work_amount: line.night_work_amount || 0,
                weekly_rest_work_amount: line.weekly_rest_work_amount || 0,
                bonus_amount: line.bonus_amount || 0,
                commission_amount: line.commission_amount || 0,
                other_income: line.other_income || 0,
                total_earned: line.total_earned || 0,
                isss_employee_amount: line.isss_employee_amount || 0,
                afp_employee_amount: line.afp_employee_amount || 0,
                income_tax_amount: line.income_tax_amount || 0,
                loan_deduction_amount: line.loan_deduction_amount || 0,
                other_withholdings_amount: line.other_withholdings_amount || 0,
                other_deductions: otherDed,
                total_deductions: line.total_deductions || 0,
                salary_advance_amount: line.salary_advance_amount || 0,
                payment_amount: line.payment_amount || 0,
                outside_payment_amount: line.outside_payment_amount || 0,
                total_to_settle: line.total_to_settle || 0,
            };

            deptMap[deptName].lines.push(processedLine);

            // Accumulate departmental subtotals & grand totals
            const keys = Object.keys(this.getEmptyTotals());
            keys.forEach((k) => {
                const val = processedLine[k] || 0;
                deptMap[deptName].totals[k] += val;
                grand[k] += val;
            });
        });

        this.state.departments = Object.values(deptMap);
        this.state.grandTotals = grand;
    }

    async onLineFieldChange(lineId, fieldName, ev) {
        const val = parseFloat(ev.target.value) || 0.0;
        try {
            await this.orm.write("hr.payroll.line", [lineId], {
                [fieldName]: val,
            });
            await this.loadData();
        } catch (e) {
            console.error(`Error updating ${fieldName}:`, e);
        }
    }

    async onOutsidePaymentChange(lineId, ev) {
        return this.onLineFieldChange(lineId, "outside_payment_amount", ev);
    }

    formatCurrency(val) {
        if (val === undefined || val === null || isNaN(val)) {
            return "$ 0.00";
        }
        const formatted = new Intl.NumberFormat("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(val);
        return `$ ${formatted}`;
    }

    formatNumber(val) {
        if (val === undefined || val === null || isNaN(val)) {
            return "0";
        }
        return val % 1 === 0 ? val.toFixed(0) : val.toFixed(2);
    }
}

export const payrollSpreadsheetWidget = {
    component: PayrollSpreadsheetWidget,
};

registry.category("fields").add("payroll_spreadsheet", payrollSpreadsheetWidget);
