import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today

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

	# ------------------------------------------------------------------ #
	def get_employee(self):
		"""Employee of the Driver linked to the trip (ERPNext Driver -> Employee)."""
		if not self.trip:
			return None
		driver = frappe.db.get_value("Trip", self.trip, "driver")
		if driver and frappe.get_meta("Driver").has_field("employee"):
			return frappe.db.get_value("Driver", driver, "employee")
		return None

	def get_company(self, employee):
		company = frappe.db.get_value("Employee", employee, "company") if employee else None
		return company or frappe.defaults.get_user_default("company")

	def get_or_create_expense_claim_type(self):
		"""Reuse an Expense Claim Type matching the expense, creating one if needed."""
		name = frappe.db.get_value("Expense Claim Type", {"expense_type": self.expense_type}, "name")
		if name:
			return name
		return frappe.get_doc(
			{"doctype": "Expense Claim Type", "expense_type": self.expense_type}
		).insert(ignore_permissions=True).name
