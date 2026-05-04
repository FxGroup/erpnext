# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import copy
from collections import OrderedDict

import frappe
from frappe import _, _dict
from frappe.query_builder import Criterion
from frappe.utils import cstr, getdate

from erpnext import get_company_currency, get_default_company
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
	get_dimension_with_children,
)
from erpnext.accounts.report.financial_statements import get_cost_centers_with_children
from erpnext.accounts.report.utils import convert_to_presentation_currency, get_currency
from erpnext.accounts.utils import get_account_currency


def execute(filters=None):
	if not filters:
		return [], []

	account_details = {}

	if filters and filters.get("print_in_account_currency") and not filters.get("account"):
		frappe.throw(_("Select an account to print in account currency"))

	for acc in frappe.db.sql("""select name, is_group from tabAccount""", as_dict=1):
		account_details.setdefault(acc.name, acc)

	if filters.get("party"):
		filters.party = frappe.parse_json(filters.get("party"))

	validate_filters(filters, account_details)

	validate_party(filters)

	filters = set_account_currency(filters)

	columns = get_columns(filters)

	res = get_result(filters, account_details)

	return columns, res


def validate_filters(filters, account_details):
	if not filters.get("company"):
		frappe.throw(_("{0} is mandatory").format(_("Company")))

	if not filters.get("from_date") and not filters.get("to_date"):
		frappe.throw(
			_("{0} and {1} are mandatory").format(frappe.bold(_("From Date")), frappe.bold(_("To Date")))
		)

	if filters.get("account"):
		filters.account = frappe.parse_json(filters.get("account"))
		for account in filters.account:
			if not account_details.get(account):
				frappe.throw(_("Account {0} does not exists").format(account))

	if not filters.get("categorize_by") and filters.get("group_by"):
		filters["categorize_by"] = filters["group_by"]
		filters["categorize_by"] = filters["categorize_by"].replace("Group by", "Categorize by")

	if filters.get("account") and filters.get("categorize_by") == "Categorize by Account":
		filters.account = frappe.parse_json(filters.get("account"))
		for account in filters.account:
			if account_details[account].is_group == 0:
				frappe.throw(_("Can not filter based on Child Account, if grouped by Account"))

	if filters.get("voucher_no") and filters.get("categorize_by") in ["Categorize by Voucher"]:
		frappe.throw(_("Can not filter based on Voucher No, if grouped by Voucher"))

	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date"))

	if filters.get("project"):
		filters.project = frappe.parse_json(filters.get("project"))

	if filters.get("cost_center"):
		filters.cost_center = frappe.parse_json(filters.get("cost_center"))


def validate_party(filters):
	party_type, party = filters.get("party_type"), filters.get("party")

	if party and party_type:
		for d in party:
			if not frappe.db.exists(party_type, d):
				frappe.throw(_("Invalid {0}: {1}").format(party_type, d))


def set_account_currency(filters):
	if filters.get("account") or (filters.get("party") and len(filters.party) == 1):
		filters["company_currency"] = frappe.get_cached_value("Company", filters.company, "default_currency")
		account_currency = None

		if filters.get("account"):
			if len(filters.get("account")) == 1:
				account_currency = get_account_currency(filters.account[0])
			else:
				currency = get_account_currency(filters.account[0])
				is_same_account_currency = True
				for account in filters.get("account"):
					if get_account_currency(account) != currency:
						is_same_account_currency = False
						break

				if is_same_account_currency:
					account_currency = currency

		elif filters.get("party") and filters.get("party_type"):
			gle_currency = frappe.db.get_value(
				"GL Entry",
				{"party_type": filters.party_type, "party": filters.party[0], "company": filters.company},
				"account_currency",
			)

			if gle_currency:
				account_currency = gle_currency
			else:
				account_currency = (
					None
					if filters.party_type in ["Employee", "Shareholder", "Member"]
					else frappe.get_cached_value(filters.party_type, filters.party[0], "default_currency")
				)

		filters["account_currency"] = account_currency or filters.company_currency
		if filters.account_currency != filters.company_currency and not filters.presentation_currency:
			filters.presentation_currency = filters.account_currency

	return filters

def get_result(filters, account_details):
	accounting_dimensions = []
	if filters.get("include_dimensions"):
		accounting_dimensions = get_accounting_dimensions()

	gl_entries = get_gl_entries(filters, accounting_dimensions)

	# Get all parties with balances if filter is enabled
	all_party_balances = None
	if filters.get("include_all_parties"):
		all_party_balances = get_all_parties_with_balances(filters, accounting_dimensions)

	data = get_data_with_opening_closing(filters, account_details, accounting_dimensions, gl_entries, all_party_balances)

	data = add_transaction_date_to_si(data)

	result = get_result_as_list(data, filters)

	return result


