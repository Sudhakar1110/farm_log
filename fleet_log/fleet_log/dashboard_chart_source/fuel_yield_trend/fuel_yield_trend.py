import frappe
from frappe.utils import flt, getdate, now_datetime
from frappe.utils.dateutils import (
	get_dates_from_timegrain,
	get_from_date_from_timespan,
	get_period,
)


def get_chart_data(chart_name=None, timespan="Last Year", time_interval="Monthly", filters=None):
	"""Line chart: average trip_yield over time, as a separate dataset per vehicle.

	The chart is rendered on the Fleet Log workspace and on the public dashboard.
	"""
	from_date = get_from_date_from_timespan(now_datetime(), timespan)
	to_date = now_datetime()

	trips = frappe.db.sql(
		"""
		SELECT vehicle, end_time, trip_yield
		FROM `tabTrip`
		WHERE status IN ('Completed', 'Reconciled')
		  AND trip_yield > 0
		  AND end_time BETWEEN %(from_date)s AND %(to_date)s
		ORDER BY end_time ASC
		""",
		{"from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	# Build time buckets (each bucket is a date object representing the bucket end)
	dates = get_dates_from_timegrain(from_date, to_date, time_interval)
	labels = [get_period(d, time_interval) for d in dates]

	# Per-vehicle: map from bucket date -> list of trip_yield values
	vehicle_data = {}
	for t in trips:
		vehicle = t.vehicle
		if vehicle not in vehicle_data:
			vehicle_data[vehicle] = {d: [] for d in dates}
		for d in dates:
			if getdate(t.end_time) <= d:
				vehicle_data[vehicle][d].append(flt(t.trip_yield))
				break

	# Batch-resolve vehicle display labels (works for both ERPNext and fallback Vehicle)
	vehicle_labels = {}
	if vehicle_data:
		vehicle_labels = dict(
			frappe.db.get_all(
				"Vehicle",
				filters={"name": ("in", list(vehicle_data.keys()))},
				fields=["name", "license_plate"],
				as_list=True,
			)
		)

	datasets = []
	for vehicle, buckets in vehicle_data.items():
		vehicle_name = vehicle_labels.get(vehicle) or vehicle
		values = []
		for d in dates:
			yields = buckets[d]
			values.append(round(sum(yields) / len(yields), 2) if yields else 0)
		datasets.append({"name": vehicle_name, "values": values})

	return {"labels": labels, "datasets": datasets}