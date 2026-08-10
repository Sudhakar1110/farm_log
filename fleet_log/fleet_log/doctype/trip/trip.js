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
		// Pre-fill start odometer from the vehicle's last known reading
		// (standalone mode only: drivers cannot read ERPNext's Vehicle master)
		if (!frappe.boot.erpnext_installed) {
			frm.add_fetch("vehicle", "current_odometer", "start_odometer");
		}
	}
});
