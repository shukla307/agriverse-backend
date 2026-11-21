# Agriverse backend (Django REST)

> **Two versions live in this repo:**
> - **`v1` branch: the new backend, where all work happens now.** It follows the plan in `~/agriverse/docs/` (05 data model, 06 API). It's described in the next section.
> - **`master`: what's live on Render today** (the old app). Everything after "Old backend (master)" describes it. Don't push `v1` to `master` until launch day: the new API would break the app farmers already have.

## v1 (new backend)

- **Python 3.11** venv at `./venv`.
- **PostgreSQL only**, also locally: Homebrew `postgresql@14`, database `agriverse_dev`. The `.env` has `DATABASE_URL=postgres:///agriverse_dev` and `DEBUG=True`. SQLite is not supported; the settings refuse to start without `DATABASE_URL`.
- Apps:
  - `core/`: settings and `errors.py`. Every error is `{"error": {"code", "fields"}}`; raise `ApiError("code", status)`.
  - `accounts/`: phone-first `User`, with no username or password for farmers. `firebase.py` verifies Firebase ID tokens with only the project ID, so no secret key file is needed. `phone.py` holds the `+91[6-9]` rule.
  - `catalog/`: `JobCategory` (the 6 Home jobs + अन्य) and `MachineType` (power/implement, `search_aliases` as a
    Postgres array, lower-cased on save, `default_hours_per_acre`). Seeded by migration `0002_seed_catalogue`
    (glossary §7, decision §4, 05 §3.4a); JCB is inactive. Admin pages for both.
  - `listings/`: `Listing` (one machine; place copied from an address or a map pin; no operator field,
    `operator_included` = power machine), `ListingOption` (attachment + unit + price; inactive = removed, kept for
    bookings), `Photo` (0–5, first = cover), `WeeklyHours` (sets `has_schedule`; bookable = active + schedule).
    All rules in `serializers.py`. Admin: **"new machine types to add"** (proxy `NewTypeRequest`) + action
    "Move to a real machine type" (`moving.py`). The machine type can't change after create (decision §18).
  - `core/storage.py` `bucket_path(url)`: the one check for "is this link a file in OUR bucket?".
    `core/pagination.py` `Pages`: 20 per page → `{count, next_page, results}`.
  - The old `api/` and `equipment/` apps (and the commented-out `core/firebase.py`) were **deleted on `v1`** (cleanup, 2026-11-19). They exist only on `master`.
- Endpoints so far: `POST /api/auth/firebase/`, `POST /api/auth/token/refresh/`, `POST /api/auth/logout/`, `GET`/`PATCH /api/me/`, `GET`/`POST /api/me/addresses/`, `PATCH /api/me/addresses/{id}/` (own, not deleted; edits the same row), `POST /api/listings/`, `GET /api/listings/mine/`, `GET`/`PATCH`/`DELETE /api/listings/{id}/` (GET: owner →
  owner view, anyone else → renter view without phone/landmark/pin; PATCH/DELETE owner only), `…/options/`, `…/photos/` (+ `order/`), `PUT …/weekly-hours/`, `GET /api/search/` (renters: radius, job/machine/q/unit/HP, nearest first; empty → hint, the job chip counts as a filter like unit/HP; `listings/search.py`; any-spelling rules in `catalog/matching.py`, cases in `catalog/tests/spelling_cases.json`, shared with the app), `GET /api/machine-types/` (ETag = fingerprint of the content, so any admin edit
  changes it; `If-None-Match` → 304), `GET /api/app-config/`.
- `Address` (घर/खेत/अन्य, India-only pin, village may be blank, max 20 per user). The first one, or `make_home`, becomes `User.home_address`. Login's `is_new_user` = no name **or** no home address.
- `PATCH /api/me/` only accepts a `photo_url` pointing to `profiles/<firebase_uid>/avatar.jpg` in `FIREBASE_STORAGE_BUCKET` (default `<project>.firebasestorage.app`), so nobody can attach another person's photo. The full plan is in `~/agriverse/docs/06-api.md`.
- JWT: access 30 min, refresh 365 days, rotating + blacklist. Default permission is `IsAuthenticated`; public views set `AllowAny` + `authentication_classes = []`. Throttles are in settings (`auth_firebase` 10/h).

```bash
cd ~/agriverse-backend && source venv/bin/activate
pip install -r requirements-dev.txt          # requirements.txt = production only
python manage.py migrate
python manage.py runserver 0.0.0.0:8000      # emulator reaches it at http://10.0.2.2:8000
python -m pytest                             # Firebase is faked; uses a throwaway test_agriverse_dev DB
```

