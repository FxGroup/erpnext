# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _, msgprint


import frappe, erpnext
from frappe import _, msgprint
from frappe.utils import get_url
from erpnext.stock.get_item_details import get_price_list_rate_for
def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns(filters)
	data = get_entries(filters)

	return columns, data


def get_columns(filters):
	if not filters.get("doctype"):
		msgprint(_("Please select the document type first"), raise_exception=1)

	columns = [
		# {
		# 	"label": _("Customer"),
		# 	"options": "Customer",
		# 	"fieldname": "customer",
		# 	"fieldtype": "Link",
		# 	"width": 100
		# },
		{
			"label": _("Sales Partner Code"),
			"options": "Customer",
			"fieldname": "sales_partner_code",
			"fieldtype": "Link",
			"width": 100
		},
		{
			"label": _("Sales Partner"),
			"options": "Sales Partner",
			"fieldname": "sales_partner",
			"fieldtype": "Link",
			"width": 100
		},
				{
			"label": _("Sales Partner Email"),
			"fieldname": "customer_primary_email_address",
			"fieldtype": "DATA",
			"width": 200
		},
		{
			"label": _("Sales Partner Rebate preference"),
			"fieldname": "bank_details",
			"fieldtype": "Data",
			"width": 200
		},
		{
			"label": _(filters["doctype"]),
			"options": filters["doctype"],
			"fieldname": "name",
			"fieldtype": "Link",
			"width": 140,
		},
		{
			"label": _("Shipment Country"),
			"fieldname": "shipment_country",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": _("Customer Name"),
			"fieldname": "customer_name",
			"fieldtype": "Data",
			"width": 140
		},
		# {
		# 	"label": _("Territory"),
		# 	"options": "Territory",
		# 	"fieldname": "territory",
		# 	"fieldtype": "Link",
		# 	"width": 100
		# },
		{
			"label": _("Posting Date"),
			"fieldname": "posting_date",
			"fieldtype": "Date",
			"width": 100
		},
	]
	if filters.get("group_by") != "Customer Name":
		columns += [
			{
				"label": _("Item Code"),
				"fieldname": "item_code",
				"fieldtype": "Link",
				"options": "Item",
				"width": 120
			},
			{
				"label": _("Item Name"),
				"fieldname": "item_name",
				"fieldtype": "Data",
				"width": 140
			},
			# {
			# 	"label": _("Item Group"),
			# 	"fieldname": "item_group",
			# 	"fieldtype": "Link",
			# 	"options": "Item Group",
			# 	"width": 100
			# },
			{
				"label": _("Brand"),
				"fieldname": "brand",
				"fieldtype": "Link",
				"options": "Brand",
				"width": 100
			},
		]
	columns += [ 
		{
			"label": _("Currency"),
			"field_name": "currency",
			"fieldtype": "Data",
			"width": 150
		},
		{
			"label": _("Quantity"),
			"fieldname": "qty",
			"fieldtype": "Float",
			"width": 80
		},
		# {
		# 	"label": _("Rate"),
		# 	"fieldname": "rate",
		# 	"fieldtype": "Currency",
		# 	"width": 80
		# },
		# {
		# 	"label": _("Wholesale Price"),
		# 	"field_name": "wholesale_price",
		# 	"fieldtype": "Currency",
		# 	"width": 150
		# },
		{
			"label": _("Wholesale Amount"),
			"field_name": "wholesale_amount",
			"fieldtype": "Currency",
			"width": 150
		},
		{
			"label": _("Sale Amount"),
			"fieldname": "amount",
			"fieldtype": "Currency",
			"width": 120
		},

		# {
		# 	"label": _("Commission Rate %"),
		# 	"fieldname": "commission_rate",
		# 	"fieldtype": "Data",
		# 	"width": 80
		# },
		# {
		# 	"label": _("Commission"),
		# 	"fieldname": "commission",
		# 	"fieldtype": "Currency",
		# 	"width": 120
		# },
		{
			"label": _("Rebate (SalePrice - WS)"),
			"fieldname": "commission_wholesale",
			"fieldtype": "Currency",
			"width": 120
		}
		
	]

	if erpnext.get_default_company() != "RN Labs":
		columns.append({
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 120,
		})

	return columns


