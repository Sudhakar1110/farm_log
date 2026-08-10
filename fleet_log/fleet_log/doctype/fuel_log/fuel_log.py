import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from fleet_log.utils import (
	get_driver_for_user,
	get_vehicle_average_yield,
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
		if self.trip and frappe.db.get_value("Trip", self.trip, "status") == "Reconciled":
			frappe.throw(_("Fuel Logs cannot be added to a Reconciled Trip."))

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

	def compute_fill_up_yield_and_flag(self):
		"""Light sanity check against the vehicle's last known odometer and
		average yield. Flags readings more than 2x / less than 0.3x the average
		as Suspicious (a warning, not a hard block)."""
		last_known = get_vehicle_current_odometer(self.vehicle)
		average_yield = get_vehicle_average_yield(self.vehicle)

		if last_known and flt(self.fuel_quantity) > 0:
			self.fill_up_yield = flt(
				(flt(self.odometer_at_fill) - flt(last_known)) / flt(self.fuel_quantity), 2
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
