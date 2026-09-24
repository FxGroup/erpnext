# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _, qb
from frappe.model.document import Document
from frappe.query_builder import Criterion
from frappe.query_builder.functions import Abs, Sum
from frappe.utils.data import comma_and, now

from erpnext.accounts.utils import (
	cancel_exchange_gain_loss_journal,
	unlink_ref_doc_from_payment_entries,
	update_voucher_outstanding,
)


class UnreconcilePayment(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.accounts.doctype.unreconcile_payment_entries.unreconcile_payment_entries import (
			UnreconcilePaymentEntries,
		)

		allocations: DF.Table[UnreconcilePaymentEntries]
		amended_from: DF.Link | None
		company: DF.Link | None
		voucher_no: DF.DynamicLink | None
		voucher_type: DF.Link | None
	# end: auto-generated types

	def validate(self):
		self.supported_types = ["Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice"]
		if self.voucher_type not in self.supported_types:
			frappe.throw(_("Only {0} are supported").format(comma_and(self.supported_types)))

		if self.voucher_type in ["Sales Invoice", "Purchase Invoice"] and not frappe.db.get_value(
			self.voucher_type, self.voucher_no, "is_return"
		):
			frappe.throw(
				_("{0} {1} can only be unreconciled if it is a return").format(
					_(self.voucher_type), frappe.bold(self.voucher_no)
				)
			)

	@frappe.whitelist()
	def get_allocations_from_payment(self):
		return get_linked_payments_for_doc(
			company=self.company,
			doctype=self.voucher_type,
			docname=self.voucher_no,
			as_payment=True,
		)

	def add_references(self):
		allocations = self.get_allocations_from_payment()

		for alloc in allocations:
			self.append("allocations", alloc)

	def on_submit(self):
		# todo: more granular unreconciliation
		for alloc in self.allocations:
			doc = frappe.get_doc(alloc.reference_doctype, alloc.reference_name)
			unlink_ref_doc_from_payment_entries(doc, self.voucher_no)
			cancel_exchange_gain_loss_journal(doc, self.voucher_type, self.voucher_no)

			# update outstanding amounts
			update_voucher_outstanding(
				alloc.reference_doctype,
				alloc.reference_name,
				alloc.account,
				alloc.party_type,
				alloc.party,
			)

			frappe.db.set_value("Unreconcile Payment Entries", alloc.name, "unlinked", True)

		if self.voucher_type in ["Sales Invoice", "Purchase Invoice"]:
			self.restore_return_invoice_as_standalone()
			self.show_return_unlinked_message()

	def show_return_unlinked_message(self):
		note_type = _("Credit Note") if self.voucher_type == "Sales Invoice" else _("Debit Note")
		references = comma_and([x.reference_name for x in self.allocations])
		frappe.msgprint(_("{0} {1} is un-linked from {2}").format(note_type, self.voucher_no, references))

	def restore_return_invoice_as_standalone(self):
		gle = qb.DocType("GL Entry")

		for alloc in self.allocations:
			(
				qb.update(gle)
				.set(gle.against_voucher_type, self.voucher_type)
				.set(gle.against_voucher, self.voucher_no)
				.set(gle.modified, now())
				.set(gle.modified_by, frappe.session.user)
				.where(
					(gle.voucher_type == self.voucher_type)
					& (gle.voucher_no == self.voucher_no)
					& (gle.account == alloc.account)
					& (gle.party_type == alloc.party_type)
					& (gle.party == alloc.party)
					& (gle.against_voucher.isnull())
					& (gle.is_cancelled == 0)
				)
				.run()
			)

		frappe.db.set_value(
			self.voucher_type, self.voucher_no, "update_outstanding_for_self", 1, update_modified=False
		)

		for alloc in self.allocations:
			update_voucher_outstanding(
				self.voucher_type,
				self.voucher_no,
				alloc.account,
				alloc.party_type,
				alloc.party,
			)


@frappe.whitelist()
def doc_has_references(doctype: str | None = None, docname: str | None = None):
	count = 0
	if doctype in ["Sales Invoice", "Purchase Invoice"]:
		count = frappe.db.count(
			"Payment Ledger Entry",
			filters={"delinked": 0, "against_voucher_no": docname, "amount": ["<", 0]},
		)
	else:
		count = frappe.db.count(
			"Payment Ledger Entry",
			filters={"delinked": 0, "voucher_no": docname, "against_voucher_no": ["!=", docname]},
		)
		count += frappe.db.count(
			"Advance Payment Ledger Entry",
			filters={
				"delinked": 0,
				"voucher_no": docname,
				"voucher_type": doctype,
				"event": ["=", "Submit"],
			},
		)

	return count


@frappe.whitelist()
def get_linked_payments_for_doc(
	company: str | None = None,
	doctype: str | None = None,
	docname: str | None = None,
	as_payment: bool = False,
) -> list:
	if company and doctype and docname:
		_dt = doctype
		_dn = docname
		ple = qb.DocType("Payment Ledger Entry")
		if _dt in ["Sales Invoice", "Purchase Invoice"] and not as_payment:
			criteria = [
				(ple.company == company),
				(ple.delinked == 0),
				(ple.against_voucher_no == _dn),
				(ple.amount < 0),
			]

			res = (
				qb.from_(ple)
				.select(
					ple.account,
					ple.party_type,
					ple.party,
					ple.company,
					ple.voucher_type.as_("reference_doctype"),
					ple.voucher_no.as_("reference_name"),
					Abs(Sum(ple.amount_in_account_currency)).as_("allocated_amount"),
					ple.account_currency,
				)
				.where(Criterion.all(criteria))
				.groupby(ple.voucher_no, ple.against_voucher_no)
				.having(qb.Field("allocated_amount") > 0)
				.run(as_dict=True)
			)
			return res
		else:
			criteria = [
				(ple.company == company),
				(ple.delinked == 0),
				(ple.voucher_no == _dn),
				(ple.against_voucher_no != _dn),
			]

			query = (
				qb.from_(ple)
				.select(
					ple.company,
					ple.account,
					ple.party_type,
					ple.party,
					ple.against_voucher_type.as_("reference_doctype"),
					ple.against_voucher_no.as_("reference_name"),
					Abs(Sum(ple.amount_in_account_currency)).as_("allocated_amount"),
					ple.account_currency,
				)
				.where(Criterion.all(criteria))
				.groupby(ple.against_voucher_no)
			)

			res = query.run(as_dict=True)

			if _dt not in ["Sales Invoice", "Purchase Invoice"]:
				res += get_linked_advances(company, _dn)

			return res

	return []


def get_linked_advances(company, docname):
	adv = qb.DocType("Advance Payment Ledger Entry")
	criteria = [
		(adv.company == company),
		(adv.delinked == 0),
		(adv.voucher_no == docname),
		(adv.event == "Submit"),
	]

	return (
		qb.from_(adv)
		.select(
			adv.company,
			adv.against_voucher_type.as_("reference_doctype"),
			adv.against_voucher_no.as_("reference_name"),
			Abs(Sum(adv.amount)).as_("allocated_amount"),
			adv.currency,
		)
		.where(Criterion.all(criteria))
		.having(qb.Field("allocated_amount") > 0)
		.groupby(adv.against_voucher_no)
		.run(as_dict=True)
	)


@frappe.whitelist()
def create_unreconcile_doc_for_selection(selections=None):
	if selections:
		selections = json.loads(selections)
		# assuming each row is a unique voucher
		for row in selections:
			unrecon = frappe.new_doc("Unreconcile Payment")
			unrecon.company = row.get("company")
			unrecon.voucher_type = row.get("voucher_type")
			unrecon.voucher_no = row.get("voucher_no")
			unrecon.add_references()

			# remove unselected references
			unrecon.allocations = [
				x
				for x in unrecon.allocations
				if x.reference_doctype == row.get("against_voucher_type")
				and x.reference_name == row.get("against_voucher_no")
			]
			unrecon.save().submit()
