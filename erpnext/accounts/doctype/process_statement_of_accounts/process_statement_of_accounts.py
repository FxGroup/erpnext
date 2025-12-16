# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import re
import copy
import frappe

from frappe import _
from frappe.utils.pdf import get_pdf
from frappe.model.document import Document
from erpnext import get_default_company
from frappe.utils.jinja import validate_template
from frappe.www.printview import get_print_style
from frappe.utils import add_days, nowdate, add_months, format_date, getdate, today
from erpnext.accounts.report.general_ledger.general_ledger import execute as get_soa
from erpnext.accounts.report.accounts_receivable.accounts_receivable import execute as get_ar_soa
from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (execute as get_ageing)


logger = frappe.logger("Process Statement of Account", allow_site=False, file_count=1, max_size=500000000)


class ProcessStatementOfAccounts(Document):
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
	logger.info("[Get Report PDF] Starting PDF generation. Consolidated: {}, Customer: {}".format(consolidated, customer))

	if customer:
		doc.customers = [
			cust for cust in doc.customers if cust.customer == customer
		]

	statement_dict = get_statement_dict(doc)

	if not bool(statement_dict):
		logger.info("[Get Report PDF] No statements generated")
		return False
	elif consolidated:
		delimiter = '<div style="page-break-before: always;"></div>' if doc.include_break else ""
		result = delimiter.join(list(statement_dict.values()))
		logger.info("[Get Report PDF] Generating consolidated PDF for {} customers".format(len(statement_dict)))
		return get_pdf(result, {"orientation": doc.orientation})
	else:
		logger.info("[Get Report PDF] Generating individual PDFs for {} customers".format(len(statement_dict)))
		for idx, (customer, statement_html) in enumerate(statement_dict.items(), 1):
			logger.info("[Get Report PDF][{}/{}] Generating statement for customer: {}".format(idx, len(statement_dict), customer))
			statement_dict[customer] = get_pdf(statement_html, {"orientation": doc.orientation}, meta={"base64":base64})

		logger.info("[Get Report PDF] Finished generating {} individual PDFs".format(len(statement_dict)))
		return statement_dict


def consolidate_vouchers(res):
	consolidated = []
	voucher_map = {}

	for row in res:
		if not row.get('voucher_no'):
			consolidated.append(row)
			continue

		voucher_key = (
			row.get('voucher_type'),
			row.get('voucher_no'),
			row.get('party')
		)

		if voucher_key not in voucher_map:
			voucher_map[voucher_key] = len(consolidated)
			consolidated.append(row.copy())
		else:
			idx = voucher_map[voucher_key]
			consolidated[idx]['debit'] = (consolidated[idx].get('debit', 0) or 0) + (row.get('debit', 0) or 0)
			consolidated[idx]['credit'] = (consolidated[idx].get('credit', 0) or 0) + (row.get('credit', 0) or 0)
			consolidated[idx]['debit_in_account_currency'] = (consolidated[idx].get('debit_in_account_currency', 0) or 0) + (row.get('debit_in_account_currency', 0) or 0)
			consolidated[idx]['credit_in_account_currency'] = (consolidated[idx].get('credit_in_account_currency', 0) or 0) + (row.get('credit_in_account_currency', 0) or 0)

			if consolidated[idx].get('against_voucher'):
				consolidated[idx]['against_voucher'] = None
				consolidated[idx]['against_voucher_type'] = None

	balance = 0
	for row in consolidated:
		if row.get('account') == "'Opening'":
			balance = row.get('balance', 0) or 0
		elif row.get('voucher_no'):
			debit = row.get('debit', 0) or 0
			credit = row.get('credit', 0) or 0
			balance += debit - credit
			row['balance'] = balance
		elif row.get('account') in ["'Total'", "'Closing (Opening + Total)'"]:
			row['balance'] = balance

	return consolidated


