frappe.ui.form.on("Trip", {
	refresh(frm) {
		if (!frm.is_new()) {
			// Show the yield flag as a coloured indicator when the trip is closed
			const flags = {
				"Normal": "green",
				"Below Average": "orange",
				"Critical": "red"
			};
			if (frm.doc.yield_flag && flags[frm.doc.yield_flag]) {
				frm.dashboard.set_headline(
					__("Yield Flag: {0}").format(frm.doc.yield_flag),
					flags[frm.doc.yield_flag]
				);
			}
		}
		// Pre-fill start odometer from the vehicle's last known reading.
		// The Driver role is granted read access to Vehicle in both modes
		// (fallback doctype or ERPNext custom docperm), so the fetch works
		// everywhere; it simply no-ops if the user lacks read access.
		frm.add_fetch("vehicle", "current_odometer", "start_odometer");
	}
});
