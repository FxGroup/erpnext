from frappe import _
from erpnext import get_default_company

def get_data():
	args = {
		"heatmap": True,
		"heatmap_message": _("This is based on stock movement. See {0} for details").format(
			'<a href="/app/query-report/Stock Ledger">' + _("Stock Ledger") + "</a>"
		),
		"fieldname": "item_code",
		"non_standard_fieldnames": {
			"Work Order": "production_item",
			"Product Bundle": "new_item_code",
			"BOM": "item",
			"Batch": "item",
			"Memos": "item"
		},
		"transactions": [
			{
				'label': _('Groups'),
				'items': ['BOM', 'Product Bundle', 'Item Alternative', 'Batch']
			},
			{
				'label': _('Buy'),
				'items': ['Purchase Order', 'Purchase Receipt', 'Purchase Invoice', 'Stock Entry']
			},
			{
				'label': _('Sell'),
				'items': ['Sales Invoice', 'Backorder', 'Memos']
			},
			{
				'label': _('Manufacture'),
				'items': ['Production Plan', 'Work Order', 'Item Manufacturer']
			},
			{
				'label': _('Pricing'),
				'items': ['Item Price', 'Pricing Rule']
			},
			{
				'label': _('Traceability'),
				'items': ['Serial No', 'Batch']
			},
			{
				'label': _('Move'),
				'items': ['Stock Entry', 'Stock Reconciliation']
			}
		]
	}

	if get_default_company() == "FxMed":
		args['non_standard_fieldnames']['Course'] = "education_item"
		args['transactions'].append({
			'label': 'FxLearn',
			'items': ["Course"]
		})
	
	return args