def get_gl_entries(filters, accounting_dimensions):
	currency_map = get_currency(filters)
	select_fields = """, debit, credit, debit_in_account_currency,
		credit_in_account_currency """

	if filters.get("show_remarks"):
		if remarks_length := frappe.db.get_single_value("Accounts Settings", "general_ledger_remarks_length"):
			select_fields += f",substr(remarks, 1, {remarks_length}) as 'remarks'"
		else:
			select_fields += """,remarks"""
	
	if filters.get("show_statement_remarks"):
		select_fields += f", CASE WHEN INSTR(remarks, '\n') > 0 THEN SUBSTR(remarks, 1, INSTR(remarks, '\n') - 1) ELSE remarks END as 'remarks'"

	order_by_statement = "order by posting_date, account, creation"

	if filters.get("include_dimensions"):
		order_by_statement = "order by posting_date, creation"

	if filters.get("categorize_by") == "Categorize by Voucher":
		order_by_statement = "order by posting_date, voucher_type, voucher_no"
	if filters.get("categorize_by") == "Categorize by Account":
		order_by_statement = "order by account, posting_date, creation"

	if filters.get("include_default_book_entries"):
		filters["company_fb"] = frappe.get_cached_value(
			"Company", filters.get("company"), "default_finance_book"
		)

	if filters.get("show_outstanding_amount"):
		filters["show_outstanding_amount"] = 1

	dimension_fields = ""
	if accounting_dimensions:
		dimension_fields = ", ".join(accounting_dimensions) + ","

	transaction_currency_fields = ""
	if filters.get("add_values_in_transaction_currency"):
		transaction_currency_fields = (
			"debit_in_transaction_currency, credit_in_transaction_currency, transaction_currency,"
		)

	gl_entries = frappe.db.sql(
		f"""
		select
			name as gl_entry, posting_date, account, party_type, party,
			voucher_type, voucher_subtype, voucher_no, {dimension_fields}
			cost_center, project, {transaction_currency_fields}
			against_voucher_type, against_voucher, account_currency,
			against, is_opening, creation {select_fields}
		from `tabGL Entry`
		where company=%(company)s {get_conditions(filters)}
		{order_by_statement}
	""",
		filters,
		as_dict=1
	)

	party_name_map = get_party_name_map()

	for gl_entry in gl_entries:
		if gl_entry.party_type and gl_entry.party:
			gl_entry.party_name = party_name_map.get(gl_entry.party_type, {}).get(gl_entry.party)

	if filters.get("presentation_currency"):
		return convert_to_presentation_currency(gl_entries, currency_map, filters)
	else:
		return gl_entries


def get_all_parties_with_balances(filters, accounting_dimensions):
	"""Get all parties with their opening and closing balances, even if no transactions in period"""
	if not filters.get("include_all_parties") or not filters.get("party_type") or not filters.get("party"):
		return None

	# Get all parties of the specified type
	party_type = filters.get("party_type")
	all_parties = filters.get("party")

	party_balances = {}
	currency_map = get_currency(filters)

	# Build account filter if specified
	account_condition = ""
	if filters.get("account"):
		accounts = filters.get("account")
		if not isinstance(accounts, list):
			accounts = [accounts]
		account_condition = "AND account IN %(accounts)s"

	for party in all_parties:
		# Prepare parameters for SQL query
		query_params = {
			"company": filters.get("company"),
			"party_type": party_type,
			"party": party,
			"from_date": filters.get("from_date"),
			"to_date": filters.get("to_date")
		}
		if filters.get("account"):
			query_params["accounts"] = accounts if isinstance(accounts, list) else [accounts]

		# Get opening balance (before from_date)
		opening_entries = frappe.db.sql(
			f"""
			SELECT
				SUM(debit) as debit,
				SUM(credit) as credit,
				SUM(debit_in_account_currency) as debit_in_account_currency,
				SUM(credit_in_account_currency) as credit_in_account_currency,
				account_currency
			FROM `tabGL Entry`
			WHERE company = %(company)s
				AND party_type = %(party_type)s
				AND party = %(party)s
				AND posting_date < %(from_date)s
				AND is_cancelled = 0
				{account_condition}
			""",
			query_params,
			as_dict=1
		)

		# Get closing balance (up to to_date)
		closing_entries = frappe.db.sql(
			f"""
			SELECT
				SUM(debit) as debit,
				SUM(credit) as credit,
				SUM(debit_in_account_currency) as debit_in_account_currency,
				SUM(credit_in_account_currency) as credit_in_account_currency,
				account_currency
			FROM `tabGL Entry`
			WHERE company = %(company)s
				AND party_type = %(party_type)s
				AND party = %(party)s
				AND posting_date <= %(to_date)s
				AND is_cancelled = 0
				{account_condition}
			""",
			query_params,
			as_dict=1
		)

		opening_data = opening_entries[0] if opening_entries and opening_entries[0].get("debit") is not None else {
			"debit": 0,
			"credit": 0,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": 0,
			"account_currency": filters.get("account_currency")
		}

		closing_data = closing_entries[0] if closing_entries and closing_entries[0].get("debit") is not None else {
			"debit": 0,
			"credit": 0,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": 0,
			"account_currency": filters.get("account_currency")
		}

		party_balances[party] = {
			"opening": opening_data,
			"closing": closing_data
		}

	return party_balances


