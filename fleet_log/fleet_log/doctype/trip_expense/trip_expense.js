frappe.ui.form.on("Trip Expense", {
	refresh(frm) {
		if (!frappe.boot.erpnext_installed || frm.is_new() || !frm.doc.amount) {
			return;
		}

		if (!frm.doc.erpnext_expense_claim) {
			frm.add_custom_button(
				__("Create Expense Claim"),
				() => {
					frm.call("create_expense_claim").then(() => frm.reload_doc());
				},
				__("Create")
			);
		}

		if (!frm.doc.erpnext_journal_entry) {
			frm.add_custom_button(
				__("Create Journal Entry"),
				() => {
					frm.call("create_journal_entry").then(() => frm.reload_doc());
				},
				__("Create")
			);
		}
	}
});