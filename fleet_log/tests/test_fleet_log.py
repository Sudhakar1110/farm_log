import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from fleet_log.utils import is_erpnext_installed


class TestFleetLog(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.vehicle = self.make_vehicle()
		self.driver = self.make_driver()

	# ------------------------------------------------------------------ #
	# helpers
	# ------------------------------------------------------------------ #
	def make_vehicle(self):
		plate = f"TST-{frappe.generate_hash(6)}"
		if is_erpnext_installed():
			return frappe.get_doc(
				{
					"doctype": "Vehicle",
					"license_plate": plate,
					"make": "Test Make",
					"model": "Test Model",
					"last_odometer": 1000,
					"fuel_type": "Petrol",
					"uom": "Litre",
				}
			).insert(ignore_permissions=True)
		return frappe.get_doc(
			{
				"doctype": "Vehicle",
				"registration_number": plate,
				"current_odometer": 1000,
			}
		).insert(ignore_permissions=True)

	def make_driver(self):
		name = f"Test Driver {frappe.generate_hash(4)}"
		if is_erpnext_installed():
			return frappe.get_doc(
				{"doctype": "Driver", "full_name": name, "status": "Active"}
			).insert(ignore_permissions=True)
		return frappe.get_doc({"doctype": "Driver", "driver_name": name}).insert(
			ignore_permissions=True
		)

	def make_trip(self, start_odometer=1000):
		return frappe.get_doc(
			{
				"doctype": "Trip",
				"vehicle": self.vehicle.name,
				"driver": self.driver.name,
				"start_odometer": start_odometer,
				"start_time": now_datetime(),
				"status": "Assigned",
			}
		).insert(ignore_permissions=True)

	def make_driver_user(self):
		"""Create a system User holding the Driver role, linked to self.driver."""
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"driver-{frappe.generate_hash(4)}@example.com",
				"first_name": "Driver",
				"send_welcome_email": 0,
				"roles": [{"role": "Driver"}],
			}
		).insert(ignore_permissions=True)
		if frappe.get_meta("Driver").has_field("user"):
			frappe.db.set_value("Driver", self.driver.name, "user", user.name)
		return user

	def reconcile_trip(self, trip):
		"""Run a completed trip through Reconciled and submit it."""
		trip.status = "Reconciled"
		trip.save(ignore_permissions=True)
		trip.submit()
		return trip

	def complete_trip(self, trip, end_odometer=1300):
		"""Utility: run a trip through Assigned → In Progress → Completed."""
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)
		trip.end_odometer = end_odometer
		trip.end_time = now_datetime()
		trip.save(ignore_permissions=True)
		trip.status = "Completed"
		trip.save(ignore_permissions=True)
		return trip

	# ------------------------------------------------------------------ #
	# tests
	# ------------------------------------------------------------------ #
	def test_trip_lifecycle_and_metrics(self):
		trip = self.make_trip()
		self.assertEqual(trip.status, "Assigned")

		# start the trip
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)
		self.assertEqual(trip.status, "In Progress")

		# save end readings first, then complete
		trip.end_odometer = 1300
		trip.end_time = now_datetime()
		trip.save(ignore_permissions=True)
		trip.status = "Completed"
		trip.save(ignore_permissions=True)

		self.assertEqual(trip.distance_covered, 300)
		self.assertEqual(trip.total_fuel_used, 0)
		self.assertEqual(trip.trip_yield, 0)  # no fuel logs -> guarded
		self.assertEqual(trip.yield_flag, "Normal")
		self.assertEqual(frappe.db.get_value("Vehicle", self.vehicle.name, "current_odometer"), 1300)

	def test_rejects_backwards_odometer(self):
		trip = self.make_trip()
		trip.end_odometer = 900  # less than start
		with self.assertRaises(frappe.ValidationError):
			trip.save(ignore_permissions=True)

	def test_requires_end_details_to_complete(self):
		trip = self.make_trip()
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)
		trip.status = "Completed"  # end_odometer / end_time missing
		with self.assertRaises(frappe.ValidationError):
			trip.save(ignore_permissions=True)

	def test_fuel_log_rejects_backwards_odometer(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Fuel Log",
					"vehicle": self.vehicle.name,
					"fuel_quantity": 10,
					"fuel_cost": 100,
					"odometer_at_fill": 900,  # less than the vehicle's 1000
				}
			).insert(ignore_permissions=True)

	def test_fuel_log_sanity_flag(self):
		frappe.db.set_value("Vehicle", self.vehicle.name, "average_yield", 10)
		log = frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1010,
			}
		).insert(ignore_permissions=True)
		# (1010 - 1000) / 10 = 1 km/l -> 0.1x of the 10 km/l average -> Suspicious
		self.assertEqual(log.fill_up_yield, 1)
		self.assertEqual(log.sanity_flag, "Suspicious")

	def test_yield_flag_critical(self):
		frappe.db.set_value("Vehicle", self.vehicle.name, "average_yield", 10)
		trip = self.make_trip()
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)

		# 20 litres for 100 km -> yield 5, i.e. 50% of the 10 km/l average
		frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": trip.name,
				"fuel_quantity": 20,
				"fuel_cost": 100,
				"odometer_at_fill": 1100,
			}
		).insert(ignore_permissions=True)

		trip.end_odometer = 1100
		trip.end_time = now_datetime()
		trip.save(ignore_permissions=True)
		trip.status = "Completed"
		trip.save(ignore_permissions=True)

		self.assertEqual(trip.total_fuel_used, 20)
		self.assertEqual(trip.trip_yield, 5)
		self.assertEqual(trip.yield_flag, "Critical")

	def test_reconciled_trip_is_locked_for_drivers(self):
		"""Reconciled trips use docstatus=1 (submitted) so Frappe prevents edits
		at the framework level. A Driver-role user cannot save."""
		trip = self.complete_trip(self.make_trip(), end_odometer=1200)
		trip.status = "Reconciled"
		trip.save(ignore_permissions=True)
		self.assertEqual(trip.status, "Reconciled")
		self.assertEqual(trip.docstatus, 0)

		# Now submit the trip (workflow action would do this, test it directly)
		trip.submit()
		self.assertEqual(trip.docstatus, 1)

		driver_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"driver-{frappe.generate_hash(4)}@example.com",
				"first_name": "Test Driver",
				"send_welcome_email": 0,
				"roles": [{"role": "Driver"}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(driver_user.name)
		doc = frappe.get_doc("Trip", trip.name)
		doc.purpose = "attempted edit"
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)

		frappe.set_user("Administrator")
		# Fleet Manager can cancel and amend
		trip = frappe.get_doc("Trip", trip.name)
		trip.cancel()
		self.assertEqual(trip.docstatus, 2)

	def test_fuel_log_vehicle_mismatch_rejected(self):
		"""A Fuel Log must have the same vehicle as its linked Trip."""
		trip = self.make_trip()
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)

		# Create a second vehicle
		plate2 = f"TST-{frappe.generate_hash(6)}"
		if is_erpnext_installed():
			v2 = frappe.get_doc(
				{
					"doctype": "Vehicle",
					"license_plate": plate2,
					"make": "Other",
					"model": "Other",
					"fuel_type": "Diesel",
					"uom": "Litre",
				}
			).insert(ignore_permissions=True)
		else:
			v2 = frappe.get_doc(
				{"doctype": "Vehicle", "registration_number": plate2}
			).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Fuel Log",
					"vehicle": v2.name,
					"trip": trip.name,
					"fuel_quantity": 10,
					"fuel_cost": 100,
					"odometer_at_fill": 1100,
				}
			).insert(ignore_permissions=True)

	def test_dashboard_chart_source_no_data(self):
		"""get_chart_data with no trips returns empty labels/datasets."""
		from fleet_log.fleet_log.dashboard_chart_source.fuel_yield_trend.fuel_yield_trend import (
			get_chart_data,
		)

		result = get_chart_data()
		self.assertIn("labels", result)
		self.assertIn("datasets", result)
		self.assertIsInstance(result["labels"], list)
		self.assertIsInstance(result["datasets"], list)

	def test_dashboard_chart_source_with_data(self):
		"""get_chart_data returns per-vehicle datasets after trips exist."""
		trip = self.complete_trip(self.make_trip(), end_odometer=1300)

		from fleet_log.fleet_log.dashboard_chart_source.fuel_yield_trend.fuel_yield_trend import (
			get_chart_data,
		)

		# no fuel → yield 0 → trip_yield > 0 filter excludes it
		result = get_chart_data()
		vehicle_labels = {ds["name"] for ds in result["datasets"]}
		self.assertIn(
			frappe.db.get_value("Vehicle", self.vehicle.name, "license_plate")
			or self.vehicle.name,
			vehicle_labels,
		)

		self.assertGreater(len(result["labels"]), 0)

	# ------------------------------------------------------------------ #
	# workflow enforcement (A1 / B1)
	# ------------------------------------------------------------------ #
	def test_skipping_workflow_states_rejected(self):
		"""Assigned -> Completed directly (skipping In Progress) must fail."""
		trip = self.make_trip()
		trip.end_odometer = 1300
		trip.end_time = now_datetime()
		trip.status = "Completed"
		with self.assertRaises(frappe.ValidationError):
			trip.save(ignore_permissions=True)

	def test_backward_transition_rejected(self):
		"""Completed -> Assigned (going backwards) must fail."""
		trip = self.complete_trip(self.make_trip(), end_odometer=1200)
		trip.status = "Assigned"
		with self.assertRaises(frappe.ValidationError):
			trip.save(ignore_permissions=True)

	def test_driver_cannot_skip_to_reconciled(self):
		"""A Driver-role user cannot reconcile a trip directly."""
		user = self.make_driver_user()
		trip = self.complete_trip(self.make_trip(), end_odometer=1200)
		frappe.set_user(user.name)
		try:
			doc = frappe.get_doc("Trip", trip.name)
			doc.status = "Reconciled"
			# the workflow role check rejects the save (ValidationError) or the
			# framework blocks it earlier (PermissionError) - both are fine
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
				doc.save(ignore_permissions=True)
		finally:
			frappe.set_user("Administrator")

	def test_manager_can_cancel_trip(self):
		"""Fleet Manager can cancel an Assigned/In Progress/Completed trip."""
		trip = self.complete_trip(self.make_trip(), end_odometer=1200)
		trip.status = "Cancelled"
		trip.save(ignore_permissions=True)
		self.assertEqual(trip.status, "Cancelled")

	def test_end_time_before_start_rejected(self):
		trip = self.make_trip()
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)
		trip.end_odometer = 1200
		trip.end_time = frappe.utils.add_to_date(now_datetime(), hours=-2)
		trip.status = "Completed"
		with self.assertRaises(frappe.ValidationError):
			trip.save(ignore_permissions=True)

	# ------------------------------------------------------------------ #
	# cancel / delete integrity (A2 / A3)
	# ------------------------------------------------------------------ #
	def test_cancel_recomputes_vehicle_average(self):
		"""Cancelling a reconciled trip re-rolls the vehicle's average."""
		t1 = self.make_trip()
		t1.status = "In Progress"
		t1.save(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": t1.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1050,
			}
		).insert(ignore_permissions=True)
		t1.end_odometer = 1100
		t1.end_time = now_datetime()
		t1.save(ignore_permissions=True)
		t1.status = "Completed"
		t1.save(ignore_permissions=True)
		self.reconcile_trip(t1)

		t2 = self.make_trip(start_odometer=1100)
		t2.status = "In Progress"
		t2.save(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": t2.name,
				"fuel_quantity": 20,
				"fuel_cost": 200,
				"odometer_at_fill": 1150,
			}
		).insert(ignore_permissions=True)
		t2.end_odometer = 1200
		t2.end_time = now_datetime()
		t2.save(ignore_permissions=True)
		t2.status = "Completed"
		t2.save(ignore_permissions=True)
		self.reconcile_trip(t2)

		# (10 + 5) / 2 = 7.5 before the cancel
		self.assertEqual(
			frappe.db.get_value("Vehicle", self.vehicle.name, "average_yield"), 7.5
		)

		t2 = frappe.get_doc("Trip", t2.name)
		t2.cancel()
		# only t1 (yield 10) remains
		self.assertEqual(
			frappe.db.get_value("Vehicle", self.vehicle.name, "average_yield"), 10
		)

	def test_delete_trip_unlinks_fuel_logs(self):
		trip = self.make_trip()
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)
		log = frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": trip.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1050,
			}
		).insert(ignore_permissions=True)
		trip.delete()
		self.assertIsNone(frappe.db.get_value("Fuel Log", log.name, "trip"))

	# ------------------------------------------------------------------ #
	# permissions (A4)
	# ------------------------------------------------------------------ #
	def test_permission_query_conditions_scopes_driver(self):
		user = self.make_driver_user()
		from fleet_log.utils import permission_query_conditions

		cond = permission_query_conditions(user=user.name, doctype="Trip")
		self.assertIn(self.driver.name, cond)
		self.assertIn("tabTrip", cond)
		# unrelated doctypes and managers are unaffected
		self.assertEqual(permission_query_conditions(user=user.name, doctype="ToDo"), "")
		self.assertEqual(permission_query_conditions(user="Administrator", doctype="Trip"), "")

	def test_has_permission_driver_own_records_only(self):
		user = self.make_driver_user()
		from fleet_log.utils import has_permission

		trip = self.make_trip()
		other = self.make_driver()
		other_trip = frappe.get_doc(
			{
				"doctype": "Trip",
				"vehicle": self.vehicle.name,
				"driver": other.name,
				"start_odometer": 1000,
				"start_time": now_datetime(),
				"status": "Assigned",
			}
		).insert(ignore_permissions=True)

		self.assertIsNone(
			has_permission(frappe.get_doc("Trip", trip.name), ptype="read", user=user.name)
		)
		self.assertFalse(
			has_permission(frappe.get_doc("Trip", other_trip.name), ptype="read", user=user.name)
		)

	def test_fuel_log_scoped_by_trip_owner(self):
		"""A fuel log created by a manager for a driver's trip is visible to the driver."""
		user = self.make_driver_user()
		trip = self.make_trip()
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)
		log = frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": trip.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1050,
			}
		).insert(ignore_permissions=True)
		self.assertFalse(log.filled_by)  # created by the manager

		from fleet_log.utils import has_permission, permission_query_conditions

		self.assertIsNone(
			has_permission(frappe.get_doc("Fuel Log", log.name), ptype="read", user=user.name)
		)
		cond = permission_query_conditions(user=user.name, doctype="Fuel Log")
		self.assertIn("tabTrip", cond)

	# ------------------------------------------------------------------ #
	# fuel log validation (A6 / A7 / B4)
	# ------------------------------------------------------------------ #
	def test_fuel_log_odometer_outside_trip_range_rejected(self):
		trip = self.make_trip(start_odometer=1050)  # vehicle current is 1000
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)

		# below the trip's start odometer
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Fuel Log",
					"vehicle": self.vehicle.name,
					"trip": trip.name,
					"fuel_quantity": 10,
					"fuel_cost": 100,
					"odometer_at_fill": 1010,
				}
			).insert(ignore_permissions=True)

		# above the trip's end odometer
		trip.end_odometer = 1200
		trip.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Fuel Log",
					"vehicle": self.vehicle.name,
					"trip": trip.name,
					"fuel_quantity": 10,
					"fuel_cost": 100,
					"odometer_at_fill": 1300,
				}
			).insert(ignore_permissions=True)

		# inside the range works
		ok = frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": trip.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1100,
			}
		).insert(ignore_permissions=True)
		self.assertTrue(ok.name)

	def test_fuel_log_price_per_litre(self):
		log = frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"fuel_quantity": 20,
				"fuel_cost": 200,
				"odometer_at_fill": 1010,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(log.price_per_litre, 10)

	# ------------------------------------------------------------------ #
	# doc events / schedulers (A8 / B2 / B3 / D)
	# ------------------------------------------------------------------ #
	def test_trip_totals_sync_on_fuel_log_change(self):
		trip = self.complete_trip(self.make_trip(), end_odometer=1200)
		frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": trip.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1200,
			}
		).insert(ignore_permissions=True)
		trip.reload()
		self.assertEqual(trip.total_fuel_used, 10)
		self.assertEqual(trip.trip_yield, 20)  # 200 km / 10 L

	def test_flag_stale_trips_dedupe(self):
		from frappe.utils import add_to_date

		from fleet_log.utils import flag_stale_trips

		trip = self.make_trip()
		trip.start_time = add_to_date(now_datetime(), hours=-48)
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)

		flag_stale_trips()
		flag_stale_trips()
		count = frappe.db.count(
			"Notification Log", {"document_type": "Trip", "document_name": trip.name}
		)
		self.assertEqual(count, 1)

	def test_flag_assigned_trips_never_started(self):
		from frappe.utils import add_to_date

		from fleet_log.utils import flag_stale_trips

		trip = self.make_trip()
		frappe.db.set_value("Trip", trip.name, "creation", add_to_date(now_datetime(), days=-3))
		flag_stale_trips()
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"document_type": "Trip", "document_name": trip.name}
			)
		)

	def test_vehicle_maintenance_due_notification(self):
		from fleet_log.utils import check_vehicle_maintenance

		meta = frappe.get_meta("Vehicle")
		if not meta.has_field("service_interval_km"):
			self.skipTest("Vehicle has no service schedule fields")
		frappe.db.set_value(
			"Vehicle",
			self.vehicle.name,
			{"service_interval_km": 500, "last_service_odometer": 0, "current_odometer": 1200},
		)
		check_vehicle_maintenance()
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"document_type": "Vehicle", "document_name": self.vehicle.name}
			)
		)

	def test_license_expiry_notification(self):
		from frappe.utils import add_days, getdate, today

		from fleet_log.utils import check_license_expiry

		meta = frappe.get_meta("Driver")
		field = "license_expiry" if meta.has_field("license_expiry") else None
		if not field and meta.has_field("expiry_date"):
			field = "expiry_date"
		if not field:
			self.skipTest("Driver has no license expiry field")
		frappe.db.set_value("Driver", self.driver.name, field, add_days(getdate(today()), 5))
		check_license_expiry()
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"document_type": "Driver", "document_name": self.driver.name}
			)
		)

	# ------------------------------------------------------------------ #
	# cold-start baseline (B17)
	# ------------------------------------------------------------------ #
	def test_effective_average_fallback(self):
		"""Before any Reconciled trip, completed trips provide the baseline."""
		t1 = self.make_trip()
		t1.status = "In Progress"
		t1.save(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": t1.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1050,
			}
		).insert(ignore_permissions=True)
		t1.end_odometer = 1100
		t1.end_time = now_datetime()
		t1.save(ignore_permissions=True)
		t1.status = "Completed"
		t1.save(ignore_permissions=True)

		t2 = self.make_trip(start_odometer=1100)
		t2.status = "In Progress"
		t2.save(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": t2.name,
				"fuel_quantity": 20,
				"fuel_cost": 200,
				"odometer_at_fill": 1150,
			}
		).insert(ignore_permissions=True)
		t2.end_odometer = 1200
		t2.end_time = now_datetime()
		t2.save(ignore_permissions=True)
		t2.status = "Completed"
		t2.save(ignore_permissions=True)

		self.assertEqual(t2.trip_yield, 5)
		self.assertEqual(t2.yield_flag, "Critical")  # 5 vs fallback baseline 10

	# ------------------------------------------------------------------ #
	# reports (B15 / D)
	# ------------------------------------------------------------------ #
	def test_reports_execute(self):
		trip = self.complete_trip(self.make_trip(), end_odometer=1200)
		frappe.get_doc(
			{
				"doctype": "Fuel Log",
				"vehicle": self.vehicle.name,
				"trip": trip.name,
				"fuel_quantity": 10,
				"fuel_cost": 100,
				"odometer_at_fill": 1200,
			}
		).insert(ignore_permissions=True)

		from fleet_log.fleet_log.reports.cost_per_vehicle.cost_per_vehicle import execute as c1
		from fleet_log.fleet_log.reports.driver_mileage_report.driver_mileage_report import execute as c2
		from fleet_log.fleet_log.reports.flagged_trips_report.flagged_trips_report import execute as c3
		from fleet_log.fleet_log.reports.fuel_cost_per_driver.fuel_cost_per_driver import execute as c4
		from fleet_log.fleet_log.reports.fuel_price_trend.fuel_price_trend import execute as c5
		from fleet_log.fleet_log.reports.fuel_yield_trend.fuel_yield_trend import execute as c6

		filters = {
			"from_date": "2000-01-01",
			"to_date": "2999-12-31",
			"vehicle": self.vehicle.name,
		}
		for fn in (c1, c2, c3, c4, c5, c6):
			columns, rows = fn(filters)
			self.assertIsInstance(columns, list)
			self.assertIsInstance(rows, list)