def get_entries(filters):
	date_field = "transaction_date" if filters.get("doctype") in ["Sales Order", "Sales Invoice"] else "posting_date"

	conditions = get_conditions(filters, date_field)
	if filters.get("group_by") == "Default (Document type)":
		group_by = "dt.name, dt_item.item_code"
	elif filters.get("group_by") == "Customer Name":
		group_by = "dt.customer_name"
	if filters.get('doctype') == "Sales Invoice":
		fields = "dt.is_return, "
	else:
		fields = ""
	entries = frappe.db.sql(
		"""
		SELECT
			s.customer as sales_partner_code,
			dt.name, 
			if(s.preference = "Refund to Account", a.branch_code, 'N/A') as branch_code,
			if(s.preference = "Refund to Account", a.bank_account_no, 'N/A') as account_number,
			dt.customer, dt.territory, dt.{date_field} as posting_date, dt.currency, dt.country_code as 'shipment_country',
			dt.shipping_address_name, 
			s.preference as bank_details, dt.selling_price_list as price_list,
			dt_item.item_code, dt_item.item_name, dt.customer_name, {fields}
			SUM(dt_item.net_rate) as rate, SUM(dt_item.qty) as qty, SUM(dt_item.net_amount) as amount,
			ROUND(((dt_item.net_rate * dt.commission_rate) / 100), 2) as commission,
			dt_item.brand, dt.sales_partner,dts.customer_primary_email_address, dt.commission_rate, dt_item.item_group, dt_item.item_code, dt_item.stock_uom
		FROM
			`tab{doctype}` dt
		join `tab{doctype} Item` dt_item on dt_item.parent = dt.name
		join `tabSales Partner` s on s.name = dt.sales_partner
		LEFT join `tabCustomer` dts on dts.name = s.customer
		LEFT join `tabBank Account` a on s.bank_account = a.name
		
		WHERE
			{cond} and dt.name = dt_item.parent 
			and dt.docstatus = 1
			and dt_item.item_code NOT IN ("HAND-FEE", "SHIP1", "SHIP2", "SHIP3", "CREDIT ADJ")
			and dt.sales_partner is not null 
			and dt.sales_partner != ''
		GROUP BY {group_by}
		order by dt.customer_name
		
		""".format(
			date_field=date_field, doctype=filters.get("doctype"), cond=conditions, group_by=group_by, fields=fields
		),
		filters,
		as_dict=1
	)
	if filters["doctype"] == "Sales Invoice":
		entries = calculate_ws_commission(entries, filters)
	for entry in entries:
		if not entry['shipment_country']:
			address = frappe.get_value("Address", entry['shipping_address_name'], "country")
			if address == "New Zealand":
				entry['shipment_country'] = "NZ"
			elif address == "Australia":
				entry['shipment_country'] = "AU"
	return entries


def get_conditions(filters, date_field):
	conditions = "1=1"

	for field in ["company", "customer", "territory", "sales_partner"]:
		if filters.get(field):
			conditions += f" and dt.{field} = %({field})s"

	if filters.get("from_date"):
		conditions += f" and dt.{date_field} >= %(from_date)s"

	if filters.get("to_date"):
		conditions += f" and dt.{date_field} <= %(to_date)s"

	if filters.get("currency"):
		conditions += " and dt.currency = %(currency)s"

	if not filters.get("show_return_entries"):
		conditions += " and dt_item.qty > 0.0"
	
	if filters.get("get_products") or filters.get("get_tests"):
		in_group = ["'Test Kits'","'Tests'"] if filters.get("get_tests") == 1 else []
		if filters.get("get_products") == 1:
			in_group.append("'Products'")
		conditions += f" and dt_item.item_group in ({','.join(in_group)})"
	# frappe.msgprint(conditions)

	if filters.get("brand"):
		conditions += " and dt_item.brand = %(brand)s"

	if filters.get("item_group"):
		lft, rgt = frappe.get_cached_value("Item Group", filters.get("item_group"), ["lft", "rgt"])

		conditions += f""" and dt_item.item_group in (select name from
			`tabItem Group` where lft >= {lft} and rgt <= {rgt})"""

	return conditions




def calculate_ws_commission(entries, filters):
	from frappe.utils import flt
	item_prices = frappe.db.get_all("Item Price", 
	filters=[["selling", "=", "1"]],
	or_filters=[
    ["valid_upto", ">=", filters.get("from_date")],
    ["valid_upto", "is", "not set"]
	],
	fields=["item_code", "price_list", "valid_from", "valid_upto", "price_list_rate"],
	order_by="valid_from")
	item_dict = {}
	for item in item_prices:
		if item["item_code"] not in item_dict:
			item_dict[item["item_code"]] = {}
		current_item_dict = item_dict[item["item_code"]]
		if item["price_list"] not in current_item_dict:
			current_item_dict[item["price_list"]] = []
		current_item_dict[item["price_list"]].append(item)

	for entry in entries:
		entry_price_list = entry["price_list"]
		entry_price_list = entry_price_list.split()[0]
		entry_price_list += " Wholesale"

		entry['wholesale_price'], entry['wholesale_amount'] = get_wholesale_price(entry)
  
		if not entry['wholesale_price'] and not entry['wholesale_amount']:
			entry['wholesale_price'] = "N/A"
			entry['wholesale_amount'] = "N/A"
			continue

		if entry['is_return']:
			entry['wholesale_price'] = entry['wholesale_price'] * -1
			entry['wholesale_amount'] = entry['wholesale_amount'] * -1
		if entry['wholesale_price'] or entry['wholesale_price'] == 0:
			entry["commission_wholesale"] = flt(entry["amount"] - (entry['wholesale_price']*abs(entry["qty"])), 2)
			if entry["commission_wholesale"] < 0 and not entry['is_return']:
				entry["commission_wholesale"] = 0
		else:
			msgprint("No wholesale price for <a href='" + get_url() + "/app/item/" + entry["item_code"] + "' target='_blank'>" + entry["item_code"] + "</a>. Please set a wholesale price and then re-run report")
			return []
		
	return entries

def get_wholesale_price(entry):
	get_price_list_rate_args = {
		"customer": entry.customer,
		"item_code": entry.item_code,
		"transaction_date": entry.posting_date,
		"posting_date": entry.posting_date,
		"uom": entry.stock_uom
	}

	price_list = entry.price_list.split(" ")[0] + " Wholesale"
	get_price_list_rate_args['price_list'] = price_list
	price_list_rate = get_price_list_rate_for(get_price_list_rate_args, entry.item_code)
	if price_list_rate or price_list_rate == 0:
		return abs(price_list_rate), abs(price_list_rate)*abs(entry.qty)
	else:
		return None, None
