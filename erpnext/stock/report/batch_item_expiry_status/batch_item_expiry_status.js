// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Batch Item Expiry Status"] = {
	filters: [
		{
			fieldname: "exp_from_date",
			label: __("Expiry From Date"),
			fieldtype: "Date",
			width: "80",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[1],
			reqd: 1,
		},
		{
			fieldname: "exp_to_date",
			label: __("Expiry To Date"),
			fieldtype: "Date",
			width: "80",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "expiry_in_days",
			label: __("Expiry (In Days) <="),
			fieldtype: "Int",
		},
		{
			fieldname: "item",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
			width: "100",
			get_query: function () {
				return {
					filters: { has_batch_no: 1 },
				};
			},
		},
	],
};