def get_conditions(filters):
	conditions = []

	ignore_is_opening = frappe.db.get_single_value(
		"Accounts Settings", "ignore_is_opening_check_for_reporting"
	)

	if filters.get("account"):
		filters.account = get_accounts_with_children(filters.account)
		if filters.account:
			conditions.append("account in %(account)s")

	if filters.get("cost_center"):
		filters.cost_center = get_cost_centers_with_children(filters.cost_center)
		conditions.append("cost_center in %(cost_center)s")

	if filters.get("voucher_no"):
		conditions.append("voucher_no=%(voucher_no)s")

	if filters.get("against_voucher_no"):
		conditions.append("against_voucher=%(against_voucher_no)s")

	if filters.get("ignore_err"):
		err_journals = frappe.db.get_all(
			"Journal Entry",
			filters={
				"company": filters.get("company"),
				"docstatus": 1,
				"voucher_type": ("in", ["Exchange Rate Revaluation", "Exchange Gain Or Loss"]),
			},
			as_list=True,
		)
		if err_journals:
			filters.update({"voucher_no_not_in": [x[0] for x in err_journals]})

	if filters.get("ignore_cr_dr_notes"):
		system_generated_cr_dr_journals = frappe.db.get_all(
			"Journal Entry",
			filters={
				"company": filters.get("company"),
				"docstatus": 1,
				"voucher_type": ("in", ["Credit Note", "Debit Note"]),
				"is_system_generated": 1,
			},
			as_list=True,
		)
		if system_generated_cr_dr_journals:
			vouchers_to_ignore = (filters.get("voucher_no_not_in") or []) + [
				x[0] for x in system_generated_cr_dr_journals
			]
			filters.update({"voucher_no_not_in": vouchers_to_ignore})

	if filters.get("voucher_no_not_in"):
		conditions.append("voucher_no not in %(voucher_no_not_in)s")

	if filters.get("categorize_by") == "Categorize by Party" and not filters.get("party_type"):
		conditions.append("party_type in ('Customer', 'Supplier')")

	if filters.get("party_type"):
		conditions.append("party_type=%(party_type)s")

	if filters.get("party"):
		conditions.append("party in %(party)s")

	if not (
		filters.get("account")
		or filters.get("party")
		or filters.get("categorize_by") in ["Categorize by Account", "Categorize by Party"]
	):
		if not ignore_is_opening:
			conditions.append("(posting_date >=%(from_date)s or is_opening = 'Yes')")
		else:
			conditions.append("posting_date >=%(from_date)s")

	if not ignore_is_opening:
		conditions.append("(posting_date <=%(to_date)s or is_opening = 'Yes')")
	else:
		conditions.append("posting_date <=%(to_date)s")

	if filters.get("project"):
		conditions.append("project in %(project)s")

	if filters.get("include_default_book_entries"):
		if filters.get("finance_book"):
			if filters.get("company_fb") and cstr(filters.get("finance_book")) != cstr(
				filters.get("company_fb")
			):
				frappe.throw(
					_("To use a different finance book, please uncheck 'Include Default FB Entries'")
				)
			else:
				conditions.append("(finance_book in (%(finance_book)s, '') OR finance_book IS NULL)")
		else:
			conditions.append("(finance_book in (%(company_fb)s, '') OR finance_book IS NULL)")
	else:
		if filters.get("finance_book"):
			conditions.append("(finance_book in (%(finance_book)s, '') OR finance_book IS NULL)")
		else:
			conditions.append("(finance_book in ('') OR finance_book IS NULL)")

	if not filters.get("show_cancelled_entries"):
		conditions.append("is_cancelled = 0")

	from frappe.desk.reportview import build_match_conditions

	match_conditions = build_match_conditions("GL Entry")

	if match_conditions:
		conditions.append(match_conditions)

	accounting_dimensions = get_accounting_dimensions(as_list=False)

	if accounting_dimensions:
		for dimension in accounting_dimensions:
			# Ignore 'Finance Book' set up as dimension in below logic, as it is already handled in above section
			if not dimension.disabled and dimension.document_type != "Finance Book":
				if filters.get(dimension.fieldname):
					if frappe.get_cached_value("DocType", dimension.document_type, "is_tree"):
						filters[dimension.fieldname] = get_dimension_with_children(
							dimension.document_type, filters.get(dimension.fieldname)
						)
						conditions.append(f"{dimension.fieldname} in %({dimension.fieldname})s")
					else:
						conditions.append(f"{dimension.fieldname} in %({dimension.fieldname})s")

	return "and {}".format(" and ".join(conditions)) if conditions else ""


