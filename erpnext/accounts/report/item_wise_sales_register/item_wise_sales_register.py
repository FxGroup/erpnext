# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.meta import get_field_precision
from frappe.query_builder import functions as fn
from frappe.utils import flt
from frappe.utils.nestedset import get_descendants_of
from frappe.utils.xlsxutils import handle_html

from erpnext.accounts.report.utils import get_values_for_columns


def execute(filters=None):
	return _execute(filters)


def _execute(filters=None, additional_table_columns=None, additional_conditions=None):
	if not filters:
		filters = {}

	company_currency = frappe.get_cached_value("Company", filters.get("company"), "default_currency")
	columns = get_columns(additional_table_columns, filters)

	# Phase 1: discover all tax column names via a single JOIN query — no IN clause, no row data loaded
	tax_columns, tax_accounts = _discover_tax_columns(filters, columns, company_currency)

	# Load all customers once — cheaper than a large IN clause for long date ranges
	customer_details = {
		r[0]: frappe._dict({"customer_name": r[1], "customer_group": r[2]})
		for r in frappe.db.sql("SELECT name, customer_name, customer_group FROM `tabCustomer`")
	}

	data = []
	total_row_map = {}
	skip_total_row = 0
	prev_group_by_value = ""
	last_d = None
	grand_total = None

	if filters.get("group_by"):
		grand_total = get_grand_total(filters, "Sales Invoice")
		group_by_field, subtotal_display_field = get_group_by_and_display_fields(filters)

	# Phase 2: process data one month at a time — peak memory capped at ~1 month of rows
	for chunk_filters in _monthly_date_chunks(filters):
		chunk_items = get_items(chunk_filters, additional_table_columns, additional_conditions)
		if not chunk_items:
			continue

		itemised_tax = _compute_itemised_tax(chunk_items, tax_accounts, company_currency)

		for d in chunk_items:
			customer_record = customer_details.get(d.customer) or frappe._dict()

			row = {
				"item_code": d.item_code,
				"item_name": d.si_item_name if d.si_item_name else d.i_item_name,
				"item_group": d.si_item_group if d.si_item_group else d.i_item_group,
				"description": d.description,
				"invoice": d.parent,
				"posting_date": d.posting_date,
				"customer": d.customer,
				"customer_name": customer_record.get("customer_name"),
				"customer_group": customer_record.get("customer_group"),
				**get_values_for_columns(additional_table_columns, d),
				"territory": d.territory,
				"income_account": get_income_account(d),
				"cost_center": d.cost_center,
				"stock_qty": d.stock_qty,
				"stock_uom": d.stock_uom,
			}

			if d.stock_uom != d.uom and d.stock_qty:
				row.update({"rate": (d.base_net_rate * d.qty) / d.stock_qty, "amount": d.base_net_amount})
			else:
				row.update({"rate": d.base_net_rate, "amount": d.base_net_amount})

			total_tax = 0
			total_other_charges = 0
			for tax, tax_data in (itemised_tax.get(d.name) or {}).items():
				amt = flt(tax_data.get("tax_amount"))
				if tax_data.get("is_other_charges"):
					total_other_charges += amt
				else:
					total_tax += amt
					row[f"{tax}_rate"] = tax_data.get("tax_rate", 0)
					row[f"{tax}_amount"] = amt

			row.update(
				{
					"total_tax": total_tax,
					"total_other_charges": total_other_charges,
					"total": d.base_net_amount + total_tax,
					"currency": company_currency,
				}
			)

			if filters.get("group_by"):
				row.update({"percent_gt": flt(row["total"] / grand_total) * 100})
				data, prev_group_by_value = add_total_row(
					data, filters, prev_group_by_value, d, total_row_map,
					group_by_field, subtotal_display_field, grand_total, tax_columns,
				)
				add_sub_total_row(row, total_row_map, d.get(group_by_field, ""), tax_columns)

			data.append(row)
			last_d = d

		del chunk_items, itemised_tax

	if filters.get("group_by") and last_d:
		total_row = total_row_map.get(prev_group_by_value or last_d.get("item_name"))
		total_row["percent_gt"] = flt(total_row["total"] / grand_total * 100)
		data.append(total_row)
		data.append({})
		add_sub_total_row(total_row, total_row_map, "total_row", tax_columns)
		data.append(total_row_map.get("total_row"))
		skip_total_row = 1

	return columns, data, None, None, None, skip_total_row


