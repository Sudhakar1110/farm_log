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

# License expiry alert window (days)
LICENSE_EXPIRY_WINDOW_DAYS = 30

# Vehicle service defaults when the fields are left empty
DEFAULT_SERVICE_INTERVAL_KM = 10000
DEFAULT_SERVICE_INTERVAL_MONTHS = 6


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
# Notifications
# ---------------------------------------------------------------------------
def notify_user(user, subject, document_type, document_name, dedupe_hours=None):
	"""Create a Notification Log for a user (in-app alert).

	Pass dedupe_hours to skip creating the log when an identical one for the
	same document/user already exists within that window (prevents spam from
	recurring scheduled jobs).
	"""
	if not user or user == "Guest":
		return
	if dedupe_hours:
		exists = frappe.db.exists(
			"Notification Log",
			{
				"for_user": user,
				"document_type": document_type,
				"document_name": document_name,
				"subject": subject,
				"creation": [">=", add_to_date(now_datetime(), hours=-dedupe_hours)],
			},
		)
		if exists:
			return
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"subject": subject,
			"type": "Alert",
			"document_type": document_type,
			"document_name": document_name,
			"for_user": user,
		}
	).insert(ignore_permissions=True)


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


def get_effective_average_yield(vehicle):
	"""Baseline yield used for flagging.

	Returns the stored rolling average when available; otherwise falls back to
	the average over all completed trips of the vehicle. This closes the
	cold-start gap where a brand-new vehicle (no Reconciled trips yet) would
	otherwise compare every trip against 0 and flag everything "Normal".
	"""
	avg = get_vehicle_average_yield(vehicle)
	if avg > 0:
		return avg
	if not vehicle:
		return 0
	yields = frappe.db.get_all(
		"Trip",
		filters={
			"vehicle": vehicle,
			"status": ["in", ("Completed", "Reconciled")],
			"docstatus": ["!=", 2],
			"trip_yield": [">", 0],
		},
		pluck="trip_yield",
	)
	return flt(sum(flt(y) for y in yields) / len(yields), 2) if yields else 0


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


