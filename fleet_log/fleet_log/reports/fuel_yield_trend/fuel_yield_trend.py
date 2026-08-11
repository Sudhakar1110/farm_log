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
		{"fieldname": "end_time", "label": _("End Time"), "fieldtype": "Datetime", "width": 150},
		{
			"fieldname": "distance_covered",
			"label": _("Distance (km)"),
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"fieldname": "total_fuel_used",
			"label": _("Fuel Used (litres)"),
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"fieldname": "trip_yield",
			"label": _("Trip Yield (km/litre)"),
			"fieldtype": "Float",
			"width": 130,
		},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
	]


def get_data(filters):
	conditions = ["t.status in ('Completed', 'Reconciled')", "t.trip_yield > 0"]
	params = {}
	if filters.get("vehicle"):
		conditions.append("t.vehicle = %(vehicle)s")
		params["vehicle"] = filters.get("vehicle")
	if filters.get("from_date"):
		conditions.append("t.end_time >= %(from_date)s")
		params["from_date"] = filters.get("from_date")
	if filters.get("to_date"):
		conditions.append("t.end_time <= %(to_date)s")
		params["to_date"] = filters.get("to_date")

	return frappe.db.sql(
		"""
		select t.name as trip, t.vehicle as vehicle, t.end_time as end_time,
			t.distance_covered as distance_covered,
			t.total_fuel_used as total_fuel_used,
			t.trip_yield as trip_yield, t.status as status
		from `tabTrip` t
		where {conditions}
		order by t.vehicle asc, t.end_time asc
		""".format(conditions=" and ".join(conditions)),
		params,
		as_dict=True,
	)