def _discover_tax_columns(filters, columns, company_currency, doctype="Sales Invoice", tax_doctype="Sales Taxes and Charges"):
	"""Return (tax_columns_list, tax_accounts) and mutate columns with display definitions.

	Uses a JOIN instead of parent IN (...) so no invoice name list is built in memory.
	"""
	tax_accounts = set(
		frappe.qb.from_(frappe.qb.DocType("Account"))
		.select(frappe.qb.DocType("Account").name)
		.where(frappe.qb.DocType("Account").account_type == "Tax")
		.run()
	)

	conditions = [
		"stc.parenttype = %s",
		"stc.docstatus = 1",
		"stc.description IS NOT NULL",
		"stc.description != ''",
		"stc.base_tax_amount_after_discount_amount != 0",
		"si.docstatus = 1",
	]
	params = [doctype]

	if filters.get("company"):
		conditions.append("si.company = %s")
		params.append(filters["company"])
	if filters.get("from_date"):
		conditions.append("si.posting_date >= %s")
		params.append(filters["from_date"])
	if filters.get("to_date"):
		conditions.append("si.posting_date <= %s")
		params.append(filters["to_date"])

	raw = frappe.db.sql(
		f"""
		SELECT DISTINCT stc.description, stc.account_head
		FROM `tab{tax_doctype}` stc
		JOIN `tab{doctype}` si ON si.name = stc.parent
		WHERE {" AND ".join(conditions)}
		ORDER BY stc.description
		""",
		params,
	)

	tax_columns_dict = {}
	other_charges_columns = set()
	scrubbed_map = {}

	for description, account_head in raw:
		description = handle_html(description)
		scrubbed = scrubbed_map.get(description)
		if not scrubbed:
			scrubbed = frappe.scrub(description)
			scrubbed_map[description] = scrubbed
		if scrubbed not in tax_columns_dict:
			tax_columns_dict[scrubbed] = description
			if tuple([account_head]) not in tax_accounts:
				other_charges_columns.add(scrubbed)

	# Suppress Tax columns that are the GST component of a suppressed shipping charge
	for scrubbed_desc in list(tax_columns_dict.keys()):
		if scrubbed_desc.endswith("_tax") and scrubbed_desc not in other_charges_columns:
			if scrubbed_desc[:-4] in other_charges_columns:
				other_charges_columns.add(scrubbed_desc)

	tax_columns_list = sorted(tax_columns_dict.keys())
	for scrubbed_desc in tax_columns_list:
		if scrubbed_desc in other_charges_columns:
			continue
		desc = tax_columns_dict[scrubbed_desc]
		columns.append({"label": _(desc + " Rate"), "fieldname": f"{scrubbed_desc}_rate", "fieldtype": "Float", "width": 100})
		columns.append({"label": _(desc + " Amount"), "fieldname": f"{scrubbed_desc}_amount", "fieldtype": "Currency", "options": "currency", "width": 100})

	columns += [
		{"label": _("Total Tax"), "fieldname": "total_tax", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Total Other Charges"), "fieldname": "total_other_charges", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Currency", "width": 80, "hidden": 1},
	]

	return tax_columns_list, tax_accounts


def _monthly_date_chunks(filters):
	"""Yield monthly filter dicts newest-first, matching posting_date desc ordering."""
	from frappe.utils import getdate, add_months, add_days

	if not filters.get("from_date") or not filters.get("to_date"):
		yield filters
		return

	start = getdate(filters["from_date"])
	end = getdate(filters["to_date"])

	chunks = []
	current = start
	while current <= end:
		next_month = add_months(current, 1)
		chunk_end = min(add_days(next_month, -1), end)
		chunk_f = dict(filters)
		chunk_f["from_date"] = str(current)
		chunk_f["to_date"] = str(chunk_end)
		chunks.append(chunk_f)
		current = next_month

	yield from reversed(chunks)


