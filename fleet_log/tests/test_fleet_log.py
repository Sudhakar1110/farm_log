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
		trip = self.make_trip()
		trip.status = "In Progress"
		trip.save(ignore_permissions=True)
		trip.end_odometer = 1200
		trip.end_time = now_datetime()
		trip.save(ignore_permissions=True)
		trip.status = "Completed"
		trip.save(ignore_permissions=True)
		trip.status = "Reconciled"
		trip.save(ignore_permissions=True)
		self.assertEqual(trip.status, "Reconciled")

		driver_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"driver-{frappe.generate_hash(4)}@example.com",
				"first_name": "Test Driver",
				"send_welcome_email": 0,
				"roles": [{"role": "Driver"}],
			}
		).insert(ignore_permissions=True)

		# link the Driver record to the user so the reconciled lock (not the
		# generic permission hook) is what blocks the edit
		frappe.db.set_value("Driver", self.driver.name, "user", driver_user.name)

		frappe.set_user(driver_user.name)
		doc = frappe.get_doc("Trip", trip.name)
		doc.purpose = "attempted edit"
		# blocked either by the controller permission hook (PermissionError) or
		# by the reconciled lock in validate (ValidationError)
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			doc.save()

		frappe.set_user("Administrator")
		trip = frappe.get_doc("Trip", trip.name)
		trip.purpose = "edited by manager"
		trip.save(ignore_permissions=True)
		self.assertEqual(trip.purpose, "edited by manager")
