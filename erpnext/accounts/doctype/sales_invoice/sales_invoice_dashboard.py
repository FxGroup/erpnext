from frappe import _


def get_data():
	return {
		'fieldname': 'sales_invoice',
		'non_standard_fieldnames': {
			'Delivery Note': 'against_sales_invoice',
			'Journal Entry': 'reference_name',
			'Payment Entry': 'reference_name',
			'Payment Request': 'reference_name',
			'Sales Invoice': 'return_against',
			'Auto Repeat': 'reference_document',
			'Integration Request': 'reference_id_',
			"Shipment Entry": "route_invoice"
		},
		"internal_links": {
			"Sales Order": ["items", "sales_order"],
			"Timesheet": ["timesheets", "time_sheet"],
		},
		"transactions": [
			{
				"label": _("Payment"),
				"items": [
					"Payment Entry",
					"Payment Request",
					"Journal Entry",
					"Invoice Discounting",
					"Dunning",
					"Invoice Rebate"
				],
			},
			{
				'label': _('Returns & WooCommerce Integration'),
				'items': ['Sales Invoice', 'Integration Request']
			},
			{
				'label': _('Testing'),
				'items': ['Sample']
			},
			{
				'label': _('Shipments'),
				'items': ['Shipment Entry']
			},
		]
	}