def get_party_name_map():
	party_map = {}

	customers = frappe.get_all("Customer", fields=["name", "customer_name"])
	party_map["Customer"] = {c.name: c.customer_name for c in customers}

	suppliers = frappe.get_all("Supplier", fields=["name", "supplier_name"])
	party_map["Supplier"] = {s.name: s.supplier_name for s in suppliers}

	employees = frappe.get_all("Employee", fields=["name", "employee_name"])
	party_map["Employee"] = {e.name: e.employee_name for e in employees}
	return party_map


def get_accounts_with_children(accounts):
	if not isinstance(accounts, list):
		accounts = [d.strip() for d in accounts.strip().split(",") if d]

	if not accounts:
		return

	doctype = frappe.qb.DocType("Account")
	accounts_data = (
		frappe.qb.from_(doctype)
		.select(doctype.lft, doctype.rgt)
		.where(doctype.name.isin(accounts))
		.run(as_dict=True)
	)

	conditions = []
	for account in accounts_data:
		conditions.append((doctype.lft >= account.lft) & (doctype.rgt <= account.rgt))

	return frappe.qb.from_(doctype).select(doctype.name).where(Criterion.any(conditions)).run(pluck=True)


def set_bill_no(gl_entries, filters):
	inv_details = get_supplier_invoice_details(filters)
	si_details = get_sales_invoice_details(filters)
	if filters.get("company") == "RN Labs":
		si_patient_details = get_sales_invoice_patient_details(filters)
	
	for gl in gl_entries:
		gl["bill_no"] = ""
		gl["outstanding_amount"] = 0
		gl["due_date"] = ""
		gl["transaction_date"] = ""
		gl["patient_name"] = ""
		if gl.get("against_voucher") is None:
			continue
		
		if gl.get("voucher_type") == "Purchase Invoice":
			gl["bill_no"] = inv_details.get(gl.get("against_voucher"), {}).get("bill_no", "")
			gl["outstanding_amount"] = inv_details.get(gl.get("against_voucher"), {}).get("outstanding_amount", 0)
			gl["due_date"] = inv_details.get(gl.get("against_voucher"), {}).get("due_date", "")
			gl["transaction_date"] = inv_details.get(gl.get("against_voucher"), {}).get("transaction_date", "")
		elif gl.get("voucher_type") == "Sales Invoice":
			gl["bill_no"] = si_details.get(gl.get("against_voucher"), {}).get("bill_no", "")
			gl["outstanding_amount"] = si_details.get(gl.get("against_voucher"), {}).get("outstanding_amount", 0)
			gl["due_date"] = si_details.get(gl.get("against_voucher"), {}).get("due_date", "")
			gl["transaction_date"] = si_details.get(gl.get("against_voucher"), {}).get("transaction_date", "")
			if filters.get("company") == "RN Labs":
				gl["patient_name"] = si_patient_details.get(gl.get("against_voucher"), {}).get("patient_name", "")


def get_voucher_titles(gl_entries):
	voucher_map = {}
	for gle in gl_entries:
		if gle.get("voucher_type") and gle.get("voucher_no"):
			voucher_map.setdefault(gle.voucher_type, set()).add(gle.voucher_no)

	title_map = {}
	for voucher_type, voucher_nos in voucher_map.items():
		meta = frappe.get_meta(voucher_type)
		if not meta.get_field("title"):
			continue
		for r in frappe.get_all(voucher_type, filters={"name": ["in", list(voucher_nos)]}, fields=["name", "title"]):
			title_map[r.name] = r.title or ""

	return title_map


