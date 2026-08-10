import frappe
from frappe import _
from frappe.utils import add_to_date, flt, now_datetime

# ---------------------------------------------------------------------------
# Yield-flag thresholds (fraction of the vehicle's average yield)
# ---------------------------------------------------------------------------
NORMAL_THRESHOLD = 0.85  # within 15% of average        -> Normal
BELOW_AVG_THRESHOLD = 0.70  # 15-30% below average          -> Below Average
# < 70% of average (more than 30% below)               -> Critical

STALE_TRIP_HOURS = 24


# ---------------------------------------------------------------------------
# ERPNext detection
# ---------------------------------------------------------------------------
def is_erpnext_installed():
	"""True when ERPNext is installed on the current site.

	This is the single place that checks for ERPNext; doctypes are only
	referenced when this returns True (or guarded with has_field checks).
	"""
	return "erpnext" in frappe.get_installed_apps()


# ---------------------------------------------------------------------------
# Driver / user mapping
# ---------------------------------------------------------------------------
def get_driver_for_user(user=None):
	"""Return the Driver record linked to a user via Driver.user, if any."""
	user = user or frappe.session.user
	if not user:
		return None
	return frappe.db.get_value("Driver", {"user": user}, "name")


def get_fleet_manager_users():
	"""Users holding the Fleet Manager role (plus Administrator)."""
	users = set(frappe.db.get_all("Has Role", filters={"role": "Fleet Manager"}, pluck="parent"))
	users.add("Administrator")
	return sorted(u for u in users if u)


