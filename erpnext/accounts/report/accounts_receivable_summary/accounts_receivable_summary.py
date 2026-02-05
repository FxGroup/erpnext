# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors and contributors
# For license information, please see license.txt


import frappe
from frappe import _, scrub
from frappe.utils import cint, flt

from erpnext.accounts.party import get_partywise_advanced_payment_amount
from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport
from erpnext.accounts.utils import get_currency_precision, get_party_types_from_account_type


def execute(filters=None):
	args = {
		"account_type": "Receivable",
		"naming_by": ["Selling Settings", "cust_master_name"],
	}

	return AccountsReceivableSummary(filters).run(args)


class AccountsReceivableSummary(ReceivablePayableReport):
	def run(self, args):
		self.account_type = args.get("account_type")
		self.party_type = get_party_types_from_account_type(self.account_type)
		self.party_naming_by = frappe.db.get_value(args.get("naming_by")[0], None, args.get("naming_by")[1])
		self.company_currency = frappe.get_cached_value("Company", self.filters.company, "default_currency")
		self.get_columns()
		self.get_data(args)
		return self.columns, self.data

	def get_data(self, args):
		self.data = []
		self.receivables = ReceivablePayableReport(self.filters).run(args)[1]
		self.currency_precision = get_currency_precision() or 2
		self.get_party_total(args)

		party = None
		for party_type in self.party_type:
			if self.filters.get(scrub(party_type)):
				party = self.filters.get(scrub(party_type))

		party_advance_amount = (
			get_partywise_advanced_payment_amount(
				self.party_type,
				self.filters.report_date,
				self.filters.show_future_payments,
				self.filters.company,
				party=party,
			)
			or {}
		)

		if self.filters.show_gl_balance:
			gl_balance_map = get_gl_balance(
				report_date=self.filters.report_date, 
				company=self.filters.company, 
				account_type=self.account_type,
				party_type=self.filters.party_type if self.filters.get("party_type") else None,
				party=self.filters.party if self.filters.get("party") else None
			)

		for party, party_dict in self.party_total.items():
			if flt(party_dict.outstanding, self.currency_precision) == 0:
				continue

			row = frappe._dict()

			row.party = party
			if self.party_naming_by == "Naming Series":
				if self.account_type == "Payable":
					doctype = "Supplier"
					fieldname = "supplier_name"
				else:
					doctype = "Customer"
					fieldname = "customer_name"
				row.party_name = frappe.get_cached_value(doctype, party, fieldname)

			row.update(party_dict)

			# Advance against party
			row.advance = party_advance_amount.get(party, 0)

			# In AR/AP, advance shown in paid columns,
			# but in summary report advance shown in separate column
			row.paid -= row.advance

			if self.filters.show_gl_balance:
				party_gl_balance = gl_balance_map.get(party) or {}
				row.gl_balance = party_gl_balance.get('balance', 0)
				row.gl_balance_in_account_currency = party_gl_balance.get('balance_in_account_currency', 0)
				row.diff = flt(row.outstanding) - flt(row.gl_balance)
				row.diff_in_account_currency = flt(row.outstanding_in_account_currency) - flt(row.gl_balance_in_account_currency)

			if self.filters.show_future_payments:
				row.remaining_balance = flt(row.outstanding) - flt(row.future_amount)
				row.remaining_balance_in_account_currency = flt(row.outstanding_in_account_currency) - flt(row.future_amount_in_account_currency)

			# Perform currency conversion if the filter is enabled
			if self.filters.get("in_party_currency") and row.currency != self.company_currency:
				# Change the fields to the _in_account_currency versions
				amount_fields = [
					"invoiced",
					"paid",
					"credit_note",
					"outstanding",
					"total_due",
					"not_yet_due",
					"future_amount",
					"gl_balance",
					"diff"
				]
    
				for i in self.range_numbers:
					amount_fields.append(f"range{i}")
     
				for field in amount_fields:
					row[field] = row.get(f"{field}_in_account_currency", 0.0)

			self.data.append(row)

	def get_party_total(self, args):
		self.party_total = frappe._dict()

		for d in self.receivables:
			self.init_party_total(d)

			# Add all amount columns
			for k in list(self.party_total[d.party]):
				if isinstance(self.party_total[d.party][k], float):
					self.party_total[d.party][k] += d.get(k) or 0.0

			# set territory, customer_group, sales person etc
			self.set_party_details(d)

	def init_party_total(self, row):
		default_dict = {
			"invoiced": 0.0,
			"paid": 0.0,
			"paid_in_account_currency": 0.0,
			"credit_note": 0.0,
			"credit_note_in_account_currency": 0.0,
			"outstanding": 0.0,
			"outstanding_in_account_currency": 0.0,
			"total_due": 0.0,
			"total_due_in_account_currency": 0.0,
			"not_yet_due": 0.0,
			"not_yet_due_in_account_currency": 0.0,
			"future_amount": 0.0,
			"future_amount_in_account_currency": 0.0,
			"sales_person": [],
			"party_type": row.party_type
		}
  
		for i in self.range_numbers:
			range_key = f"range{i}"
			range_key_in_account_currency = f"range{i}_in_account_currency"
			default_dict[range_key] = 0.0
			default_dict[range_key_in_account_currency] = 0.0

		self.party_total.setdefault(
			row.party,
			frappe._dict(default_dict),
		)

	def set_party_details(self, row):
		self.party_total[row.party].currency = row.currency

		for key in ("territory", "customer_group", "supplier_group", "customer_status"):
			if row.get(key):
				self.party_total[row.party][key] = row.get(key, "")
		if row.sales_person:
			self.party_total[row.party].sales_person.append(row.get("sales_person", ""))

		if self.filters.sales_partner:
			self.party_total[row.party]["default_sales_partner"] = row.get("default_sales_partner", "")

	def get_columns(self):
		self.columns = []
		self.add_column(
			label=_("Party Type"),
			fieldname="party_type",
			fieldtype="Data",
			width=100,
		)
		self.add_column(
			label=_("Party"),
			fieldname="party",
			fieldtype="Dynamic Link",
			options="party_type",
			width=180,
		)

		if self.party_naming_by == "Naming Series":
			self.add_column(
				label=_("Supplier Name") if self.account_type == "Payable" else _("Customer Name"),
				fieldname="party_name",
				fieldtype="Data",
			)

		credit_debit_label = "Credit Note" if self.account_type == "Receivable" else "Debit Note"

		self.add_column(_("Advance Amount"), fieldname="advance")
		self.add_column(_("Invoiced Amount"), fieldname="invoiced")
		self.add_column(_("Paid Amount"), fieldname="paid")
		self.add_column(_(credit_debit_label), fieldname="credit_note")
		self.add_column(_("Outstanding Amount"), fieldname="outstanding")

		if self.filters.show_gl_balance:
			self.add_column(_("GL Balance"), fieldname="gl_balance")
			self.add_column(_("Difference"), fieldname="diff")

		self.add_column(_("Not Yet Due"), fieldname="not_yet_due")	
		self.setup_ageing_columns()
		self.add_column(label=_("Total Amount Due"), fieldname="total_due")

		if self.filters.show_future_payments:
			self.add_column(label=_("Future Payment Amount"), fieldname="future_amount")
			self.add_column(label=_("Remaining Balance"), fieldname="remaining_balance")

		if self.account_type == "Receivable":
			self.add_column(
				label=_("Territory"), fieldname="territory", fieldtype="Link", options="Territory"
			)
			self.add_column(
				label=_("Customer Group"),
				fieldname="customer_group",
				fieldtype="Link",
				options="Customer Group",
			)
			if self.filters.show_sales_person:
				self.add_column(label=_("Sales Person"), fieldname="sales_person", fieldtype="Data")

			if self.filters.sales_partner:
				self.add_column(label=_("Sales Partner"), fieldname="default_sales_partner", fieldtype="Data")

		else:
			self.add_column(
				label=_("Supplier Group"),
				fieldname="supplier_group",
				fieldtype="Link",
				options="Supplier Group",
			)

		self.add_column(
			label=_("Currency"), fieldname="currency", fieldtype="Link", options="Currency", width=80
		)