def get_data_with_opening_closing(filters, account_details, accounting_dimensions, gl_entries, all_party_balances=None):
	data = []
	totals_dict = get_totals_dict()

	set_bill_no(gl_entries, filters)

	title_map = get_voucher_titles(gl_entries)
	for gle in gl_entries:
		gle["title"] = title_map.get(gle.get("voucher_no"), "")

	gle_map = initialize_gle_map(gl_entries, filters, totals_dict)

	totals, entries = get_accountwise_gle(filters, accounting_dimensions, gl_entries, gle_map, totals_dict)

	# If include_all_parties is enabled, add missing parties AFTER filtering by date
	if all_party_balances and filters.get("categorize_by") == "Categorize by Party":
		# Get parties that have actual entries (not just existing in gle_map)
		parties_with_entries = set([party for party, acc_dict in gle_map.items() if acc_dict.entries])
		party_name_map = get_party_name_map()

		for party, balances in all_party_balances.items():
			if party not in parties_with_entries:
				# This party has no transactions in the selected period
				opening_bal = balances["opening"]
				closing_bal = balances["closing"]

				# Skip parties with no balances at all (never had any transactions)
				if (opening_bal.get("debit", 0) == 0 and opening_bal.get("credit", 0) == 0 and
				    closing_bal.get("debit", 0) == 0 and closing_bal.get("credit", 0) == 0):
					continue

				# Initialize or update entry in gle_map for this party
				if party not in gle_map:
					gle_map[party] = _dict(totals=copy.deepcopy(totals_dict), entries=[])

				# Set opening balance
				gle_map[party].totals.opening.debit = opening_bal.get("debit", 0)
				gle_map[party].totals.opening.credit = opening_bal.get("credit", 0)
				gle_map[party].totals.opening.debit_in_account_currency = opening_bal.get("debit_in_account_currency", 0)
				gle_map[party].totals.opening.credit_in_account_currency = opening_bal.get("credit_in_account_currency", 0)

				# Total remains 0 (no transactions in period) - already set by totals_dict

				# Set closing balance (same as opening if no transactions in period)
				gle_map[party].totals.closing.debit = closing_bal.get("debit", 0)
				gle_map[party].totals.closing.credit = closing_bal.get("credit", 0)
				gle_map[party].totals.closing.debit_in_account_currency = closing_bal.get("debit_in_account_currency", 0)
				gle_map[party].totals.closing.credit_in_account_currency = closing_bal.get("credit_in_account_currency", 0)

				# Set the account name to show party info
				party_display = f"{party}"
				if party_name_map.get(filters.get("party_type"), {}).get(party):
					party_display = f"{party} - {party_name_map.get(filters.get('party_type'), {}).get(party)}"

				# Use quotes to match the standard format from get_totals_dict()
				gle_map[party].totals.opening.account = "'Opening'"
				gle_map[party].totals.total.account = "'Total'"
				gle_map[party].totals.closing.account = "'Closing (Opening + Total)'"
				

	# Opening for filtered account
	data.append(totals.opening)

	if filters.get("categorize_by") != "Categorize by Voucher (Consolidated)":
		for _acc, acc_dict in gle_map.items():
			# acc
			# Include entries with transactions OR parties with balances when include_all_parties is enabled
			has_balances = (acc_dict.totals.opening.debit != 0 or acc_dict.totals.opening.credit != 0 or
			                acc_dict.totals.closing.debit != 0 or acc_dict.totals.closing.credit != 0)
			include_party = filters.get("include_all_parties") and filters.get("categorize_by") == "Categorize by Party" and has_balances

			if acc_dict.entries or include_party:
				# opening
				data.append({"debit_in_transaction_currency": None, "credit_in_transaction_currency": None})
				if (not filters.get("categorize_by") and not filters.get("voucher_no")) or (
					filters.get("categorize_by") and filters.get("categorize_by") != "Categorize by Voucher"
				):
					data.append(acc_dict.totals.opening)

				data += acc_dict.entries

				# totals
				if filters.get("categorize_by") or not filters.voucher_no:
					data.append(acc_dict.totals.total)

				# closing
				if (not filters.get("categorize_by") and not filters.get("voucher_no")) or (
					filters.get("categorize_by") and filters.get("categorize_by") != "Categorize by Voucher"
				):
					data.append(acc_dict.totals.closing)

		data.append({"debit_in_transaction_currency": None, "credit_in_transaction_currency": None})
	else:
		data += entries

	# totals
	data.append(totals.total)

	# closing
	data.append(totals.closing)

	return data


def get_totals_dict():
	def _get_debit_credit_dict(label):
		return _dict(
			account=f"'{label}'",
			debit=0.0,
			credit=0.0,
			debit_in_account_currency=0.0,
			credit_in_account_currency=0.0,
			debit_in_transaction_currency=None,
			credit_in_transaction_currency=None,
		)

	return _dict(
		opening=_get_debit_credit_dict(_("Opening")),
		total=_get_debit_credit_dict(_("Total")),
		closing=_get_debit_credit_dict(_("Closing (Opening + Total)")),
	)


def group_by_field(group_by):
	if group_by == "Categorize by Party":
		return "party"
	elif group_by in ["Categorize by Voucher (Consolidated)", "Categorize by Account"]:
		return "account"
	else:
		return "voucher_no"


def initialize_gle_map(gl_entries, filters, totals_dict):
	gle_map = OrderedDict()
	group_by = group_by_field(filters.get("categorize_by"))

	for gle in gl_entries:
		gle_map.setdefault(gle.get(group_by), _dict(totals=copy.deepcopy(totals_dict), entries=[]))
	return gle_map


