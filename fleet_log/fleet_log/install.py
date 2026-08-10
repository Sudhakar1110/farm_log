import os

import frappe
from frappe.modules.import_file import import_file_by_path

from fleet_log.utils import is_erpnext_installed


def after_install():
	"""Run once at `bench install-app fleet_log`.

	Mode detection (everything is idempotent, so a follow-up `bench migrate`
	through :meth:`after_migrate` is always safe):

	- ERPNext present -> extend ERPNext's Vehicle / Driver via Custom Fields.
	- Plain Frappe    -> create self-contained fallback Vehicle / Driver doctypes.
	Roles, the Trip workflow, the dashboard chart and the workspace are always created.
	"""
	create_roles()
	create_workflow()
	create_vehicle_driver_support()
	create_dashboard_chart()
	create_workspace()


def after_migrate():
	"""Re-run install-time setup after every migrate (idempotent).

	This keeps the environment consistent if `after_install` was interrupted,
	or if the site's ERPNext status changes between install and migrate.
	"""
	create_roles()
	create_workflow()
	create_vehicle_driver_support()
	create_dashboard_chart()
	create_workspace()


# ---------------------------------------------------------------------------
# Vehicle / Driver support
# ---------------------------------------------------------------------------
def create_vehicle_driver_support():
	if is_erpnext_installed():
		create_erpnext_custom_fields()
		ensure_driver_can_read_vehicles()
	else:
		import_fallback_doctypes()


def import_fallback_doctypes():
	"""Import the local Vehicle/Driver doctypes (plain-Frappe sites only).

	The JSON fixtures live under `fallback_doctypes/`, OUTSIDE the module sync
	path, so `bench migrate` never clashes with ERPNext's own Vehicle/Driver
	doctypes when ERPNext is installed. `import_file_by_path` is idempotent -
	it skips files whose hash is unchanged.
	"""
	base = frappe.get_app_path("fleet_log", "fallback_doctypes")
	for doctype in ("Vehicle", "Driver"):
		if frappe.db.exists("DocType", doctype):
			continue
		path = os.path.join(base, doctype.lower(), f"{doctype}.json")
		import_file_by_path(path)
	frappe.clear_cache()


def create_erpnext_custom_fields():
	"""Extend ERPNext's Vehicle/Driver with the fields fleet_log needs.

	ERPNext v15 already has Vehicle.license_plate / last_odometer / fuel_type
	and Driver.full_name / license_number / expiry_date / employee - those are
	reused as-is. Only the missing fields are added as Custom Fields.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(get_fleet_log_custom_fields(), ignore_validate=True)


def get_fleet_log_custom_fields():
	return {
		"Vehicle": [
			{
				"fieldname": "current_odometer",
				"label": "Current Odometer",
				"fieldtype": "Float",
				"precision": "2",
				"read_only": 1,
				"insert_after": "last_odometer",
				"description": "Updated automatically when a Trip is completed.",
			},
			{
				"fieldname": "vehicle_type",
				"label": "Vehicle Type",
				"fieldtype": "Select",
				"options": "Car\nVan\nTruck\nBike\nOther",
				"insert_after": "current_odometer",
			},
			{
				"fieldname": "average_yield",
				"label": "Average Yield (km/litre)",
				"fieldtype": "Float",
				"precision": "2",
				"read_only": 1,
				"insert_after": "vehicle_type",
				"description": "Rolling average, recalculated after every Reconciled trip.",
			},
		],
		"Driver": [
			{
				"fieldname": "user",
				"label": "User",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "employee",
				"description": "Links the Driver to a system User so Driver-role users are scoped to their own trips and fuel logs.",
			},
			{
				"fieldname": "assigned_vehicle",
				"label": "Assigned Vehicle",
				"fieldtype": "Link",
				"options": "Vehicle",
				"insert_after": "user",
			},
		],
	}


def ensure_driver_can_read_vehicles():
	"""Give the Driver role read access to ERPNext's Vehicle doctype.

	Drivers need to see vehicle odometer readings (the Trip/Fuel Log forms
	pre-fill them from the Vehicle). Existing ERPNext permissions are preserved
	(setup_custom_perms copies them into Custom DocPerm first).
	"""
	if frappe.db.exists("Custom DocPerm", {"parent": "Vehicle", "role": "Driver"}):
		return
	frappe.permissions.add_permission("Vehicle", "Driver", ptype="read")


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
def create_roles():
	"""Create the Fleet Manager and Driver roles if missing."""
	for role_name in ("Fleet Manager", "Driver"):
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc(
			{"doctype": "Role", "role_name": role_name, "desk_access": 1, "is_custom": 0}
		).insert(ignore_permissions=True, ignore_mandatory=True)
	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------
def create_workflow():
	"""Import the Trip workflow fixture (idempotent).

	The fixture lives under the module's `workflow/` folder, which is not part
	of the standard doctype sync path, so it is imported explicitly. Workflow
	State and Workflow Action Master records are created automatically by the
	Workflow document's on_update during the import.
	"""
	if frappe.db.exists("Workflow", "Trip Workflow"):
		return
	path = frappe.get_app_path(
		"fleet_log", "fleet_log", "workflow", "trip_workflow", "trip_workflow.json"
	)
	import_file_by_path(path)
	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Dashboard Chart
# ---------------------------------------------------------------------------
def create_dashboard_chart():
	"""Import the Fuel Yield Trend Dashboard Chart fixture (idempotent).

	The fixture references the custom "Fuel Yield Trend" Dashboard Chart Source,
	which provides per-vehicle trip_yield line chart data. The Dashboard Chart
	is then linked from the Fleet Log workspace.
	"""
	if frappe.db.exists("Dashboard Chart", "Fuel Yield Trend"):
		return
	path = frappe.get_app_path(
		"fleet_log", "fleet_log", "dashboard_chart", "fuel_yield_trend", "fuel_yield_trend.json"
	)
	import_file_by_path(path)
	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
def create_workspace():
	"""Import the Fleet Log workspace fixture (idempotent).

	The workspace provides the Fleet Log page in the Frappe UI, including the
	Fuel Yield Trend line chart and links to all doctypes and reports in the
	module.
	"""
	if frappe.db.exists("Workspace", "Fleet Log"):
		return
	path = frappe.get_app_path(
		"fleet_log", "fleet_log", "workspace", "fleet_log", "fleet_log.json"
	)
	import_file_by_path(path)
	frappe.clear_cache()