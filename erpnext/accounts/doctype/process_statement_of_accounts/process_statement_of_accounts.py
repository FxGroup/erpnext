# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import copy

import frappe
import os
import re
from frappe import _
from frappe.desk.reportview import get_match_cond
from frappe.model.document import Document
from frappe.utils import add_days, nowdate, add_months, format_date, getdate, today, flt
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

import pdb
from erpnext import get_default_company

logger = frappe.logger(module="CustomerStatements", allow_site=True, with_more_info=False, file_count=2)

class ProcessStatementOfAccounts(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.accounts.doctype.process_statement_of_accounts_cc.process_statement_of_accounts_cc import (
			ProcessStatementOfAccountsCC,
		)
		from erpnext.accounts.doctype.process_statement_of_accounts_customer.process_statement_of_accounts_customer import (
			ProcessStatementOfAccountsCustomer,
		)
		from erpnext.accounts.doctype.psoa_cost_center.psoa_cost_center import PSOACostCenter
		from erpnext.accounts.doctype.psoa_project.psoa_project import PSOAProject

		account: DF.Link | None
		ageing_based_on: DF.Literal["Due Date", "Posting Date"]
		based_on_payment_terms: DF.Check
		body: DF.TextEditor | None
		categorize_by: DF.Literal["", "Categorize by Voucher", "Categorize by Voucher (Consolidated)"]
		cc_to: DF.TableMultiSelect[ProcessStatementOfAccountsCC]
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
		self.validate_account()
		self.validate_company_for_table("Cost Center")
		self.validate_company_for_table("Project")

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

	def validate_account(self):
		if not self.account:
			return

		if self.company != frappe.get_cached_value("Account", self.account, "company"):
			frappe.throw(
				_("Account {0} doesn't belong to Company {1}").format(
					frappe.bold(self.account),
					frappe.bold(self.company),
				)
			)

	def validate_company_for_table(self, doctype):
		field = frappe.scrub(doctype)
		if not self.get(field):
			return

		fieldname = field + "_name"

		values = set(d.get(fieldname) for d in self.get(field))
		invalid_values = frappe.db.get_all(
			doctype, filters={"name": ["in", values], "company": ["!=", self.company]}, pluck="name"
		)

		if invalid_values:
			msg = _("<p>Following {0}s doesn't belong to Company {1} :</p>").format(
				doctype, frappe.bold(self.company)
			)

			msg += (
				"<ul>"
				+ "".join(_("<li>{}</li>").format(frappe.bold(row)) for row in invalid_values)
				+ "</ul>"
			)

			frappe.throw(_(msg))

def get_report_pdf(doc, consolidated=True, customer=None, base64=False):
	#Allow user to download report for just 1 customer
	if customer:
		doc.customers = [
			cust for cust in doc.customers if cust.customer == customer
		]
	statement_dict = get_statement_dict(doc)
	
	if not bool(statement_dict):
		return False
	elif consolidated:
		delimiter = '<div style="page-break-before: always;"></div>' if doc.include_break else ""
		result = delimiter.join(list(statement_dict.values()))
		return get_pdf(result, {"orientation": doc.orientation})
	else:
		for customer, statement_html in statement_dict.items():
			logger.info("Generating statement for customer: {}".format(customer))
			statement_dict[customer] = get_pdf(statement_html, {"orientation": doc.orientation}, meta={"base64":base64})
		return statement_dict

def get_statement_dict(doc, get_statement_dict=False):
	statement_dict = {}
	ageing = ""

	filters = get_common_filters(doc)

	if doc.ignore_exchange_rate_revaluation_journals:
		filters.update({"ignore_err": True})

	if doc.ignore_cr_dr_notes:
		filters.update({"ignore_cr_dr_notes": True})

	logger.info("Building party list for processing")
	party_list = []
	for entry in doc.customers:
		party_list.append(entry.customer)

	logger.info("Party list built. Party count: {}".format(len(party_list)))
	logger.info("Starting AR Ageing calculation")

	if doc.include_ageing:
		ageing = set_ageing(doc,party_list)

	logger.info("AR Ageing calculation completed")
	logger.info("Starting GL Data fetch")

	if doc.report == "General Ledger":
		filters.update(get_gl_filters(doc))
		col, res = get_soa(filters)

	logger.info("GL data fetch completed")

	for entry in doc.customers:
		logger.info("Processing customer: {}".format(entry.customer))

		if doc.report == "General Ledger":

			# Filter logic: Keep rows where party matches OR rows that are Opening/Total/Closing
			# that belong to this customer's section (determined by proximity to party rows)
			filtered_res = []
			in_customer_section = False
			pending_opening_rows = []

			for i, r in enumerate(res):
				account = r.get("account", "")

				# Skip rows without an account value
				if not account:
					continue

				# Check if this is a party-specific row (transaction row)
				if r.get("party") == entry.customer:
					# Add any pending opening rows we collected
					filtered_res.extend(pending_opening_rows)
					pending_opening_rows = []

					# Add this transaction row
					filtered_res.append(r)
					in_customer_section = True

				elif r.get("party") and r.get("party") != entry.customer:
					# Different customer - exit this customer's section
					in_customer_section = False
					pending_opening_rows = []

				elif not r.get("party"):
					# This row has no party field - could be Opening/Total/Closing or separator

					# Check if it's a party-specific Opening/Total/Closing row
					if account in ["'Opening'", "'Total'", "'Closing (Opening + Total)'"]:
						if in_customer_section:
							# We're in the customer section, so this Total/Closing row belongs to them
							filtered_res.append(r)

							# Closing row is the last row for this customer - stop processing
							if account == "'Closing (Opening + Total)'":
								break
						elif account == "'Opening'":
							# Only collect the opening row if we haven't found the customer yet
							# Replace any previously collected rows - we only want the most recent opening
							if not filtered_res:
								pending_opening_rows = [r]
						# Note: Total and Closing rows are NOT collected - they only apply if we're already in_customer_section

					# Check if it's a separator row (blank row between parties)
					elif r.get("debit_in_transaction_currency") is None:
						if in_customer_section:
							# Add separator after customer section
							filtered_res.append(r)
							# Exit customer section after separator
							in_customer_section = False
						else:
							# Clear pending rows when we hit a separator - new section starting
							if not filtered_res:
								pending_opening_rows = []

			customer_res = filtered_res

			if not customer_res:
				continue

			# Clean up account field values by removing quotes and simplifying closing label
			for row in customer_res:
				if row.get("account"):
					row["account"] = (
						row["account"]
						.replace("'", "")
						.replace("Closing (Opening + Total)", "Closing")
					)

			#Cleanup Res
			new_res = []
			for item in customer_res:
				# Clean up account field values by removing quotes and simplifying closing label
				if item.get("debit") == item.get("credit") and item.get("account") not in ["Closing", "Opening"]:
					continue
				else:
					new_res.append(item)

			customer_res = new_res

			if len(customer_res) == 2:
				#No Transactions this month
				if customer_res[1]["debit"] == 0 or (customer_res[1]["balance"] > -0.01 and customer_res[1]["balance"] < 0.01):
					#No outstanding balance
					if not doc.produce_0_statements:
						continue
			if len(customer_res) == 0:
				if not doc.produce_0_statements:
					continue

			if len(customer_res) >= 1:
				if customer_res[-1]["balance"] == 0:
					#No outstanding balance
					if not doc.produce_0_statements:
						continue

				if doc.exclude_balances_below:
					if customer_res[-1]["balance"] < float(doc.exclude_balances_below):
						continue

		else:
			#Block this path for now - Untested
			return []
			filters.update(get_ar_filters(doc, entry))
			ar_res = get_ar_soa(filters)
			col, customer_res = ar_res[0], ar_res[1]
			if not customer_res:
				continue

		customer_ageing = []
		for customer in ageing:
			if customer.get("party") == entry.customer:
				customer_ageing = [customer]
				break

		statement_dict[entry.customer] = (
			[customer_res, customer_ageing] if get_statement_dict else get_html(doc, filters, entry, col, customer_res, customer_ageing)
		)

	return statement_dict

def set_ageing(doc, party_list):
	ageing_filters = frappe._dict(
		{
			"company": doc.company,
			"report_date": doc.to_date,
			"ageing_based_on": doc.ageing_based_on,
			"calculate_ageing_with": "Report Date",
			"range": "30, 60, 90, 120",
			"party_type": "Customer",
			"party": party_list,
			"show_gl_balance": 1,
			"show_future_payments": 1,
			"convert_currency": 1
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
			"in_party_currency": True
		}
	)

def get_gl_filters(doc):
	return {
		"from_date": doc.from_date,
		"to_date": doc.to_date,
		"report_date": doc.to_date,  # Use to_date for historical statements
		"party_type": "Customer",
		"party": [c.customer for c in doc.customers],
		"party_name": "Customer",
		"presentation_currency": doc.currency,
		"categorize_by": "Categorize by Party",
		"currency": doc.currency,
		"project": [p.project_name for p in doc.project],
		"show_opening_entries": 1,
		"include_default_book_entries": 0,
		"tax_id": None,
		"show_net_values_in_party_account": True,
		"include_all_parties": True
	}

def get_ar_filters(doc, entry):
	return {
		"report_date": doc.posting_date if doc.posting_date else None,
		"party_type": "Customer",
		"party": [entry.customer],
		"in_party_currency": True,
		"customer_name": entry.customer_name if entry.customer_name else None,
		"payment_terms_template": doc.payment_terms_template if doc.payment_terms_template else None,
		"sales_partner": doc.sales_partner if doc.sales_partner else None,
		"sales_person": doc.sales_person if doc.sales_person else None,
		"territory": doc.territory if doc.territory else None,
		"based_on_payment_terms": doc.based_on_payment_terms,
		"show_future_payments": doc.show_future_payments,
		"report_name": "Accounts Receivable",
		"ageing_based_on": doc.ageing_based_on,
		"range1": 30,
		"range2": 60,
		"range3": 90,
		"range4": 120,
	}

def get_html(doc, filters, entry, col, res, ageing):
	base_template_path = "frappe/www/printview.html"
	template_path = "erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts_accounts_receivable.html"
	if doc.report == "General Ledger":
		template_path = (
			"erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts.html"
		)

	process_soa_html = frappe.get_hooks("process_soa_html")
	# fetching custom print format for Process Statement of Accounts
	if process_soa_html and process_soa_html.get(doc.report):
		template_path = process_soa_html[doc.report][-1]

	if doc.print_format:
		custom_html, custom_css = frappe.db.get_value("Print Format", doc.print_format, ["html", "css"])
		template_path = f"<style>{custom_css}</style> {custom_html}"

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


def get_customers_based_on_territory_or_customer_group(customer_collection, collection_name, currency):
	fields_dict = {
		"Customer Group": "customer_group",
		"Territory": "territory",
	}
	collection = frappe.get_doc(customer_collection, collection_name)
	selected = [
		customer.name
		for customer in frappe.get_list(
			customer_collection,
			filters=[
				["lft", ">=", collection.lft], 
				["rgt", "<=", collection.rgt],
				["default_currency", "=", currency]
			],
			fields=["name"],
			order_by="lft asc, rgt desc",
		)
	]
	return frappe.get_list(
		"Customer",
		fields=["name", "customer_name", "customer_primary_email_address", "customer_statement_email_address"],
		filters=[[fields_dict[customer_collection], "IN", selected]],
	)

def get_logic_context(doc):
	return {"doc": doc, "nowdate": nowdate, "frappe": frappe._dict(utils=frappe.utils)}

def get_customers_based_on_custom_logic(custom_logic, currency=None):
	"""
	Get list of customers based on custom logic evaluation.

	Args:
		custom_logic (str): Custom logic expression to evaluate
		currency (str): Optional currency filter

	Returns:
		list: List of customer dictionaries
	"""
	customerList = frappe.db.sql(
		"""
		SELECT
			name,
			customer_primary_email_address,
			customer_statement_email_address
		FROM
			`tabCustomer`
		WHERE
			disabled = 0
			AND customer_group != 'Patient'
			{currency_filter}
		""".format(
			currency_filter=f"AND default_currency = '{currency}'" if currency else ""
		),
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

def get_customers_based_on_sales_person(sales_person, currency):
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
			fields=["name", "customer_name", "customer_primary_email_address", "customer_statement_email_address"],
			filters=[
				["name", "in", list(sales_person_records["Customer"])],
				["default_currency", "=", currency],
			]
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
			
			# if clist.primary_email:
			# 	primaryEmails = re.split('; |, |\*|\n', clist.primary_email)
			# 	for primaryEmail in primaryEmails:
			# 		recipients.append(primaryEmail)
			
			
	cc = []
	if doc.cc_to != "":
		try:
			cc = [frappe.get_value("User", user.cc, "email") for user in doc.cc_to]
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
def fetch_customers(collection, collection_name, currency, logic=None):
	customer_list = []
	customers = []

	if collection == "Sales Person":
		customers = get_customers_based_on_sales_person(collection_name, currency)
		if not bool(customers):
			frappe.throw(_("No Customers found with selected options."))
	elif collection == "Custom Logic":
		customers = get_customers_based_on_custom_logic(logic, currency)
		if not bool(customers):
			frappe.throw(_("No Customers found with selected options."))
	else:
		if collection == "Sales Partner":
			customers = frappe.get_list(
				"Customer",
				fields=["name", "customer_name"],
				filters=[
					["default_sales_partner", "=", collection_name],
					["default_currency", "=", currency]
				],
			)
		else:
			customers = get_customers_based_on_territory_or_customer_group(
				collection, collection_name, currency
			)

	for customer in customers:

		if customer['customer_statement_email_address'] or customer['customer_primary_email_address']:
			customer_list.append(
				{
					"name": customer.name,
					"customer_name": customer.customer_name,
					"primary_email": customer['customer_statement_email_address'] or customer['customer_primary_email_address'],
					"billing_email": customer['customer_statement_email_address'] or customer['customer_primary_email_address'],
				}
			)
	return customer_list

@frappe.whitelist()
def download_statements(document_name):
	doc = frappe.get_doc("Process Statement Of Accounts", document_name)
	logger.info("Starting PDF generation for all customers")
	report = get_report_pdf(doc)
	logger.info("Finished PDF generation for all customers")
	if report:
		frappe.local.response.filename = doc.company + " - Statement of Account.pdf"
		frappe.local.response.filecontent = report
		frappe.local.response.type = "download"

@frappe.whitelist()
def download_individual_statement(document_name,customer):
	doc = frappe.get_doc("Process Statement Of Accounts", document_name)
	logger.info("Starting PDF generation for customer: {}".format(customer))
	report = get_report_pdf(doc,consolidated=True,customer=customer)
	logger.info("Finished PDF generation for customer: {}".format(customer))
	if report:
		frappe.local.response.filename = doc.company + " - Statement of Account - " + customer + ".pdf"
		frappe.local.response.filecontent = report
		frappe.local.response.type = "download"


@frappe.whitelist()
def send_emails(document_name, from_scheduler=False):

	doc = frappe.get_doc("Process Statement Of Accounts", document_name)

	#Send email to admin
	#frappe.publish_realtime(event='msgprint', message="Customer statements running.<br><br><b style='color:red;'>Dont reboot the server</b>",user = "Administrator")
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
	elif company == "NaturalMeds":
		sender = "ar@naturalmeds.co.nz"
		enqueue_args["sender"] = sender
	else:
		company = None
		sender = None

	frappe.enqueue(**enqueue_args)
	
	logger.info("Starting PDF generation for all customers")
	report = get_report_pdf(doc, consolidated=False)
	logger.info("Finished PDF generation for all customers")

	if report:
		for customer, report_pdf in report.items():
			attachments = [{"fname": doc.company + " - Statement of Account - " + customer + ".pdf", "fcontent": report_pdf}]

			recipients, cc = get_recipients_and_cc(customer, doc)
			if not recipients:
				continue
			context = get_context(customer, doc)
			subject = frappe.render_template(doc.subject, context)
			message = frappe.render_template(doc.body, context)

			recipients = ["IT@fxmed.co.nz"]  # For testing only

			enqueue_args = {
				"queue":"short",
				"method":frappe.sendmail,
				"recipients":recipients,
				"cc":cc,
				"subject":subject,
				"message":message,
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

			recipient = ", ".join(recipients)

			#Create Statement Doc
			create_statement(doc, customer, recipient)

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

		logger.info("Processing Process Statement Of Accounts: {}".format(entry.name))

		processStatementDoc = frappe.get_doc("Process Statement Of Accounts", entry)

		if processStatementDoc.collection_name or (processStatementDoc.customer_collection == "Custom Logic" and processStatementDoc.logic):
			#Refresh customers in 'Customers' table
			if processStatementDoc.customer_collection == "Custom Logic":
				custom_logic = processStatementDoc.logic
			else:
				custom_logic = None

			processStatementDoc.set('customers', [])

			logger.info("Refreshing customer list based on collection: {}".format(processStatementDoc.customer_collection))

			customerList = fetch_customers(processStatementDoc.customer_collection, processStatementDoc.collection_name, processStatementDoc.currency, processStatementDoc.logic)
			
			logger.info("Fetched {} customers based on collection.".format(len(customerList)))
			logger.info("Appending customers to Process Statement Of Accounts: {}".format(processStatementDoc.name))
			for customer in customerList:
				processStatementDoc.append('customers', {
					"customer": customer['name'],
					"primary_email": customer['primary_email'],
					"billing_email": customer['billing_email']
				})
			processStatementDoc.save()
		
		#Send Emails
		logger.info("Sending emails for Process Statement Of Accounts: {}".format(entry.name))
		send_emails(entry.name, from_scheduler=True)
		logger.info("Finished sending emails for Process Statement Of Accounts: {}".format(entry.name))
		logger.info("Finished processing Process Statement Of Accounts: {}".format(entry.name))
	return True

def create_statement(doc, customer, recipient):
	"""
	Create or retrieve a Statement of Account record.

	Checks if a statement already exists for the given customer and date range.
	If found, returns the existing statement. Otherwise, creates a new one.

	Args:
		doc: Process Statement of Accounts document
		customer (str): Customer name
		recipient (str): Email recipient address

	Returns:
		Statement of Account document (name if existing, document if new)
	"""
	# Check if statement already exists for this customer and date range
	existing_statements = frappe.get_list(
		"Statement of Account",
		filters={
			"customer": customer,
			"from_date": doc.from_date,
			"to_date": doc.to_date
		},
		as_list=True
	)

	if existing_statements:
		return existing_statements[0]

	# Create new statement
	current_datetime = frappe.utils.now_datetime()

	customer_doc = frappe.get_doc("Customer", customer)

	statement = frappe.new_doc("Statement of Account")
	statement.update({
		"naming_series": "CUS-STMT-.YYYY.-",
		"original_date_processed": current_datetime,
		"latest_date_processed": current_datetime,
		"emails_sent": 1,
		"company": doc.company,
		"currency": doc.currency,
		"customer": customer,
		"email": recipient,
		"group_by": doc.group_by,
		"from_date": doc.from_date,
		"to_date": doc.to_date,
		"orientation": doc.orientation,
		"ageing_based_on": doc.ageing_based_on,
		"letter_head": doc.letter_head,
		"include_ageing": doc.include_ageing,
		"subject": doc.subject,
		"body": doc.body
	})

	statement.save()
	return statement