def get_statement_dict(doc, get_statement_dict=False):
	logger.info("[Get Statement Dict] Starting statement dictionary generation")
	statement_dict = {}
	ageing = ""

	filters = get_common_filters(doc)

	if doc.ignore_exchange_rate_revaluation_journals:
		filters.update({"ignore_err": True})

	if doc.ignore_cr_dr_notes:
		filters.update({"ignore_cr_dr_notes": True})

	party_list = []
	for entry in doc.customers:
		party_list.append(entry.customer)

	logger.info("[Get Statement Dict] Party list built. Party count: {}".format(len(party_list)))
	logger.info("[Get Statement Dict] Starting AR Ageing calculation")

	if doc.include_ageing:
		ageing = set_ageing(doc,party_list)

	logger.info("[Get Statement Dict] AR Ageing calculation completed")
	logger.info("[Get Statement Dict] Starting GL Data fetch")

	if doc.report == "General Ledger":
		filters.update(get_gl_filters(doc))
		col, res = get_soa(filters)
	else:
		return []

	logger.info("[Get Statement Dict] GL data fetch completed")

	for idx, entry in enumerate(doc.customers, 1):
		logger.info("[Get Statement Dict][{}/{}] Processing customer: {}".format(idx, len(doc.customers), entry.customer))

		if doc.report == "General Ledger":
			filtered_res = []
			in_customer_section = False
			pending_opening_rows = []

			for i, r in enumerate(res):
				account = r.get("account", "")

				if not account:
					continue

				if r.get("party") == entry.customer:
					filtered_res.extend(pending_opening_rows)
					pending_opening_rows = []
					filtered_res.append(r)
					in_customer_section = True
				elif r.get("party") and r.get("party") != entry.customer:
					in_customer_section = False
					pending_opening_rows = []
				elif not r.get("party"):
					if account in ["'Opening'", "'Total'", "'Closing (Opening + Total)'"]:
						if in_customer_section:
							filtered_res.append(r)

							if account == "'Closing (Opening + Total)'":
								break
						elif account == "'Opening'":
							if not filtered_res:
								pending_opening_rows = [r]
					elif r.get("debit_in_transaction_currency") is None:
						if in_customer_section:
							filtered_res.append(r)
							in_customer_section = False
						else:
							if not filtered_res:
								pending_opening_rows = []

			# Consolidate duplicate vouchers (payments/invoices split across multiple rows) per customer
			customer_res = consolidate_vouchers(filtered_res)

			if not customer_res:
				continue

			for row in customer_res:
				if row.get("account"):
					row["account"] = (
						row["account"]
						.replace("'", "")
						.replace("Closing (Opening + Total)", "Closing")
					)

			new_res = []
			for item in customer_res:
				if item.get("debit") == item.get("credit") and item.get("account") not in ["Closing", "Opening"]:
					continue
				else:
					new_res.append(item)

			customer_res = new_res

			if len(customer_res) == 2:
				if customer_res[1]["debit"] == 0 or (customer_res[1]["balance"] > -0.01 and customer_res[1]["balance"] < 0.01):
					if not doc.produce_0_statements:
						continue
  
			if len(customer_res) == 0:
				if not doc.produce_0_statements:
					continue

			if len(customer_res) >= 1:
				if customer_res[-1]["balance"] == 0:
					if not doc.produce_0_statements:
						continue

				if doc.exclude_balances_below:
					if customer_res[-1]["balance"] < float(doc.exclude_balances_below):
						continue

		else:
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

	logger.info("[Get Statement Dict] Completed statement dictionary generation. Total statements: {}".format(len(statement_dict)))
	return statement_dict


def set_ageing(doc, party_list):
	logger.info("[Set Ageing] Starting ageing calculation for {} parties".format(len(party_list)))
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

	logger.info("[Set Ageing] Ageing calculation completed. Records: {}".format(len(ageing) if ageing else 0))
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
		"report_date": doc.to_date,
		"party_type": "Customer",
		"party": [c.customer for c in doc.customers],
		"party_name": [c.customer_name for c in doc.customers],
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
	logger.info("[Get HTML][{}] Rendering HTML template for this customer".format(entry.customer))
	base_template_path = "frappe/www/printview.html"
	template_path = "erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts_accounts_receivable.html"
	if doc.report == "General Ledger":
		template_path = (
			"erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts.html"
		)

	process_soa_html = frappe.get_hooks("process_soa_html")
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
			"entry": entry,
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

	logger.info("[Get HTML][{}] HTML template rendered successfully for this customer.".format(entry.customer))
	return html


