import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"fieldname": "vehicle",
			"label": _("Vehicle"),
			"fieldtype": "Link",
			"options": "Vehicle",
			"width": 160,
		},
		{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 90},
		{
			"fieldname": "fuel_cost",
			"label": _("Fuel Cost"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "other_cost",
			"label": _("Other Cost"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "total_cost",
			"label": _("Total Cost"),
			"fieldtype": "Currency",
			"width": 120,
		},
	]


def get_data(filters):
	"""Total fuel + expense cost per vehicle per month (grouping done in
	Python so the report runs identically on MariaDB and PostgreSQL)."""
	rows = frappe.db.sql(
		"""
		select f.vehicle as vehicle, f.creation as creation,
			f.fuel_cost as fuel_cost, 0 as other_cost
		from `tabFuel Log` f
		union all
		select t.vehicle as vehicle, e.creation as creation,
			0 as fuel_cost, e.amount as other_cost
		from `tabTrip Expense` e
		inner join `tabTrip` t on t.name = e.trip
		""",
		as_dict=True,
	)

	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else None
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else None
	vehicle = filters.get("vehicle")

	monthly = {}
	for row in rows:
		date = getdate(row.creation)
		if from_date and date < from_date:
			continue
		if to_date and date > to_date:
			continue
		if vehicle and row.vehicle != vehicle:
			continue

		key = (row.vehicle, date.strftime("%Y-%m"))
		entry = monthly.setdefault(
			key,
			{
				"vehicle": row.vehicle,
				"month": date.strftime("%Y-%m"),
				"fuel_cost": 0,
				"other_cost": 0,
				"total_cost": 0,
			},
		)
		entry["fuel_cost"] += flt(row.fuel_cost)
		entry["other_cost"] += flt(row.other_cost)
		entry["total_cost"] = entry["fuel_cost"] + entry["other_cost"]

	return [monthly[key] for key in sorted(monthly, key=lambda k: (k[1], k[0]), reverse=True)]