def is_fleet_manager(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "Fleet Manager" in frappe.get_roles(user)


# ---------------------------------------------------------------------------
# Vehicle helpers
# ---------------------------------------------------------------------------
def get_vehicle_current_odometer(vehicle):
	"""Last known odometer of a vehicle.

	Prefers our `current_odometer`, but falls back to ERPNext's `last_odometer`
	when the field is absent OR still empty (ERPNext mode: the custom field
	starts blank until the first trip completes).
	"""
	if not vehicle:
		return 0
	meta = frappe.get_meta("Vehicle")
	value = None
	if meta.has_field("current_odometer"):
		value = frappe.db.get_value("Vehicle", vehicle, "current_odometer")
	if value in (None, "") and meta.has_field("last_odometer"):
		value = frappe.db.get_value("Vehicle", vehicle, "last_odometer")
	return flt(value or 0)


def get_vehicle_average_yield(vehicle):
	if not vehicle:
		return 0
	return flt(frappe.db.get_value("Vehicle", vehicle, "average_yield") or 0)


def update_vehicle_odometer(vehicle, odometer):
	"""Sync the vehicle's odometer after a trip is completed."""
	if not vehicle:
		return
	meta = frappe.get_meta("Vehicle")
	value = flt(odometer)
	if meta.has_field("current_odometer"):
		frappe.db.set_value("Vehicle", vehicle, "current_odometer", value)
	# keep ERPNext's canonical odometer field in sync too (harmless in both modes)
	if meta.has_field("last_odometer"):
		frappe.db.set_value("Vehicle", vehicle, "last_odometer", value)


def update_vehicle_average_yield(vehicle):
	"""Rolling average of trip_yield over all Reconciled trips of the vehicle."""
	if not vehicle:
		return
	yields = frappe.db.get_all(
		"Trip",
		filters={"vehicle": vehicle, "status": "Reconciled", "trip_yield": [">", 0]},
		pluck="trip_yield",
	)
	average = sum(flt(y) for y in yields) / len(yields) if yields else 0
	if frappe.get_meta("Vehicle").has_field("average_yield"):
		frappe.db.set_value("Vehicle", vehicle, "average_yield", flt(average, 2))


# ---------------------------------------------------------------------------
# Yield calculations (shared by Trip and Fuel Log)
# ---------------------------------------------------------------------------
def calculate_trip_yield(distance_covered, total_fuel_used):
	"""km per litre. Returns 0 when there is no fuel data (no divide-by-zero)."""
	if flt(total_fuel_used) <= 0:
		return 0
	return flt(flt(distance_covered) / flt(total_fuel_used), 2)


def evaluate_yield_flag(trip_yield, average_yield):
	"""Compare a trip's yield against the vehicle's average and set the flag."""
	trip_yield, average_yield = flt(trip_yield), flt(average_yield)
	if average_yield <= 0 or trip_yield <= 0:
		# no baseline yet, or no fuel data to judge against
		return "Normal"
	ratio = trip_yield / average_yield
	if ratio >= NORMAL_THRESHOLD:
		return "Normal"
	if ratio >= BELOW_AVG_THRESHOLD:
		return "Below Average"
	return "Critical"


def get_total_fuel_used(trip_name):
	"""Sum of fuel_quantity across all Fuel Logs linked to a Trip."""
	if not trip_name:
		return 0
	quantities = frappe.db.get_all("Fuel Log", filters={"trip": trip_name}, pluck="fuel_quantity")
	return flt(sum(flt(q) for q in quantities), 2)


def recalculate_trip_metrics(trip_name):
	"""Recompute distance, total fuel, yield and flag for a Trip from the DB.

	Used by the Fuel Log doc_event so totals stay live as fuel logs are
	added/removed. Uses db_set so it never re-triggers validate (no recursion).
	"""
	if not trip_name:
		return
	trip = frappe.get_doc("Trip", trip_name)
	status = trip.status or "Assigned"
	if status not in ("Completed", "Reconciled"):
		return
	if trip.end_odometer is not None and trip.start_odometer is not None:
		trip.distance_covered = flt(trip.end_odometer) - flt(trip.start_odometer)
	trip.total_fuel_used = get_total_fuel_used(trip_name)
	trip.trip_yield = calculate_trip_yield(trip.distance_covered, trip.total_fuel_used)
	trip.yield_flag = evaluate_yield_flag(trip.trip_yield, get_vehicle_average_yield(trip.vehicle))
	trip.db_set(
		{
			"distance_covered": trip.distance_covered,
			"total_fuel_used": trip.total_fuel_used,
			"trip_yield": trip.trip_yield,
			"yield_flag": trip.yield_flag,
		},
		update_modified=False,
	)


# ---------------------------------------------------------------------------
# Doc events
# ---------------------------------------------------------------------------
def update_trip_fuel_totals(doc, method=None):
	"""hooks.doc_events: keep a Trip's totals/yield in sync when a Fuel Log changes."""
	if doc.doctype != "Fuel Log" or not doc.trip:
		return
	if frappe.db.get_value("Trip", doc.trip, "status") != "Reconciled":
		recalculate_trip_metrics(doc.trip)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def permission_query_conditions(user=None, doctype=None):
	"""hooks.permission_query_conditions: scope Driver-role users to their own
	records (Trips / Fuel Logs / Trip Expenses). Called with the queried
	doctype, so other doctypes are unaffected."""
	if not user:
		user = frappe.session.user
	if not doctype or user == "Administrator" or is_fleet_manager(user):
		return ""
	if "Driver" not in frappe.get_roles(user):
		return ""
	driver = get_driver_for_user(user)
	if not driver:
		# a Driver-role user without a Driver record sees nothing
		return "1 = 0"
	escaped = frappe.db.escape(driver)
	if doctype == "Trip":
		return f"`tabTrip`.`driver` = {escaped}"
	if doctype == "Fuel Log":
		return f"`tabFuel Log`.`filled_by` = {escaped}"
	if doctype == "Trip Expense":
		return (
			f"`tabTrip Expense`.`trip` in "
			f"(select `name` from `tabTrip` where `driver` = {escaped})"
		)
	return ""


def has_permission(doc, ptype=None, user=None, debug=None):
	"""hooks.has_permission: a Driver-role user may only touch their own
	fleet records. Returns None (no opinion) when the record is theirs, False
	otherwise. Fleet Managers / System Managers are never restricted here."""
	user = user or frappe.session.user
	if user == "Administrator" or is_fleet_manager(user) or "System Manager" in frappe.get_roles(user):
		return None
	if "Driver" not in frappe.get_roles(user):
		return None
	driver = get_driver_for_user(user)
	if not driver:
		return False
	if doc.doctype == "Trip":
		return None if doc.driver == driver else False
	if doc.doctype == "Fuel Log":
		return None if doc.filled_by == driver else False
	if doc.doctype == "Trip Expense":
		owner_driver = frappe.db.get_value("Trip", doc.trip, "driver") if doc.trip else None
		return None if owner_driver == driver else False
	return None


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
def flag_stale_trips():
	"""Daily job: notify the driver and fleet managers about Trips that have
	been 'In Progress' for more than 24 hours."""
	cutoff = add_to_date(now_datetime(), hours=-STALE_TRIP_HOURS)
	stale_trips = frappe.db.get_all(
		"Trip",
		filters={"status": "In Progress", "start_time": ["<", cutoff]},
		fields=["name", "driver", "vehicle"],
	)
	for trip in stale_trips:
		recipients = set(get_fleet_manager_users())
		driver_user = frappe.db.get_value("Driver", trip.driver, "user") if trip.driver else None
		if driver_user:
			recipients.add(driver_user)
		for user in recipients:
			if not user:
				continue
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": _(
						"Trip {0} ({1}) has been 'In Progress' for more than {2} hours"
					).format(trip.name, trip.vehicle, STALE_TRIP_HOURS),
					"type": "Alert",
					"document_type": "Trip",
					"document_name": trip.name,
					"for_user": user,
				}
			).insert(ignore_permissions=True)
	frappe.db.commit()


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def boot_session(bootinfo):
	"""Expose the ERPNext flag to the client (used to conditionally render UI)."""
	bootinfo.erpnext_installed = is_erpnext_installed()
