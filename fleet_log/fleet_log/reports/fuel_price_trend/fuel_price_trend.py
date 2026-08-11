import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"fieldname": "fuel_log",
			"label": _("Fuel Log"),
			"fieldtype": "Link",
			"options": "Fuel Log",
			"width": 140,
		},
		{
			"fieldname": "vehicle",
			"label": _("Vehicle"),
			"fieldtype": "Link",
			"options": "Vehicle",
			"width": 150,
		},
		{"fieldname": "creation", "label": _("Date"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "fuel_type", "label": _("Fuel Type"), "fieldtype": "Data", "width": 100},
		{"fieldname": "fuel_vendor", "label": _("Vendor / Station"), "fieldtype": "Data", "width": 150},
		{
			"fieldname": "fuel_quantity",
			"label": _("Quantity (litres)"),
			"fieldtype": "Float",
			"width": 120,
		},
		{"fieldname": "fuel_cost", "label": _("Fuel Cost"), "fieldtype": "Currency", "width": 120},
		{
			"fieldname": "price_per_litre",
			"label": _("Price per Litre"),
			"fieldtype": "Currency",
			"width": 130,
		},
	]


def get_data(filters):
	conditions = ["1 = 1"]
	params = {}
	if filters.get("vehicle"):
		conditions.append("f.vehicle = %(vehicle)s")
		params["vehicle"] = filters.get("vehicle")
	if filters.get("fuel_type"):
		conditions.append("f.fuel_type = %(fuel_type)s")
		params["fuel_type"] = filters.get("fuel_type")
	if filters.get("from_date"):
		conditions.append("f.creation >= %(from_date)s")
		params["from_date"] = filters.get("from_date")
	if filters.get("to_date"):
		conditions.append("f.creation <= %(to_date)s")
		params["to_date"] = filters.get("to_date")

	rows = frappe.db.sql(
		"""
		select f.name as fuel_log, f.vehicle as vehicle, f.creation as creation,
			f.fuel_type as fuel_type, f.fuel_vendor as fuel_vendor,
			f.fuel_quantity as fuel_quantity, f.fuel_cost as fuel_cost,
			f.price_per_litre as price_per_litre
		from `tabFuel Log` f
		where {conditions}
		order by f.creation desc
		""".format(conditions=" and ".join(conditions)),
		params,
		as_dict=True,
	)

	# backfill price per litre for records created before the field existed
	for row in rows:
		row.price_per_litre = flt(row.price_per_litre) or (
			flt(row.fuel_cost) / flt(row.fuel_quantity) if flt(row.fuel_quantity) else 0
		)

	return rows