- **Speed check data:** `python manage.py seed_listings --count 1000` puts fake listings around Prayagraj into the
  dev DB (DEBUG only; only tractors get attachments); `seed_listings --delete` removes exactly those. Run `ANALYZE` after seeding, or Postgres
  plans with stale row counts.
- **Django admin** (`/admin/`, incl. "new machine types to add"): create your own login once with
  `python manage.py createsuperuser` (phone like +919XXXXXXXXX + a strong password; Claude never sets it).
- `requirements.txt` is a full pin list generated from the venv (minus dev tools). After adding a package, regenerate it the same way and check with `pip install --dry-run --ignore-installed -r requirements.txt`.
- `.claude/settings.json` blocks reading **and writing** any `.env*` file, including `.env.example`. Ask the owner to edit those.

---

# Old backend (master), live today

The REST API for the Agriverse Flutter app (`~/agriverse_app`). Users can sign up, log in, list farm equipment or crop residue for rent, and search for equipment near a location.

- Django 4.2, Django REST Framework 3.16, `djangorestframework_simplejwt` 5.5
- Deployed on Render at `https://agriverse-backend-zmmu.onrender.com`, served by gunicorn + WhiteNoise. `runtime.txt` pins Python 3.11.9.
- The local `venv` runs **Python 3.9.6**, which doesn't match `runtime.txt`.

## Layout

```
core/        Django project: settings.py, urls.py (root router), wsgi/asgi
             firebase.py (fully commented out, Firebase Admin is no longer used)
api/         Custom User model + auth endpoints (signup, login, me, forgot password)
equipment/   Equipment model + listing/search endpoints
manage.py
db.sqlite3   Local SQLite DB (gitignored)
staticfiles/ collectstatic output (gitignored)
```

Both `api/admin.py` and `equipment/admin.py` are empty, so no models are registered in Django admin. A lot of commented-out older code remains in `views.py` and `serializers.py`. Treat it as dead code.

## Apps

### `core` (project package)
- `settings.py` loads `.env` via `python-dotenv`. Config comes from these env vars:
  - `SECRET_KEY` (falls back to an unsafe dev value)
  - `DEBUG` (the string `"True"` turns it on; default off)
  - `ALLOWED_HOSTS` (comma-separated; default `localhost`)
  - `DATABASE_URL`, parsed by `dj_database_url`. **No default.** If it's unset, Django has no database configured.
- `AUTH_USER_MODEL = "api.User"`
- `CORS_ALLOW_ALL_ORIGINS = True`
- `rest_framework_simplejwt.token_blacklist` is installed, but no URLs use it.
- `urls.py`: `GET /` returns `{"status": "API is running"}`. `/admin/`, `/api/` → `api.urls`, `/api/equipment/` → `equipment.urls`.

### `api`: users and auth
**Model `User`** (`api/models.py`) extends `AbstractUser`:
| field | type |
|---|---|
| `phone_number` | CharField(20), unique, nullable |
| `location` | CharField(255), nullable (free-text address) |
| `latitude`, `longitude` | FloatField, nullable |

Users can log in with either `username` or `phone_number`. The app stores phone numbers as `+91XXXXXXXXXX`.

### `equipment`: rental listings
**Model `Equipment`** (`equipment/models.py`):
| field | type / notes |
|---|---|
| `owner` | FK → User, `related_name="equipments"`, CASCADE |
| `equipment_name` | CharField(150) |
| `price_per_day` | Decimal(10,2) |
| `description` | TextField, blank ok |
| `contact_number` | CharField(15), nullable. On create, defaults to the owner's `phone_number` |
| `location` | CharField(255), nullable. On create, defaults to the owner's `location` |
| `latitude`, `longitude` | Float, nullable. **Always copied from the owner's profile on create.** Read-only in the serializer |
| `image_url` | URLField, nullable. A Firebase Storage URL uploaded by the app |
| `available` | bool, default True (forced to True on create) |
| `created_at` | auto_now_add |

`EquipmentSerializer` exposes every field above except `owner`. It adds a read-only `owner_username` instead.

## Auth

- **JWT through SimpleJWT.** `DEFAULT_AUTHENTICATION_CLASSES` is JWTAuthentication only. Send the header `Authorization: Bearer <access>`.
- The access token lasts 60 minutes and the refresh token lasts 7 days.
- Tokens are issued only by `POST /api/auth/login/`, using `RefreshToken.for_user`. **There is no token-refresh or logout/blacklist endpoint.**
- No default permission class is set, so each view sets its own (`AllowAny` or `IsAuthenticated`).
- Firebase phone-OTP login and reset were removed in commit `7a28e99`. The code is still there, commented out.

