"""Bulk data import helpers.

Run from the bench console / scheduler:

	bench --site <site> execute fleet_log.data_import.import_trips_from_csv \
		--kwargs '{"file_path": "/tmp/trips.csv"}'

CSV column headers (Trips):
	vehicle, driver, purpose, start_location, end_location,
	start_odometer, end_odometer, start_time, end_time, status, trip_type

CSV column headers (Fuel Logs):
	vehicle, trip, fuel_quantity, fuel_cost, odometer_at_fill,
	fuel_type, fuel_vendor, filled_by

Date/time columns accept "YYYY-MM-DD HH:MM:SS" (or "YYYY-MM-DD").
"""

import csv

import frappe
from frappe import _
from frappe.utils import flt


def _csv_rows(file_path):
	with open(file_path, newline="", encoding="utf-8-sig") as f:
		rows = list(csv.DictReader(f))
	if not rows:
		frappe.throw(_("The CSV file is empty or missing a header row."))
	return rows


def import_trips_from_csv(file_path, commit=True):
	"""Bulk-import Trips. Returns the number of records created.

	The workflow state machine only allows new trips to start as 'Assigned'
	(or 'In Progress'), so rows carrying a terminal status (Completed /
	Reconciled / Cancelled) are imported as 'Assigned' - historical trips can
	then be completed through the normal workflow.
	"""
	rows = _csv_rows(file_path)
	created = 0
	adjusted = 0
	for row in rows:
		status = row.get("status") or "Assigned"
		if status not in ("Assigned", "In Progress"):
			adjusted += 1
			status = "Assigned"
		doc = frappe.get_doc(
			{
				"doctype": "Trip",
				"vehicle": row.get("vehicle"),
				"driver": row.get("driver") or None,
				"purpose": row.get("purpose") or None,
				"trip_type": row.get("trip_type") or "Other",
				"start_location": row.get("start_location") or None,
				"start_odometer": flt(row.get("start_odometer") or 0),
				"start_time": row.get("start_time") or None,
				"end_location": row.get("end_location") or None,
				"end_odometer": row.get("end_odometer") or None,
				"end_time": row.get("end_time") or None,
				"status": status,
			}
		).insert(ignore_permissions=True)
		created += 1
	if commit:
		frappe.db.commit()
	message = _("{0} Trip(s) imported.").format(created)
	if adjusted:
		message += " " + _(
			"{0} record(s) with a terminal status were imported as 'Assigned' "
			"because the workflow requires trips to start in 'Assigned'."
		).format(adjusted)
	frappe.msgprint(message)
	return created


def import_fuel_logs_from_csv(file_path, commit=True):
	"""Bulk-import Fuel Logs. Returns the number of records created."""
	rows = _csv_rows(file_path)
	created = 0
	for row in rows:
		doc = frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": row.get("vehicle"),
				"trip": row.get("trip") or None,
				"fuel_quantity": flt(row.get("fuel_quantity") or 0),
				"fuel_cost": flt(row.get("fuel_cost") or 0),
				"odometer_at_fill": flt(row.get("odometer_at_fill") or 0),
				"fuel_type": row.get("fuel_type") or None,
				"fuel_vendor": row.get("fuel_vendor") or None,
				"filled_by": row.get("filled_by") or None,
			}
		).insert(ignore_permissions=True)
		created += 1
	if commit:
		frappe.db.commit()
	frappe.msgprint(_("{0} Fuel Log(s) imported.").format(created))
	return created
