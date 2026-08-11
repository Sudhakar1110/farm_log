import frappe
from frappe.model.document import Document


class Driver(Document):
	"""Fallback Driver master, used only when ERPNext is not installed.

	When ERPNext is installed the doctype of the same name is ERPNext's own
	controller and this file is never imported.
	"""

	pass
