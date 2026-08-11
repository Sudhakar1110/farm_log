import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime

from fleet_log.utils import (
	calculate_trip_yield,
	evaluate_yield_flag,
	get_driver_for_user,
	get_effective_average_yield,
	get_fleet_manager_users,
	get_total_fuel_used,
	get_vehicle_current_odometer,
	is_fleet_manager,
	notify_user,
	update_vehicle_average_yield,
	update_vehicle_odometer,
)

COMPLETED_STATES = ("Completed", "Reconciled")

# ------------------------------------------------------------------ #
# Workflow state machine (mirrors the Trip Workflow fixture).
# ------------------------------------------------------------------ #
VALID_TRANSITIONS = {
	"Assigned": {"In Progress", "Cancelled"},
	"In Progress": {"Completed", "Cancelled"},
	"Completed": {"Reconciled", "Cancelled"},
	"Reconciled": set(),
	"Cancelled": set(),
}

# Transitions a plain Driver-role user may perform directly.
# Fleet Managers / System Managers / Administrator may perform any valid one.
DRIVER_TRANSITIONS = {("Assigned", "In Progress"), ("In Progress", "Completed")}


class Trip(Document):
	def validate(self):
		self.set_driver_if_empty()
		self.validate_odometer_readings()
		self.validate_time_readings()
		self.validate_transitions()
		self.warn_on_odometer_mismatch()
		self.warn_on_assigned_vehicle_mismatch()
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
		self.notify_assignment_and_status()

	def on_submit(self):
		"""Reconciled (submitted) trips roll their yield into the vehicle's
		rolling average. This fires from the workflow's Completed → Reconciled
		transition (docstatus 0 → 1)."""
		self.update_vehicle_average()

	def on_cancel(self):
		"""Re-roll the vehicle's rolling average without this cancelled trip."""
		update_vehicle_average_yield(self.vehicle)

	def on_trash(self):
		if self.status == "Reconciled" and not is_fleet_manager():
			frappe.throw(_("Reconciled Trips cannot be deleted."))
		self.cleanup_linked_records()
		if self.status == "Reconciled":
			# keep the rolling average consistent if a reconciled trip is removed.
			# on_trash runs before the row is deleted, so exclude this trip.
			update_vehicle_average_yield(self.vehicle, exclude_trip=self.name)

	# ------------------------------------------------------------------ #
	# helpers
	# ------------------------------------------------------------------ #
	def get_previous_status(self):
		previous = self.get_doc_before_save()
		return (previous.get("status") if previous else None) or "Assigned"

	def get_status(self):
		return self.status or "Assigned"

	def get_driver_user(self):
		if not self.driver:
			return None
		return frappe.db.get_value("Driver", self.driver, "user")

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

	def validate_time_readings(self):
		if self.start_time and self.end_time and get_datetime(self.end_time) < get_datetime(self.start_time):
			frappe.throw(
				_("End Time ({0}) must be later than Start Time ({1}).").format(
					self.end_time, self.start_time
				)
			)

	def validate_transitions(self):
		"""Server-side enforcement of the workflow state machine (mirrors the
		workflow fixture so direct saves, scripts and the API are equally safe).

		Enforces both the *source* state (no skipping / going backwards) and the
		role allowed to perform the transition.
		"""
		previous_status = self.get_previous_status()
		status = self.get_status()
		if previous_status == status:
			return

		if status not in VALID_TRANSITIONS.get(previous_status, set()):
			frappe.throw(
				_("Invalid status transition from {0} to {1}.").format(previous_status, status)
			)

		roles = frappe.get_roles()
		is_manager = is_fleet_manager() or "System Manager" in roles
		if not is_manager and "Driver" not in roles:
			frappe.throw(
				_("You are not allowed to change the Trip status from {0} to {1}.").format(
					previous_status, status
				)
			)
		if not is_manager and (previous_status, status) not in DRIVER_TRANSITIONS:
			frappe.throw(
				_("You are not allowed to change the Trip status from {0} to {1}.").format(
					previous_status, status
				)
			)

		# per-target-state field requirements
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

	def warn_on_assigned_vehicle_mismatch(self):
		"""Warn (never block) when the trip's vehicle differs from the driver's
		assigned vehicle."""
		if not self.driver or not self.vehicle:
			return
		meta = frappe.get_meta("Driver")
		if not meta.has_field("assigned_vehicle"):
			return
		assigned = frappe.db.get_value("Driver", self.driver, "assigned_vehicle")
		if assigned and assigned != self.vehicle:
			frappe.msgprint(
				_(
					"Driver {0} is assigned to Vehicle {1}, but this trip is for Vehicle {2}."
				).format(self.driver, assigned, self.vehicle),
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
			self.trip_yield, get_effective_average_yield(self.vehicle)
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

	def notify_assignment_and_status(self):
		"""Notify the driver when a trip is assigned to them, and notify the
		driver + fleet managers when a trip's status changes."""
		previous = self.get_doc_before_save()
		status = self.get_status()
		previous_status = (previous.get("status") if previous else None) or "Assigned"
		driver_user = self.get_driver_user()

		if previous is None:
			# brand-new trip: notify the driver they have been assigned a trip
			if self.driver and driver_user and driver_user != frappe.session.user:
				notify_user(
					driver_user,
					_("New Trip {0} has been assigned to you ({1}).").format(
						self.name, self.vehicle
					),
					"Trip",
					self.name,
				)
			return

		if previous_status == status:
			return

		subject = _("Trip {0} status changed from {1} to {2}").format(
			self.name, previous_status, status
		)
		if driver_user and driver_user != frappe.session.user:
			notify_user(driver_user, subject, "Trip", self.name)
		if status in ("Completed", "Reconciled", "Cancelled"):
			for user in get_fleet_manager_users():
				if user != frappe.session.user:
					notify_user(user, subject, "Trip", self.name)

	def cleanup_linked_records(self):
		"""When a Trip is deleted: unlink its Fuel Logs (they become standalone
		logs) and remove its Trip Expenses (which cannot exist without a trip).

		Expenses that were already pushed to ERPNext block the deletion so no
		accounting trail is silently destroyed.
		"""
		fuel_logs = frappe.db.get_all("Fuel Log", filters={"trip": self.name}, pluck="name")
		for name in fuel_logs:
			frappe.db.set_value("Fuel Log", name, "trip", None)

		expenses = frappe.db.get_all(
			"Trip Expense",
			filters={"trip": self.name},
			fields=["name", "erpnext_expense_claim", "erpnext_journal_entry"],
		)
		pushed = [e.name for e in expenses if e.erpnext_expense_claim or e.erpnext_journal_entry]
		if pushed:
			frappe.throw(
				_(
					"Trip {0} has Trip Expense(s) {1} already pushed to ERPNext. "
					"Delete those expenses first."
				).format(self.name, ", ".join(pushed))
			)
		for expense in expenses:
			frappe.delete_doc("Trip Expense", expense.name, force=True, ignore_permissions=True)

		if fuel_logs or expenses:
			frappe.msgprint(
				_(
					"Deleted Trip {0}: unlinked {1} Fuel Log(s) and deleted {2} Trip Expense(s)."
				).format(self.name, len(fuel_logs), len(expenses)),
				indicator="green",
				alert=True,
			)