def get_gl_balance(report_date, company, account_type=None, party_type=None, party=None, finance_book=None):
	"""
	Get GL balance for parties, matching the logic used in General Ledger report.
	Includes opening entries and handles finance book filtering.
	"""
	filters = {
		"company": company,
		"is_cancelled": 0,
		"report_date": report_date,
		"party_type": party_type,
		"party": party,
	}

	# Include entries up to report_date OR opening entries
	date_conditions = "AND (posting_date <= %(report_date)s OR is_opening = 'Yes')"

	if account_type == "Payable":
		balance_calc_fields = ["party", "SUM(credit - debit) AS balance", "SUM(credit_in_account_currency - debit_in_account_currency) as balance_in_account_currency"]
	else:
		balance_calc_fields = ["party", "SUM(debit - credit) AS balance", "SUM(debit_in_account_currency - credit_in_account_currency) as balance_in_account_currency"]

	party_type_condition = ""
	if party_type:
		party_type_condition = " AND party_type = %(party_type)s"

	party_condition = ""
	if party:
		party_condition = " AND party IN %(party)s"

	# Handle finance book (similar to General Ledger logic)
	finance_book_condition = ""
	if finance_book:
		finance_book_condition = "AND (finance_book in (%(finance_book)s, '') OR finance_book IS NULL)"
		filters["finance_book"] = finance_book

	result = frappe.db.sql(f"""
		SELECT
			{", ".join(balance_calc_fields)}
		FROM `tabGL Entry`
		WHERE company = %(company)s
			AND is_cancelled = %(is_cancelled)s
			{date_conditions}
			{party_type_condition}
			{party_condition}
			{finance_book_condition}
			AND party IS NOT NULL
		GROUP BY party
	""", filters, as_dict=1)

	return frappe._dict({d.party: {"balance": d.balance, "balance_in_account_currency": d.balance_in_account_currency} for d in result})
	