def _compute_itemised_tax(chunk_items, tax_accounts, company_currency, doctype="Sales Invoice", tax_doctype="Sales Taxes and Charges"):
	"""Compute itemised_tax for a chunk of invoice items (typically one month)."""
	import json

	add_deduct_tax_field = "charge_type"
	extra_conditions = ""
	if doctype == "Purchase Invoice":
		extra_conditions = " AND category IN ('Total', 'Valuation and Total') AND base_tax_amount_after_discount_amount != 0"
		add_deduct_tax_field = "add_deduct_tax"

	tax_amount_precision = (
		get_field_precision(frappe.get_meta(tax_doctype).get_field("tax_amount"), currency=company_currency)
		or 2
	)

	invoice_item_row = {}
	item_row_map = {}
	for d in chunk_items:
		invoice_item_row.setdefault(d.parent, []).append(d)
		item_row_map.setdefault(d.parent, {}).setdefault(
			d.item_code if d.item_code else (d.si_item_name or d.i_item_name or ''), []
		).append(d)

	item_net_amount_map = {
		(parent, item_code): sum(flt(d.base_net_amount) for d in rows)
		for parent, items in item_row_map.items()
		for item_code, rows in items.items()
	}

	invoice_names = list(invoice_item_row.keys())
	if not invoice_names:
		return {}

	tax_details = frappe.db.sql(
		f"""
		SELECT name, parent, description, item_wise_tax_detail, account_head,
			charge_type, {add_deduct_tax_field}, base_tax_amount_after_discount_amount
		FROM `tab{tax_doctype}`
		WHERE parenttype = %s AND docstatus = 1
			AND description IS NOT NULL AND description != ''
			AND parent IN ({", ".join(["%s"] * len(invoice_names))})
			{extra_conditions}
		""",
		tuple([doctype, *invoice_names]),
	)

	itemised_tax = {}
	scrubbed_map = {}

	for (
		_name, parent, description, item_wise_tax_detail,
		account_head, charge_type, add_deduct_tax_val, tax_amount,
	) in tax_details:
		description = handle_html(description)
		scrubbed = scrubbed_map.get(description)
		if not scrubbed:
			scrubbed = frappe.scrub(description)
			scrubbed_map[description] = scrubbed

		is_other_charges = 0 if tuple([account_head]) in tax_accounts else 1

		if item_wise_tax_detail:
			try:
				item_wise_tax_detail = json.loads(item_wise_tax_detail)
				for item_code, tax_data in item_wise_tax_detail.items():
					if isinstance(tax_data, list):
						tax_rate, tax_amount = tax_data
					else:
						tax_rate = tax_data
						tax_amount = 0

					if charge_type == "Actual" and not tax_rate:
						tax_rate = "NA"

					item_rows = item_row_map.get(parent, {}).get(item_code, [])
					item_net_amount = item_net_amount_map.get((parent, item_code), 0)

					for d in item_rows:
						item_tax_amount = flt((tax_amount * d.base_net_amount) / item_net_amount) if item_net_amount else 0
						if item_tax_amount:
							tax_value = flt(item_tax_amount, tax_amount_precision)
							if doctype == "Purchase Invoice" and add_deduct_tax_val == "Deduct":
								tax_value *= -1
							itemised_tax.setdefault(d.name, {})[scrubbed] = frappe._dict({
								"tax_rate": tax_rate,
								"tax_amount": tax_value,
								"is_other_charges": is_other_charges,
							})
			except ValueError:
				continue
		elif charge_type == "Actual" and tax_amount:
			for d in invoice_item_row.get(parent, []):
				itemised_tax.setdefault(d.name, {})[scrubbed] = frappe._dict({
					"tax_rate": "NA",
					"tax_amount": flt((tax_amount * d.base_net_amount) / d.base_net_total, tax_amount_precision),
					"is_other_charges": is_other_charges,
				})

	return itemised_tax


def get_income_account(row):
	if row.enable_deferred_revenue:
		return row.deferred_revenue_account
	elif row.is_internal_customer == 1:
		return row.unrealized_profit_loss_account
	else:
		return row.income_account


