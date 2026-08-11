import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from fleet_log.utils import (
	get_driver_for_user,
	get_effective_average_yield,
	get_vehicle_current_odometer,
	is_fleet_manager,
)

# sanity thresholds as fractions of the vehicle's average yield
SUSPICIOUS_UPPER = 2.0
SUSPICIOUS_LOWER = 0.3


class FuelLog(Document):
	def validate(self):
		self.set_filled_by_if_empty()
		self.validate_fuel_quantity()
		self.validate_linked_trip()
		self.validate_odometer()
		self.compute_fill_up_yield_and_flag()
		self.compute_price_per_litre()

	def set_filled_by_if_empty(self):
		"""Driver-role users are scoped to their own records, so a driver
		logging fuel gets `filled_by` set to their own Driver record."""
		if self.filled_by:
			return
		if "Driver" in frappe.get_roles() and not is_fleet_manager():
			self.filled_by = get_driver_for_user() or self.filled_by

	# ------------------------------------------------------------------ #
	def validate_fuel_quantity(self):
		if flt(self.fuel_quantity) <= 0:
			frappe.throw(_("Fuel Quantity must be greater than zero."))

	def validate_linked_trip(self):
		if not self.trip:
			return
		trip_status, trip_vehicle, trip_start_odometer, trip_end_odometer = frappe.db.get_value(
			"Trip", self.trip, ["status", "vehicle", "start_odometer", "end_odometer"]
		) or (None, None, None, None)
		if trip_status == "Reconciled":
			frappe.throw(_("Fuel Logs cannot be added to a Reconciled Trip."))
		if self.vehicle and trip_vehicle and self.vehicle != trip_vehicle:
			frappe.throw(
				_(
					"Vehicle mismatch: Fuel Log is for {0} but Trip {1} is for {2}. "
					"Set the correct vehicle or remove the Trip link."
				).format(self.vehicle, self.trip, trip_vehicle)
			)
		# A fill-up must fall inside the linked trip's odometer window
		if self.odometer_at_fill is not None:
			if trip_start_odometer is not None and flt(self.odometer_at_fill) < flt(trip_start_odometer):
				frappe.throw(
					_(
						"Odometer at fill ({0}) is before the trip's start odometer ({1}). "
						"Correct the reading or remove the Trip link."
					).format(flt(self.odometer_at_fill), flt(trip_start_odometer))
				)
			if trip_end_odometer is not None and flt(self.odometer_at_fill) > flt(trip_end_odometer):
				frappe.throw(
					_(
						"Odometer at fill ({0}) is after the trip's end odometer ({1}). "
						"Either remove the Trip link (this fill-up happened after the trip) "
						"or correct the reading."
					).format(flt(self.odometer_at_fill), flt(trip_end_odometer))
				)

	def validate_odometer(self):
		"""Reject odometer readings that would imply the vehicle went backwards."""
		if not self.vehicle or not self.odometer_at_fill:
			return
		last_known = get_vehicle_current_odometer(self.vehicle)
		if last_known and flt(self.odometer_at_fill) < flt(last_known):
			frappe.throw(
				_(
					"Odometer at fill ({0}) is less than the vehicle's last known odometer ({1}). "
					"The vehicle cannot go backwards."
				).format(flt(self.odometer_at_fill), flt(last_known))
			)

	def get_last_fill_odometer(self):
		"""Odometer of the most recent earlier fill-up for the same vehicle.

		Used as the baseline for fill-up yield so a mid-trip fill-up is
		measured against the previous fill (distance since last fill), not
		against the odometer at the last completed trip.
		"""
		if not self.vehicle or not self.odometer_at_fill:
			return None
		filters = {
			"vehicle": self.vehicle,
			"odometer_at_fill": ["<", flt(self.odometer_at_fill)],
		}
		if self.get("name"):
			filters["name"] = ["!=", self.name]
		return frappe.db.get_value(
			"Fuel Log", filters, "odometer_at_fill", order_by="odometer_at_fill desc"
		)

	def compute_fill_up_yield_and_flag(self):
		"""Sanity check the fill-up against the distance since the last fill and
		the vehicle's average yield. Flags readings more than 2x / less than
		0.3x the average as Suspicious (a warning, not a hard block)."""
		baseline = self.get_last_fill_odometer() or get_vehicle_current_odometer(self.vehicle)
		average_yield = get_effective_average_yield(self.vehicle)

		if (
			baseline
			and flt(self.odometer_at_fill) > flt(baseline)
			and flt(self.fuel_quantity) > 0
		):
			self.fill_up_yield = flt(
				(flt(self.odometer_at_fill) - flt(baseline)) / flt(self.fuel_quantity), 2
			)
		else:
			self.fill_up_yield = 0

		self.sanity_flag = "OK"
		if (
			average_yield > 0
			and self.fill_up_yield > 0
			and (
				self.fill_up_yield > SUSPICIOUS_UPPER * average_yield
				or self.fill_up_yield < SUSPICIOUS_LOWER * average_yield
			)
		):
			self.sanity_flag = "Suspicious"

	def compute_price_per_litre(self):
		"""Derived fuel price per litre (fuel_cost / fuel_quantity)."""
		if flt(self.fuel_quantity) > 0:
			self.price_per_litre = flt(flt(self.fuel_cost) / flt(self.fuel_quantity), 4)
		else:
			self.price_per_litre = 0
