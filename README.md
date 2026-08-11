# Fleet Log

**Vehicle trips, odometer readings, fuel logging and fuel yield (mileage) tracking for Frappe v15.**

`fleet_log` runs **standalone on a site with just Frappe v15**, and **auto-detects and integrates with ERPNext v15** when it is installed on the same site — reusing ERPNext's existing Fleet Management and Accounts doctypes instead of duplicating them.

---

## Features

- **Trip** doctype with a workflow-driven lifecycle: `Assigned → In Progress → Completed → Reconciled` (plus `Cancelled`)
  - `distance_covered = end_odometer − start_odometer`
  - `total_fuel_used` = sum of linked Fuel Log quantities
  - `trip_yield` (km/litre) computed on completion, guarded against divide-by-zero
  - `yield_flag`: `Normal` (within 15% of the vehicle average) / `Below Average` (15–30% below) / `Critical` (>30% below)
  - `Vehicle.current_odometer` is auto-updated when a trip completes
  - `trip_type` (Field Work / Delivery / Personal / Other), optional `company`, and GPS `start_geo` / `end_geo` fields
  - drivers are notified when a trip is assigned to them, and on every status change
- **Workflow state machine is enforced on direct saves** (not just the workflow UI):
  - skipping states (`Assigned → Completed`) or going backwards (`Completed → Assigned`) is rejected
  - a Driver can only perform `Assigned → In Progress` and `In Progress → Completed`
  - `Completed → Reconciled` and any `→ Cancelled` require the Fleet Manager
- **Fuel Log** doctype
  - Standalone fuel logs or logs tied to an open trip
  - `fill_up_yield` uses **distance since the previous fill-up** (not the last completed trip), so mid-trip fill-ups are measured correctly
  - `sanity_flag`: flagged `Suspicious` when a fill-up implies more than 2x or less than 0.3x the vehicle's average yield
  - `fuel_type`, `fuel_vendor` (station), and a computed `price_per_litre`
  - a fill-up's `odometer_at_fill` must fall inside its linked trip's odometer window
- **Trip Expense** doctype (tolls, parking, other) — on ERPNext sites **Create Expense Claim** / **Create Journal Entry** buttons push the expense into ERPNext
- **Validation is server-side** (never just client-side)
  - `end_odometer <= start_odometer` and `end_time < start_time` are rejected
  - `Fuel Log.odometer_at_fill < vehicle.current_odometer` is rejected (the vehicle cannot go backwards)
  - a new trip whose `start_odometer` does not match the vehicle's `current_odometer` is **warned** (off-system usage), never blocked
  - a trip on a vehicle other than the driver's `assigned_vehicle` is **warned**
- **Scheduled jobs** (daily, `hooks.py → scheduler_events`):
  - `flag_stale_trips` — notify for trips stuck `In Progress` > 24 h and trips assigned but never started > 24 h (deduplicated, no daily spam)
  - `check_vehicle_maintenance` — odometer/date-based service reminders
  - `check_license_expiry` — driver license expiry alerts (30-day window)
- **Six Query Reports**: Cost per Vehicle, Driver Mileage Report, Fuel Yield Trend, Flagged Trips Report, Fuel Price Trend, Fuel Cost per Driver
- **KPI Number Cards** on the Fleet Log workspace (Fuel Cost This Month, Trips Completed, Flagged Trips, Fleet Size)
- **Print formats** for Trip, Fuel Log and Trip Expense; a **Trip Log web form** for drivers; **REST API endpoints** (`fleet_log.api`) for mobile field capture; **CSV bulk import** helpers (`fleet_log.data_import`)
- **Branded web portal** at `/fleet_portal` — a role-aware dashboard (trips, fuel logs, vehicles, expenses, account) built on the **Bizaxl design system** (DM Sans, navy/mint/blue palette, Lucide icons, glassmorphism). Drivers get a scoped field portal; Fleet Managers get the full reconcile workflow. Uses the same whitelisted API, so every action is permission-checked server-side
- **Roles**: `Fleet Manager` (full access, may reconcile) and `Driver` (own trips / fuel logs only)
  - `permission_query_conditions` scopes Driver-role users to `driver = <their linked Driver record>`; fuel logs are visible to the driver who filled them **or** whose trip they belong to

---

## Works standalone on Frappe v15

```
bench new-app fleet_log            # or copy this app into apps/
bench --site <sitename> install-app fleet_log
```

> **Install note for bench ≥ 5.29 (uv):** bench 5.29+ installs app Python
> dependencies with `uv`, which refuses Frappe v15's transitive `gunicorn`
> git-URL dependency. The app already declares that requirement in
> `pyproject.toml`, so `bench get-app` works out of the box. If you still see
> `Failed to resolve dependencies for frappe ... gunicorn was included as a URL
> dependency`, either pull the latest app code, or install with pip instead:
>
> ```
> BENCH_DISABLE_UV=1 bench get-app https://github.com/Sudhakar1110/farm_log.git --skip-assets
> ```

When **ERPNext is not installed**, the app creates its own fallback masters:

- **Vehicle**: `registration_number` (required, unique), `vehicle_type`, `fuel_type`, `current_odometer` (read-only, auto-updated), `average_yield` (read-only, rolling average)
- **Driver**: `driver_name` (required), `user` (links to a system User for permission scoping), `license_number`, `license_expiry`, `contact_number`, `assigned_vehicle`

