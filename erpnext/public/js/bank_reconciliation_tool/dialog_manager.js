frappe.provide("erpnext.accounts.bank_reconciliation");

erpnext.accounts.bank_reconciliation.DialogManager = class DialogManager {
	constructor(
		company,
		bank_account,
		bank_statement_from_date,
		bank_statement_to_date,
		filter_by_reference_date,
		from_reference_date,
		to_reference_date
	) {
		this.bank_account = bank_account;
		this.company = company;
		this.make_dialog();
		this.bank_statement_from_date = bank_statement_from_date;
		this.bank_statement_to_date = bank_statement_to_date;
		this.filter_by_reference_date = filter_by_reference_date;
		this.from_reference_date = from_reference_date;
		this.to_reference_date = to_reference_date;
	}
	show_dialog(bank_transaction_name, update_dt_cards) {
		this.bank_transaction_name = bank_transaction_name;
		this.update_dt_cards = update_dt_cards;
		frappe.call({
			method: "frappe.client.get_value",
			args: {
				doctype: "Bank Transaction",
				filters: { name: this.bank_transaction_name },
				fieldname: [
					"date",
					"deposit",
					"withdrawal",
					"currency",
					"description",
					"name",
					"bank_account",
					"company",
					"reference_number",
					"party_type",
					"party",
					"unallocated_amount",
					"allocated_amount",
					"transaction_type",
				],
			},
			callback: (r) => {
				if (r.message) {
					this.bank_transaction = r.message;
					r.message.payment_entry = 1;
					r.message.journal_entry = 1;
					this.dialog.set_values(r.message);
					this.dialog.fields_dict.posting_date.value = r.message.date
					this.dialog.fields_dict.reference_date.value = r.message.date

					if (r.message.deposit > 1) {
						this.dialog.fields_dict.party_type.set_value("Customer")
					}
					this.copy_data_to_voucher();
					this.dialog.show();
					// this.dialog.refresh();
				}
			},
		});
	}

	copy_data_to_voucher() {
		let copied = {
			reference_number: this.bank_transaction.reference_number || this.bank_transaction.description,
			posting_date: this.bank_transaction.date,
			reference_date: this.bank_transaction.date,
			mode_of_payment: this.bank_transaction.transaction_type,
		};
		this.dialog.set_values(copied);
	}

	get_linked_vouchers(document_types) {
		frappe.call({
			method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.get_linked_payments",
			args: {
				bank_transaction_name: this.bank_transaction_name,
				document_types: document_types,
				from_date: this.bank_statement_from_date,
				to_date: this.bank_statement_to_date,
				filter_by_reference_date: this.filter_by_reference_date,
				from_reference_date: this.from_reference_date,
				to_reference_date: this.to_reference_date,
			},

			callback: (result) => {
				const data = result.message;

				if (data && data.length > 0) {
					const proposals_wrapper = this.dialog.fields_dict.payment_proposals.$wrapper;
					proposals_wrapper.show();
					this.dialog.fields_dict.no_matching_vouchers.$wrapper.hide();
					this.data = data.map((row) => this.format_row(row));
					this.get_dt_columns();
					this.get_datatable(proposals_wrapper);
				} else {
					const proposals_wrapper = this.dialog.fields_dict.payment_proposals.$wrapper;
					proposals_wrapper.hide();
					this.dialog.fields_dict.no_matching_vouchers.$wrapper.show();
				}
				this.dialog.show();
			},
		});
	}

	get_dt_columns() {
		this.columns = [
			{
				name: __("Document Type"),
				editable: false,
				width: 125,
			},
			{
				name: __("Document Name"),
				editable: false,
				width: 125,
				format: (value, row) => {
					return frappe.form.formatters.Link(value, { options: row[2].content });
				},
			},
			{
				name: __("Reference Date"),
				editable: false,
				width: 120,
				format: frappe.form.formatters.Date,
			},
			{
				name: __("Remaining"),
				editable: false,
				width: 100,
				// format: (value) => {
				// 	if (value){
				// 		return "<div style='text-align:left;'>" + "$ " + value.toFixed(2) + "</div>";
				// 	}	else {
				// 		return "<div style='text-align:left;'>" + "$ " + value + "</div>";
				// 	}
					
				// }
			},
			{
				name: __("Reference Number"),
				editable: false,
				width: 200,
			},
			{
				name: __("Party"),
				editable: false,
				width: 100,
			},
		];
	}

	format_row(row) {
		return [
			row["doctype"],
			row["name"],
			row["reference_date"] || row["posting_date"],
			format_currency(row["paid_amount"], row["currency"]),
			row["reference_no"],
			row["party"],
		];
	}

	get_datatable(proposals_wrapper) {
		if (!this.datatable) {
			const datatable_options = {
				columns: this.columns,
				data: this.data,
				dynamicRowHeight: true,
				checkboxColumn: true,
				inlineFilters: true,
			};
			this.datatable = new frappe.DataTable(proposals_wrapper.get(0), datatable_options);
		} else {
			this.datatable.refresh(this.data, this.columns);
			this.datatable.rowmanager.checkMap = [];
		}
	}

	make_dialog() {
		const me = this;
		me.selected_payment = null;

		const fields = [
			{
				label: __("Action"),
				fieldname: "action",
				fieldtype: "Select",
				options: `Match Against Voucher\nCreate Voucher\nUpdate Bank Transaction`,
				default: "Match Against Voucher",
			},
			{
				fieldname: "column_break_4",
				fieldtype: "Column Break",
			},
			{
				label: __("Document Type"),
				fieldname: "document_type",
				fieldtype: "Select",
				options: `Payment Entry\nJournal Entry`,
				default: "Payment Entry",
				depends_on: "eval:doc.action=='Create Voucher'",
			},
			{
				fieldtype: "Section Break",
				fieldname: "section_break_1",
				label: __("Filters"),
				depends_on: "eval:doc.action=='Match Against Voucher'",
			},
			// {
			// 	fieldname: "column_break_5",
			// 	fieldtype: "Column Break",
			// },
			// {
			// 	fieldtype: "Check",
			// 	label: "Sales Invoice",
			// 	fieldname: "sales_invoice",
			// 	onchange: () => this.update_options(),
			// },
			// {
			// 	fieldtype: "Check",
			// 	label: "Purchase Invoice",
			// 	fieldname: "purchase_invoice",
			// 	onchange: () => this.update_options(),
			// },
		];

		frappe.call({
			method: "erpnext.accounts.doctype.bank_transaction.bank_transaction.get_doctypes_for_bank_reconciliation",
			callback: (r) => {
				$.each(r.message, (_i, entry) => {
					if (_i % 3 == 0) {
						fields.push({
							fieldtype: "Column Break",
						});
					}
					fields.push({
						fieldtype: "Check",
						label: entry,
						fieldname: frappe.scrub(entry),
						onchange: () => this.update_options(),
					});
				});

				fields.push(...this.get_voucher_fields());

				me.dialog = new frappe.ui.Dialog({
					title: __("Reconcile the Bank Transaction"),
					fields: fields,
					size: "large",
					primary_action: (values) => this.reconciliation_dialog_primary_action(values),
				});
			},
		});
	}

	get_voucher_fields() {
		return [
			// {
			// 	fieldtype: "Check",
			// 	label: "Loan Disbursement",
			// 	fieldname: "loan_disbursement",
			// 	onchange: () => this.update_options(),
			// },
			// {
			// 	fieldname: "column_break_5",
			// 	fieldtype: "Column Break",
			// },
			{
				fieldtype: "Check",
				label: "Bank Transaction",
				fieldname: "bank_transaction",
				onchange: () => this.update_options(),
			},
			{
				fieldtype: "Check",
				label: "Show Only Exact Amount",
				fieldname: "exact_match",
				onchange: () => this.update_options(),
				default: 1
			},
			{
				fieldtype: "Section Break",
				fieldname: "section_break_1",
				label: __("Select Vouchers to Match"),
				depends_on: "eval:doc.action=='Match Against Voucher'",
			},
			{
				fieldtype: "HTML",
				fieldname: "payment_proposals",
			},
			{
				fieldtype: "HTML",
				fieldname: "no_matching_vouchers",
				options: __('<div class="text-muted text-center">{0}</div>', [
					__("No Matching Vouchers Found"),
				]),
			},
			{
				fieldtype: "Section Break",
				fieldname: "details",
				label: "Details",
				depends_on: "eval:doc.action!='Match Against Voucher'",
			},
			{
				fieldname: "reference_number",
				fieldtype: "Data",
				label: "Reference Number",
				mandatory_depends_on: "eval:doc.action=='Create Voucher'",
			},
			{
				default: "Today",
				fieldname: "posting_date",
				fieldtype: "Date",
				label: "Posting Date",
				reqd: 1,
				depends_on: "eval:doc.action=='Create Voucher'",
			},
			{
				fieldname: "reference_date",
				fieldtype: "Date",
				label: "Cheque/Reference Date",
				mandatory_depends_on: "eval:doc.action=='Create Voucher'",
				depends_on: "eval:doc.action=='Create Voucher'",
				reqd: 1,
			},
			{
				fieldname: "mode_of_payment",
				fieldtype: "Link",
				label: "Mode of Payment",
				options: "Mode of Payment",
				depends_on: "eval:doc.action=='Create Voucher'",
			},
			{
				fieldname: "user_remark",
				fieldtype: "Data",
				label: "User Remark",
				depends_on: "eval:doc.action=='Create Voucher' && doc.document_type=='Journal Entry'",
			},
			{
				fieldname: "edit_in_full_page",
				fieldtype: "Button",
				label: "Edit in Full Page",
				click: () => {
					this.edit_in_full_page();
				},
				depends_on: "eval:doc.action=='Create Voucher'",
			},
			{
				fieldname: "column_break_7",
				fieldtype: "Column Break",
			},
			{
				default: "Bank Entry",
				fieldname: "journal_entry_type",
				fieldtype: "Select",
				label: "Journal Entry Type",
				options:
					"Journal Entry\nInter Company Journal Entry\nBank Entry\nCash Entry\nCredit Card Entry\nDebit Note\nCredit Note\nContra Entry\nExcise Entry\nWrite Off Entry\nOpening Entry\nDepreciation Entry\nExchange Rate Revaluation\nDeferred Revenue\nDeferred Expense",
				depends_on: "eval:doc.action=='Create Voucher' &&  doc.document_type=='Journal Entry'",
				mandatory_depends_on:
					"eval:doc.action=='Create Voucher' &&  doc.document_type=='Journal Entry'",
			},
			{
				fieldname: "second_account",
				fieldtype: "Link",
				label: "Account",
				options: "Account",
				depends_on: "eval:doc.action=='Create Voucher' &&  doc.document_type=='Journal Entry'",
				mandatory_depends_on:
					"eval:doc.action=='Create Voucher' &&  doc.document_type=='Journal Entry'",
				get_query: () => {
					return {
						filters: {
							is_group: 0,
							company: this.company,
						},
					};
				},
				onchange: function (values) {
					if(cur_dialog.fields_dict.second_account.value){
						frappe.call({
							method:"frappe.client.get_value",
							args: {
								doctype:"Account",
								filters: {
									name: cur_dialog.fields_dict.second_account.value
								},
								fieldname:["account_type", "account_currency"]
							},
							async: false,
							callback: function(q) { 
								if (q.message.account_type != "Receivable" && q.message.account_type != "Payable") {
									cur_dialog.set_df_property('party','hidden',1)
									cur_dialog.set_value('party','')
									cur_dialog.set_df_property('party_type','hidden',1)
									cur_dialog.set_value('party_type','')
								} else {
									cur_dialog.set_df_property('party','hidden',0)
									cur_dialog.set_df_property('party_type','hidden',0)
								}
								frappe.call({
									method: 'frappe.client.get_value',
									args: {
									doctype: 'Bank Account',
									name: cur_frm.doc.bank_account,
									fieldname: 'account',
									async: false,
									},
									callback: function(r){
										frappe.call({
											method: 'frappe.client.get_value',
											args: {
											doctype: 'Account',
											name: r.message.account,
											fieldname: 'account_currency',
											async: false,
											},
											callback: function(r){
												if(q.message.account_currency != r.message.account_currency){
													cur_dialog.set_value('multi_currency',1)
													cur_dialog.set_df_property('multi_currency','hidden',0)
												} else{
													cur_dialog.set_value('multi_currency',0)
													cur_dialog.set_df_property('multi_currency','hidden',1)
												}
											}
										});
									}
								});

							}
						})
					}
				},
			},
			{
				fieldname: "multi_currency",
				fieldtype: "Check",
				label: "Multi Currency",
				depends_on:"eval:doc.action=='Create Voucher' && doc.document_type=='Journal Entry'",
			},
			{
				fieldname: "party_type",
				fieldtype: "Link",
				label: "Party Type",
				options: "DocType",
				mandatory_depends_on:
					"eval:doc.action=='Create Voucher' &&  doc.document_type=='Payment Entry'",
				get_query: function () {
					return {
						filters: {
							name: ["in", Object.keys(frappe.boot.party_account_types)],
						},
					};
				},
			},
			{
				fieldname: "party",
				fieldtype: "Dynamic Link",
				label: "Party",
				options: "party_type",
				mandatory_depends_on:
					"eval:doc.action=='Create Voucher' && doc.document_type=='Payment Entry'",
				onchange: function (values) {
					if (cur_dialog){
						if (cur_dialog.fields_dict.action.value == "Create Voucher" && cur_dialog.fields_dict.document_type.value == "Payment Entry" && values.party_type == "Customer" && values.party) {
							frappe.call({
									method: 'frappe.client.get_value',
									args: {
									doctype: 'Customer',
									name: values.party,
									fieldname: 'cost_center',
									async: false,
								},
								callback: function(r){
								}
							});
						}
					}
				}
			},
			{
				fieldname: "je_cost_center",
				fieldtype: "Link",
				options: "Cost Center",
				label: "Cost Center",
				depends_on: "eval:doc.action=='Create Voucher' && doc.document_type=='Journal Entry'"
			},
			{
				fieldname: "project",
				fieldtype: "Link",
				label: "Project",
				options: "Project",
				depends_on: "eval:doc.action=='Create Voucher' && doc.document_type=='Payment Entry'",
			},
			{
				fieldname: "cost_center",
				fieldtype: "Link",
				label: "Cost Center",
				options: "Cost Center",
				depends_on: "eval:doc.action=='Create Voucher' && doc.document_type=='Payment Entry'",
				get_query: () => {
					return {
						filters: {
							is_group: 0,
							company: this.company,
						},
					};
				},
			},
			{
				fieldtype: "Section Break",
				fieldname: "details_section",
				label: "Transaction Details",
			},
			{
				fieldname: "date",
				fieldtype: "Date",
				label: "Date",
				read_only: 1,
			},
			{
				fieldname: "deposit",
				fieldtype: "Currency",
				label: "Deposit",
				options: "account_currency",
				read_only: 1,
			},
			{
				fieldname: "withdrawal",
				fieldtype: "Currency",
				label: "Withdrawal",
				options: "account_currency",
				read_only: 1,
			},
			{
				fieldname: "column_break_17",
				fieldtype: "Column Break",
				read_only: 1,
			},
			{
				fieldname: "description",
				fieldtype: "Small Text",
				label: "Description",
				read_only: 1,
			},
			{
				fieldname: "allocated_amount",
				fieldtype: "Currency",
				label: "Allocated Amount",
				options: "account_currency",
				read_only: 1,
			},
			{
				fieldname: "unallocated_amount",
				fieldtype: "Currency",
				label: "Unallocated Amount",
				options: "account_currency",
				read_only: 1,
			},
			{
				fieldname: "account_currency",
				fieldtype: "Link",
				label: "Currency",
				options: "Currency",
				read_only: 1,
				hidden: 1,
			},
		];
	}

	get_selected_attributes() {
		let selected_attributes = [];
		this.dialog.$wrapper.find(".checkbox input").each((i, col) => {
			if ($(col).is(":checked")) {
				selected_attributes.push($(col).attr("data-fieldname"));
			}
		});

		return selected_attributes;
	}

	update_options() {
		let selected_attributes = this.get_selected_attributes();
		this.get_linked_vouchers(selected_attributes);
	}

	reconciliation_dialog_primary_action(values) {
		if (values.action == "Match Against Voucher") this.match(values);
		if (values.action == "Create Voucher" && values.document_type == "Payment Entry")
			this.add_payment_entry(values);
		if (values.action == "Create Voucher" && values.document_type == "Journal Entry")
			this.add_journal_entry(values);
		else if (values.action == "Update Bank Transaction") this.update_transaction(values);
	}

	match() {
		var selected_map = this.datatable.rowmanager.checkMap;
		let rows = [];
		selected_map.forEach((val, index) => {
			if (val == 1) rows.push(this.datatable.datamanager.rows[index]);
		});
		let vouchers = [];
		rows.forEach((x) => {
			vouchers.push({
				payment_doctype: x[2].content,
				payment_name: x[3].content,
				amount: x[5].content,
			});
		});
		frappe.call({
			method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.reconcile_vouchers",
			args: {
				bank_transaction_name: this.bank_transaction.name,
				vouchers: vouchers,
			},
			callback: (response) => {
				const alert_string = __("Bank Transaction {0} Matched", [this.bank_transaction.name]);
				frappe.show_alert(alert_string);
				this.update_dt_cards(response.message);
				this.dialog.hide();
			},
		});
	}

	add_payment_entry(values) {
		frappe.call({
			method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_payment_entry_bts",
			args: {
				bank_transaction_name: this.bank_transaction.name,
				reference_number: values.reference_number,
				reference_date: values.reference_date,
				party_type: values.party_type,
				party: values.party,
				posting_date: values.posting_date,
				mode_of_payment: values.mode_of_payment,
				project: values.project,
				cost_center: values.cost_center,
			},
			callback: (response) => {
				const alert_string = __("Bank Transaction {0} added as Payment Entry", [
					this.bank_transaction.name,
				]);
				frappe.show_alert(alert_string);
				this.update_dt_cards(response.message);
				this.dialog.hide();
				if (values.party && values.party_type){
					this.route_to_payment_reconcile(values.party,values.party_type)
				}
				
			},
		});
	}
	route_to_payment_reconcile(party, party_type) {
		frappe.open_in_new_tab = true;
		frappe.set_route("Form", "Payment Reconciliation", {
			party: party,
			party_type: party_type
		});
	}
	add_journal_entry(values) {
		frappe.call({
			method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_journal_entry_bts",
			args: {
				bank_transaction_name: this.bank_transaction.name,
				reference_number: values.reference_number,
				reference_date: values.reference_date,
				party_type: values.party_type,
				party: values.party,
				cost_center: values.je_cost_center,
				posting_date: values.posting_date,
				mode_of_payment: values.mode_of_payment,
				user_remark: values.user_remark,
				entry_type: values.journal_entry_type,
				second_account: values.second_account,
				multi_currency: values.multi_currency
			},
			callback: (response) => {
				const alert_string = __("Bank Transaction {0} added as Journal Entry", [
					this.bank_transaction.name,
				]);
				frappe.show_alert(alert_string);
				this.update_dt_cards(response.message);
				this.dialog.hide();
				if (values.party && values.party_type && values.party.length > 0){
					this.route_to_payment_reconcile(values.party,values.party_type)
				}
			},
		});
	}

	update_transaction(values) {
		frappe.call({
			method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.update_bank_transaction",
			args: {
				bank_transaction_name: this.bank_transaction.name,
				reference_number: values.reference_number,
				party_type: values.party_type,
				party: values.party,
			},
			callback: (response) => {
				const alert_string = __("Bank Transaction {0} updated", [this.bank_transaction.name]);
				frappe.show_alert(alert_string);
				this.update_dt_cards(response.message);
				this.dialog.hide();
			},
		});
	}

	edit_in_full_page() {
		const values = this.dialog.get_values(true);

		if (values.document_type == "Journal Entry") {
			if (!values.second_account) {
				frappe.msgprint("Please set the <b>account</b> before continuing.");
				return;
			}
		} else if (values.document_type == "Payment Entry") {
			if (!values.party_type || !values.party) {
				let missingFields = !values.party_type ? "party type" : "party";
				frappe.msgprint(`Please set the <b>${missingFields}</b> before continuing.`);
				return;
			}
		}
		
		if (values.document_type == "Payment Entry") {
			frappe.call({
				method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_payment_entry_bts",
				args: {
					bank_transaction_name: this.bank_transaction.name,
					reference_number: values.reference_number,
					reference_date: values.reference_date,
					party_type: values.party_type,
					party: values.party,
					posting_date: values.posting_date,
					mode_of_payment: values.mode_of_payment,
					project: values.project,
					cost_center: values.cost_center,
					allow_edit: true,
					user_remark: values.user_remark,
				},
				callback: (r) => {
					const doc = frappe.model.sync(r.message);
					frappe.set_route("Form", doc[0].doctype, doc[0].name);
				},
			});
		} else {
			frappe.call({
				method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_journal_entry_bts",
				args: {
					bank_transaction_name: this.bank_transaction.name,
					reference_number: values.reference_number,
					reference_date: values.reference_date,
					party_type: values.party_type,
					party: values.party,
					cost_center: values.je_cost_center,
					posting_date: values.posting_date,
					mode_of_payment: values.mode_of_payment,
					user_remark: values.user_remark,
					entry_type: values.journal_entry_type,
					second_account: values.second_account,
					allow_edit: true,
				},
				callback: (r) => {
					var doc = frappe.model.sync(r.message);
					frappe.set_route("Form", doc[0].doctype, doc[0].name);
				},
			});
		}
	}
};