## API endpoints

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | `/` | none | (none) | `{"status": "API is running"}` |
| POST | `/api/auth/signup/` | none | `username, first_name, last_name, phone_number, location, password, confirm_password, latitude?, longitude?` | 201 `{id, username}`. 400 returns field errors |
| POST | `/api/auth/login/` | none | `identifier` (username or phone), `password` | 200 `{user:{id,username,phone_number}, tokens:{refresh,access}}`. 400 returns `non_field_errors: ["Invalid credentials."]` |
| GET | `/api/auth/me/` | JWT | (none) | `{id, username, phone_number, first_name, last_name, location, latitude, longitude}` |
| POST | `/api/auth/check-phone/` | none | `phone_number` | 200 `{"detail":"phone registered"}` or 404 |
| POST | `/api/auth/forgot-password/` | none | `phone_number, new_password, confirm_password` | 200 `{"detail":"Password updated successfully."}` |
| GET | `/api/equipment/` | JWT | `?search=` (name icontains) | list of all equipment, newest first (no pagination) |
| POST | `/api/equipment/` | JWT | `equipment_name, price_per_day, description?, contact_number?, location?, image_url?` | 201 equipment. Owner, lat/lng and contact/location defaults come from `request.user` |
| GET | `/api/equipment/mine/` | JWT | (none) | list of the current user's equipment |
| GET/PUT/PATCH/DELETE | `/api/equipment/mine/<id>/` | JWT | equipment fields | Only the owner's own items; anyone else's id returns 404. DELETE returns 204 |
| GET | `/api/equipment/search/` | none | `lat`, `lng` (required), `q`, `distance` (km, default 10), `page` | `{message, page, total_pages, total_results, results:[…]}` |
| (any) | `/admin/` | Django session | (none) | Django admin (no models registered) |

How the search endpoint (`equipment/views.py`, `EquipmentSearchView`) works:
- It filters to `available=True` and name `icontains q`.
- It skips items with no lat/lng and computes haversine distance in km.
- It returns items within `distance`, sorted by distance. If none are in range, it returns **all** items sorted by distance, with a `message` saying so.
- It paginates **7 per page** with Django `Paginator`.
- Each result has these fields: `id, equipment_name, price_per_day, distance_km, available, contact_number, description, location, image_url, owner_username, first_name, last_name`.
- If nothing matches the name filter, it returns `{"message":"No equipment found","results":[]}`, with no `total_pages`.
- `haversine()` contains leftover debug `print`s.

Known gotchas:
- `ForgotPasswordView` has no verification step. Anyone who knows a registered phone number can set that account's password. (The serializer also saves the password twice.)
- `/api/auth/check-phone/` isn't used by the live app.

## Running locally

```bash
cd ~/agriverse-backend
source venv/bin/activate
pip install -r requirements.txt

# .env doesn't set DATABASE_URL, so pass it explicitly for local SQLite:
export DATABASE_URL=sqlite:///db.sqlite3
export DEBUG=True
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

- Without `DATABASE_URL`, `manage.py check` passes but anything that touches the DB fails.
- Android emulator → host: `http://10.0.2.2:8000`. A physical device needs the machine's LAN IP and `ALLOWED_HOSTS` to include it.
- **Never read or print `.env` or `serviceAccountKey.json`.** `.claude/settings.json` denies access to both.
- Production: Render builds from `requirements.txt` and runs gunicorn `core.wsgi`. `DATABASE_URL`, `SECRET_KEY`, `ALLOWED_HOSTS` and `DEBUG` are set in the Render environment. Run `python manage.py collectstatic` for WhiteNoise.

## Testing

```bash
source venv/bin/activate
DATABASE_URL=sqlite:///db.sqlite3 python manage.py test
```

- `api/tests.py` and `equipment/tests.py` are empty stubs, so this currently runs 0 tests.
- With a sqlite `DATABASE_URL`, Django builds a throwaway test DB. **Don't run tests with `DATABASE_URL` pointed at the production Postgres.**
- Quick manual checks:
  ```bash
  curl -X POST localhost:8000/api/auth/login/ -H 'Content-Type: application/json' \
       -d '{"identifier":"<user>","password":"<pw>"}'
  curl localhost:8000/api/auth/me/ -H "Authorization: Bearer <access>"
  curl "localhost:8000/api/equipment/search/?lat=28.6&lng=77.2&distance=30&q=tractor"
  ```

## Changing the API
Any endpoint or response-shape change must also be made in `~/agriverse_app/lib/services/api_service.dart` and/or `auth_service.dart`, plus the screens that read the fields. `~/agriverse/ARCHITECTURE.md` maps each endpoint to the screens that use it. New model fields need `python manage.py makemigrations` and a committed migration.