def update_vehicle_average_yield(vehicle, exclude_trip=None):
	"""Rolling average of trip_yield over all Reconciled trips of the vehicle.

	Cancelled trips (docstatus 2) are excluded so the average never includes
	yields that were rolled back via on_cancel. Pass exclude_trip (e.g. the
	trip being deleted, whose row still exists while on_trash runs) to drop it
	from the calculation.
	"""
	if not vehicle:
		return
	filters = {
		"vehicle": vehicle,
		"status": "Reconciled",
		"docstatus": ["!=", 2],
		"trip_yield": [">", 0],
	}
	if exclude_trip:
		filters["name"] = ["!=", exclude_trip]
	yields = frappe.db.get_all("Trip", filters=filters, pluck="trip_yield")
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
	trip.yield_flag = evaluate_yield_flag(
		trip.trip_yield, get_effective_average_yield(trip.vehicle)
	)
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
	doctype, so other doctypes are unaffected.

	A Driver sees:
	- Trips where they are the driver,
	- Fuel Logs they filled OR that belong to one of their trips,
	- Trip Expenses of their trips.
	"""
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
		return (
			f"(`tabFuel Log`.`filled_by` = {escaped} or "
			f"`tabFuel Log`.`trip` in (select `name` from `tabTrip` where `driver` = {escaped}))"
		)
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
		if doc.filled_by == driver:
			return None
		if doc.trip:
			owner_driver = frappe.db.get_value("Trip", doc.trip, "driver")
			return None if owner_driver == driver else False
		return False
	if doc.doctype == "Trip Expense":
		owner_driver = frappe.db.get_value("Trip", doc.trip, "driver") if doc.trip else None
		return None if owner_driver == driver else False
	return None


# ---------------------------------------------------------------------------
# Scheduler: stale trips
# ---------------------------------------------------------------------------
def _notify_trip_stakeholders(trip, kind):
	"""Notify the driver and fleet managers about a stale/abandoned trip.

	kind: "in-progress" or "assigned". Deduplicated within a 7-day window so a
	trip stuck for weeks does not generate a fresh bell every day.
	"""
	recipients = set(get_fleet_manager_users())
	driver_user = frappe.db.get_value("Driver", trip.driver, "user") if trip.driver else None
	if driver_user:
		recipients.add(driver_user)
	for user in recipients:
		if not user:
			continue
		if kind == "in-progress":
			subject = _(
				"Trip {0} ({1}) has been 'In Progress' for more than {2} hours"
			).format(trip.name, trip.vehicle, STALE_TRIP_HOURS)
		else:
			subject = _(
				"Trip {0} ({1}) was assigned more than {2} hours ago but has not started"
			).format(trip.name, trip.vehicle, STALE_TRIP_HOURS)
		notify_user(user, subject, "Trip", trip.name, dedupe_hours=24 * 7)


def flag_stale_trips():
	"""Daily job: notify about Trips stuck 'In Progress' for > 24 hours and
	Trips that were assigned but never started after 24 hours."""
	cutoff = add_to_date(now_datetime(), hours=-STALE_TRIP_HOURS)
	stale_trips = frappe.db.get_all(
		"Trip",
		filters={"status": "In Progress", "start_time": ["<", cutoff]},
		fields=["name", "driver", "vehicle"],
	)
	for trip in stale_trips:
		_notify_trip_stakeholders(trip, "in-progress")

	never_started = frappe.db.get_all(
		"Trip",
		filters={"status": "Assigned", "creation": ["<", cutoff]},
		fields=["name", "driver", "vehicle"],
	)
	for trip in never_started:
		_notify_trip_stakeholders(trip, "assigned")

	frappe.db.commit()


# ---------------------------------------------------------------------------
# Scheduler: vehicle maintenance
# ---------------------------------------------------------------------------
def get_vehicle_service_due(vehicle):
	"""Return (km_due, date_due) for a vehicle based on its service schedule."""
	meta = frappe.get_meta("Vehicle")
	if not meta.has_field("service_interval_km"):
		return None, None
	interval_km = flt(vehicle.get("service_interval_km")) or DEFAULT_SERVICE_INTERVAL_KM
	interval_months = int(flt(vehicle.get("service_interval_months")) or DEFAULT_SERVICE_INTERVAL_MONTHS)
	last_odo = flt(vehicle.get("last_service_odometer"))
	last_date = vehicle.get("last_service_date")

	from frappe.utils import add_months

	# If the vehicle has never been serviced, the first interval is measured
	# from the vehicle's creation/0 odometer.
	km_due = (last_odo + interval_km) if last_odo else interval_km
	date_due = add_months(last_date, interval_months) if last_date else None
	return km_due, date_due


def check_vehicle_maintenance():
	"""Daily job: notify Fleet Managers when a vehicle crosses its service due
	odometer or due date."""
	meta = frappe.get_meta("Vehicle")
	if not meta.has_field("service_interval_km"):
		return
	from frappe.utils import getdate, today

	today_date = getdate(today())
	vehicles = frappe.db.get_all(
		"Vehicle",
		fields=[
			"name",
			"current_odometer",
			"service_interval_km",
			"service_interval_months",
			"last_service_odometer",
			"last_service_date",
		],
	)
	for v in vehicles:
		km_due, date_due = get_vehicle_service_due(v)
		km_due_flagged = km_due is not None and flt(v.current_odometer) >= flt(km_due)
		date_due_flagged = bool(date_due and today_date >= getdate(date_due))
		if not km_due_flagged and not date_due_flagged:
			continue
		subject = _("Maintenance due for Vehicle {0} (odometer {1})").format(
			v.name, flt(v.current_odometer)
		)
		for user in get_fleet_manager_users():
			# repeat at most monthly until the service is actually logged
			notify_user(user, subject, "Vehicle", v.name, dedupe_hours=24 * 30)
	frappe.db.commit()


# ---------------------------------------------------------------------------
# Scheduler: driver license expiry
# ---------------------------------------------------------------------------
def check_license_expiry():
	"""Daily job: notify about driver licenses that have expired or expire
	within the next 30 days."""
	meta = frappe.get_meta("Driver")
	expiry_field = None
	for field in ("license_expiry", "expiry_date"):
		if meta.has_field(field):
			expiry_field = field
			break
	if not expiry_field:
		return
	from frappe.utils import getdate, today

	today_date = getdate(today())
	drivers = frappe.db.get_all(
		"Driver", filters={expiry_field: ["is", "set"]}, fields=["name", "user", expiry_field]
	)
	for driver in drivers:
		expiry = getdate(driver.get(expiry_field))
		days_left = (expiry - today_date).days
		if days_left < 0:
			subject = _("Driving license for Driver {0} expired on {1}").format(
				driver.name, expiry
			)
		elif days_left <= LICENSE_EXPIRY_WINDOW_DAYS:
			subject = _("Driving license for Driver {0} expires in {1} day(s)").format(
				driver.name, max(days_left, 0)
			)
		else:
			continue
		recipients = set(get_fleet_manager_users())
		if driver.user:
			recipients.add(driver.user)
		for user in recipients:
			# repeat at most weekly until the license is renewed
			notify_user(user, subject, "Driver", driver.name, dedupe_hours=24 * 7)
	frappe.db.commit()


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def boot_session(bootinfo):
	"""Expose the ERPNext flag to the client (used to conditionally render UI)."""
	bootinfo.erpnext_installed = is_erpnext_installed()


# ---------------------------------------------------------------------------
# Install verification
# ---------------------------------------------------------------------------
def verify_install():
	"""Check that everything fleet_log is supposed to install actually exists.

	Run from the bench root:

	    bench --site <sitename> execute fleet_log.utils.verify_install

	Prints one line per expected record (OK / MISSING) and returns a dict with
	the counts. Read-only (no data is changed).
	"""
	expected = [
		("Role", "Fleet Manager"),
		("Role", "Driver"),
		("DocType", "Trip"),
		("DocType", "Fuel Log"),
		("DocType", "Trip Expense"),
		("DocType", "Vehicle"),
		("DocType", "Driver"),
		("Workflow", "Trip Workflow"),
		("Workflow State", "Assigned"),
		("Workflow State", "In Progress"),
		("Workflow State", "Completed"),
		("Workflow State", "Reconciled"),
		("Workflow State", "Cancelled"),
		("Dashboard Chart", "Fuel Yield Trend"),
		("Dashboard", "Fuel Yield Trend"),
		("Workspace", "Fleet Log"),
		("Number Card", "Fuel Cost This Month"),
		("Number Card", "Trips Completed"),
		("Number Card", "Flagged Trips"),
		("Number Card", "Fleet Size"),
		("Report", "Cost per Vehicle"),
		("Report", "Driver Mileage Report"),
		("Report", "Flagged Trips Report"),
		("Report", "Fuel Yield Trend"),
		("Report", "Fuel Price Trend"),
		("Report", "Fuel Cost per Driver"),
		("Print Format", "Trip Print"),
		("Print Format", "Fuel Log Print"),
		("Print Format", "Trip Expense Print"),
		("Web Form", "Trip Log"),
	]

	missing = []
	for doctype, name in expected:
		exists = frappe.db.exists(doctype, name)
		print(f"{'OK      ' if exists else 'MISSING '} {doctype}: {name}")
		if not exists:
			missing.append((doctype, name))

	print("")
	if missing:
		print(f"{len(missing)} missing record(s):")
		for doctype, name in missing:
			print(f"  - {doctype}: {name}")
	else:
		print("All expected fleet_log records are present.")

	return {"checked": len(expected), "missing": len(missing), "missing_records": missing}
