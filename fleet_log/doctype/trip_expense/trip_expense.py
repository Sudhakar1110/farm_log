import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today

from fleet_log.utils import is_erpnext_installed


class TripExpense(Document):
	@frappe.whitelist()
	def create_expense_claim(self):
		"""Push this expense into ERPNext's Expense Claim doctype (ERPNext sites only).

		The claim is created in Draft so the ERPNext approval flow is respected.
		Returns the name of the created Expense Claim.
		"""
		if not is_erpnext_installed():
			frappe.throw(
				_("ERPNext is not installed on this site, so an Expense Claim cannot be created.")
			)
		if not frappe.has_permission("Expense Claim", "create"):
			frappe.throw(_("You do not have permission to create Expense Claims."))
		if self.erpnext_expense_claim:
			frappe.throw(
				_("An Expense Claim has already been created for this expense: {0}").format(
					self.erpnext_expense_claim
				)
			)

		employee = self.get_employee()
		if not employee:
			frappe.throw(
				_("Set the Employee on the Driver linked to Trip {0} to create an Expense Claim.").format(
					self.trip
				)
			)

		claim = frappe.get_doc(
			{
				"doctype": "Expense Claim",
				"employee": employee,
				"posting_date": getdate(today()),
				"expenses": [
					{
						"expense_date": getdate(today()),
						"expense_type": self.get_or_create_expense_claim_type(),
						"claim_amount": self.amount,
						"description": _("Fleet trip expense ({0}) for Trip {1}").format(
							self.expense_type, self.trip
						),
					}
				],
			}
		)
		company = self.get_company(employee)
		if company:
			claim.company = company
		claim.insert()
		self.db_set("erpnext_expense_claim", claim.name)
		frappe.msgprint(
			_("Expense Claim {0} has been created (Draft). Submit it in ERPNext to post it.").format(
				claim.name
			)
		)
		return claim.name

	@frappe.whitelist()
	def create_journal_entry(self):
		"""Create a Journal Entry for this expense (ERPNext sites only).

		This provides an alternative to Expense Claims for sites that do not
		have Employees set up but do have a Chart of Accounts. The Journal Entry
		is created in Draft so the user can review/adjust the accounts before
		submitting.

		Account selection:
		- Debit:  the company's default expense account (Accounts Settings).
		- Credit: the company's default payable account.

		Returns the name of the created Journal Entry.
		"""
		if not is_erpnext_installed():
			frappe.throw(
				_("ERPNext is not installed on this site, so a Journal Entry cannot be created.")
			)
		if not frappe.has_permission("Journal Entry", "create"):
			frappe.throw(_("You do not have permission to create Journal Entries."))
		if self.erpnext_journal_entry:
			frappe.throw(
				_("A Journal Entry has already been created for this expense: {0}").format(
					self.erpnext_journal_entry
				)
			)

		company = self.get_company()
		debit_account = self.get_default_expense_account(company)
		credit_account = self.get_default_payable_account(company)

		if not debit_account or not credit_account:
			frappe.throw(
				_(
					"Could not determine default Expense Account or Payable Account "
					"for Company {0}. Set them in Accounting Settings or create the "
					"Journal Entry manually."
				).format(company)
			)

		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Journal Entry",
				"posting_date": getdate(today()),
				"company": company,
				"accounts": [
					{
						"account": debit_account,
						"debit_in_account_currency": flt(self.amount),
						"cost_center": self.get_default_cost_center(company),
					},
					{
						"account": credit_account,
						"credit_in_account_currency": flt(self.amount),
					},
				],
				"user_remark": _("Fleet trip expense ({0}) for Trip {1}").format(
					self.expense_type, self.trip
				),
			}
		)
		je.insert()
		self.db_set("erpnext_journal_entry", je.name)
		frappe.msgprint(
			_("Journal Entry {0} has been created (Draft). Review and submit it in the Accounts module.").format(
				je.name
			)
		)
		return je.name

	# ------------------------------------------------------------------ #
	# helpers
	# ------------------------------------------------------------------ #
	def get_employee(self):
		"""Employee of the Driver linked to the trip (ERPNext Driver -> Employee)."""
		if not self.trip:
			return None
		driver = frappe.db.get_value("Trip", self.trip, "driver")
		if driver and frappe.get_meta("Driver").has_field("employee"):
			return frappe.db.get_value("Driver", driver, "employee")
		return None

	def get_company(self, employee=None):
		if employee:
			company = frappe.db.get_value("Employee", employee, "company")
			if company:
				return company
		return frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)

	def get_or_create_expense_claim_type(self):
		"""Reuse an Expense Claim Type matching the expense, creating one if needed."""
		name = frappe.db.get_value("Expense Claim Type", {"expense_type": self.expense_type}, "name")
		if name:
			return name
		return frappe.get_doc(
			{"doctype": "Expense Claim Type", "expense_type": self.expense_type}
		).insert(ignore_permissions=True).name

	# ------------------------------------------------------------------ #
	# Account helpers (used for Journal Entry)
	# ------------------------------------------------------------------ #
	def get_default_expense_account(self, company):
		"""Return the company's default expense account from Accounts Settings."""
		return frappe.db.get_single_value("Accounts Settings", "default_expense_account") or frappe.db.get_value(
			"Account",
			{"company": company, "is_group": 0, "account_type": "Chargeable"},
			"name",
		)

	def get_default_payable_account(self, company):
		"""Return the company's default payable account."""
		# Try Accounts Settings first, then fall back to a standard payable account
		account = frappe.db.get_single_value("Accounts Settings", "default_payable_account")
		if account:
			return account
		# Try to find a "Creditors" or "Sundry Creditors" account
		return frappe.db.get_value(
			"Account",
			{
				"company": company,
				"is_group": 0,
				"account_type": "Payable",
				"disabled": 0,
			},
			"name",
		)

	def get_default_cost_center(self, company):
		"""Return the company's default cost center."""
		return frappe.db.get_value(
			"Cost Center",
			{"company": company, "is_group": 0, "disabled": 0},
			"name",
		)