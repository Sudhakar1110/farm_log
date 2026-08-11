"""Whitelisted REST endpoints for mobile / field data capture.

All endpoints run under the normal permission system: Driver-role users are
scoped to their own records by the `has_permission` / `permission_query_conditions`
hooks, so these methods are safe to expose to the web.

Example (from a mobile client):

	POST /api/method/fleet_log.api.create_trip
	    {"vehicle": "V-0001", "purpose": "Field spray", "start_odometer": 12450}
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime


@frappe.whitelist()
def get_my_vehicles():
	"""Vehicles the current user can read (used to populate pickers)."""
	fields = ["name"]
	meta = frappe.get_meta("Vehicle")
	if meta.has_field("registration_number"):
		fields.append("registration_number")
	if meta.has_field("license_plate"):
		fields.append("license_plate")
	if meta.has_field("current_odometer"):
		fields.append("current_odometer")
	if meta.has_field("last_odometer"):
		fields.append("last_odometer")
	return frappe.db.get_all("Vehicle", fields=fields, order_by="name")


@frappe.whitelist()
def create_trip(
	vehicle,
	driver=None,
	purpose=None,
	start_location=None,
	start_odometer=0,
	start_time=None,
	trip_type="Other",
):
	"""Create a Trip (Assigned). The controller sets the driver for Driver-role
	users and runs all validation (workflow transitions, odometer checks, etc.)."""
	if not vehicle:
		frappe.throw(_("vehicle is required"))
	doc = frappe.get_doc(
		{
			"doctype": "Trip",
			"vehicle": vehicle,
			"driver": driver or None,
			"purpose": purpose or None,
			"start_location": start_location or None,
			"start_odometer": flt(start_odometer),
			"start_time": start_time or now_datetime(),
			"trip_type": trip_type or "Other",
		}
	).insert()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def update_trip(
	name,
	start_odometer=None,
	start_time=None,
	end_location=None,
	end_odometer=None,
	end_time=None,
	status=None,
):
	"""Update an existing Trip (end readings, or a workflow transition)."""
	doc = frappe.get_doc("Trip", name)
	if start_odometer is not None:
		doc.start_odometer = flt(start_odometer)
	if start_time:
		doc.start_time = start_time
	if end_location:
		doc.end_location = end_location
	if end_odometer is not None:
		doc.end_odometer = flt(end_odometer)
	if end_time:
		doc.end_time = end_time
	if status:
		doc.status = status
	doc.save()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def log_fuel(
	vehicle,
	fuel_quantity,
	fuel_cost,
	odometer_at_fill,
	trip=None,
	fuel_type=None,
	fuel_vendor=None,
):
	"""Create a Fuel Log (standalone or linked to a trip)."""
	if not vehicle or not fuel_quantity or fuel_cost is None or odometer_at_fill is None:
		frappe.throw(_("vehicle, fuel_quantity, fuel_cost and odometer_at_fill are required"))
	doc = frappe.get_doc(
		{
			"doctype": "Fuel Log",
			"vehicle": vehicle,
			"trip": trip or None,
			"fuel_quantity": flt(fuel_quantity),
			"fuel_cost": flt(fuel_cost),
			"odometer_at_fill": flt(odometer_at_fill),
			"fuel_type": fuel_type or None,
			"fuel_vendor": fuel_vendor or None,
		}
	).insert()
	return {"name": doc.name, "sanity_flag": doc.sanity_flag}


@frappe.whitelist()
def get_my_trips(limit=50, status=None):
	"""Trips visible to the current user (scoped for Driver-role users)."""
	filters = {}
	if status:
		filters["status"] = status
	return frappe.db.get_all(
		"Trip",
		filters=filters,
		fields=[
			"name",
			"vehicle",
			"driver",
			"status",
			"purpose",
			"start_location",
			"start_odometer",
			"start_time",
			"end_location",
			"end_odometer",
			"end_time",
			"distance_covered",
			"total_fuel_used",
			"trip_yield",
			"yield_flag",
		],
		order_by="creation desc",
		limit_page_length=cint(limit) or 50,
	)


@frappe.whitelist()
def get_my_fuel_logs(limit=50):
	"""Fuel Logs visible to the current user (scoped for Driver-role users)."""
	return frappe.db.get_all(
		"Fuel Log",
		fields=[
			"name",
			"vehicle",
			"trip",
			"fuel_quantity",
			"fuel_cost",
			"fuel_type",
			"fuel_vendor",
			"odometer_at_fill",
			"fill_up_yield",
			"sanity_flag",
			"price_per_litre",
		],
		order_by="creation desc",
		limit_page_length=cint(limit) or 50,
	)
