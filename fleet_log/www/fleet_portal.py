import frappe

no_cache = 1


def get_context(context):
	"""Expose the current session and CSRF token to the portal template.

	The portal renders its own login screen for guests and a branded app shell
	for authenticated users. All data flows through the whitelisted
	``fleet_log.api`` endpoints, so the page itself never needs to touch the
	database directly.
	"""
	context.user = frappe.session.user
	context.csrf_token = getattr(frappe.session, "csrf_token", "") or ""
	context.full_name = frappe.utils.get_fullname(frappe.session.user)
