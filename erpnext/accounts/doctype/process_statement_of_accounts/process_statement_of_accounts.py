# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import copy

import frappe
import os
import re
from frappe import _
from frappe.desk.reportview import get_match_cond
from frappe.model.document import Document
from frappe.utils import add_days, nowdate, add_months, format_date, getdate, today
from frappe.utils.jinja import validate_template
from frappe.utils.pdf import get_pdf
from frappe.www.printview import get_print_style

from erpnext import get_company_currency
from erpnext.accounts.party import get_party_account_currency
from erpnext.accounts.report.accounts_receivable.accounts_receivable import execute as get_ar_soa
from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (
	execute as get_ageing,
)

from erpnext.accounts.report.accounts_receivable.accounts_receivable import (
	execute as get_outstanding,
)
from erpnext.accounts.report.general_ledger.general_ledger import execute as get_soa

from fxnmrnth.fxnmrnth.doctype.statement_of_account.statement_of_account import create_statement
import pdb
from erpnext import get_default_company

logger = frappe.logger(module="CustomerStatements", allow_site=True, with_more_info=False, file_count=2)

class ProcessStatementOfAccounts(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.accounts.doctype.process_statement_of_accounts_customer.process_statement_of_accounts_customer import (
			ProcessStatementOfAccountsCustomer,
		)
		from erpnext.accounts.doctype.psoa_cost_center.psoa_cost_center import PSOACostCenter
		from erpnext.accounts.doctype.psoa_project.psoa_project import PSOAProject

		account: DF.Link | None
		ageing_based_on: DF.Literal["Due Date", "Posting Date"]
		based_on_payment_terms: DF.Check
		body: DF.TextEditor | None
		cc_to: DF.Link | None
		collection_name: DF.DynamicLink | None
		company: DF.Link
		cost_center: DF.TableMultiSelect[PSOACostCenter]
		currency: DF.Link | None
		customer_collection: DF.Literal["", "Customer Group", "Territory", "Sales Partner", "Sales Person"]
		customers: DF.Table[ProcessStatementOfAccountsCustomer]
		enable_auto_email: DF.Check
		filter_duration: DF.Int
		finance_book: DF.Link | None
		frequency: DF.Literal["Weekly", "Monthly", "Quarterly"]
		from_date: DF.Date | None
		group_by: DF.Literal["", "Group by Voucher", "Group by Voucher (Consolidated)"]
		ignore_cr_dr_notes: DF.Check
		ignore_exchange_rate_revaluation_journals: DF.Check
		include_ageing: DF.Check
		include_break: DF.Check
		letter_head: DF.Link | None
		orientation: DF.Literal["Landscape", "Portrait"]
		payment_terms_template: DF.Link | None
		pdf_name: DF.Data | None
		posting_date: DF.Date | None
		primary_mandatory: DF.Check
		project: DF.TableMultiSelect[PSOAProject]
		report: DF.Literal["General Ledger", "Accounts Receivable"]
		sales_partner: DF.Link | None
		sales_person: DF.Link | None
		sender: DF.Link | None
		show_net_values_in_party_account: DF.Check
		show_remarks: DF.Check
		start_date: DF.Date | None
		subject: DF.Data | None
		terms_and_conditions: DF.Link | None
		territory: DF.Link | None
		to_date: DF.Date | None
	# end: auto-generated types

	def validate(self):
		if not self.subject:
			self.subject = "Statement Of Accounts for {{ customer.customer_name }}"
		if not self.body:
			if self.report == "General Ledger":
				body_str = " from {{ doc.from_date }} to {{ doc.to_date }}."
			else:
				body_str = " until {{ doc.posting_date }}."
			self.body = "Hello {{ customer.customer_name }},<br>PFA your Statement Of Accounts" + body_str
		if not self.pdf_name:
			self.pdf_name = "{{ customer.customer_name }}"

		validate_template(self.subject)
		validate_template(self.body)

		if not self.customers:
			frappe.throw(_("Customers not selected."))

		if self.enable_auto_email:
			if self.start_date and getdate(self.start_date) >= getdate(today()):
				self.to_date = self.start_date
				self.from_date = add_months(self.to_date, -1 * self.filter_duration)


def get_report_pdf(doc, consolidated=True, customer=None):
	statement_dict = {}
	ageing = ""

	#Get Process ID
	pid = os.getpid()

	i=0
	numberOfCustomers = len(doc.customers)
	for entry in doc.customers:
		i += 1
		if customer:
			#Single Statement
			if entry.customer != customer:
				continue
		else:
			#Bulk Run
			logger.info("PID[" + str(pid) + "] Processing: " + str(i) + " of " + str(numberOfCustomers))
					
		tax_id = frappe.get_doc('Customer', entry.customer).tax_id
		customer_name = frappe.get_doc('Customer', entry.customer).customer_name
		presentation_currency = get_party_account_currency('Customer', entry.customer, doc.company) \
				or doc.currency or get_company_currency(doc.company)
		if doc.letter_head:
			from frappe.www.printview import get_letter_head

		filters = get_common_filters(doc)
		if doc.ignore_exchange_rate_revaluation_journals:
			filters.update({"ignore_err": True})

			if letter_head.content:
				letter_head.content = frappe.utils.jinja.render_template(
					letter_head.content, {"doc": doc.as_dict()}
				)

			if letter_head.footer:
				letter_head.footer = frappe.utils.jinja.render_template(
					letter_head.footer, {"doc": doc.as_dict()}
				)

		filters= frappe._dict({
			'from_date': doc.from_date,
			'to_date': doc.to_date,
			'company': doc.company,
			'finance_book': doc.finance_book if doc.finance_book else None,
			'account': [doc.account] if doc.account else None,
			'party_type': 'Customer',
			'party': [entry.customer],
			'party_name': [customer_name],
			'presentation_currency': presentation_currency,
			'group_by': doc.group_by,
			'currency': doc.currency,
			'cost_center': [cc.cost_center_name for cc in doc.cost_center],
			'project': [p.project_name for p in doc.project],
			'show_opening_entries': 1,
			'show_due_date': 1,
			'include_default_book_entries': 0,
			'tax_id': tax_id if tax_id else None,
			'show_statement_remarks': 1
		})
		
		col, res = get_soa(filters)
		new_res = []
		for item in res[0:]:
			if item.debit == item.credit and item.account != "'Total'" and item.account != "'Opening'":
				continue
			else:
				new_res.append(item)
		res = new_res
		for x in [0, -2, -1]:
			res[x]["account"] = res[x]["account"].replace("'", "")
		
		if len(res) == 3:
			#No Transactions this month
			if res[2]["debit"] == 0 or (res[2]["balance"] > -0.01 and res[2]["balance"] < 0.01):
				#No outstanding balance
				if not doc.produce_0_statements:
					continue
				else:
					res.insert(2,{
						"account":"No transactions during the period",
						"debit":"",
						"credit":"",
						"debit_in_account_currency":"",
						"credit_in_account_currency":"",
						"balance":0,
						"account_currency": res[1]["account_currency"]
					})
			else:
				res.insert(2,{
					"account":"No transactions during the period",
					"debit":"",
					"credit":"",
					"debit_in_account_currency":"",
					"credit_in_account_currency":"",
					"balance":0,
					"account_currency": res[1]["account_currency"]
				})

		if res[-1]["balance"] == 0:
			#No outstanding balance
			if not doc.produce_0_statements:
				continue
		
		if doc.exclude_balances_below:
			if res[-1]["balance"] < float(doc.exclude_balances_below):
				continue
		
		if doc.include_ageing:
			ageing_filters = frappe._dict({
				'company': doc.company,
				'report_date': doc.to_date,
				'ageing_based_on': doc.ageing_based_on,
				'range1': 30,
				'range2': 60,
				'range3': 90,
				'range4': 120,
				'party_type': "Customer",
				'party': [entry.customer],
				'show_not_yet_due': 1
			})
			col1, ageing = get_ageing(ageing_filters)

			if ageing:
				ageing[0]["ageing_based_on"] = doc.ageing_based_on
	
		outstanding_filters = frappe._dict({
			'company': doc.company,
			'report_date': doc.to_date,
			'ageing_based_on': doc.ageing_based_on,
			'range1': 30,
			'range2': 60,
			'range3': 90,
			'range4': 120,
			'party_type': "Customer",
			'party': [entry.customer]
		})
		outstanding = get_outstanding(outstanding_filters)[1]

		outstandingDocs = []
		
		for voucher in outstanding:
			if not 'due_date' in voucher:
				voucher['due_date'] = voucher['posting_date']
			
			if voucher['posting_date'] < doc.from_date:
				outstandingDocs.append(voucher)

		html = frappe.render_template(
			template_path,
			{
				"filters": filters,
				"data": res,
				"outstandingDocs": outstandingDocs,
				"ageing": ageing[0] if (doc.include_ageing and ageing) else None,
				"letter_head": letter_head if doc.letter_head else None,
				"terms_and_conditions": frappe.db.get_value(
					"Terms and Conditions", doc.terms_and_conditions, "terms"
				)
				if doc.terms_and_conditions
				else None,
			},
		)
		
		html = frappe.render_template(
			base_template_path,
			{"body": html, "css": get_print_style(), "title": "Statement For " + entry.customer},
		)
		statement_dict[entry.customer] = html

	if not bool(statement_dict):
		return False
	elif consolidated:
		delimiter = '<div style="page-break-before: always;"></div>' if doc.include_break else ""
		result = delimiter.join(list(statement_dict.values()))
		return get_pdf(result, {"orientation": doc.orientation})
	else:
		i=0
		pid = os.getpid()
		numberOfCustomers = len(statement_dict.items())
		for customer, statement_html in statement_dict.items():
			i += 1
			logger.info("PID[" + str(pid) + "] Generating PDF for Customer " + str(i) + " of " + str(numberOfCustomers))
			statement_dict[customer] = get_pdf(statement_html, {"orientation": doc.orientation})
		return statement_dict


def get_statement_dict(doc, get_statement_dict=False):
	statement_dict = {}
	ageing = ""

	for entry in doc.customers:
		if doc.include_ageing:
			ageing = set_ageing(doc, entry)

		tax_id = frappe.get_doc("Customer", entry.customer).tax_id
		presentation_currency = (
			get_party_account_currency("Customer", entry.customer, doc.company)
			or doc.currency
			or get_company_currency(doc.company)
		)

		filters = get_common_filters(doc)
		if doc.ignore_exchange_rate_revaluation_journals:
			filters.update({"ignore_err": True})

		if doc.ignore_cr_dr_notes:
			filters.update({"ignore_cr_dr_notes": True})

		if doc.report == "General Ledger":
			filters.update(get_gl_filters(doc, entry, tax_id, presentation_currency))
			col, res = get_soa(filters)
			for x in [0, -2, -1]:
				res[x]["account"] = res[x]["account"].replace("'", "")
			if len(res) == 3:
				continue
		else:
			filters.update(get_ar_filters(doc, entry))
			ar_res = get_ar_soa(filters)
			col, res = ar_res[0], ar_res[1]
			if not res:
				continue

		statement_dict[entry.customer] = (
			[res, ageing] if get_statement_dict else get_html(doc, filters, entry, col, res, ageing)
		)

	return statement_dict


def set_ageing(doc, entry):
	ageing_filters = frappe._dict(
		{
			"company": doc.company,
			"report_date": doc.posting_date,
			"ageing_based_on": doc.ageing_based_on,
			"range1": 30,
			"range2": 60,
			"range3": 90,
			"range4": 120,
			"party_type": "Customer",
			"party": [entry.customer],
		}
	)
	col1, ageing = get_ageing(ageing_filters)

	if ageing:
		ageing[0]["ageing_based_on"] = doc.ageing_based_on

	return ageing


def get_common_filters(doc):
	return frappe._dict(
		{
			"company": doc.company,
			"finance_book": doc.finance_book if doc.finance_book else None,
			"account": [doc.account] if doc.account else None,
			"cost_center": [cc.cost_center_name for cc in doc.cost_center],
			"show_remarks": doc.show_remarks,
		}
	)


def get_gl_filters(doc, entry, tax_id, presentation_currency):
	return {
		"from_date": doc.from_date,
		"to_date": doc.to_date,
		"party_type": "Customer",
		"party": [entry.customer],
		"party_name": [entry.customer_name] if entry.customer_name else None,
		"presentation_currency": presentation_currency,
		"group_by": doc.group_by,
		"currency": doc.currency,
		"project": [p.project_name for p in doc.project],
		"show_opening_entries": 0,
		"include_default_book_entries": 0,
		"tax_id": tax_id if tax_id else None,
		"show_net_values_in_party_account": doc.show_net_values_in_party_account,
	}


def get_ar_filters(doc, entry):
	return {
		"report_date": doc.posting_date if doc.posting_date else None,
		"party_type": "Customer",
		"party": [entry.customer],
		"customer_name": entry.customer_name if entry.customer_name else None,
		"payment_terms_template": doc.payment_terms_template if doc.payment_terms_template else None,
		"sales_partner": doc.sales_partner if doc.sales_partner else None,
		"sales_person": doc.sales_person if doc.sales_person else None,
		"territory": doc.territory if doc.territory else None,
		"based_on_payment_terms": doc.based_on_payment_terms,
		"report_name": "Accounts Receivable",
		"ageing_based_on": doc.ageing_based_on,
		"range1": 30,
		"range2": 60,
		"range3": 90,
		"range4": 120,
	}


def get_html(doc, filters, entry, col, res, ageing):
	base_template_path = "frappe/www/printview.html"
	template_path = (
		"erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts.html"
		if doc.report == "General Ledger"
		else "erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts_accounts_receivable.html"
	)

	if doc.letter_head:
		from frappe.www.printview import get_letter_head

		letter_head = get_letter_head(doc, 0)

	html = frappe.render_template(
		template_path,
		{
			"filters": filters,
			"data": res,
			"report": {"report_name": doc.report, "columns": col},
			"ageing": ageing[0] if (doc.include_ageing and ageing) else None,
			"letter_head": letter_head if doc.letter_head else None,
			"terms_and_conditions": frappe.db.get_value(
				"Terms and Conditions", doc.terms_and_conditions, "terms"
			)
			if doc.terms_and_conditions
			else None,
		},
	)

	html = frappe.render_template(
		base_template_path,
		{"body": html, "css": get_print_style(), "title": "Statement For " + entry.customer},
	)
	return html


def get_customers_based_on_territory_or_customer_group(customer_collection, collection_name):
	fields_dict = {
		"Customer Group": "customer_group",
		"Territory": "territory",
	}
	collection = frappe.get_doc(customer_collection, collection_name)
	selected = [
		customer.name
		for customer in frappe.get_list(
			customer_collection,
			filters=[["lft", ">=", collection.lft], ["rgt", "<=", collection.rgt]],
			fields=["name"],
			order_by="lft asc, rgt desc",
		)
	]
	return frappe.get_list(
		"Customer",
		fields=["name", "customer_name", "email_id"],
		filters=[[fields_dict[customer_collection], "IN", selected]],
	)

def get_logic_context(doc):
	return {"doc": doc, "nowdate": nowdate, "frappe": frappe._dict(utils=frappe.utils)}

def get_customers_based_on_custom_logic(custom_logic):
	'''Get list of customers'''
	customerList = frappe.db.sql("""
		SELECT
			name,
			customer_statement_email_address as email_id
		FROM 
			`tabCustomer`
		WHERE
			disabled = 0 AND
			customer_group != 'Patient'""",
		as_dict=1,
	)

	passCustomerList = []

	for customer in customerList:
		doc = frappe.get_doc("Customer", customer.name)
		skipped = 0

		if custom_logic:
			or_condition = " or \\\n"
			if or_condition in custom_logic:
				conditions = custom_logic.split(or_condition)
				for condition in conditions:
					condition = condition.strip()
					if frappe.safe_eval(condition, None, get_logic_context(doc)):
						skipped = 1
						break
				if skipped:
					continue
			else:
				if frappe.safe_eval(custom_logic.strip(), None, get_logic_context(doc)):
					skipped = 1
					continue
			
		if skipped == 0:
			passCustomerList.append(customer)

	return passCustomerList

def get_customers_based_on_sales_person(sales_person):
	lft, rgt = frappe.db.get_value("Sales Person", sales_person, ["lft", "rgt"])
	records = frappe.db.sql(
		"""
		select distinct parent, parenttype
		from `tabSales Team` steam
		where parenttype = 'Customer'
			and exists(select name from `tabSales Person` where lft >= %s and rgt <= %s and name = steam.sales_person)
	""",
		(lft, rgt),
		as_dict=1,
	)
	sales_person_records = frappe._dict()
	for d in records:
		sales_person_records.setdefault(d.parenttype, set()).add(d.parent)
	if sales_person_records.get("Customer"):
		return frappe.get_list(
			"Customer",
			fields=["name", "customer_name", "email_id"],
			filters=[["name", "in", list(sales_person_records["Customer"])]],
		)
	else:
		return []


def get_recipients_and_cc(customer, doc):
	recipients = []
	for clist in doc.customers:
		if clist.customer == customer:
			try:
				billingEmails = re.split('; |, |\*|\n', clist.billing_email)
			except Exception as e:
				print(clist.customer)
				continue
			for billingEmail in billingEmails:
				recipients.append(billingEmail)
			
			if doc.primary_mandatory and clist.primary_email:
				primaryEmails = re.split('; |, |\*|\n', clist.primary_email)
				for primaryEmail in primaryEmails:
					recipients.append(primaryEmail)
	cc = []
	if doc.cc_to != "":
		try:
			cc = [frappe.get_value("User", doc.cc_to, "email")]
		except Exception:
			pass

	return recipients, cc


def get_context(customer, doc):
	template_doc = copy.deepcopy(doc)
	del template_doc.customers
	template_doc.from_date = format_date(template_doc.from_date)
	template_doc.to_date = format_date(template_doc.to_date)
	return {
		"doc": template_doc,
		"customer": frappe.get_doc("Customer", customer),
		"frappe": frappe.utils,
	}


@frappe.whitelist()
def fetch_customers(customer_collection, collection_name, primary_mandatory, custom_logic):
	customer_list = []
	customers = []

	if customer_collection == "Sales Person":
		customers = get_customers_based_on_sales_person(collection_name)
		if not bool(customers):
			frappe.throw(_("No Customers found with selected options."))
	elif customer_collection == "Custom Logic":
		customers = get_customers_based_on_custom_logic(custom_logic)
		if not bool(customers):
			frappe.throw(_("No Customers found with selected options."))
	else:
		if customer_collection == "Sales Partner":
			customers = frappe.get_list(
				"Customer",
				fields=["name", "customer_name", "email_id"],
				filters=[["default_sales_partner", "=", collection_name]],
			)
		else:
			customers = get_customers_based_on_territory_or_customer_group(
				customer_collection, collection_name
			)

	for customer in customers:
		primary_email = customer.get("email_id") or ""
		billing_email = get_customer_emails(customer.name, 1, billing_and_primary=False)

		if int(primary_mandatory):
			if primary_email == "":
				continue

		customer_list.append(
			{
				"name": customer.name,
				"customer_name": customer.customer_name,
				"primary_email": primary_email,
				"billing_email": billing_email,
			}
		)
	return customer_list


@frappe.whitelist()
def get_customer_emails(customer_name, primary_mandatory, billing_and_primary=True):
	"""Returns first email from Contact Email table as a Billing email
	when Is Billing Contact checked
	and Primary email- email with Is Primary checked"""

	# billing_email = frappe.db.sql(
	# 	"""
	# 	SELECT
	# 		email.email_id
	# 	FROM
	# 		`tabContact Email` AS email
	# 	JOIN
	# 		`tabDynamic Link` AS link
	# 	ON
	# 		email.parent=link.parent
	# 	JOIN
	# 		`tabContact` AS contact
	# 	ON
	# 		contact.name=link.parent
	# 	WHERE
	# 		link.link_doctype='Customer'
	# 		and link.link_name=%s
	# 		and contact.is_billing_contact=1
	# 	ORDER BY
	# 		contact.creation desc""",
	# 	customer_name,
	# )

	"""Returns customer statement email address"""
	billing_email = frappe.db.sql(
		"""
		SELECT
			customer_statement_email_address
		FROM
			`tabCustomer`
		WHERE
			name=%s""",
		customer_name,
	)

	if len(billing_email) == 0 or (billing_email[0][0] is None):
		if billing_and_primary:
			frappe.throw(_("No billing email found for customer: {0}").format(customer_name))
		else:
			return ""

	if billing_and_primary:
		# primary_email = frappe.get_value("Customer", customer_name, "email_id")
		primary_email = frappe.get_value("Customer", customer_name, "customer_statement_email_address")
		if primary_email is None and int(primary_mandatory):
			frappe.throw(_("No primary email found for customer: {0}").format(customer_name))
		return [primary_email or "", billing_email[0][0]]
	else:
		return billing_email[0][0] or ""

@frappe.whitelist()
def download_statements(document_name):
	doc = frappe.get_doc("Process Statement Of Accounts", document_name)
	report = get_report_pdf(doc)
	if report:
		frappe.local.response.filename = doc.company + " - Statement of Account.pdf"
		frappe.local.response.filecontent = report
		frappe.local.response.type = "download"

@frappe.whitelist()
def download_individual_statement(document_name,customer):
	doc = frappe.get_doc("Process Statement Of Accounts", document_name)
	report = get_report_pdf(doc,consolidated=True,customer=customer)
	if report:
		frappe.local.response.filename = doc.company + " - Statement of Account - " + customer + ".pdf"
		frappe.local.response.filecontent = report
		frappe.local.response.type = "download"


@frappe.whitelist()
def send_emails(document_name, from_scheduler=False):

	doc = frappe.get_doc("Process Statement Of Accounts", document_name)

	#Send email to admin
	frappe.publish_realtime(event='msgprint', message="Customer statements running.<br><br><b style='color:red;'>Dont reboot the server</b>",user = "Administrator")
	company = get_default_company()
	
	enqueue_args = {
		"queue": "short",
		"method": frappe.sendmail,
		"recipients": "IT@Fxmed.co.nz",
		"subject": doc.company + ": Customer Statements Sending Started",
		"message": (
			"Hi IT,<br><br><b>Company</b>: " + str(doc.company) +
			"<br><b>From</b>: " + str(doc.from_date) +
			"<br><b>To</b>: " + str(doc.to_date) +
			"<br><br><b>DO NOT RESTART UNTIL COMPLETE</b><br><br>Kind Regards, ERPNext"
		),
		"is_async": True,
		"reference_doctype": "Process Statement Of Accounts",
		"reference_name": document_name
	}

	if company == "FxMed":
		sender = "ar@fxmed.co.nz"
		enqueue_args["sender"] = sender
	elif company == "RN Labs":
		sender = "ar@rnlabs.com.au"
		enqueue_args["sender"] = sender
	else:
		company = None
		sender = None

	frappe.enqueue(**enqueue_args)

	report = get_report_pdf(doc, consolidated=False)

	if report:
		for customer, report_pdf in report.items():
			attachments = [{"fname": doc.company + " - Statement of Account - " + customer + ".pdf", "fcontent": report_pdf}]

			recipients, cc = get_recipients_and_cc(customer, doc)
			if not recipients:
				continue
			context = get_context(customer, doc)
			subject = frappe.render_template(doc.subject, context)
			message = frappe.render_template(doc.body, context)

			enqueue_args = {
				"queue":"short",
				"method":frappe.sendmail,
				"recipients":recipients,
				# sender=frappe.session.user, #Send as default outgoing
				"cc":cc,
				"subject":subject,
				"message":message,
				# now=True,
				"is_async":True,
				"reference_doctype":"Process Statement Of Accounts",
				"reference_name":document_name,
				"attachments":attachments,
			}

			if company == "FxMed" or company == "RN Labs":
				enqueue_args["sender"] = sender

			frappe.enqueue(**enqueue_args)

			customerDoc = frappe.get_doc('Customer', customer)
			customerDoc.add_comment("Comment",'Customer has been sent a Statement of Accounts Email from us.')

			#Create Statement Doc
			create_statement(doc, customer, recipients[0])

		# if doc.enable_auto_email and from_scheduler:
		# 	new_to_date = getdate(today())
		# 	if doc.frequency == "Weekly":
		# 		new_to_date = add_days(new_to_date, 7)
		# 	else:
		# 		new_to_date = add_months(new_to_date, 1 if doc.frequency == "Monthly" else 3)
		# 	new_from_date = add_months(new_to_date, -1 * doc.filter_duration)
		# 	doc.add_comment(
		# 		"Comment", "Emails sent on: " + frappe.utils.format_datetime(frappe.utils.now())
		# 	)
		# 	doc.db_set("to_date", new_to_date, commit=True)
		# 	doc.db_set("from_date", new_from_date, commit=True)
		
		if doc.schedule_send and from_scheduler:

			new_from_date = add_months(doc.from_date, 1)
			temp_to_date = add_months(doc.from_date, 2)
			new_to_date =  add_days(temp_to_date, -1)
			doc.add_comment(
				"Comment", "Emails sent on: " + frappe.utils.format_datetime(frappe.utils.now())
			)

			doc.db_set("schedule_send", 0, commit=True)
			doc.db_set("from_date", new_from_date, commit=True)
			doc.db_set("to_date", new_to_date, commit=True)

			enqueue_args = {
				"queue":"short",
				"method":frappe.sendmail,
				"recipients":["IT@Fxmed.co.nz","ar@fxmed.co.nz"],
				# sender=frappe.session.user, #Send as default outgoing
				"subject": doc.company + ": Customer Statements Sending Complete",
				"message":"Hi IT,<br><br><b>Company</b>: " + str(doc.company) + "<br><b>From</b>: " + str(doc.from_date) + "<br><b>To</b>: " + str(doc.to_date) + "<br><b>Customers Analysed</b>: " + str(len(doc.customers)) + "<br><b>Customers Sent</b>: " + str(len(report)) + "<br><br>Kind Regards, ERPNext",
				# now=True,
				"is_async":True,
				"reference_doctype":"Process Statement Of Accounts",
				"reference_name":document_name
			}

			if company == "FxMed":
				enqueue_args["sender"] = sender

			frappe.enqueue(**enqueue_args)
		

		enqueue_args = {
			"queue":"short",
			"method":frappe.sendmail,
			"recipients":["IT@Fxmed.co.nz","ar@fxmed.co.nz"],
			# sender=frappe.session.user, #Send as default outgoing
			"subject": doc.company + ": Customer Statements Sending Complete",
			"message":"Hi IT,<br><br><b>Company</b>: " + str(doc.company) + "<br><b>From</b>: " + str(doc.from_date) + "<br><b>To</b>: " + str(doc.to_date) + "<br><b>Customers Analysed</b>: " + str(len(doc.customers)) + "<br><b>Customers Sent</b>: " + str(len(report)) + "<br><br>Kind Regards, ERPNext",
			# now=True,
			"is_async":True,
			"reference_doctype":"Process Statement Of Accounts",
			"reference_name":document_name
		}

		if company == "FxMed":
			enqueue_args["sender"] = sender
			
		#Send email to admin
		frappe.enqueue(**enqueue_args)

		frappe.publish_realtime(event='msgprint', message="Customer statements finished",user = "Administrator")
		return True
	else:
		return False


@frappe.whitelist()
def send_auto_email():
	
	# Disabling because we will never auto-send as we need to do all reconciliations before sending
	# selected = frappe.get_list(
	# 	"Process Statement Of Accounts",
	# 	filters={"to_date": today(), "enable_auto_email": 1},
	# )

	selected = frappe.get_list(
		"Process Statement Of Accounts",
		filters={"schedule_send": 1},
	)

	for entry in selected:
		customerAccountDoc = frappe.get_doc("Process Statement Of Accounts", entry)

		if customerAccountDoc.collection_name or (customerAccountDoc.customer_collection == "Custom Logic" and customerAccountDoc.logic):
			#Refresh customers in 'Customers' table
			if customerAccountDoc.customer_collection == "Custom Logic":
				custom_logic = customerAccountDoc.logic
			else:
				custom_logic = None
			
			customerAccountDoc.set('customers', [])
			customerList = fetch_customers(customerAccountDoc.customer_collection, customerAccountDoc.collection_name, customerAccountDoc.primary_mandatory, custom_logic)
			for customer in customerList:
				customerAccountDoc.append('customers', {
					"customer": customer['name'],
					"primary_email": customer['primary_email'],
					"billing_email": customer['billing_email']
				})
			customerAccountDoc.save()

		send_emails(entry.name, from_scheduler=True)
	return True