def get_accountwise_gle(filters, accounting_dimensions, gl_entries, gle_map, totals):
	entries = []
	consolidated_gle = OrderedDict()
	group_by = group_by_field(filters.get("categorize_by"))
	group_by_voucher_consolidated = filters.get("categorize_by") == "Categorize by Voucher (Consolidated)"

	if filters.get("show_net_values_in_party_account"):
		account_type_map = get_account_type_map(filters.get("company"))

	immutable_ledger = frappe.db.get_single_value("Accounts Settings", "enable_immutable_ledger")

	def update_value_in_dict(data, key, gle, show_net_values=False):
		data[key].debit += gle.debit
		data[key].credit += gle.credit

		data[key].debit_in_account_currency += gle.debit_in_account_currency
		data[key].credit_in_account_currency += gle.credit_in_account_currency

		if filters.get("add_values_in_transaction_currency") and key not in ["opening", "closing", "total"]:
			data[key].debit_in_transaction_currency += gle.debit_in_transaction_currency
			data[key].credit_in_transaction_currency += gle.credit_in_transaction_currency

		if (
			filters.get("show_net_values_in_party_account")
			and account_type_map.get(data[key].account)
			in (
				"Receivable",
				"Payable",
			)
		) or show_net_values:
			net_value = data[key].debit - data[key].credit
			net_value_in_account_currency = (
				data[key].debit_in_account_currency - data[key].credit_in_account_currency
			)

			if net_value < 0:
				dr_or_cr = "credit"
				rev_dr_or_cr = "debit"
			else:
				dr_or_cr = "debit"
				rev_dr_or_cr = "credit"

			data[key][dr_or_cr] = abs(net_value)
			data[key][dr_or_cr + "_in_account_currency"] = abs(net_value_in_account_currency)
			data[key][rev_dr_or_cr] = 0
			data[key][rev_dr_or_cr + "_in_account_currency"] = 0

		if data[key].against_voucher and gle.against_voucher:
			data[key].against_voucher += ", " + gle.against_voucher

	from_date, to_date = getdate(filters.from_date), getdate(filters.to_date)
	show_opening_entries = filters.get("show_opening_entries")

	for gle in gl_entries:
		group_by_value = gle.get(group_by)
		gle.voucher_type = gle.voucher_type

		if gle.posting_date < from_date or (cstr(gle.is_opening) == "Yes" and not show_opening_entries):
			if not group_by_voucher_consolidated:
				update_value_in_dict(gle_map[group_by_value].totals, "opening", gle, True)
				update_value_in_dict(gle_map[group_by_value].totals, "closing", gle, True)

			update_value_in_dict(totals, "opening", gle, True)
			update_value_in_dict(totals, "closing", gle, True)

		elif gle.posting_date <= to_date or (cstr(gle.is_opening) == "Yes" and show_opening_entries):
			if not group_by_voucher_consolidated:
				update_value_in_dict(gle_map[group_by_value].totals, "total", gle)
				update_value_in_dict(gle_map[group_by_value].totals, "closing", gle)
				update_value_in_dict(totals, "total", gle)
				update_value_in_dict(totals, "closing", gle)

				gle_map[group_by_value].entries.append(gle)

			elif group_by_voucher_consolidated:
				keylist = [
					gle.get("posting_date"),
					gle.get("voucher_type"),
					gle.get("voucher_no"),
					gle.get("account"),
					gle.get("party_type"),
					gle.get("party"),
				]

				if immutable_ledger:
					keylist.append(gle.get("creation"))

				if filters.get("include_dimensions"):
					for dim in accounting_dimensions:
						keylist.append(gle.get(dim))
					keylist.append(gle.get("cost_center"))
					keylist.append(gle.get("project"))

				key = tuple(keylist)
				if key not in consolidated_gle:
					consolidated_gle.setdefault(key, gle)
				else:
					update_value_in_dict(consolidated_gle, key, gle)

		if filters.get("include_dimensions"):
			dimensions = [*accounting_dimensions, "cost_center", "project"]

			for dimension in dimensions:
				if val := gle.get(dimension):
					gle[dimension] = _(val)

	for value in consolidated_gle.values():
		update_value_in_dict(totals, "total", value)
		update_value_in_dict(totals, "closing", value)
		entries.append(value)

	return totals, entries


def get_account_type_map(company):
	account_type_map = frappe._dict(
		frappe.get_all("Account", fields=["name", "account_type"], filters={"company": company}, as_list=1)
	)

	return account_type_map

def get_result_as_list(data, filters):
	balance = 0
	
	for d in data:
		if not d.get("posting_date"):
			balance = 0

		balance = get_balance(d, balance, "debit", "credit")

		d["balance"] = balance

		d["account_currency"] = filters.account_currency

		d["presentation_currency"] = filters.presentation_currency

	return data


def get_supplier_invoice_details(filters):
	conditions = ["bill_no is not null", "bill_no != ''"]

	if filters.get("party_type") == "Supplier" and filters.get("party"):
		conditions.append("supplier in %(party)s")

	if filters.get("voucher_no"):
		conditions.append("name = %(voucher_no)s")

	if filters.get("from_date"):
		conditions.append("posting_date >= %(from_date)s")

	if filters.get("to_date"):
		conditions.append("posting_date <= %(to_date)s")

	if filters.get("show_cancelled_entries") == False:
		conditions.append("docstatus = 1")

	condition_string = " and ".join(conditions)

	inv_details = {}
	for d in frappe.db.sql(
		f"""select name, bill_no, outstanding_amount, due_date, bill_date
		from `tabPurchase Invoice`
		where {condition_string}""",
		filters,
		as_dict=1
	):
		inv_details[d.name] = {
			"bill_no": d.bill_no,
			"outstanding_amount": d.outstanding_amount,
			"due_date": d.due_date,
			"transaction_date": d.bill_date
		}

	return inv_details

