from frappe import _
from erpnext import get_default_company


def get_data():
	dashboardData = {
		"fieldname": "customer",
		"non_standard_fieldnames": {
			"Payment Entry": "party",
			"Quotation": "party_name",
			"Opportunity": "party_name",
			'Medical Objects': 'customer_id',
			'Sample': 'practitioner_id',
			"Bank Account": "party",
			"Subscription": "party",
			'Journal Entry': 'party',
			'Customer': 'creation',
		},
		"dynamic_links": {
			"party_name": ["Customer", "quotation_to"],
			"party": ["Customer", "party_type"],
		},
		'transactions': [
			# {
			# 	'label': _('Pre Sales'),
			# 	'items': ['Opportunity', 'Quotation']
			# },
			{
				'label': _('Pricing/Payments'),
				'items': ['Pricing Rule', 'Payment Entry', 'Journal Entry', 'Bank Account', 'Statement of Account']
			},
			{
				'label': _('Orders'),
				'items': ['Sales Invoice', 'Backorder']
			},
			# {
			# 	'label': _('Support'),
			# 	'items': ['Issue']
			# },
			# {
			# 	'label': _('Projects'),
			# 	'items': ['Project']
			# },
			{
				'label': _('CRM'),
				'items': ["Interactions", "Memos"]
			},
			# {
			# 	'label': _('Support'),
			# 	'items': ['Issue', 'Maintenance Visit', 'Installation Note', 'Warranty Claim']
			# },
			# {
			# 	'label': _('Subscriptions'),
			# 	'items': ['Subscription']
			# }
		]
	}
	company = get_default_company()
	if company == "RN Labs" or company == "FxMed":
		dashboardData['transactions'].append({
			'label': 'Testing DB',
			'items': ['Medical Objects', 'Sample', 'Customer']
		})
		for item in dashboardData['transactions']:
			if item['label'] == 'Pricing/Payments':
				item['items'].append("Rebate Statement")
				break
		dashboardData['non_standard_fieldnames']['Rebate Statement'] = "sales_partner_customer"


	if company == "FxMed":
		dashboardData["transactions"].append({
			'label': 'FxLearn',
			'items': ['Enrolment']
		})
	if company == "RN Labs":
		dashboardData["transactions"].append({
			'label': 'RN Education',
			'items': ['Enrolment']
		})
	
	if company == "NaturalMeds":
		dashboardData["transactions"].append({
			'label': 'Integration Documents',
			'items': ['Email Subscription']
		})
		dashboardData['non_standard_fieldnames']['Email Subscription'] = "party"

	return dashboardData