def get_customers_based_on_territory_or_customer_group(customer_collection, collection_name, currency):
	logger.info("[Get Customers Territory/Group] Fetching customers for collection: {}, name: {}, currency: {}".format(customer_collection, collection_name, currency))
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
 
	customers = frappe.get_list(
		"Customer",
		fields=["name", "customer_name", "customer_primary_email_address", "customer_statement_email_address"],
		filters=[[fields_dict[customer_collection], "IN", selected]],
	)
 
	logger.info("[Get Customers Territory/Group] Found {} customers".format(len(customers)))
	return customers


def get_logic_context(doc):
	return {"doc": doc, "nowdate": nowdate, "frappe": frappe._dict(utils=frappe.utils)}


def get_customers_based_on_custom_logic(custom_logic, currency=None):
	logger.info("[Get Customers Custom Logic] Fetching customers with custom logic, currency: {}".format(currency))
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

	logger.info("[Get Customers Custom Logic] Filtered to {} customers from {} total".format(len(passCustomerList), len(customerList)))
	return passCustomerList


def get_customers_based_on_sales_person(sales_person, currency):
	logger.info("[Get Customers Sales Person] Fetching customers for sales person: {}, currency: {}".format(sales_person, currency))
	lft, rgt = frappe.db.get_value("Sales Person", sales_person, ["lft", "rgt"])
	records = frappe.db.sql("""
		SELECT
			DISTINCT parent,
			parenttype
		FROM
			`tabSales Team` steam
		WHERE
			parenttype = 'Customer'
			AND EXISTS(
				SELECT
					name
				FROM
					`tabSales Person`
				WHERE
					lft >= %s
					AND rgt <= %s
					AND name = steam.sales_person
			)
	""", (lft, rgt), as_dict=1)
 
	sales_person_records = frappe._dict()
	for d in records:
		sales_person_records.setdefault(d.parenttype, set()).add(d.parent)
  
	if sales_person_records.get("Customer"):
		customers = frappe.get_list(
			"Customer",
			fields=["name", "customer_name", "customer_primary_email_address", "customer_statement_email_address"],
			filters=[
				["name", "in", list(sales_person_records["Customer"])],
				["default_currency", "=", currency],
			]
		)
		logger.info("[Get Customers Sales Person] Found {} customers".format(len(customers)))
		return customers
	else:
		logger.info("[Get Customers Sales Person] No customers found")
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
	logger.info("[Fetch Customers] Fetching customers for collection: {}, collection_name: {}, currency: {}".format(collection, collection_name, currency))
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
	
	logger.info("[Fetch Customers] Successfully returning customer list with {} records.".format(len(customer_list)))
	return customer_list


@frappe.whitelist()
def download_statements(document_name):
	doc = frappe.get_doc("Process Statement Of Accounts", document_name)

	logger.info("[Download Statement] Starting PDF generation for all customers using doc: {}".format(document_name))
	report = get_report_pdf(doc)
	logger.info("[Download Statement]  Finished PDF generation for all customers")
 
	if report:
		frappe.local.response.filename = doc.company + " - Statement of Account.pdf"
		frappe.local.response.filecontent = report
		frappe.local.response.type = "download"


@frappe.whitelist()
def download_individual_statement(document_name, customer):
	doc = frappe.get_doc("Process Statement Of Accounts", document_name)
 
	logger.info("[Download Indv Statement] Starting PDF generation for customer: {} using doc: ".format(customer, document_name))
	report = get_report_pdf(doc,consolidated=True,customer=customer)
	logger.info("Download Indv Statement] Finished PDF generation for customer: {}".format(customer))
 
	if report:
		frappe.local.response.filename = doc.company + " - Statement of Account - " + customer + ".pdf"
		frappe.local.response.filecontent = report
		frappe.local.response.type = "download"