def get_columns(additional_table_columns, filters):
	columns = []

	if filters.get("group_by") != ("Item"):
		columns.extend(
			[
				{
					"label": _("Item Code"),
					"fieldname": "item_code",
					"fieldtype": "Link",
					"options": "Item",
					"width": 120,
				},
				{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 120},
			]
		)

	if filters.get("group_by") not in ("Item", "Item Group"):
		columns.extend(
			[
				{
					"label": _("Item Group"),
					"fieldname": "item_group",
					"fieldtype": "Link",
					"options": "Item Group",
					"width": 120,
				}
			]
		)

	columns.extend(
		[
			{"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 150},
			{
				"label": _("Invoice"),
				"fieldname": "invoice",
				"fieldtype": "Link",
				"options": "Sales Invoice",
				"width": 150,
			},
			{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 120},
		]
	)

	if filters.get("group_by") != "Customer":
		columns.extend(
			[
				{
					"label": _("Customer Group"),
					"fieldname": "customer_group",
					"fieldtype": "Link",
					"options": "Customer Group",
					"width": 120,
				}
			]
		)

	if filters.get("group_by") not in ("Customer", "Customer Group"):
		columns.extend(
			[
				{
					"label": _("Customer"),
					"fieldname": "customer",
					"fieldtype": "Link",
					"options": "Customer",
					"width": 120,
				},
				{
					"label": _("Customer Name"),
					"fieldname": "customer_name",
					"fieldtype": "Data",
					"width": 120,
				},
			]
		)

	if additional_table_columns:
		columns += additional_table_columns

	if filters.get("group_by") != "Territory":
		columns.extend(
			[
				{
					"label": _("Territory"),
					"fieldname": "territory",
					"fieldtype": "Link",
					"options": "Territory",
					"width": 80,
				}
			]
		)

	columns += [
		{
			"label": _("Income Account"),
			"fieldname": "income_account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 100,
		},
		{
			"label": _("Cost Center"),
			"fieldname": "cost_center",
			"fieldtype": "Link",
			"options": "Cost Center",
			"width": 100,
		},
		{"label": _("Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "width": 100},
		{
			"label": _("Stock UOM"),
			"fieldname": "stock_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 100,
		},
		{
			"label": _("Rate"),
			"fieldname": "rate",
			"fieldtype": "Float",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Amount"),
			"fieldname": "amount",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
	]

	if filters.get("group_by"):
		columns.append(
			{"label": _("% Of Grand Total"), "fieldname": "percent_gt", "fieldtype": "Float", "width": 80}
		)

	return columns


def apply_conditions(query, si, sii, sip, filters, additional_conditions=None):
	for opts in ("company", "customer"):
		if filters.get(opts):
			query = query.where(si[opts] == filters[opts])

	if filters.get("from_date"):
		query = query.where(si.posting_date >= filters.get("from_date"))

	if filters.get("to_date"):
		query = query.where(si.posting_date <= filters.get("to_date"))

	if filters.get("mode_of_payment"):
		subquery = (
			frappe.qb.from_(sip)
			.select(sip.parent)
			.where(sip.mode_of_payment == filters.get("mode_of_payment"))
			.groupby(sip.parent)
		)
		query = query.where(si.name.isin(subquery))

	if filters.get("warehouse"):
		if frappe.db.get_value("Warehouse", filters.get("warehouse"), "is_group"):
			lft, rgt = frappe.db.get_all(
				"Warehouse", filters={"name": filters.get("warehouse")}, fields=["lft", "rgt"], as_list=True
			)[0]
			warehouses = frappe.db.get_all("Warehouse", {"lft": (">", lft), "rgt": ("<", rgt)}, pluck="name")
			query = query.where(sii.warehouse.isin(warehouses))
		else:
			query = query.where(sii.warehouse == filters.get("warehouse"))

	if filters.get("brand"):
		query = query.where(sii.brand == filters.get("brand"))

	if filters.get("item_code"):
		query = query.where(sii.item_code == filters.get("item_code"))

	if filters.get("item_group"):
		if frappe.db.get_value("Item Group", filters.get("item_group"), "is_group"):
			item_groups = get_descendants_of("Item Group", filters.get("item_group"))
			item_groups.append(filters.get("item_group"))
			query = query.where(sii.item_group.isin(item_groups))
		else:
			query = query.where(sii.item_group == filters.get("item_group"))

	if filters.get("income_account"):
		query = query.where(
			(sii.income_account == filters.get("income_account"))
			| (sii.deferred_revenue_account == filters.get("income_account"))
			| (si.unrealized_profit_loss_account == filters.get("income_account"))
		)

	for key, value in (additional_conditions or {}).items():
		query = query.where(si[key] == value)

	return query


def apply_order_by_conditions(doctype, query, filters):
	invoice = f"`tab{doctype}`"
	invoice_item = f"`tab{doctype} Item`"

	if not filters.get("group_by"):
		query += f" order by {invoice}.posting_date desc, {invoice_item}.item_group desc"
	elif filters.get("group_by") == "Invoice":
		query += f" order by {invoice_item}.parent desc"
	elif filters.get("group_by") == "Item":
		query += f" order by {invoice_item}.item_code"
	elif filters.get("group_by") == "Item Group":
		query += f" order by {invoice_item}.item_group"
	elif filters.get("group_by") in ("Customer", "Customer Group", "Territory", "Supplier"):
		filter_field = frappe.scrub(filters.get("group_by"))
		query += f" order by {filter_field} desc"

	return query


def get_items(filters, additional_query_columns, additional_conditions=None):
	doctype = "Sales Invoice"
	si = frappe.qb.DocType("Sales Invoice")
	sii = frappe.qb.DocType("Sales Invoice Item")
	sip = frappe.qb.DocType("Sales Invoice Payment")
	item = frappe.qb.DocType("Item")

	query = (
		frappe.qb.from_(si)
		.join(sii)
		.on(si.name == sii.parent)
		.left_join(item)
		.on(sii.item_code == item.name)
		.select(
			sii.name,
			sii.parent,
			si.posting_date,
			si.unrealized_profit_loss_account,
			si.is_internal_customer,
			si.customer,
			fn.IfNull(si.territory, "Not Specified").as_("territory"),
			si.base_net_total,
			sii.item_code,
			sii.description,
			sii.item_name.as_("si_item_name"),
			sii.item_group.as_("si_item_group"),
			item.item_name.as_("i_item_name"),
			item.item_group.as_("i_item_group"),
			sii.income_account,
			sii.cost_center,
			sii.enable_deferred_revenue,
			sii.deferred_revenue_account,
			sii.stock_qty,
			sii.stock_uom,
			sii.base_net_rate,
			sii.base_net_amount,
			sii.uom,
			sii.qty,
		)
		.where(si.docstatus == 1)
		.where(sii.parenttype == doctype)
	)

	if additional_query_columns:
		for column in additional_query_columns:
			if column.get("_doctype"):
				table = frappe.qb.DocType(column.get("_doctype"))
				query = query.select(table[column.get("fieldname")])
			else:
				query = query.select(si[column.get("fieldname")])

	if filters.get("customer"):
		query = query.where(si.customer == filters["customer"])

	if filters.get("customer_group"):
		query = query.where(si.customer_group == filters["customer_group"])

	query = apply_conditions(query, si, sii, sip, filters, additional_conditions)

	from frappe.desk.reportview import build_match_conditions

	query, params = query.walk()
	match_conditions = build_match_conditions(doctype)

	if match_conditions:
		query += " and " + match_conditions

	query = apply_order_by_conditions(doctype, query, filters)

	return frappe.db.sql(query, params, as_dict=True)


def get_delivery_notes_against_sales_order(item_list):
	so_dn_map = frappe._dict()
	so_item_rows = list(set([d.so_detail for d in item_list]))

	if so_item_rows:
		dn_item = frappe.qb.DocType("Delivery Note Item")
		delivery_notes = (
			frappe.qb.from_(dn_item)
			.select(dn_item.parent, dn_item.so_detail)
			.where(dn_item.docstatus == 1)
			.where(dn_item.so_detail.isin(so_item_rows))
			.groupby(dn_item.so_detail, dn_item.parent)
			.run(as_dict=True)
		)

		for dn in delivery_notes:
			so_dn_map.setdefault(dn.so_detail, []).append(dn.parent)

	return so_dn_map


def get_grand_total(filters, doctype):
	return flt(
		frappe.db.get_value(
			doctype,
			{
				"docstatus": 1,
				"posting_date": ("between", [filters.get("from_date"), filters.get("to_date")]),
			},
			"sum(base_grand_total)",
		)
	)

def get_tax_accounts(
	item_list,
	columns,
	company_currency,
	doctype="Sales Invoice",
	tax_doctype="Sales Taxes and Charges",
):
	import json

	TAX_CHUNK_SIZE = 500

	tax_columns = {}
	other_charges_columns = set()
	itemised_tax = {}
	scrubbed_description_map = {}
	add_deduct_tax_field = "charge_type"

	tax_amount_precision = (
		get_field_precision(frappe.get_meta(tax_doctype).get_field("tax_amount"), currency=company_currency)
		or 2
	)

	# Group items by invoice — used for tax distribution
	invoice_item_row = {}
	item_row_map = {}
	for d in item_list:
		invoice_item_row.setdefault(d.parent, []).append(d)
		item_row_map.setdefault(d.parent, {}).setdefault(d.item_code or d.item_name, []).append(d)

	purchase_invoice_conditions = ""
	if doctype == "Purchase Invoice":
		purchase_invoice_conditions = (
			" AND category IN ('Total', 'Valuation and Total')"
			" AND base_tax_amount_after_discount_amount != 0"
		)
		add_deduct_tax_field = "add_deduct_tax"

	# Fetch tax account names once — used to distinguish tax vs other-charges
	tax_accounts = set(
		frappe.qb.from_(frappe.qb.DocType("Account"))
		.select(frappe.qb.DocType("Account").name)
		.where(frappe.qb.DocType("Account").account_type == "Tax")
		.run()
	)

	# Process invoices in chunks to avoid huge IN clauses and limit peak memory
	invoice_names = list(invoice_item_row.keys())
	for chunk_start in range(0, len(invoice_names), TAX_CHUNK_SIZE):
		chunk = invoice_names[chunk_start : chunk_start + TAX_CHUNK_SIZE]

		# Pre-compute net amounts for this chunk only — discarded after each chunk
		chunk_net_amount_map = {
			(parent, item_code): sum(flt(d.base_net_amount) for d in rows)
			for parent in chunk
			for item_code, rows in item_row_map.get(parent, {}).items()
		}

		chunk_tax_details = frappe.db.sql(
			f"""
			SELECT
				name, parent, description, item_wise_tax_detail, account_head,
				charge_type, {add_deduct_tax_field}, base_tax_amount_after_discount_amount
			FROM `tab{tax_doctype}`
			WHERE
				parenttype = %s AND docstatus = 1
				AND description IS NOT NULL AND description != ''
				AND parent IN ({", ".join(["%s"] * len(chunk))})
				{purchase_invoice_conditions}
			ORDER BY description
			""",
			tuple([doctype, *chunk]),
		)

		for (
			_name,
			parent,
			description,
			item_wise_tax_detail,
			account_head,
			charge_type,
			add_deduct_tax_val,
			tax_amount,
		) in chunk_tax_details:
			description = handle_html(description)
			scrubbed_description = scrubbed_description_map.get(description)
			if not scrubbed_description:
				scrubbed_description = frappe.scrub(description)
				scrubbed_description_map[description] = scrubbed_description

			if scrubbed_description not in tax_columns and tax_amount:
				# description was a text editor field — markup can break column naming
				tax_columns[scrubbed_description] = description
				if tuple([account_head]) not in tax_accounts:
					other_charges_columns.add(scrubbed_description)

			if item_wise_tax_detail:
				try:
					item_wise_tax_detail = json.loads(item_wise_tax_detail)

					for item_code, tax_data in item_wise_tax_detail.items():
						if isinstance(tax_data, list):
							tax_rate, tax_amount = tax_data
						else:
							tax_rate = tax_data
							tax_amount = 0

						if charge_type == "Actual" and not tax_rate:
							tax_rate = "NA"

						item_rows = item_row_map.get(parent, {}).get(item_code, [])
						item_net_amount = chunk_net_amount_map.get((parent, item_code), 0)

						for d in item_rows:
							item_tax_amount = (
								flt((tax_amount * d.base_net_amount) / item_net_amount) if item_net_amount else 0
							)
							if item_tax_amount:
								tax_value = flt(item_tax_amount, tax_amount_precision)
								if doctype == "Purchase Invoice" and add_deduct_tax_val == "Deduct":
									tax_value *= -1

								itemised_tax.setdefault(d.name, {})[scrubbed_description] = frappe._dict(
									{
										"tax_rate": tax_rate,
										"tax_amount": tax_value,
										"is_other_charges": 0 if tuple([account_head]) in tax_accounts else 1,
									}
								)

				except ValueError:
					continue
			elif charge_type == "Actual" and tax_amount:
				for d in invoice_item_row.get(parent, []):
					itemised_tax.setdefault(d.name, {})[scrubbed_description] = frappe._dict(
						{
							"tax_rate": "NA",
							"tax_amount": flt(
								(tax_amount * d.base_net_amount) / d.base_net_total, tax_amount_precision
							),
						}
					)

		del chunk_tax_details, chunk_net_amount_map

	del invoice_item_row, item_row_map

	# Suppress Tax columns that are the GST component of an already-suppressed shipping charge
	# e.g. "Nationwide Tax" is suppressed if "Nationwide" is in other_charges_columns
	for scrubbed_desc in list(tax_columns.keys()):
		if scrubbed_desc.endswith("_tax") and scrubbed_desc not in other_charges_columns:
			if scrubbed_desc[:-4] in other_charges_columns:
				other_charges_columns.add(scrubbed_desc)

	tax_columns_list = list(tax_columns.keys())
	tax_columns_list.sort()
	for scrubbed_desc in tax_columns_list:
		if scrubbed_desc in other_charges_columns:
			continue
		desc = tax_columns[scrubbed_desc]
		columns.append(
			{
				"label": _(desc + " Rate"),
				"fieldname": f"{scrubbed_desc}_rate",
				"fieldtype": "Float",
				"width": 100,
			}
		)
		columns.append(
			{
				"label": _(desc + " Amount"),
				"fieldname": f"{scrubbed_desc}_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 100,
			}
		)

	columns += [
		{
			"label": _("Total Tax"),
			"fieldname": "total_tax",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Total Other Charges"),
			"fieldname": "total_other_charges",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Total"),
			"fieldname": "total",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Currency",
			"width": 80,
			"hidden": 1,
		},
	]

	return itemised_tax, tax_columns_list


def add_total_row(
	data,
	filters,
	prev_group_by_value,
	item,
	total_row_map,
	group_by_field,
	subtotal_display_field,
	grand_total,
	tax_columns,
):
	if prev_group_by_value != item.get(group_by_field, ""):
		if prev_group_by_value:
			total_row = total_row_map.get(prev_group_by_value)
			data.append(total_row)
			data.append({})
			add_sub_total_row(total_row, total_row_map, "total_row", tax_columns)

		prev_group_by_value = item.get(group_by_field, "")

		total_row_map.setdefault(
			item.get(group_by_field, ""),
			{
				subtotal_display_field: get_display_value(filters, group_by_field, item),
				"stock_qty": 0.0,
				"amount": 0.0,
				"bold": 1,
				"total_tax": 0.0,
				"total": 0.0,
				"percent_gt": 0.0,
			},
		)

		total_row_map.setdefault(
			"total_row",
			{
				subtotal_display_field: "Total",
				"stock_qty": 0.0,
				"amount": 0.0,
				"bold": 1,
				"total_tax": 0.0,
				"total": 0.0,
				"percent_gt": 0.0,
			},
		)

	return data, prev_group_by_value


def get_display_value(filters, group_by_field, item):
	if filters.get("group_by") == "Item":
		if item.get("item_code") != item.get("item_name"):
			value = f"{item.get('item_code')}: {item.get('item_name')}"
		else:
			value = item.get("item_code", "")
	elif filters.get("group_by") in ("Customer", "Supplier"):
		party = frappe.scrub(filters.get("group_by"))
		if item.get(party) != item.get(party + "_name"):
			value = f"{item.get(party)}: {item.get(party + '_name')}"
		else:
			value = item.get(party)
	else:
		value = item.get(group_by_field)

	return value


def get_group_by_and_display_fields(filters):
	if filters.get("group_by") == "Item":
		group_by_field = "item_code"
		subtotal_display_field = "invoice"
	elif filters.get("group_by") == "Invoice":
		group_by_field = "parent"
		subtotal_display_field = "item_code"
	else:
		group_by_field = frappe.scrub(filters.get("group_by"))
		subtotal_display_field = "item_code"

	return group_by_field, subtotal_display_field


def add_sub_total_row(item, total_row_map, group_by_value, tax_columns):
	total_row = total_row_map.get(group_by_value)
	total_row["stock_qty"] += item["stock_qty"]
	total_row["amount"] += item["amount"]
	total_row["total_tax"] += item["total_tax"]
	total_row["total"] += item["total"]
	total_row["percent_gt"] += item["percent_gt"]

	for tax in tax_columns:
		total_row.setdefault(f"{tax}_amount", 0.0)
		total_row[f"{tax}_amount"] += flt(item.get(f"{tax}_amount", 0))