def get_sales_invoice_details(filters):
	conditions = []

	if filters.get("party_type") == "Customer" and filters.get("party"):
		conditions.append("customer in %(party)s")

	if filters.get("voucher_no"):
		conditions.append("name = %(voucher_no)s")

	if filters.get("from_date"):
		conditions.append("transaction_date >= %(from_date)s")

	if filters.get("to_date"):
		conditions.append("transaction_date <= %(to_date)s")

	if filters.get("show_cancelled_entries") == False:
		conditions.append("docstatus = 1")

	condition_string = " and ".join(conditions) if conditions else "1=1"

	inv_details = {}
	for d in frappe.db.sql(
		f"""select name, po_no, outstanding_amount, due_date, transaction_date
		from `tabSales Invoice`
		where {condition_string}""",
		filters,
		as_dict=1
	):
		inv_details[d.name] = {
			"bill_no": d.po_no,
			"outstanding_amount": d.outstanding_amount,
			"due_date": d.due_date,
			"transaction_date": d.transaction_date
		}

	return inv_details

def get_sales_invoice_patient_details(filters):
	conditions = [
		"temporary_delivery_address_line_1 is not null",
		"temporary_delivery_address_line_1 != ''"
	]

	if filters.get("party_type") == "Customer" and filters.get("party"):
		conditions.append("customer in %(party)s")

	if filters.get("voucher_no"):
		conditions.append("name = %(voucher_no)s")

	if filters.get("from_date"):
		conditions.append("posting_date >= %(from_date)s")

	if filters.get("to_date"):
		conditions.append("posting_date <= %(to_date)s")

	if filters.get("show_cancelled_entries") == False:
		conditions.append("docstatus = 1")

	condition_string = " and ".join(conditions)

	inv_details = {}
	for d in frappe.db.sql(
		f"""select name, temporary_delivery_address_line_1 as patient_name
		from `tabSales Invoice`
		where {condition_string}""",
		filters,
		as_dict=1
	):
		inv_details[d.name] = {"patient_name": d.patient_name}

	return inv_details

def add_transaction_date_to_si(data):
	invoices = []
	purchase_receipt_nos = []
	purchase_invoice_nos = []

	for item in data:
		if item.get("voucher_type") == "Sales Invoice":
			invoices.append(item["voucher_no"])
		elif item.get("voucher_type") == "Purchase Receipt":
			purchase_receipt_nos.append(item["voucher_no"])
		elif item.get("voucher_type") == "Purchase Invoice":
			purchase_invoice_nos.append(item["voucher_no"])

	if purchase_receipt_nos:
		pr_supplier_map = {
			r.name: r.supplier
			for r in frappe.get_all(
				"Purchase Receipt",
				filters=[["name", "in", purchase_receipt_nos]],
				fields=["name", "supplier"],
			)
		}
		for item in data:
			if item.get("voucher_type") == "Purchase Receipt" and item["voucher_no"] in pr_supplier_map:
				item["party_type"] = "Supplier"
				item["party"] = pr_supplier_map[item["voucher_no"]]

	if purchase_invoice_nos:
		pi_supplier_map = {
			r.name: r.supplier
			for r in frappe.get_all(
				"Purchase Invoice",
				filters=[["name", "in", purchase_invoice_nos]],
				fields=["name", "supplier"],
			)
		}
		for item in data:
			if item.get("voucher_type") == "Purchase Invoice" and item["voucher_no"] in pi_supplier_map:
				item["party_type"] = "Supplier"
				item["party"] = pi_supplier_map[item["voucher_no"]]

	invoice_dict = {
		r.name: r.transaction_date
		for r in frappe.get_all(
			"Sales Invoice",
			filters=[["name", "IN", invoices]],
			fields=["name", "transaction_date"],
		)
	}

	for item in data:
		if item.get("voucher_type") == "Sales Invoice":
			item["transaction_date"] = invoice_dict.get(item["voucher_no"])

	return data

def get_balance(row, balance, debit_field, credit_field):
	balance += row.get(debit_field, 0) - row.get(credit_field, 0)

	return balance


