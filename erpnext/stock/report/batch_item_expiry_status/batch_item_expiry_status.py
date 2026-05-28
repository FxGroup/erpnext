# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import Date


def execute(filters=None):
	validate_filters(filters)

	columns = get_columns()
	data = get_data(filters)

	return columns, data


def validate_filters(filters):
	if not filters:
		frappe.throw(_("Please select the required filters"))

	if not filters.get("exp_from_date"):
		frappe.throw(_("'Expiry From Date' is required"))

	if not filters.get("exp_to_date"):
		frappe.throw(_("'Expiry To Date' is required"))


def get_columns():
	return [
		_("Product ID") + ":Data/Item:190",
		_("Item") + ":Link/Item:190",
		_("Item Name") + "::200",
		_("Batch") + ":Link/Batch:350",
		_("UOM") + ":Link/UOM:60",
		_("Expires On") + ":Date:100",
		_("Quantity") + ":Int:100",
		_("Expiry (In Days)") + ":Int:130",
	]


def get_data(filters):
	data = []

	for batch in get_batch_details(filters):
		data.append(
			[
				batch.product_id,
				batch.item,
				batch.item_name,
				batch.name,
				batch.stock_uom,
				batch.batch_qty,
				batch.expiry_date,
				max((batch.expiry_date - frappe.utils.datetime.date.today()).days, 0)
				if batch.expiry_date
				else None,
			]
		)

	return data


def get_batch_details(filters):
	batch = frappe.qb.DocType("Batch")
	query = (
		frappe.qb.from_(batch)
		.select(
			batch.name,
			batch.creation,
   			batch.product_id,
			batch.expiry_date,
			batch.item,
			batch.item_name,
			batch.stock_uom,
			batch.batch_qty,
		)
		.where(
			(batch.disabled == 0)
			& (batch.batch_qty > 0)
			& (Date(batch.expiry_date) >= filters["exp_from_date"])
			& (Date(batch.expiry_date) <= filters["exp_to_date"])
		)
		.orderby(batch.expiry_date)
	)

	if filters.get("expiry_in_days") is not None:
		cutoff = frappe.utils.add_days(frappe.utils.today(), int(filters["expiry_in_days"]))
		query = query.where(Date(batch.expiry_date) <= cutoff)

	if filters.get("item"):
		query = query.where(batch.item == filters["item"])

	return query.run(as_dict=True)
