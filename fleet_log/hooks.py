app_name = "fleet_log"
app_title = "Fleet Log"
app_publisher = "Fleet Log Contributors"
app_description = (
	"Vehicle trips, odometer readings, fuel logging and fuel yield (mileage) "
	"tracking for Frappe v15. Runs standalone on plain Frappe and auto-detects "
	"ERPNext v15 to reuse its Fleet Management and Accounts modules."
)
app_email = "support@example.com"
app_icon = "octicon octicon-rocket"
app_license = "MIT"
app_version = "0.1.0"

# ---------------------------------------------------------------------------
# Apps required before installing this app.
#
# Deliberately EMPTY: fleet_log must install and run on plain Frappe alone.
# ERPNext (when present on the same site) is detected at runtime through
# fleet_log.utils.is_erpnext_installed() - never via required_apps.
# ---------------------------------------------------------------------------
required_apps = []

# Includes in <head>
# ------------------
# app_include_css = "/assets/fleet_log/css/fleet_log.css"
# app_include_js = "/assets/fleet_log/js/fleet_log.js"

# ---------------------------------------------------
# Install / migrate hooks
# ---------------------------------------------------
# after_install  - creates the Trip workflow and roles, and either creates the
#                  fallback Vehicle/Driver doctypes (plain Frappe) or extends
#                  ERPNext's Vehicle/Driver via Custom Fields (ERPNext present).
after_install = "fleet_log.install.after_install"
after_migrate = "fleet_log.install.after_migrate"

# ---------------------------------------------------
# Scheduled jobs (daily)
# ---------------------------------------------------
# - flag_stale_trips: notify about Trips stuck "In Progress" > 24h and Trips
#   assigned but never started > 24h.
# - check_vehicle_maintenance: notify Fleet Managers when a vehicle crosses
#   its service due odometer or due date.
# - check_license_expiry: notify about driver licenses expired / expiring soon.
scheduler_events = {
	"daily": [
		"fleet_log.utils.flag_stale_trips",
		"fleet_log.utils.check_vehicle_maintenance",
		"fleet_log.utils.check_license_expiry",
	]
}

# ---------------------------------------------------
# Permissions
# ---------------------------------------------------
# Row-level scoping for Driver-role users. The hook receives (user, doctype)
# and returns a SQL condition only for the doctypes this app manages, so the
# condition is never applied to unrelated doctype queries.
permission_query_conditions = {
	"*": "fleet_log.utils.permission_query_conditions",
}

# Single-document hardening: a Driver-role user may only read/write Trips,
# Fuel Logs and Trip Expenses that belong to their linked Driver record.
has_permission = {
	"Trip": "fleet_log.utils.has_permission",
	"Fuel Log": "fleet_log.utils.has_permission",
	"Trip Expense": "fleet_log.utils.has_permission",
}

# ---------------------------------------------------
# Document events
# ---------------------------------------------------
# Keep a Trip's total fuel, yield and flag in sync whenever a linked Fuel Log
# is created, updated or deleted.
doc_events = {
	"Fuel Log": {
		"on_update": "fleet_log.utils.update_trip_fuel_totals",
		"on_trash": "fleet_log.utils.update_trip_fuel_totals",
	}
}

# Expose the ERPNext flag to the client so UI (e.g. the "Create Expense Claim"
# button on Trip Expense) can be rendered conditionally.
boot_session = "fleet_log.utils.boot_session"

# ---------------------------------------------------
# Website / Docs (informational only)
# ---------------------------------------------------
source_link = "https://github.com/fleet_log/fleet_log"
docs_base_url = "https://docs.frappe.io/apps"
headline = "Fleet trip & fuel log management for Frappe v15"
sub_heading = "Track vehicle trips, odometers, fuel logs and fuel yield - standalone on Frappe, or deeply integrated with ERPNext v15."
