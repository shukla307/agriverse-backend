# Agriverse backend (Django REST)

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
