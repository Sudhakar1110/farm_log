import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "trip", "label": _("Trip"), "fieldtype": "Link", "options": "Trip", "width": 140},
		{
			"fieldname": "vehicle",
			"label": _("Vehicle"),
			"fieldtype": "Link",
			"options": "Vehicle",
			"width": 150,
		},
		{
			"fieldname": "driver",
			"label": _("Driver"),
			"fieldtype": "Link",
			"options": "Driver",
			"width": 150,
		},
		{"fieldname": "end_time", "label": _("End Time"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
		{"fieldname": "yield_flag", "label": _("Yield Flag"), "fieldtype": "Data", "width": 110},
		{
			"fieldname": "trip_yield",
			"label": _("Trip Yield (km/litre)"),
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"fieldname": "suspicious_fuel_logs",
			"label": _("Suspicious Fuel Logs"),
			"fieldtype": "Int",
			"width": 130,
		},
	]


def get_data(filters):
	conditions = [
		"t.status in ('Completed', 'Reconciled')",
		"t.end_time >= %(from_date)s",
		"t.end_time <= %(to_date)s",
	]
	params = {
		"from_date": filters.get("from_date") or "1970-01-01",
		"to_date": filters.get("to_date") or "2999-12-31",
	}
	if filters.get("vehicle"):
		conditions.append("t.vehicle = %(vehicle)s")
		params["vehicle"] = filters.get("vehicle")

	rows = frappe.db.sql(
		"""
		select t.name as trip, t.vehicle as vehicle, t.driver as driver,
			t.end_time as end_time, t.status as status,
			t.yield_flag as yield_flag, t.trip_yield as trip_yield
		from `tabTrip` t
		where {conditions}
			and (t.yield_flag != 'Normal' or exists(
				select 1 from `tabFuel Log` f
				where f.trip = t.name and f.sanity_flag = 'Suspicious'
			))
		order by t.end_time desc
		""".format(conditions=" and ".join(conditions)),
		params,
		as_dict=True,
	)

	for row in rows:
		row.suspicious_fuel_logs = frappe.db.count(
			"Fuel Log", {"trip": row.trip, "sanity_flag": "Suspicious"}
		)

	return rows
