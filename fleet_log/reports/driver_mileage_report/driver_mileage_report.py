import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"fieldname": "driver",
			"label": _("Driver"),
			"fieldtype": "Link",
			"options": "Driver",
			"width": 160,
		},
		{"fieldname": "driver_name", "label": _("Driver Name"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "total_distance",
			"label": _("Total Distance (km)"),
			"fieldtype": "Float",
			"width": 140,
		},
		{"fieldname": "trip_count", "label": _("Trips"), "fieldtype": "Int", "width": 80},
		{
			"fieldname": "avg_distance",
			"label": _("Avg Distance / Trip (km)"),
			"fieldtype": "Float",
			"width": 160,
		},
	]


def get_driver_name_expression():
	"""Column holding the driver's display name differs between modes:
	fallback Driver has `driver_name`, ERPNext Driver has `full_name`."""
	meta = frappe.get_meta("Driver")
	if meta.has_field("driver_name"):
		return "d.driver_name"
	if meta.has_field("full_name"):
		return "d.full_name"
	return "d.name"


def get_data(filters):
	conditions = ["t.status in ('Completed', 'Reconciled')"]
	params = {}
	if filters.get("from_date"):
		conditions.append("t.end_time >= %(from_date)s")
		params["from_date"] = filters.get("from_date")
	if filters.get("to_date"):
		conditions.append("t.end_time <= %(to_date)s")
		params["to_date"] = filters.get("to_date")
	if filters.get("driver"):
		conditions.append("t.driver = %(driver)s")
		params["driver"] = filters.get("driver")

	name_expr = get_driver_name_expression()
	rows = frappe.db.sql(
		"""
		select t.driver as driver,
			{name_expr} as driver_name,
			sum(t.distance_covered) as total_distance,
			count(t.name) as trip_count
		from `tabTrip` t
		left join `tabDriver` d on d.name = t.driver
		where {conditions}
		group by t.driver, {name_expr}
		order by total_distance desc
		""".format(conditions=" and ".join(conditions), name_expr=name_expr),
		params,
		as_dict=True,
	)

	for row in rows:
		row.avg_distance = round(row.total_distance / row.trip_count, 2) if row.trip_count else 0

	return rows
