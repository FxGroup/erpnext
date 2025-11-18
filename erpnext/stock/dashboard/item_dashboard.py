import frappe
from frappe.model.db_query import DatabaseQuery
from frappe.utils import cint, flt
from erpnext import get_default_company
from fxnmrnth.utils.stock_receiver import check_item_exists

from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
	get_sre_reserved_qty_for_items_and_warehouses as get_reserved_stock_details,
)


@frappe.whitelist()
def get_data(item_code=None, warehouse=None, item_group=None, brand=None, start=0, sort_by='actual_qty', sort_order='desc', limit_page_length=20):
	'''Self Modification of Return data to render the item dashboard'''
	item_code_filter = ""
	product_id = None
	if item_code:
		item_code_filter = 'and bin.item_code = "{}"'.format(item_code)
		product_id = frappe.db.get_value("Item", item_code, 'product_id')
	warehouse_filter = ""
	if warehouse:
		warehouse_filter = 'and bin.warehouse = "{}"'.format(warehouse)
	brand_filter = ""
	if brand:
		brand_filter = 'and item.brand = "{}"'.format(brand)

	item_group_filter = ""
	if item_group:
		lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"])
		items = frappe.db.sql_list(
			"""
			select i.name from `tabItem` i
			where exists(select name from `tabItem Group`
				where name=i.item_group and lft >=%s and rgt<=%s)
		""", (lft, rgt))
		items = [f"""'{i}'""" for i in items]
		item_group_filter = "and bin.item_code in ({})".format(",".join(items))
	try:
		# check if user has any restrictions based on user permissions on warehouse
		if DatabaseQuery('Warehouse', user=frappe.session.user).build_match_conditions():
			warehouse_string = ', '.join([ ('\'' + w.name + '\'') for w in frappe.get_list('Warehouse')])
			warehouse_filter += "and bin.warehouse in ({})".format(warehouse_string)
	except frappe.PermissionError:
		# user does not have access on warehouse
		return []

	## This is probably a good project to modify, since we just need to use SQL to rewrite
	SQL_query = """
		Select 	bin.item_code, 
				bin.warehouse, 
				bin.projected_qty, 
				bin.reserved_qty,
				bin.reserved_qty_for_production,
				bin.reserved_qty_for_sub_contract,
				bin.actual_qty,
				bin.valuation_rate,
				item.product_id,
				item.brand,
				item.has_batch_no
		From `tabBin` bin
			Left Join `tabItem` item on bin.item_code = item.name
		INNER JOIN 
			`tabWarehouse` w on w.name = bin.warehouse
		WHERE 
			1=1
		AND 
			w.disabled = 0
				{item_code_filter}
				{warehouse_filter}
				{item_group_filter}
				{brand_filter}
		Order By {sort_by} {sort_order}
		Limit {limit_page_length} offset {limit_start}
	""".format(
		item_code_filter	=	item_code_filter,
		warehouse_filter	=	warehouse_filter,
		item_group_filter	=	item_group_filter,
		brand_filter		=	brand_filter,
		sort_by				=	sort_by,
		sort_order			=	sort_order,
		limit_page_length	=	limit_page_length,
		limit_start			=	start,
	)

	items = frappe.db.sql(SQL_query, as_dict=1)
	item_code_list = [item_code] if item_code else [i.item_code for i in items]
	warehouse_list = [warehouse] if warehouse else [i.warehouse for i in items]

	sre_reserved_stock_details = get_reserved_stock_details(item_code_list, warehouse_list)
	precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))
	current_site_qty = 0
	has_batch_no = frappe.get_value("Item", item_code, "has_batch_no")
	show_button = "Administrator" in frappe.get_roles() or "Stock Manager" in frappe.get_roles()

	for item in items:
		item.update(
			{
				"item_name": frappe.get_cached_value("Item", item.item_code, "item_name"),
				"cur_product_id": item.get("product_id"),
				"current_site": get_default_company(),
				"target_site": get_default_company(),
				"stock_uom": frappe.get_cached_value("Item", item.item_code, "stock_uom"),
				"disable_quick_entry": frappe.get_cached_value("Item", item.item_code, "has_batch_no")
				or frappe.get_cached_value("Item", item.item_code, "has_serial_no"),
				"projected_qty": flt(item.projected_qty, precision),
				"reserved_qty": flt(item.reserved_qty, precision),
				"reserved_qty_for_production": flt(item.reserved_qty_for_production, precision),
				"reserved_qty_for_sub_contract": flt(item.reserved_qty_for_sub_contract, precision),
				"actual_qty": flt(item.actual_qty, precision),
				"show_stock_buttons": show_button,
				"reserved_stock": flt(sre_reserved_stock_details.get((item.item_code, item.warehouse))),
			}
		)
		current_site_qty = flt(item.actual_qty, precision)
		
	params = {
		"item_code": item_code,
		"cur_product_id": product_id,
		"current_site": get_default_company(),
		"method": "item_exists", 
		"has_batch_no": has_batch_no
	}
	
	# Fetching Intersite Item Data
	intersite_items = check_item_exists(params)

	if intersite_items:
		if isinstance(intersite_items, dict) and intersite_items.get('error'):
			frappe.throw(intersite_items.get('error'))
		else:
			batch_match = intersite_items[0].get('batch_match', 0)
			for item in intersite_items:
				item["current_site_qty"] = current_site_qty
				item["show_stock_buttons"] = show_button

			for element in items:	
				element['batch_match'] = batch_match

			items = items + intersite_items

	return items
