/* ==========================================================================
   Bizaxl Fleet Portal — application logic
   Talks to the whitelisted fleet_log.api endpoints under the normal
   permission system (drivers are scoped to their own records server-side).
   ========================================================================== */
(function () {
	"use strict";

	// ---------------------------------------------------------------- utils
	const $ = (sel, root) => (root || document).querySelector(sel);
	const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

	const state = {
		boot: null,
		vehicles: [],
		trips: [],
		fuelLogs: [],
		expenses: [],
		view: "dashboard",
		tripFilter: "All",
	};

	let csrfToken = (window.FP && window.FP.csrf) || "";

	function esc(s) {
		return String(s == null ? "" : s)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	function parseDT(s) {
		if (!s) return null;
		const t = new Date(s.includes("T") ? s : s.replace(" ", "T"));
		return isNaN(t.getTime()) ? null : t;
	}
	function fmtDT(s) {
		const t = parseDT(s);
		if (!t) return "—";
		return t.toLocaleString(undefined, {
			day: "numeric", month: "short", year: "numeric",
			hour: "2-digit", minute: "2-digit",
		});
	}
	function fmtDate(s) {
		const t = parseDT(s);
		if (!t) return "—";
		return t.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
	}
	function fmtNum(n, d) {
		if (n == null || n === "") return "—";
		const v = Number(n);
		if (isNaN(v)) return "—";
		return v.toLocaleString(undefined, { maximumFractionDigits: d == null ? 1 : d });
	}
	function fmtMoney(n) {
		const v = Number(n || 0);
		return new Intl.NumberFormat(undefined, {
			style: "currency", currency: "INR", maximumFractionDigits: 2,
		}).format(v);
	}
	function isSameMonth(dt, ref) {
		return dt && dt.getFullYear() === ref.getFullYear() && dt.getMonth() === ref.getMonth();
	}
	function initials(name) {
		return String(name || "?")
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((p) => p[0].toUpperCase())
			.join("");
	}
	function greeting() {
		const h = new Date().getHours();
		if (h < 12) return "Good morning";
		if (h < 17) return "Good afternoon";
		return "Good evening";
	}
	function firstName(name) {
		return String(name || "").split(/\s+/)[0] || "there";
	}

	// ------------------------------------------------------------ API layer
	function extractError(data) {
		if (data && data._server_messages && data._server_messages.length) {
			try {
				const m = JSON.parse(data._server_messages[0]);
				if (m && m.message) return m.message.replace(/<[^>]+>/g, "").trim();
			} catch (e) { /* ignore */ }
		}
		if (data && data.exc_type) return String(data.exc_type).split(".").pop();
		if (data && data.message && typeof data.message === "string") return data.message;
		return "Something went wrong. Please try again.";
	}

	async function apiCall(method, args) {
		if (!csrfToken) {
			try {
				const r = await fetch("/api/method/frappe.auth.get_csrf_token", { credentials: "same-origin" });
				const d = await r.json();
				csrfToken = (d.message && d.message.csrf_token) || "";
			} catch (e) { /* offline or blocked */ }
		}
		let res;
		try {
			res = await fetch("/api/method/" + method, {
				method: "POST",
				credentials: "same-origin",
				headers: {
					"Content-Type": "application/json",
					"Accept": "application/json",
					"X-Frappe-CSRF-Token": csrfToken || "",
				},
				body: JSON.stringify(args || {}),
			});
		} catch (e) {
			throw new Error("Network error — check your connection.");
		}
		let data = {};
		try { data = await res.json(); } catch (e) { /* non-JSON */ }
		if (!res.ok || data.exc_type) throw new Error(extractError(data));
		return data.message;
	}

	// ------------------------------------------------------------- rendering
	function refreshIcons() {
		if (window.lucide) {
			try { lucide.createIcons(); } catch (e) { /* ignore */ }
		}
	}

	function chip(kind, label, dot) {
		return `<span class="chip chip-${kind}">${dot ? '<span class="dot"></span>' : ""}${esc(label)}</span>`;
	}

	function statusChip(status) {
		const s = status || "Assigned";
		const map = {
			"Assigned": ["assigned", "Assigned"],
			"In Progress": ["progress", "In Progress"],
			"Completed": ["completed", "Completed"],
			"Reconciled": ["reconciled", "Reconciled"],
			"Cancelled": ["cancelled", "Cancelled"],
		};
		const [k, l] = map[s] || ["cancelled", s];
		return chip(k, l, true);
	}

	function flagChip(flag) {
		if (!flag || flag === "Normal") return chip("normal", "Normal");
		if (flag === "Below Average") return chip("below", "Below Avg");
		return chip("critical", "Critical");
	}

	function sanityChip(flag) {
		return flag === "Suspicious" ? chip("suspicious", "Suspicious") : chip("ok", "OK");
	}

	function vehicleLabel(id) {
		if (!id) return "—";
		const v = state.vehicles.find((x) => x.name === id);
		if (!v) return id;
		return v.registration_number || v.license_plate || v.name;
	}

	function skeleton(rows) {
		let out = "";
		for (let i = 0; i < (rows || 3); i++) out += '<div class="skeleton" style="height:64px;margin-bottom:12px"></div>';
		return out;
	}

	function emptyState(icon, title, text, ctaHtml) {
		return `<div class="empty">
			<div class="empty-icon"><i data-lucide="${icon}" width="26" height="26"></i></div>
			<h3>${esc(title)}</h3>
			<p>${esc(text)}</p>
			${ctaHtml || ""}
		</div>`;
	}

	// ---------------------------------------------------------------- toasts
	function toast(msg, type) {
		const root = $("#toast-root");
		const el = document.createElement("div");
		el.className = "toast" + (type === "error" ? " toast-error" : "");
		el.innerHTML = `<i data-lucide="${type === "error" ? "circle-alert" : "circle-check"}" width="18" height="18"></i><span></span>`;
		$("span", el).textContent = msg;
		root.appendChild(el);
		refreshIcons();
		setTimeout(() => {
			el.style.transition = "opacity .35s ease";
			el.style.opacity = "0";
			setTimeout(() => el.remove(), 380);
		}, 3400);
	}

	// ---------------------------------------------------------------- modals
	let escHandler = null;

	function openModal(opts) {
		const root = $("#modal-root");
		root.innerHTML = `
			<div class="modal-backdrop" id="modal-backdrop">
				<div class="modal" role="dialog" aria-modal="true">
					<div class="modal-head">
						<div>
							<div class="modal-title">${esc(opts.title || "")}</div>
							${opts.sub ? `<div class="modal-sub">${esc(opts.sub)}</div>` : ""}
						</div>
						<button type="button" class="btn-icon" data-close aria-label="Close"><i data-lucide="x" width="18" height="18"></i></button>
					</div>
					<div class="modal-body"></div>
					${opts.footer !== false ? '<div class="modal-foot"></div>' : ""}
				</div>
			</div>`;

		const backdrop = $("#modal-backdrop", root);
		const body = $(".modal-body", root);
		body.innerHTML = opts.body || "";

		const foot = $(".modal-foot", root);
		if (foot && opts.footer !== false) {
			foot.innerHTML = `
				<button type="button" class="btn btn-tertiary" data-close>Cancel</button>
				<button type="button" class="btn ${opts.danger ? "btn-danger" : "btn-primary"}" id="modal-ok">
					<span class="btn-label">${esc(opts.okLabel || "Save")}</span>
					<span class="btn-spinner hidden"></span>
				</button>`;
		}

		const close = () => {
			root.innerHTML = "";
			if (escHandler) { document.removeEventListener("keydown", escHandler); escHandler = null; }
		};
		$$("[data-close]", root).forEach((b) => b.addEventListener("click", close));
		backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
		escHandler = (e) => { if (e.key === "Escape") close(); };
		document.addEventListener("keydown", escHandler);

		refreshIcons();
		if (opts.onOpen) opts.onOpen(body);

		if (foot && opts.footer !== false) {
			const ok = $("#modal-ok", root);
			ok.addEventListener("click", async () => {
				const btn = ok;
				const label = $(".btn-label", btn);
				const spin = $(".btn-spinner", btn);
				btn.disabled = true; label.classList.add("hidden"); spin.classList.remove("hidden");
				try {
					await opts.onSubmit(body);
					close();
				} catch (err) {
					btn.disabled = false; label.classList.remove("hidden"); spin.classList.add("hidden");
					toast(err.message || "Failed", "error");
				}
			});
		}
		return { body, close };
	}

	function busy(btn, on) {
		const label = $(".btn-label", btn);
		const spin = $(".btn-spinner", btn);
		if (!label || !spin) return;
		btn.disabled = on;
		label.classList.toggle("hidden", on);
		spin.classList.toggle("hidden", !on);
	}

	// ----------------------------------------------------------- field helpers
	function fieldHTML(label, inputHtml, hint) {
		return `<label class="field"><span class="field-label">${esc(label)}</span>${inputHtml}${hint ? `<span class="form-hint">${esc(hint)}</span>` : ""}</label>`;
	}

	function vehicleOptions(selected) {
		if (!state.vehicles.length) return '<option value="">No vehicles available</option>';
		return state.vehicles.map((v) => {
			const label = v.registration_number || v.license_plate || v.name;
			return `<option value="${esc(v.name)}" ${v.name === selected ? "selected" : ""}>${esc(label)}</option>`;
		}).join("");
	}

	// ============================================================== AUTH
	async function checkAuth() {
		const user = await apiCall("frappe.auth.get_logged_user");
		return user && user !== "Guest";
	}

	function showLogin() {
		$("#app").classList.add("hidden");
		$("#login-view").classList.remove("hidden");
		refreshIcons();
		$("#login-usr").focus();
	}

	function showApp() {
		$("#login-view").classList.add("hidden");
		$("#app").classList.remove("hidden");
		renderUserChips();
		refreshIcons();
		navigate(state.view);
	}

	function renderUserChips() {
		const b = state.boot || {};
		const name = b.full_name || b.user || "User";
		const role = b.is_fleet_manager ? "Fleet Manager" : "Driver";
		const chipHtml = `
			<div class="avatar">${esc(initials(name))}</div>
			<div>
				<div class="u-name">${esc(name)}</div>
				<div class="u-role">${esc(role)}</div>
			</div>`;
		$("#top-user-chip").innerHTML = chipHtml;
		$("#side-user-chip").innerHTML = chipHtml;
		refreshIcons();
	}

	// ============================================================== DATA
	async function loadAll() {
		const [v, t, f, e] = await Promise.all([
			apiCall("fleet_log.api.get_my_vehicles").catch(() => []),
			apiCall("fleet_log.api.get_my_trips", { limit: 200 }).catch(() => []),
			apiCall("fleet_log.api.get_my_fuel_logs", { limit: 200 }).catch(() => []),
			apiCall("fleet_log.api.get_my_expenses", { limit: 200 }).catch(() => []),
		]);
		state.vehicles = v || [];
		state.trips = t || [];
		state.fuelLogs = f || [];
		state.expenses = e || [];
	}

	async function reload() {
		await loadAll();
		renderView();
	}

	// ============================================================== VIEWS
	const VIEWS = {
		dashboard: { title: "Dashboard", sub: "Fleet at a glance" },
		trips: { title: "Trips", sub: "Create, run and reconcile trips" },
		fuel: { title: "Fuel Logs", sub: "Fill-ups, yield and sanity flags" },
		vehicles: { title: "Vehicles", sub: "Fleet odometer and yield" },
		expenses: { title: "Expenses", sub: "Tolls, parking and other trip costs" },
		account: { title: "Account", sub: "Your profile and licence" },
	};

	function navigate(view) {
		if (!VIEWS[view]) view = "dashboard";
		state.view = view;
		$$(".nav-item").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
		$("#view-title").textContent = VIEWS[view].title;
		$("#view-sub").textContent = VIEWS[view].sub;
		closeSidebar();
		if (window.location.hash !== "#" + view) window.location.hash = view;
		renderView();
	}

	function renderView() {
		const el = $("#view");
		el.innerHTML = skeleton(2);
		switch (state.view) {
			case "dashboard": renderDashboard(el); break;
			case "trips": renderTrips(el); break;
			case "fuel": renderFuel(el); break;
			case "vehicles": renderVehicles(el); break;
			case "expenses": renderExpenses(el); break;
			case "account": renderAccount(el); break;
		}
		refreshIcons();
	}

	// ------------------------------------------------------------- dashboard
	function kpiCard(label, value, foot, icon) {
		return `<div class="kpi">
			<div class="kpi-label"><i data-lucide="${icon}" width="14" height="14"></i>${esc(label)}</div>
			<div class="kpi-value">${value}</div>
			<div class="kpi-foot">${esc(foot)}</div>
		</div>`;
	}

	function renderDashboard(el) {
		const b = state.boot || {};
		const now = new Date();
		const active = state.trips.filter((t) => ["Assigned", "In Progress"].includes(t.status || "Assigned"));
		const monthTrips = state.trips.filter((t) => isSameMonth(parseDT(t.start_time) || parseDT(t.creation), now));
		const monthFuel = state.fuelLogs
			.filter((f) => isSameMonth(parseDT(f.creation), now))
			.reduce((s, f) => s + Number(f.fuel_quantity || 0), 0);
		const flagged = state.trips.filter((t) => t.yield_flag && t.yield_flag !== "Normal").length +
			state.fuelLogs.filter((f) => f.sanity_flag === "Suspicious").length;

		const roleBadge = b.is_fleet_manager
			? '<span class="badge badge-blue"><i data-lucide="shield-check" width="12" height="12"></i> Fleet Manager</span>'
			: '<span class="badge badge-mint"><i data-lucide="user-check" width="12" height="12"></i> Driver</span>';

		let licenseAlert = "";
		const drv = b.driver;
		if (drv && !b.is_fleet_manager && (drv.license_expiry || drv.expiry_date)) {
			const exp = drv.license_expiry || drv.expiry_date;
			const days = Math.ceil((parseDT(exp) - Date.now()) / 86400000);
			if (days <= 30) {
				licenseAlert = `<div class="card" style="margin-bottom:24px;border-color:rgba(245,158,11,.4);background:#FFFDF5">
					<div style="display:flex;align-items:center;gap:12px">
						<i data-lucide="${days < 0 ? "alert-octagon" : "alert-triangle"}" width="22" height="22" style="color:#B45309;flex-shrink:0"></i>
						<div>
							<div style="font-weight:700;color:#92400E">Driving licence ${days < 0 ? "expired" : "expiring soon"}</div>
							<div style="font-size:13px;color:#A16207">${days < 0 ? Math.abs(days) + " day(s) ago — renew before driving" : "Expires in " + days + " day(s)"}. Vehicle &amp; trip access may be restricted.</div>
						</div>
					</div>
				</div>`;
			}
		}

		const recent = state.trips.slice(0, 5);

		el.innerHTML = `
			${licenseAlert}
			<div class="section-head">
				<div>
					<h2>${esc(greeting())}, ${esc(firstName(b.full_name || b.user))} 👋</h2>
					<p>${esc(new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }))} &nbsp;·&nbsp; ${roleBadge}</p>
				</div>
				<div class="section-actions">
					<button class="btn btn-primary" data-action="new-trip"><i data-lucide="plus" width="16" height="16"></i> New Trip</button>
					<button class="btn btn-secondary" data-action="log-fuel"><i data-lucide="fuel" width="16" height="16"></i> Log Fuel</button>
				</div>
			</div>

			<div class="kpi-grid">
				${kpiCard("Active Trips", fmtNum(active.length, 0), "Assigned or in progress", "activity")}
				${kpiCard("Trips This Month", fmtNum(monthTrips.length, 0), "by start date", "calendar")}
				${kpiCard("Fuel This Month", fmtNum(monthFuel, 1) + " <small>L</small>", "total litres logged", "fuel")}
				${kpiCard("Flagged Items", fmtNum(flagged, 0), "yield &amp; sanity flags", "flag")}
			</div>

			<div class="section-head" style="margin-bottom:16px">
				<div><h2 style="font-size:18px">Recent trips</h2><p>Latest activity across the fleet</p></div>
				<button class="btn btn-ghost btn-sm" data-action="go-trips">View all <i data-lucide="arrow-right" width="14" height="14"></i></button>
			</div>
			${recent.length ? `<div class="trip-list" style="margin-bottom:32px">${recent.map(tripRow).join("")}</div>` : emptyState("route", "No trips yet", "Create your first trip to start tracking odometer and fuel yield.")}

			${state.vehicles.length ? `
			<div class="section-head" style="margin-bottom:16px">
				<div><h2 style="font-size:18px">Fleet snapshot</h2><p>Current odometer and yield per vehicle</p></div>
			</div>
			<div class="grid-3">${state.vehicles.slice(0, 6).map(vehicleCard).join("")}</div>` : ""}
		`;
		bindCommonActions(el);
	}

	// --------------------------------------------------------------- trips
	function tripDetail(t) {
		const label = vehicleLabel(t.vehicle);
		return `<div class="trip-detail">
				<div class="d-item"><span class="d-label">Vehicle</span><span class="d-value">${esc(label)}</span></div>
				<div class="d-item"><span class="d-label">Driver</span><span class="d-value">${esc(t.driver || "—")}</span></div>
				<div class="d-item"><span class="d-label">Start odometer</span><span class="d-value">${fmtNum(t.start_odometer, 0)} km</span></div>
				<div class="d-item"><span class="d-label">End odometer</span><span class="d-value">${fmtNum(t.end_odometer, 0)} km</span></div>
				<div class="d-item"><span class="d-label">Start time</span><span class="d-value">${esc(fmtDT(t.start_time))}</span></div>
				<div class="d-item"><span class="d-label">End time</span><span class="d-value">${esc(fmtDT(t.end_time))}</span></div>
				<div class="d-item"><span class="d-label">Start location</span><span class="d-value">${esc(t.start_location || "—")}</span></div>
				<div class="d-item"><span class="d-label">End location</span><span class="d-value">${esc(t.end_location || "—")}</span></div>
				<div class="d-item"><span class="d-label">Distance</span><span class="d-value">${fmtNum(t.distance_covered, 1)} km</span></div>
				<div class="d-item"><span class="d-label">Fuel used</span><span class="d-value">${fmtNum(t.total_fuel_used, 1)} L</span></div>
				<div class="d-item"><span class="d-label">Trip yield</span><span class="d-value">${fmtNum(t.trip_yield, 2)} km/L</span></div>
				<div class="d-item"><span class="d-label">Yield flag</span><span class="d-value">${flagChip(t.yield_flag)}</span></div>
			</div>`;
	}

	function tripRow(t) {
		const status = t.status || "Assigned";
		const label = vehicleLabel(t.vehicle);
		const actions = tripActions(t);
		return `
			<div class="trip-item" data-trip="${esc(t.name)}">
				<div class="trip-icon"><i data-lucide="route" width="20" height="20"></i></div>
				<div class="trip-main">
					<div class="trip-title">
						<span>${esc(t.purpose || label)}</span>
						${statusChip(status)}
						${t.yield_flag && t.yield_flag !== "Normal" ? flagChip(t.yield_flag) : ""}
					</div>
					<div class="trip-meta">
						<span><i data-lucide="truck" width="13" height="13"></i>${esc(label)}</span>
						<span><i data-lucide="calendar" width="13" height="13"></i>${esc(fmtDT(t.start_time))}</span>
						<span><i data-lucide="gauge" width="13" height="13"></i>${fmtNum(t.distance_covered, 1)} km</span>
						<span><i data-lucide="droplets" width="13" height="13"></i>${fmtNum(t.total_fuel_used, 1)} L</span>
						${t.trip_yield ? `<span><i data-lucide="zap" width="13" height="13"></i>${fmtNum(t.trip_yield, 2)} km/L</span>` : ""}
					</div>
				</div>
				<div class="trip-stats">
					<div class="ts-value">${fmtNum(t.distance_covered || t.end_odometer || t.start_odometer, 0)}</div>
					<div class="ts-label">km · <i data-lucide="chevron-down" width="13" height="13" class="chev"></i></div>
				</div>
				${actions ? `<div class="trip-actions" data-stop>${actions}</div>` : ""}
				${tripDetail(t)}
			</div>`;
	}

	function tripActions(t) {
		const status = t.status || "Assigned";
		const isMgr = !!(state.boot && state.boot.is_fleet_manager);
		const out = [];
		if (status === "Assigned") {
			out.push(`<button class="btn btn-primary btn-sm" data-act="begin" data-trip="${esc(t.name)}">Begin Trip</button>`);
		}
		if (status === "In Progress") {
			out.push(`<button class="btn btn-primary btn-sm" data-act="complete" data-trip="${esc(t.name)}">Complete Trip</button>`);
		}
		if (status === "Completed" && isMgr) {
			out.push(`<button class="btn btn-secondary btn-sm" data-act="reconcile" data-trip="${esc(t.name)}">Reconcile</button>`);
		}
		if (["Assigned", "In Progress", "Completed"].includes(status) && isMgr) {
			out.push(`<button class="btn btn-ghost btn-sm" data-act="cancel" data-trip="${esc(t.name)}">Cancel</button>`);
		}
		return out.join("");
	}

	function renderTrips(el) {
		const filters = ["All", "Assigned", "In Progress", "Completed", "Reconciled", "Cancelled"];
		const count = (f) => (f === "All" ? state.trips.length : state.trips.filter((t) => (t.status || "Assigned") === f).length);
		const rows = state.trips.filter((t) => state.tripFilter === "All" || (t.status || "Assigned") === state.tripFilter);

		el.innerHTML = `
			<div class="section-head">
				<div><h2>Trips</h2><p>${fmtNum(state.trips.length, 0)} trips visible to you</p></div>
				<div class="section-actions">
					<button class="btn btn-primary" data-action="new-trip"><i data-lucide="plus" width="16" height="16"></i> New Trip</button>
				</div>
			</div>
			<div class="filter-row">
				${filters.map((f) => `<button class="filter-chip ${state.tripFilter === f ? "active" : ""}" data-filter="${f}">${f} <span style="opacity:.6">· ${count(f)}</span></button>`).join("")}
			</div>
			${rows.length ? `<div class="trip-list">${rows.map(tripRow).join("")}</div>` : emptyState("route", "No trips here", "No trips match this filter yet. Create a new trip to get started.", '<button class="btn btn-primary" data-action="new-trip"><i data-lucide="plus" width="16" height="16"></i> New Trip</button>')}
		`;
		bindCommonActions(el);
		$$(".filter-chip", el).forEach((c) =>
			c.addEventListener("click", () => { state.tripFilter = c.dataset.filter; renderTrips(el); })
		);
		$$("[data-act]", el).forEach((b) => b.addEventListener("click", (e) => {
			e.stopPropagation();
			const trip = state.trips.find((t) => t.name === b.dataset.trip);
			if (trip) handleTripAction(b.dataset.act, trip);
		}));
		$$(".trip-item", el).forEach((item) =>
			item.addEventListener("click", (e) => {
				if (e.target.closest("button") || e.target.closest("[data-stop]")) return;
				item.classList.toggle("open");
			})
		);
		refreshIcons();
	}

	function handleTripAction(act, trip) {
		if (act === "begin") beginTripModal(trip);
		if (act === "complete") completeTripModal(trip);
		if (act === "reconcile") confirmAction("Reconcile trip", `Reconcile <b>${esc(trip.name)}</b>? This locks the trip and rolls its fuel yield into the vehicle's rolling average.`, "Reconcile", () => updateStatus(trip, "Reconciled"));
		if (act === "cancel") confirmAction("Cancel trip", `Cancel <b>${esc(trip.name)}</b>? This cannot be undone from the portal.`, "Cancel trip", () => updateStatus(trip, "Cancelled"), true);
	}

	async function updateStatus(trip, status) {
		const r = await apiCall("fleet_log.api.update_trip", { name: trip.name, status: status });
		toast(`Trip ${esc(trip.name)} → ${status}`);
		await reload();
	}

	// --------------------------------------------------------------- fuel
	function renderFuel(el) {
		const totalCost = state.fuelLogs.reduce((s, f) => s + Number(f.fuel_cost || 0), 0);
		const totalLitres = state.fuelLogs.reduce((s, f) => s + Number(f.fuel_quantity || 0), 0);
		const rows = state.fuelLogs.map((f) => `
			<tr>
				<td>${esc(fmtDate(f.creation))}</td>
				<td><b>${esc(vehicleLabel(f.vehicle))}</b></td>
				<td>${fmtNum(f.odometer_at_fill, 0)} km</td>
				<td>${fmtNum(f.fuel_quantity, 2)} L</td>
				<td>${fmtNum(f.price_per_litre, 2)}</td>
				<td>${fmtMoney(f.fuel_cost)}</td>
				<td>${fmtNum(f.fill_up_yield, 2)} km/L</td>
				<td>${sanityChip(f.sanity_flag)}</td>
				<td>${f.trip ? `<span class="badge">${esc(f.trip)}</span>` : '<span class="badge">Standalone</span>'}</td>
			</tr>`).join("");

		el.innerHTML = `
			<div class="section-head">
				<div><h2>Fuel Logs</h2><p>${fmtNum(state.fuelLogs.length, 0)} fill-ups · ${fmtNum(totalLitres, 1)} L · ${fmtMoney(totalCost)}</p></div>
				<div class="section-actions">
					<button class="btn btn-secondary" data-action="log-fuel"><i data-lucide="fuel" width="16" height="16"></i> Log Fuel</button>
				</div>
			</div>
			${rows ? `<div class="table-wrap"><table class="data">
				<thead><tr><th>Date</th><th>Vehicle</th><th>Odometer</th><th>Qty</th><th>₹/L</th><th>Cost</th><th>Yield</th><th>Sanity</th><th>Trip</th></tr></thead>
				<tbody>${rows}</tbody>
			</table></div>` : emptyState("fuel", "No fuel logs yet", "Log your first fill-up to start tracking yield and price per litre.")}
		`;
		bindCommonActions(el);
		refreshIcons();
	}

	// ------------------------------------------------------------ vehicles
	function vehicleCard(v) {
		const label = v.registration_number || v.license_plate || v.name;
		return `<div class="card vehicle-card">
			<div class="vehicle-head">
				<div class="vehicle-icon"><i data-lucide="truck" width="22" height="22"></i></div>
				${v.vehicle_type ? `<span class="badge">${esc(v.vehicle_type)}</span>` : ""}
			</div>
			<div class="vehicle-name">${esc(label)}</div>
			<div class="vehicle-reg">${v.fuel_type ? esc(v.fuel_type) : "—"} fuel</div>
			<div class="vehicle-stats">
				<div class="vehicle-stat">
					<div class="v-label">Odometer</div>
					<div class="v-value">${fmtNum(v.current_odometer != null ? v.current_odometer : v.last_odometer, 0)} <small style="font-size:12px;color:var(--gray-400)">km</small></div>
				</div>
				<div class="vehicle-stat">
					<div class="v-label">Avg yield</div>
					<div class="v-value">${fmtNum(v.average_yield, 2)} <small style="font-size:12px;color:var(--gray-400)">km/L</small></div>
				</div>
			</div>
		</div>`;
	}

	function renderVehicles(el) {
		el.innerHTML = `
			<div class="section-head">
				<div><h2>Vehicles</h2><p>${fmtNum(state.vehicles.length, 0)} vehicle(s) you can read</p></div>
			</div>
			${state.vehicles.length ? `<div class="grid-3">${state.vehicles.map(vehicleCard).join("")}</div>` : emptyState("truck", "No vehicles", "No vehicles are available to your account yet.")}
		`;
		bindCommonActions(el);
		refreshIcons();
	}

	// ------------------------------------------------------------ expenses
	function renderExpenses(el) {
		const total = state.expenses.reduce((s, x) => s + Number(x.amount || 0), 0);
		const rows = state.expenses.map((x) => `
			<tr>
				<td>${esc(fmtDate(x.creation))}</td>
				<td><b>${esc(x.trip)}</b></td>
				<td>${x.expense_type ? `<span class="badge">${esc(x.expense_type)}</span>` : '<span class="badge">Other</span>'}</td>
				<td style="text-align:right;font-weight:700">${fmtMoney(x.amount)}</td>
			</tr>`).join("");

		el.innerHTML = `
			<div class="section-head">
				<div><h2>Expenses</h2><p>${fmtNum(state.expenses.length, 0)} expense(s) · total ${fmtMoney(total)}</p></div>
				<div class="section-actions">
					<button class="btn btn-primary" data-action="new-expense"><i data-lucide="plus" width="16" height="16"></i> Add Expense</button>
				</div>
			</div>
			${rows ? `<div class="table-wrap"><table class="data">
				<thead><tr><th>Date</th><th>Trip</th><th>Type</th><th style="text-align:right">Amount</th></tr></thead>
				<tbody>${rows}</tbody>
			</table></div>` : emptyState("receipt", "No expenses yet", "Add tolls, parking or other trip costs here.")}
		`;
		bindCommonActions(el);
		refreshIcons();
	}

	// ------------------------------------------------------------- account
	function licenseBadge(expiry) {
		if (!expiry) return chip("cancelled", "Not set");
		const t = parseDT(expiry);
		const days = Math.ceil((t - Date.now()) / 86400000);
		if (days < 0) return chip("critical", "Expired " + Math.abs(days) + "d ago");
		if (days <= 30) return chip("below", days + "d left");
		return chip("ok", days + "d left");
	}

	function renderAccount(el) {
		const b = state.boot || {};
		const drv = b.driver || {};
		const name = b.full_name || b.user || "User";
		const role = b.is_fleet_manager ? "Fleet Manager" : "Driver";
		const expiry = drv.license_expiry || drv.expiry_date;

		el.innerHTML = `
			<div class="profile-banner">
				<div class="avatar">${esc(initials(name))}</div>
				<div>
					<div class="profile-name">${esc(name)}</div>
					<div class="profile-role">${esc(b.user)} · ${esc(role)}</div>
				</div>
				<div style="margin-left:auto">${b.is_fleet_manager ? '<span class="badge badge-blue">Full access</span>' : '<span class="badge badge-mint">Field user</span>'}</div>
			</div>

			<div class="grid-2">
				<div class="card">
					<div class="card-title" style="margin-bottom:8px">Driver record</div>
					<p class="card-sub" style="margin-bottom:12px">Linked via <code>Driver.user</code> — powers your permission scoping.</p>
					<div class="detail-list">
						<div class="detail-row"><span class="detail-label"><i data-lucide="id-card" width="15" height="15"></i> Name</span><span class="detail-value">${esc(drv.driver_name || drv.full_name || "—")}</span></div>
						<div class="detail-row"><span class="detail-label"><i data-lucide="badge-check" width="15" height="15"></i> Licence</span><span class="detail-value">${esc(drv.license_number || "—")}</span></div>
						<div class="detail-row"><span class="detail-label"><i data-lucide="calendar-clock" width="15" height="15"></i> Licence expiry</span><span class="detail-value">${licenseBadge(expiry)}</span></div>
						<div class="detail-row"><span class="detail-label"><i data-lucide="phone" width="15" height="15"></i> Contact</span><span class="detail-value">${esc(drv.contact_number || "—")}</span></div>
						<div class="detail-row"><span class="detail-label"><i data-lucide="truck" width="15" height="15"></i> Assigned vehicle</span><span class="detail-value">${esc(drv.assigned_vehicle || "—")}</span></div>
					</div>
				</div>

				<div class="card">
					<div class="card-title" style="margin-bottom:8px">Portal account</div>
					<p class="card-sub" style="margin-bottom:12px">Session and shortcuts.</p>
					<div class="detail-list">
						<div class="detail-row"><span class="detail-label"><i data-lucide="user" width="15" height="15"></i> User ID</span><span class="detail-value">${esc(b.user)}</span></div>
						<div class="detail-row"><span class="detail-label"><i data-lucide="shield" width="15" height="15"></i> Role</span><span class="detail-value">${esc(role)}</span></div>
						<div class="detail-row"><span class="detail-label"><i data-lucide="external-link" width="15" height="15"></i> Frappe Desk</span><span class="detail-value"><a href="/app" target="_blank" rel="noopener">Open desk →</a></span></div>
						<div class="detail-row"><span class="detail-label"><i data-lucide="clipboard-list" width="15" height="15"></i> Trip Log web form</span><span class="detail-value"><a href="/trip-log" target="_blank" rel="noopener">Open form →</a></span></div>
					</div>
					<div style="margin-top:16px">
						<button class="btn btn-danger btn-block" id="account-logout"><i data-lucide="log-out" width="16" height="16"></i> Sign out</button>
					</div>
				</div>
			</div>
		`;
		$("#account-logout", el).addEventListener("click", doLogout);
		refreshIcons();
	}

	// ============================================================== FORMS
	function bindCommonActions(root) {
		$$("[data-action='new-trip']", root).forEach((b) => b.addEventListener("click", () => newTripModal()));
		$$("[data-action='log-fuel']", root).forEach((b) => b.addEventListener("click", () => logFuelModal()));
		$$("[data-action='new-expense']", root).forEach((b) => b.addEventListener("click", () => expenseModal()));
		$$("[data-action='go-trips']", root).forEach((b) => b.addEventListener("click", () => navigate("trips")));
	}

	async function newTripModal() {
		const isMgr = !!(state.boot && state.boot.is_fleet_manager);
		let drivers = [];
		if (isMgr) {
			try { drivers = (await apiCall("fleet_log.api.get_drivers")) || []; } catch (e) { drivers = []; }
		}
		const defaultDriver = (state.boot && state.boot.driver && state.boot.driver.name) || "";
		const nowVal = new Date().toISOString().slice(0, 16);
		const defaultOdo = state.vehicles[0] ? (state.vehicles[0].current_odometer != null ? state.vehicles[0].current_odometer : state.vehicles[0].last_odometer) : "";
		const driverField = isMgr
			? fieldHTML("Driver", `<select class="select" id="nt-driver"><option value="">— auto (linked driver) —</option>${drivers.map((d) => `<option value="${esc(d.name)}" ${d.name === defaultDriver ? "selected" : ""}>${esc(d.driver_name || d.full_name || d.name)}</option>`).join("")}</select>`)
			: "";
		openModal({
			title: "New trip",
			sub: "The trip is created in the Assigned state.",
			okLabel: "Create trip",
			body: `
				${driverField}
				${fieldHTML("Vehicle", `<select class="select" id="nt-vehicle" required>${vehicleOptions()}</select>`)}
				<div class="form-row">
					${fieldHTML("Trip type", `<select class="select" id="nt-type"><option>Field Work</option><option>Delivery</option><option>Personal</option><option selected>Other</option></select>`)}
					${fieldHTML("Start odometer (km)", `<input class="input" type="number" min="0" step="0.1" id="nt-odo" value="${esc(defaultOdo)}" required>`)}
				</div>
				${fieldHTML("Purpose", `<input class="input" type="text" id="nt-purpose" placeholder="e.g. Field spray — North block" maxlength="140">`)}
				${fieldHTML("Start location", `<input class="input" type="text" id="nt-loc" placeholder="e.g. Depot, Chennai">`)}
				${fieldHTML("Start time", `<input class="input" type="datetime-local" id="nt-time" value="${esc(nowVal)}" required>`)}
			`,
			onOpen(body) {
				const sel = $("#nt-vehicle", body);
				sel.addEventListener("change", () => {
					const v = state.vehicles.find((x) => x.name === sel.value);
					if (v && (v.current_odometer != null || v.last_odometer != null)) {
						$("#nt-odo", body).value = v.current_odometer != null ? v.current_odometer : v.last_odometer;
					}
				});
			},
			async onSubmit(body) {
				const v = $("#nt-vehicle", body).value;
				const odo = $("#nt-odo", body).value;
				if (!v) throw new Error("Please choose a vehicle.");
				if (!odo) throw new Error("Start odometer is required.");
				const start_time = $("#nt-time", body).value ? $("#nt-time", body).value.replace("T", " ") + ":00" : undefined;
				const r = await apiCall("fleet_log.api.create_trip", {
					vehicle: v,
					driver: isMgr ? ($("#nt-driver", body).value || null) : undefined,
					trip_type: $("#nt-type", body).value,
					purpose: $("#nt-purpose", body).value,
					start_location: $("#nt-loc", body).value,
					start_odometer: odo,
					start_time: start_time,
				});
				toast(`Trip ${r.name} created (Assigned)`);
				await reload();
			},
		});
	}

	function beginTripModal(trip) {
		const t = parseDT(trip.start_time);
		openModal({
			title: "Begin trip",
			sub: `${esc(trip.name)} — confirm the start readings, then mark it In Progress.`,
			okLabel: "Begin trip",
			body: `
				${fieldHTML("Start odometer (km)", `<input class="input" type="number" min="0" step="0.1" id="bt-odo" value="${esc(trip.start_odometer)}" required>`)}
				${fieldHTML("Start time", `<input class="input" type="datetime-local" id="bt-time" value="${esc(t ? t.toISOString().slice(0, 16) : new Date().toISOString().slice(0, 16))}" required>`)}
			`,
			async onSubmit(body) {
				await apiCall("fleet_log.api.update_trip", {
					name: trip.name,
					status: "In Progress",
					start_odometer: $("#bt-odo", body).value,
					start_time: $("#bt-time", body).value.replace("T", " ") + ":00",
				});
				toast(`${trip.name} is now In Progress`);
				await reload();
			},
		});
	}

	function completeTripModal(trip) {
		const v = state.vehicles.find((x) => x.name === trip.vehicle);
		const cur = v ? (v.current_odometer != null ? v.current_odometer : v.last_odometer) : "";
		openModal({
			title: "Complete trip",
			sub: `${esc(trip.name)} — record the end readings to finish the trip.`,
			okLabel: "Complete trip",
			body: `
				${fieldHTML("End location", `<input class="input" type="text" id="ct-loc" placeholder="e.g. Warehouse, Madurai">`)}
				${fieldHTML("End odometer (km)", `<input class="input" type="number" min="0" step="0.1" id="ct-odo" value="${esc(cur)}" required>`)}
				${fieldHTML("End time", `<input class="input" type="datetime-local" id="ct-time" value="${esc(new Date().toISOString().slice(0, 16))}" required>`)}
			`,
			async onSubmit(body) {
				await apiCall("fleet_log.api.update_trip", {
					name: trip.name,
					status: "Completed",
					end_location: $("#ct-loc", body).value,
					end_odometer: $("#ct-odo", body).value,
					end_time: $("#ct-time", body).value.replace("T", " ") + ":00",
				});
				toast(`${trip.name} completed`);
				await reload();
			},
		});
	}

	function confirmAction(title, text, okLabel, fn, danger) {
		openModal({
			title: title,
			sub: "Please confirm.",
			okLabel: okLabel,
			danger: danger,
			body: `<p style="font-size:14px;color:var(--gray-500)">${text}</p>`,
			async onSubmit() {
				await fn();
			},
		});
	}

	function logFuelModal() {
		const v0 = state.vehicles[0];
		const defOdo = v0 ? (v0.current_odometer != null ? v0.current_odometer : v0.last_odometer) : "";
		const openTrips = state.trips.filter((t) => ["Assigned", "In Progress"].includes(t.status || "Assigned"));
		openModal({
			title: "Log fuel",
			sub: "Fill-up details. Yield is computed as distance since the previous fill-up.",
			okLabel: "Save fuel log",
			body: `
				<div class="form-row">
					${fieldHTML("Vehicle", `<select class="select" id="lf-vehicle" required>${vehicleOptions()}</select>`)}
					${fieldHTML("Trip (optional)", `<select class="select" id="lf-trip"><option value="">Standalone</option>${openTrips.map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("")}</select>`)}
				</div>
				${fieldHTML("Odometer at fill (km)", `<input class="input" type="number" min="0" step="0.1" id="lf-odo" value="${esc(defOdo)}" required>`)}
				<div class="form-row">
					${fieldHTML("Quantity (L)", `<input class="input" type="number" min="0.01" step="0.01" id="lf-qty" required>`)}
					${fieldHTML("Cost (₹)", `<input class="input" type="number" min="0" step="0.01" id="lf-cost" required>`)}
				</div>
				<div class="form-row">
					${fieldHTML("Fuel type", `<select class="select" id="lf-type"><option value="">—</option><option>Petrol</option><option>Diesel</option><option>CNG</option><option>Electric</option></select>`)}
					${fieldHTML("Vendor / station", `<input class="input" type="text" id="lf-vendor" placeholder="e.g. Indian Oil — Ring Rd">`)}
				</div>
				<div id="lf-preview" class="badge badge-blue" style="margin-top:4px"></div>
			`,
			onOpen(body) {
				const calc = () => {
					const q = Number($("#lf-qty", body).value);
					const c = Number($("#lf-cost", body).value);
					if (q > 0 && c > 0) {
						$("#lf-preview", body).textContent = "≈ ₹" + (c / q).toFixed(2) + " per litre";
					} else {
						$("#lf-preview", body).textContent = "";
					}
				};
				$("#lf-qty", body).addEventListener("input", calc);
				$("#lf-cost", body).addEventListener("input", calc);
			},
			async onSubmit(body) {
				const v = $("#lf-vehicle", body).value;
				const qty = $("#lf-qty", body).value;
				const cost = $("#lf-cost", body).value;
				const odo = $("#lf-odo", body).value;
				if (!v || !qty || cost === "" || !odo) throw new Error("Vehicle, quantity, cost and odometer are required.");
				const r = await apiCall("fleet_log.api.log_fuel", {
					vehicle: v,
					trip: $("#lf-trip", body).value || null,
					fuel_quantity: qty,
					fuel_cost: cost,
					odometer_at_fill: odo,
					fuel_type: $("#lf-type", body).value || null,
					fuel_vendor: $("#lf-vendor", body).value || null,
				});
				toast(r.sanity_flag === "Suspicious" ? "Saved — flagged Suspicious (yield out of range)" : "Fuel log saved");
				await reload();
			},
		});
	}

	function expenseModal() {
		const trips = state.trips.slice(0, 50);
		openModal({
			title: "Add expense",
			sub: "Attach a trip cost such as a toll or parking fee.",
			okLabel: "Save expense",
			body: `
				${fieldHTML("Trip", `<select class="select" id="ex-trip" required>${trips.length ? trips.map((t) => `<option value="${esc(t.name)}">${esc(t.name)} — ${esc(t.purpose || "")}</option>`).join("") : '<option value="">No trips available</option>'}</select>`)}
				<div class="form-row">
					${fieldHTML("Type", `<select class="select" id="ex-type"><option>Toll</option><option>Parking</option><option selected>Other</option></select>`)}
					${fieldHTML("Amount (₹)", `<input class="input" type="number" min="0" step="0.01" id="ex-amount" required>`)}
				</div>
			`,
			async onSubmit(body) {
				const trip = $("#ex-trip", body).value;
				const amount = $("#ex-amount", body).value;
				if (!trip) throw new Error("Please choose a trip.");
				if (amount === "" || Number(amount) <= 0) throw new Error("Amount must be greater than zero.");
				const r = await apiCall("fleet_log.api.create_expense", {
					trip: trip,
					expense_type: $("#ex-type", body).value,
					amount: amount,
				});
				toast("Expense added");
				await reload();
			},
		});
	}

	// ============================================================== SIDEBAR
	function openSidebar() {
		$("#sidebar").classList.add("open");
		$("#sidebar-backdrop").classList.add("show");
	}
	function closeSidebar() {
		$("#sidebar").classList.remove("open");
		$("#sidebar-backdrop").classList.remove("show");
	}

	async function doLogout() {
		try { await apiCall("logout"); } catch (e) { /* session may be gone */ }
		window.location.reload();
	}

	// ============================================================== INIT
	function bindStatic() {
		$("#login-form").addEventListener("submit", async (e) => {
			e.preventDefault();
			const btn = $("#login-btn");
			busy(btn, true);
			$("#login-error").classList.add("hidden");
			try {
				await apiCall("login", { usr: $("#login-usr").value.trim(), pwd: $("#login-pwd").value });
				window.location.hash = "dashboard";
				await bootPortal();
			} catch (err) {
				const errEl = $("#login-error");
				errEl.textContent = err.message || "Invalid credentials.";
				errEl.classList.remove("hidden");
			} finally {
				busy(btn, false);
			}
		});

		$("#logout-btn").addEventListener("click", doLogout);
		$("#menu-toggle").addEventListener("click", openSidebar);
		$("#sidebar-backdrop").addEventListener("click", closeSidebar);
		$("#btn-new-trip").addEventListener("click", () => newTripModal());
		$("#btn-log-fuel").addEventListener("click", () => logFuelModal());

		$$(".nav-item").forEach((a) => a.addEventListener("click", (e) => {
			e.preventDefault();
			navigate(a.dataset.view);
		}));

		window.addEventListener("hashchange", () => {
			const v = window.location.hash.replace("#", "");
			if (v && VIEWS[v]) navigate(v);
		});
	}

	async function bootPortal() {
		try {
			state.boot = await apiCall("fleet_log.api.get_portal_bootstrap");
		} catch (e) {
			state.boot = {
				user: (window.FP && window.FP.user) || "",
				full_name: (window.FP && window.FP.full_name) || "",
				is_fleet_manager: false,
				driver: null,
			};
		}
		await loadAll();
		showApp();
	}

	(async function init() {
		bindStatic();
		refreshIcons();
		let authed = false;
		try { authed = await checkAuth(); } catch (e) { authed = false; }
		if (authed) {
			await bootPortal();
		} else {
			showLogin();
		}
	})();
})();