def get_columns(filters):
	if filters.get("presentation_currency"):
		currency = filters["presentation_currency"]
	else:
		company = filters.get("company") or get_default_company()
		filters["presentation_currency"] = currency = get_company_currency(company)

	company_currency = get_company_currency(filters.get("company") or get_default_company())

	if (
		filters.get("show_amount_in_company_currency")
		and filters["presentation_currency"] != company_currency
	):
		frappe.throw(
			_(
				f'Presentation Currency cannot be {frappe.bold(filters["presentation_currency"])} , When {frappe.bold("Show Credit / Debit in Company Currency")} is enabled.'
			)
		)

	columns = [
		{
			"label": _("GL Entry"),
			"fieldname": "gl_entry",
			"fieldtype": "Link",
			"options": "GL Entry",
			"hidden": 1,
		},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Transaction Date"), "fieldname": "transaction_date", "fieldtype": "Date", "width": 100},
		{
			"label": _("Account"),
			"fieldname": "account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 180,
		},
		{
			"label": _("Debit ({0})").format(currency),
			"fieldname": "debit",
			"fieldtype": "Currency",
			"options": "presentation_currency",
			"width": 130,
		},
		{
			"label": _("Credit ({0})").format(currency),
			"fieldname": "credit",
			"fieldtype": "Currency",
			"options": "presentation_currency",
			"width": 130,
		},
		{
			"label": _("Balance ({0})").format(currency),
			"fieldname": "balance",
			"fieldtype": "Currency",
			"options": "presentation_currency",
			"width": 130,
		},
	]

	if filters.get("show_remarks"):
		columns.append({"label": _("Remarks"), "fieldname": "remarks", "width": 400})

	if filters.get("add_values_in_transaction_currency"):
		columns += [
			{
				"label": _("Debit (Transaction)"),
				"fieldname": "debit_in_transaction_currency",
				"fieldtype": "Currency",
				"width": 130,
				"options": "transaction_currency",
			},
			{
				"label": _("Credit (Transaction)"),
				"fieldname": "credit_in_transaction_currency",
				"fieldtype": "Currency",
				"width": 130,
				"options": "transaction_currency",
			},
			{
				"label": "Transaction Currency",
				"fieldname": "transaction_currency",
				"fieldtype": "Link",
				"options": "Currency",
				"width": 70,
			},
		]

	columns += [
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "width": 120},
		{
			"label": _("Voucher Subtype"),
			"fieldname": "voucher_subtype",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": _("Voucher No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 180,
		},
		{
			"label": _("Title"),
			"fieldname": "title",
			"fieldtype": "Data",
			"width": 200,
		},
		{"label": _("Against Account"), "fieldname": "against", "width": 120},
		{"label": _("Party Type"), "fieldname": "party_type", "width": 100},
		{"label": _("Party"), "fieldname": "party", "width": 100},
	]

	if filters.get("show_outstanding_amount"):
		columns.append(
			{"label": _("Outstanding Amount ({0})").format(currency),  "fieldname": "outstanding_amount", "fieldtype":"Float", "width": 150}
		)
	
	if filters.get("show_due_date"):
		columns.append(
			{"label": _("Due Date"),  "fieldname": "due_date", "fieldtype":"Date", "width": 150}
		)

	columns.extend(
		[
			{"label": _("Voucher Type"), "fieldname": "voucher_type", "width": 120},
			{
				"label": _("Voucher No"),
				"fieldname": "voucher_no",
				"fieldtype": "Dynamic Link",
				"options": "voucher_type",
				"width": 180,
			},
			{"label": _("Against Account"), "fieldname": "against", "width": 120},
			# {"label": _("Party Type"), "fieldname": "party_type", "width": 100},
			# {"label": _("Party"), "fieldname": "party", "width": 100},
			# {"label": _("Project"), "options": "Project", "fieldname": "project", "width": 100},
		]
	)
	supplier_master_name = frappe.db.get_single_value("Buying Settings", "supp_master_name")
	customer_master_name = frappe.db.get_single_value("Selling Settings", "cust_master_name")

	if supplier_master_name != "Supplier Name" or customer_master_name != "Customer Name":
		columns.append(
			{
				"label": _("Party Name"),
				"fieldname": "party_name",
				"fieldtype": "Data",
				"width": 150,
			}
		)

	if filters.get("include_dimensions"):
		columns.append({"label": _("Project"), "options": "Project", "fieldname": "project", "width": 100})

		for dim in get_accounting_dimensions(as_list=False):
			columns.append(
				{"label": _(dim.label), "options": dim.label, "fieldname": dim.fieldname, "width": 100}
			)
		columns.append(
			{"label": _("Cost Center"), "options": "Cost Center", "fieldname": "cost_center", "width": 100}
		)

	columns.extend(
		[
			{"label": _("Against Voucher Type"), "fieldname": "against_voucher_type", "width": 100},
			{
				"label": _("Against Voucher"),
				"fieldname": "against_voucher",
				"fieldtype": "Dynamic Link",
				"options": "against_voucher_type",
				"width": 100,
			},
			{"label": _("Invoice No"), "fieldname": "bill_no", "fieldtype": "Data", "width": 100},
		]
	)

	
	return columns
