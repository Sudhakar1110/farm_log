import frappe
from frappe import _
from frappe.utils import flt


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
			"width": 150,
		},
		{"fieldname": "driver_name", "label": _("Driver Name"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "fuel_cost",
			"label": _("Total Fuel Cost"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "fuel_litres",
			"label": _("Total Fuel (litres)"),
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"fieldname": "avg_price_per_litre",
			"label": _("Avg Price / Litre"),
			"fieldtype": "Currency",
			"width": 130,
		},
		{"fieldname": "fuel_log_count", "label": _("Fuel Logs"), "fieldtype": "Int", "width": 90},
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
	conditions = ["f.filled_by is not null"]
	params = {}
	if filters.get("from_date"):
		conditions.append("f.creation >= %(from_date)s")
		params["from_date"] = filters.get("from_date")
	if filters.get("to_date"):
		conditions.append("f.creation <= %(to_date)s")
		params["to_date"] = filters.get("to_date")
	if filters.get("driver"):
		conditions.append("f.filled_by = %(driver)s")
		params["driver"] = filters.get("driver")

	name_expr = get_driver_name_expression()
	rows = frappe.db.sql(
		"""
		select f.filled_by as driver,
			{name_expr} as driver_name,
			sum(f.fuel_cost) as fuel_cost,
			sum(f.fuel_quantity) as fuel_litres,
			count(f.name) as fuel_log_count
		from `tabFuel Log` f
		left join `tabDriver` d on d.name = f.filled_by
		where {conditions}
		group by f.filled_by, {name_expr}
		order by fuel_cost desc
		""".format(conditions=" and ".join(conditions), name_expr=name_expr),
		params,
		as_dict=True,
	)

	for row in rows:
		row.avg_price_per_litre = (
			round(flt(row.fuel_cost) / flt(row.fuel_litres), 4) if flt(row.fuel_litres) else 0
		)

	return rows