The fallback doctypes live in `fleet_log/fallback_doctypes/` (outside the standard doctype-sync path, which only scans `doctype/`) and are created by `install.py` only when ERPNext is absent — so they never conflict with ERPNext's own doctypes.

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
| Completed | Reconcile | Reconciled | Fleet Manager | — (locks the trip, rolls yield into the vehicle average) |
| Assigned / In Progress / Completed | Cancel Trip | Cancelled | Fleet Manager | — |

The workflow conditions are evaluated server-side, and the same rules (including the **source state** and **role** checks) are mirrored in `Trip.validate` so direct saves, scripts and the API are equally safe. Workflow email alerts are enabled — emails are sent when outbound email is configured. Cancelling a Reconciled trip re-rolls the vehicle's average yield.

---

## Reports

1. **Cost per Vehicle** — fuel + other costs per vehicle per month
2. **Driver Mileage Report** — distance driven per driver (with trip counts)
3. **Fuel Yield Trend** — `trip_yield` over time per vehicle (gradual decline = maintenance signal; sudden drop = theft/leak signal)
4. **Flagged Trips Report** — trips where `yield_flag != Normal` or any linked Fuel Log is `Suspicious`, for review during reconciliation
5. **Fuel Price Trend** — `price_per_litre` over time per vehicle / fuel type / vendor
6. **Fuel Cost per Driver** — total fuel spend, litres and average price per driver

Reports run identically on MariaDB and PostgreSQL (grouping is done in Python where needed).

## API, Web & Bulk Import

- **REST API** (`fleet_log.api`, whitelisted): `get_my_vehicles`, `create_trip`, `update_trip`, `log_fuel`, `get_my_trips`, `get_my_fuel_logs`, `get_my_expenses`, `create_expense`, `get_portal_bootstrap`, `get_drivers` — all respect the normal permission scoping, so they are safe for mobile/field clients.
- **Web portal**: `/fleet_portal` renders its own login screen and a role-aware app shell (Dashboard, Trips, Fuel Logs, Vehicles, Expenses, Account) styled with the Bizaxl design system (`fleet_log/www/fleet_portal/`).
- **Web form**: the published **Trip Log** web form (`/trip-log`) lets logged-in drivers create trips from the portal without desk access.
- **Bulk import**: `bench --site <site> execute fleet_log.data_import.import_trips_from_csv --kwargs '{"file_path": "/path/trips.csv"}'` (and `import_fuel_logs_from_csv`) with the column headers documented in `fleet_log/data_import.py`.

---

## Development

```
bench --site <sitename> migrate                # after pulling changes
bench --site <sitename> run-tests --app fleet_log
bench --site <sitename> execute fleet_log.utils.verify_install   # checklist of every doctype/report/workflow the app installs
```

The repo ships a GitHub Actions workflow (`.github/workflows/ci.yml`) that spins up a bench with Frappe v15 and runs the integration tests on every push/PR.

> **Install order still matters.** Install ERPNext *before* fleet_log (or on a site that already has it). If ERPNext is installed later, `after_migrate` logs a clear error pointing to a reinstall so the app can switch to ERPNext mode.

> **Editing a standard fixture?** Frappe skips re-importing a fixture unless its
> `"modified"` is *newer* than the record already in the site DB, and the
> fixtures in this app ship with a static `"modified"` date. Whenever you change
> any fixture (workspace, reports, workflow, chart, dashboard, print formats, web
> forms), **bump its `"modified"` to today** so `bench migrate` re-imports it on
> already-installed sites.

Project layout (standard bench app layout — `fleet_log/` is the importable package; `fleet_log/fleet_log/` is the module folder that `sync_for` scans for doctypes):

```
fleet_log/
├── hooks.py                        # scheduler, permissions, doc_events, install hooks
├── install.py                      # mode detection: fallbacks vs ERPNext custom fields
├── utils.py                        # is_erpnext_installed, yield math, permission hooks, schedulers
├── api.py                          # whitelisted REST endpoints (mobile field capture)
├── data_import.py                  # CSV bulk import helpers
├── config/desktop.py               # desk module icon/label
├── www/fleet_portal/               # branded web portal (Bizaxl design system)
├── fleet_log/                      # module "Fleet Log" (doctypes + fixtures)
│   ├── doctype/{trip, fuel_log, trip_expense, vehicle, driver}/
│   ├── workflow/trip_workflow/     # Trip workflow fixture
│   ├── reports/                    # the six query reports
│   ├── print_format/               # Trip / Fuel Log / Trip Expense print formats
│   ├── web_form/trip_log/          # driver-facing Trip Log web form
│   ├── workspace/fleet_log/        # workspace incl. KPI number cards
│   ├── dashboard_chart/            # Fuel Yield Trend chart
│   ├── dashboard_chart_source/     # chart data source (JS/Python)
│   └── dashboard/                  # Fuel Yield Trend dashboard (chart + KPI cards)
├── fallback_doctypes/              # Vehicle/Driver fixtures (standalone mode only)
├── tests/                          # integration tests
├── translations/                   # app translations (empty)
├── modules.txt / patches.txt       # module list / DB patches
├── MANIFEST.in / pyproject.toml / requirements.txt / README.md / license.txt
└── .github/workflows/ci.yml        # bench-based CI
```

## License

MIT
