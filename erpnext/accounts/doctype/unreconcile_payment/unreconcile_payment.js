// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Unreconcile Payment", {
	refresh(frm) {
		frm.set_query("voucher_type", function () {
			return {
				filters: {
					name: ["in", ["Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice"]],
				},
			};
		});

		frm.set_query("voucher_no", function (doc) {
			let filters = {
				company: doc.company,
				docstatus: 1,
			};
			if (["Sales Invoice", "Purchase Invoice"].includes(doc.voucher_type)) {
				filters.is_return = 1;
			}
			return { filters: filters };
		});
	},
	get_allocations: function (frm) {
		frm.clear_table("allocations");
		frappe.call({
			method: "get_allocations_from_payment",
			doc: frm.doc,
			callback: function (r) {
				if (r.message) {
					r.message.forEach((x) => {
						frm.add_child("allocations", x);
					});
					frm.refresh_fields();
				}
			},
		});
	},
});
