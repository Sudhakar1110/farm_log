import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from fleet_log.utils import (
	calculate_trip_yield,
	evaluate_yield_flag,
	get_driver_for_user,
	get_total_fuel_used,
	get_vehicle_average_yield,
	get_vehicle_current_odometer,
	is_fleet_manager,
	update_vehicle_average_yield,
	update_vehicle_odometer,
)

COMPLETED_STATES = ("Completed", "Reconciled")


class Trip(Document):
	def validate(self):
		self.set_driver_if_empty()
		self.validate_odometer_readings()
		self.validate_transitions()
		self.warn_on_odometer_mismatch()
		self.compute_metrics()

	def set_driver_if_empty(self):
		"""Driver-role users are scoped to their own records, so a driver
		creating a trip gets the driver field set to their own record."""
		if self.driver:
			return
		if "Driver" in frappe.get_roles() and not is_fleet_manager():
			self.driver = get_driver_for_user() or self.driver
			if not self.driver:
				frappe.msgprint(
					_(
						"You have the Driver role but no Driver record is linked to your User. "
						"Ask the Fleet Manager to link your User to a Driver record so you can "
						"create Trips."
					),
					indicator="orange",
					alert=True,
				)

	def on_update(self):
		self.apply_state_side_effects()

	def on_submit(self):
		"""Reconciled (submitted) trips roll their yield into the vehicle's
		rolling average. This fires from the workflow's Completed → Reconciled
		transition (docstatus 0 → 1)."""
		self.update_vehicle_average()

	def on_trash(self):
		if self.status == "Reconciled" and not is_fleet_manager():
			frappe.throw(_("Reconciled Trips cannot be deleted."))

	# ------------------------------------------------------------------ #
	# helpers
	# ------------------------------------------------------------------ #
	def get_previous_status(self):
		previous = self.get_doc_before_save()
		return (previous.get("status") if previous else None) or "Assigned"

	def get_status(self):
		return self.status or "Assigned"

	# ------------------------------------------------------------------ #
	# validation
	# ------------------------------------------------------------------ #
	def validate_odometer_readings(self):
		if self.end_odometer is not None and flt(self.end_odometer) <= flt(self.start_odometer):
			frappe.throw(
				_("End Odometer ({0}) must be greater than Start Odometer ({1}).").format(
					flt(self.end_odometer), flt(self.start_odometer)
				)
			)

	def validate_transitions(self):
		"""Server-side enforcement of the workflow transition rules (the
		workflow conditions are mirrored here so direct saves are also safe)."""
		previous_status = self.get_previous_status()
		status = self.get_status()
		if previous_status == status:
			return

		if status == "In Progress":
			if not self.start_odometer or not self.start_time:
				frappe.throw(
					_("Trip cannot be started until Start Odometer and Start Time are set.")
				)
		elif status == "Completed":
			if not self.end_odometer or not self.end_time:
				frappe.throw(
					_("Trip cannot be completed until End Odometer and End Time are set.")
				)
			if flt(self.end_odometer) <= flt(self.start_odometer):
				frappe.throw(
					_("End Odometer must be greater than Start Odometer to complete the trip.")
				)
		elif status == "Reconciled" and not is_fleet_manager():
			frappe.throw(_("Only the Fleet Manager can reconcile a Trip."))

	def warn_on_odometer_mismatch(self):
		"""Warn (never block) if a new trip's start odometer does not match the
		vehicle's last known odometer - implies off-system usage."""
		if not self.is_new() or self.get_status() != "Assigned":
			return
		if not self.vehicle or not self.start_odometer:
			return
		known = get_vehicle_current_odometer(self.vehicle)
		if known and abs(flt(self.start_odometer) - flt(known)) > 0.01:
			frappe.msgprint(
				_(
					"Start Odometer ({0}) does not match the vehicle's last known odometer ({1}). "
					"The vehicle may have been driven off-system."
				).format(flt(self.start_odometer), known),
				indicator="orange",
				alert=True,
			)

	def compute_metrics(self):
		"""Calculate distance, total fuel, yield and flag on trip close.

		Only recomputes when the status is actually *transitioning* into a
		completed state (or when odometer inputs changed on an already
		completed trip), so routine saves on live trips never touch the
		linked Fuel Logs.
		"""
		status = self.get_status()
		if status not in COMPLETED_STATES:
			return
		previous_status = self.get_previous_status()
		previous = self.get_doc_before_save()
		odometer_changed = bool(
			previous
			and (
				flt(previous.get("end_odometer") or 0) != flt(self.end_odometer or 0)
				or flt(previous.get("start_odometer") or 0) != flt(self.start_odometer or 0)
			)
		)
		if previous_status in COMPLETED_STATES and not odometer_changed:
			return
		if self.end_odometer is not None and self.start_odometer is not None:
			self.distance_covered = flt(self.end_odometer) - flt(self.start_odometer)
		self.total_fuel_used = get_total_fuel_used(self.name)
		self.trip_yield = calculate_trip_yield(self.distance_covered, self.total_fuel_used)
		self.yield_flag = evaluate_yield_flag(
			self.trip_yield, get_vehicle_average_yield(self.vehicle)
		)

	# ------------------------------------------------------------------ #
	# side effects
	# ------------------------------------------------------------------ #
	def apply_state_side_effects(self):
		status = self.get_status()
		previous_status = self.get_previous_status()

		if status == "Completed" and previous_status != "Completed":
			# update the vehicle's odometer on completion
			update_vehicle_odometer(self.vehicle, self.end_odometer)

	def update_vehicle_average(self):
		# roll the reconciled yield into the vehicle's rolling average
		update_vehicle_average_yield(self.vehicle)