@frappe.whitelist()
def send_emails(document_name, from_scheduler=False):
	doc = frappe.get_doc("Process Statement Of Accounts", document_name)
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
	elif company == "Therahealth":
		sender = "support@therahealth.com.au"
		enqueue_args["sender"] = sender
	else:
		company = None
		sender = None
		logger.error("Company not recognised for sending customer statements email.")
		return

	frappe.enqueue(**enqueue_args)
	
	logger.info("[Send Email] Starting PDF generation for all customers")
	report = get_report_pdf(doc, consolidated=False)
	logger.info("[Send Email] Finished PDF generation for all customers")

	if report:
		logger.info("[Send Email] Starting email sending to {} customers".format(len(report)))
		for customer, report_pdf in report.items():
			attachments = [{"fname": doc.company + " - Statement of Account - " + customer + ".pdf", "fcontent": report_pdf}]

			recipients, cc = get_recipients_and_cc(customer, doc)
			if not recipients:
				continue

			context = get_context(customer, doc)
			subject = frappe.render_template(doc.subject, context)
			message = frappe.render_template(doc.body, context)

			if not frappe.conf.production_site:
				recipients = "it@fxmed.co.nz"
    
			enqueue_args = {
				"queue":"short",
				"method":frappe.sendmail,
				"sender":sender,
				"recipients":recipients,
				"cc":cc,
				"subject":subject,
				"message":message,
				"is_async":True,
				"reference_doctype":"Process Statement Of Accounts",
				"reference_name":document_name,
				"attachments":attachments,
			}

			frappe.enqueue(**enqueue_args)
			customerDoc = frappe.get_doc('Customer', customer)
			customerDoc.add_comment("Comment",'Customer has been sent a Statement of Accounts Email from us.')
			recipient = ", ".join(recipients)

			create_statement(doc, customer, recipient)
			logger.info("[Send Email][{}] Successfully create Statement of Account document and sent email for this customer.".format(customer))
   
		logger.info("[Send Email] Finished email sending to all customers.")

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
				"subject": doc.company + ": Customer Statements Sending Complete",
				"sender": sender,
				"message":"Hi IT,<br><br><b>Company</b>: " + str(doc.company) + "<br><b>From</b>: " + str(doc.from_date) + "<br><b>To</b>: " + str(doc.to_date) + "<br><b>Customers Analysed</b>: " + str(len(doc.customers)) + "<br><b>Customers Sent</b>: " + str(len(report)) + "<br><br>Kind Regards, ERPNext",
				"is_async":True,
				"reference_doctype":"Process Statement Of Accounts",
				"reference_name":document_name
			}

			frappe.enqueue(**enqueue_args)

		enqueue_args = {
			"queue":"short",
			"method":frappe.sendmail,
			"recipients":["IT@Fxmed.co.nz","ar@fxmed.co.nz"],
			"sender": sender,
			"subject": doc.company + ": Customer Statements Sending Complete",
			"message":"Hi IT,<br><br><b>Company</b>: " + str(doc.company) + "<br><b>From</b>: " + str(doc.from_date) + "<br><b>To</b>: " + str(doc.to_date) + "<br><b>Customers Analysed</b>: " + str(len(doc.customers)) + "<br><b>Customers Sent</b>: " + str(len(report)) + "<br><br>Kind Regards, ERPNext",
			"is_async":True,
			"reference_doctype":"Process Statement Of Accounts",
			"reference_name":document_name
		}
			
		frappe.enqueue(**enqueue_args)
		frappe.publish_realtime(event='msgprint', message="Customer statements finished", user="Administrator")
		return True
	else:
		return False


@frappe.whitelist()
def send_auto_email():
	selected = frappe.get_list(
		"Process Statement Of Accounts",
		filters={"schedule_send": 1},
	)

	logger.info("[Send Auto Email]")
	for entry in selected:
		logger.info("[Send Auto Email] Processing Process Statement of Accounts: {}".format(entry.name))
		processStatementDoc = frappe.get_doc("Process Statement Of Accounts", entry)

		if processStatementDoc.customers:
			logger.info("[Send Auto Email] Using {} existing customers from Process Statement of Accounts document".format(len(processStatementDoc.customers)))
		else:
			logger.info("[Send Auto Email] No customers found in document, skipping")
			continue

		logger.info("[Send Auto Email] Sending emails for Process Statement of Accounts: {}".format(entry.name))
		send_emails(entry.name, from_scheduler=True)
		logger.info("[Send Auto Email] Finished sending emails for Process Statement of Accounts: {}".format(entry.name))


def create_statement(doc, customer, recipient):
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

	current_datetime = frappe.utils.now_datetime()
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