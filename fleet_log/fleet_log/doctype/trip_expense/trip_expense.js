frappe.ui.form.on("Trip Expense", {
	refresh(frm) {
		if (
			frappe.boot.erpnext_installed
			&& !frm.is_new()
			&& !frm.doc.erpnext_expense_claim
			&& frm.doc.amount
		) {
			frm.add_custom_button(
				__("Create Expense Claim"),
				() => {
					frm.call("create_expense_claim").then(() => frm.reload_doc());
				},
				__("Create")
			);
		}
	}
});
