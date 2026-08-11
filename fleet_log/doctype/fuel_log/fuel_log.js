frappe.ui.form.on("Fuel Log", {
	refresh(frm) {
		// Pull the vehicle from the linked trip, if set
		frm.add_fetch("trip", "vehicle", "vehicle");
		// Pre-fill the odometer with the vehicle's last known reading.
		// The Driver role is granted read access to Vehicle in both modes
		// (fallback doctype or ERPNext custom docperm), so the fetch works
		// everywhere; it simply no-ops if the user lacks read access.
		frm.add_fetch("vehicle", "current_odometer", "odometer_at_fill");
		// Show the sanity flag as an indicator
		if (frm.doc.sanity_flag === "Suspicious") {
			frm.dashboard.set_headline(
				__("This fill-up looks suspicious - please verify the odometer reading"),
				"red"
			);
		}
	}
});
