# Fleet Log

**Vehicle trips, odometer readings, fuel logging and fuel yield (mileage) tracking for Frappe v15.**

`fleet_log` runs **standalone on a site with just Frappe v15**, and **auto-detects and integrates with ERPNext v15** when it is installed on the same site — reusing ERPNext's existing Fleet Management and Accounts doctypes instead of duplicating them.

---

## Features

- **Trip** doctype with a workflow-driven lifecycle: `Assigned → In Progress → Completed → Reconciled`
  - `distance_covered = end_odometer − start_odometer`
  - `total_fuel_used` = sum of linked Fuel Log quantities
  - `trip_yield` (km/litre) computed on completion, guarded against divide-by-zero
  - `yield_flag`: `Normal` (within 15% of the vehicle average) / `Below Average` (15–30% below) / `Critical` (>30% below)
  - `Vehicle.current_odometer` is auto-updated when a trip completes
- **Fuel Log** doctype
  - Standalone fuel logs or logs tied to an open trip
  - `fill_up_yield` sanity check against the vehicle's last known odometer
  - `sanity_flag`: flagged `Suspicious` when a fill-up implies more than 2x or less than 0.3x the vehicle's average yield
- **Trip Expense** doctype (tolls, parking, other) — on ERPNext sites a **Create Expense Claim** button pushes the expense into ERPNext's `Expense Claim` doctype
- **Workflow** (`Trip Workflow`) with server-side transition conditions:
  - `Assigned → In Progress`: requires `start_odometer` and `start_time`
  - `In Progress → Completed`: requires `end_odometer`, `end_time` and `end_odometer > start_odometer`
  - `Completed → Reconciled`: Fleet Manager only — recalculates the vehicle's rolling `average_yield` and **locks the trip** against further edits by non-managers
- **Validation is server-side** (never just client-side)
  - `end_odometer <= start_odometer` is rejected
  - `Fuel Log.odometer_at_fill < vehicle.current_odometer` is rejected (the vehicle cannot go backwards)
  - a new trip whose `start_odometer` does not match the vehicle's `current_odometer` is **warned** (off-system usage), never blocked
- **Scheduled job** (daily, `hooks.py → scheduler_events`): `flag_stale_trips` sends a Notification Log to the driver and all Fleet Managers for any trip still `In Progress` after 24 hours
- **Four Query Reports**: Cost per Vehicle, Driver Mileage Report, Fuel Yield Trend, Flagged Trips Report
- **Roles**: `Fleet Manager` (full access, may reconcile) and `Driver` (own trips / fuel logs only)
  - `permission_query_conditions` scopes Driver-role users to `driver = <their linked Driver record>`

---

## Works standalone on Frappe v15

```
bench new-app fleet_log            # or copy this app into apps/
bench --site <sitename> install-app fleet_log
```

When **ERPNext is not installed**, the app creates its own fallback masters:

- **Vehicle**: `registration_number` (required, unique), `vehicle_type`, `fuel_type`, `current_odometer` (read-only, auto-updated), `average_yield` (read-only, rolling average)
- **Driver**: `driver_name` (required), `user` (links to a system User for permission scoping), `license_number`, `license_expiry`, `contact_number`, `assigned_vehicle`

The fallback doctypes live in `fallback_doctypes/` (outside the standard doctype-sync path) and are created by `install.py` only when ERPNext is absent — so they never conflict with ERPNext's own doctypes.

---

## Auto-detects & integrates with ERPNext v15

ERPNext is **never** declared in `required_apps`. Instead, `fleet_log.utils.is_erpnext_installed()` checks `frappe.get_installed_apps()` at runtime, and all ERPNext references are guarded with it (plus `frappe.get_meta(...).has_field(...)` checks where fields may differ).

When ERPNext v15 is installed on the same site:

- **Vehicle** — ERPNext's doctype is reused (license plate, make, model, `last_odometer`, `fuel_type`, UOM, etc.). Only the missing fields are added as **Custom Fields**: `current_odometer`, `vehicle_type`, `average_yield`. The Driver role is also granted read access to ERPNext's Vehicle (existing ERPNext permissions are preserved via Custom DocPerm).
- **Driver** — ERPNext's doctype is reused (`full_name`, `license_number`, `expiry_date`, `cell_number`, `employee`, ...). Only `user` (for permission scoping) and `assigned_vehicle` are added as Custom Fields.
- **Trip Expense** — the **Create Expense Claim** button appears on the form (only when ERPNext is detected) and creates an ERPNext `Expense Claim` in Draft, linked back to the expense. The employee is taken from the trip's Driver → Employee link.

### Install order matters

Install **ERPNext before fleet_log** (or on a site that already has ERPNext). If you install fleet_log first and ERPNext later, the fallback `Vehicle`/`Driver` doctypes would collide with ERPNext's — reinstall fleet_log afterwards to switch to ERPNext mode.

---

## Roles & permissions

| Role | Trip | Fuel Log | Trip Expense | Vehicle | Reconciling trips |
|------|------|----------|--------------|---------|-------------------|
| Fleet Manager | full | full | full | full | ✔ |
| Driver | own only | own only | own only | read (standalone) | ✘ |
| System Manager | full | full | full | full | ✔ |

- Driver-role users are scoped to their own records via `permission_query_conditions` (list/search) **and** a `has_permission` controller hook (direct document access).
- Link each Driver record to its system User (`Driver.user`) for the scoping to work.
- `Vehicle.average_yield` and `current_odometer` are read-only and only ever changed by the app.

---

## Workflow

The `Trip Workflow` uses the `status` field as its workflow state field:

| From | Action | To | Allowed roles | Condition |
|------|--------|----|---------------|-----------|
| Assigned | Begin Trip | In Progress | Driver, Fleet Manager | `start_odometer` and `start_time` set |
| In Progress | Complete Trip | Completed | Driver, Fleet Manager | `end_odometer` and `end_time` set, `end_odometer > start_odometer` |
| Completed | Reconcile | Reconciled | Fleet Manager | — |

The workflow conditions are evaluated server-side, and the same rules are mirrored in `Trip.validate` so direct saves are equally safe.

---

## Reports

1. **Cost per Vehicle** — fuel + other costs per vehicle per month
2. **Driver Mileage Report** — distance driven per driver (with trip counts)
3. **Fuel Yield Trend** — `trip_yield` over time per vehicle (gradual decline = maintenance signal; sudden drop = theft/leak signal)
4. **Flagged Trips Report** — trips where `yield_flag != Normal` or any linked Fuel Log is `Suspicious`, for review during reconciliation

Reports run identically on MariaDB and PostgreSQL (grouping is done in Python where needed).

---

## Development

```
bench --site <sitename> migrate                # after pulling changes
bench --site <sitename> run-tests --app fleet_log
```

Project layout:

```
fleet_log/
├── hooks.py                        # scheduler, permissions, doc_events, install hooks
├── fleet_log/
│   ├── install.py                  # mode detection: fallbacks vs ERPNext custom fields
│   ├── utils.py                    # is_erpnext_installed, yield math, permission hooks
│   ├── doctype/{trip, fuel_log, trip_expense, vehicle, driver}/
│   ├── workflow/trip_workflow/     # Trip workflow fixture
│   └── reports/                    # the four query reports
├── fallback_doctypes/              # Vehicle/Driver fixtures (standalone mode only)
└── tests/                          # integration tests
```

## License

MIT
