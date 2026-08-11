import json
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
	Roles, the Trip workflow, the dashboard chart, number cards, the workspace,
	print formats and web forms are always created.
	"""
	create_roles()
	ensure_app_doctypes()
	create_workflow()
	create_vehicle_driver_support()
	create_reports()
	create_dashboard_chart()
	create_workspace()
	create_number_cards()  # needs the workspace to exist so cards can be attached
	create_print_formats()
	create_web_forms()


def after_migrate():
	"""Re-run install-time setup after every migrate (idempotent).

	This keeps the environment consistent if `after_install` was interrupted,
	or if the site's ERPNext status changes between install and migrate.
	"""
	check_fallback_conflict()
	create_roles()
	ensure_app_doctypes()
	create_workflow()
	create_vehicle_driver_support()
	create_reports()
	create_dashboard_chart()
	create_workspace()
	create_number_cards()
	create_print_formats()
	create_web_forms()


def ensure_app_doctypes():
	"""Ensure this app's doctypes exist before fixtures reference them.

	The installer calls sync_for() before after_install, but on a fresh install
	the site's module map may not include this app yet, so that sync can skip it.
	Sync again here and, as a fallback, import the doctype fixtures directly
	(import_file_by_path is idempotent, and DocType.on_update creates the tables).
	"""
	from frappe.model.sync import sync_for

	sync_for("fleet_log")
	for folder, name in (
		("trip", "Trip"),
		("fuel_log", "Fuel Log"),
		("trip_expense", "Trip Expense"),
	):
		if frappe.db.exists("DocType", name):
			continue
		path = frappe.get_app_path("fleet_log", "fleet_log", "doctype", folder, f"{folder}.json")
		import_file_by_path(path)
	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Install-order safety
# ---------------------------------------------------------------------------
def check_fallback_conflict():
	"""Warn (loudly) when ERPNext was installed AFTER fleet_log while the
	fallback Vehicle/Driver doctypes were in use. Those collide with ERPNext's
	own doctypes; the fix is to reinstall fleet_log (see README)."""
	if not is_erpnext_installed():
		return
	if frappe.db.exists("DocType", {"name": "Vehicle", "module": "Fleet Log"}):
		message = (
			"fleet_log was installed in standalone mode and ERPNext was added later. "
			"The fallback Vehicle/Driver doctypes now conflict with ERPNext's own. "
			"Please back up your data, uninstall fleet_log, and reinstall it so it "
			"switches to ERPNext mode (Custom Fields instead of fallback doctypes)."
		)
		frappe.log_error(message=message, title="Fleet Log: fallback doctype conflict")
		print(f"\n[fleet_log] WARNING: {message}\n")


# ---------------------------------------------------------------------------
# Vehicle / Driver support
# ---------------------------------------------------------------------------
def create_vehicle_driver_support():
	if is_erpnext_installed():
		create_erpnext_custom_fields()
		ensure_driver_can_read_vehicles()
	else:
		import_fallback_doctypes()
		ensure_fallback_vehicle_fields()


def ensure_fallback_vehicle_fields():
	"""Upgrade path for sites that already imported the fallback Vehicle
	doctype before the service-schedule fields existed.

	The fallback fixtures are outside the standard sync path and only imported
	when the doctype is missing, so existing installs would never receive new
	fields. Add any missing ones as Custom Fields instead.
	"""
	if not frappe.db.exists("DocType", {"name": "Vehicle", "module": "Fleet Log"}):
		return
	if frappe.get_meta("Vehicle").has_field("service_interval_km"):
		return
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Vehicle": [
				{
					"fieldname": "service_interval_km",
					"label": "Service Interval (km)",
					"fieldtype": "Int",
					"default": "10000",
					"insert_after": "average_yield",
				},
				{
					"fieldname": "service_interval_months",
					"label": "Service Interval (months)",
					"fieldtype": "Int",
					"default": "6",
					"insert_after": "service_interval_km",
				},
				{
					"fieldname": "last_service_odometer",
					"label": "Last Service Odometer",
					"fieldtype": "Float",
					"precision": "2",
					"insert_after": "service_interval_months",
				},
				{
					"fieldname": "last_service_date",
					"label": "Last Service Date",
					"fieldtype": "Date",
					"insert_after": "last_service_odometer",
				},
			],
		},
		ignore_validate=True,
	)
	frappe.clear_cache()


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
			{
				"fieldname": "service_interval_km",
				"label": "Service Interval (km)",
				"fieldtype": "Int",
				"default": "10000",
				"insert_after": "average_yield",
				"description": "Reminder when the odometer crosses the last service odometer plus this interval.",
			},
			{
				"fieldname": "service_interval_months",
				"label": "Service Interval (months)",
				"fieldtype": "Int",
				"default": "6",
				"insert_after": "service_interval_km",
			},
			{
				"fieldname": "last_service_odometer",
				"label": "Last Service Odometer",
				"fieldtype": "Float",
				"precision": "2",
				"insert_after": "service_interval_months",
			},
			{
				"fieldname": "last_service_date",
				"label": "Last Service Date",
				"fieldtype": "Date",
				"insert_after": "last_service_odometer",
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
		"Trip": [
			{
				"fieldname": "cost_center",
				"label": "Cost Center",
				"fieldtype": "Link",
				"options": "Cost Center",
				"insert_after": "company",
				"description": "Optional cost center for multi-department cost attribution (ERPNext only).",
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
	of the standard doctype sync path, so it is imported explicitly. If the
	fixture changes (e.g. a new state was added), an existing workflow is
	updated in place so existing sites pick up the new fixture.
	"""
	path = frappe.get_app_path(
		"fleet_log", "fleet_log", "workflow", "trip_workflow", "trip_workflow.json"
	)
	with open(path, encoding="utf-8") as f:
		fixture = json.load(f)

	if frappe.db.exists("Workflow", "Trip Workflow"):
		doc = frappe.get_doc("Workflow", "Trip Workflow")
		states = {s.state for s in doc.states}
		if "Cancelled" not in states or not doc.send_email_alert:
			# apply the latest fixture onto the existing workflow. Child tables
			# are cleared and re-appended so no duplicate states/transitions.
			doc.send_email_alert = fixture["send_email_alert"]
			doc.workflow_state_field = fixture["workflow_state_field"]
			doc.states = []
			for state in fixture["states"]:
				doc.append("states", state)
			doc.transitions = []
			for transition in fixture["transitions"]:
				doc.append("transitions", transition)
			doc.save(ignore_permissions=True)
			frappe.clear_cache()
		return

	import_file_by_path(path)
	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def create_reports():
	"""Import the app's query reports (idempotent; also auto-synced on migrate)."""
	for name in (
		"cost_per_vehicle",
		"driver_mileage_report",
		"flagged_trips_report",
		"fuel_cost_per_driver",
		"fuel_price_trend",
		"fuel_yield_trend",
	):
		path = frappe.get_app_path("fleet_log", "fleet_log", "reports", name, f"{name}.json")
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

	The chart source is imported first: during `install-app` the site's module
	map can skip the app being installed, so the source is not guaranteed to
	exist from the standard sync yet.
	"""
	if frappe.db.exists("Dashboard Chart", "Fuel Yield Trend"):
		return
	source_path = frappe.get_app_path(
		"fleet_log", "fleet_log", "dashboard_chart_source", "fuel_yield_trend", "fuel_yield_trend.json"
	)
	import_file_by_path(source_path)
	path = frappe.get_app_path(
		"fleet_log", "fleet_log", "dashboard_chart", "fuel_yield_trend", "fuel_yield_trend.json"
	)
	import_file_by_path(path)
	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Number Cards (KPIs)
# ---------------------------------------------------------------------------
NUMBER_CARDS = [
	{
		"label": "Fuel Cost This Month",
		"document_type": "Fuel Log",
		"function": "Sum",
		"aggregate_function_based_on": "fuel_cost",
		"stats_time_interval": "Monthly",
		"color": "#f39c12",
		"filters_json": "{}",
		"type": "Document",
	},
	{
		"label": "Trips Completed",
		"document_type": "Trip",
		"function": "Count",
		"stats_time_interval": "Monthly",
		"color": "#2490EF",
		"filters_json": json.dumps({"status": ["in", ["Completed", "Reconciled"]]}),
		"type": "Document",
	},
	{
		"label": "Flagged Trips",
		"document_type": "Trip",
		"function": "Count",
		"color": "#e74c3c",
		"filters_json": json.dumps({"yield_flag": ["!=", "Normal"]}),
		"type": "Document",
	},
	{
		"label": "Fleet Size",
		"document_type": "Vehicle",
		"function": "Count",
		"color": "#27ae60",
		"filters_json": "{}",
		"type": "Document",
	},
]


def create_number_cards():
	"""Create the KPI Number Cards and attach them to the Fleet Log workspace."""
	for card in NUMBER_CARDS:
		if frappe.db.exists("Number Card", card["label"]):
			continue
		frappe.get_doc({"doctype": "Number Card", **card}).insert(
			ignore_permissions=True, ignore_mandatory=True
		)

	if frappe.db.exists("Workspace", "Fleet Log"):
		workspace = frappe.get_doc("Workspace", "Fleet Log")
		existing = {row.number_card_name for row in (workspace.number_cards or [])}
		for card in NUMBER_CARDS:
			if card["label"] not in existing:
				workspace.append("number_cards", {"number_card_name": card["label"]})
		if workspace.number_cards:
			workspace.save(ignore_permissions=True)
	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Print Formats & Web Forms
# ---------------------------------------------------------------------------
def create_print_formats():
	"""Import the app's print formats (idempotent; also auto-synced on migrate)."""
	for name, folder in (
		("Trip Print", "trip_print"),
		("Fuel Log Print", "fuel_log_print"),
		("Trip Expense Print", "trip_expense_print"),
	):
		if frappe.db.exists("Print Format", name):
			continue
		path = frappe.get_app_path(
			"fleet_log", "fleet_log", "print_format", folder, f"{folder}.json"
		)
		import_file_by_path(path)
	frappe.clear_cache()


def create_web_forms():
	"""Import the driver-facing web forms (idempotent; also auto-synced on migrate)."""
	for name, folder in (("Trip Log", "trip_log"),):
		if frappe.db.exists("Web Form", name):
			continue
		path = frappe.get_app_path("fleet_log", "fleet_log", "web_form", folder, f"{folder}.json")
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
