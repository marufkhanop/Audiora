# Audiora — Audiobook Streaming Platform

A Django audiobook web app. Visitors browse the library, listeners create an
account (email or Google), save favorites, and resume playback where they left
off. Library, users, storage accounts, and uploads are managed from Django
admin.

## What's included

- Guest homepage with a featured hero, search, category chips, and per-category
  rows; signed-in listeners get a personalized library homepage.
- Listener accounts, optional Google sign-in, **Recent** listening history, and
  a **Saved** page with a bookmark button on each audiobook detail page.
- Persistent audio player with resume positions, ±15s skips, speed/volume
  memory, media-session support, and a collapsible desktop bar.
- Hybrid media storage: **Local Server Storage** by default, plus any number of
  named Cloudinary accounts. Pick the destination per upload in admin.
- Storage dashboard with usage, per-account books, and safe account deletion.
- Admin and listener sessions use separate cookies, so logging into one does
  not log you out of the other.

---

## 1. Install (one time)

Open a terminal in this project folder, then run these one by one.

**Windows:**

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Linux / macOS:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Use Python 3.12+ for the tested Django 6.1. Django 5.x also works on Python
3.10+. Cloudinary credentials are optional — Local Server Storage works with
no cloud account — but install all six packages because migrations reference
the Cloudinary storage classes.

You should see `(venv)` at the start of your terminal line.
That means the environment is active. Every command below runs
with the venv activated — just activate it again if you open a
new terminal.

## 2. Create the database, administrator, and optional demo library

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo  # optional: adds 8 demo audiobooks
```

- `migrate` creates the database file (`db.sqlite3`)
- `createsuperuser` prompts you to set a private administrator username and password
- `seed_demo` adds demo audiobooks only; it does not create an administrator account
- Skip `seed_demo` if you want an empty library

## 3. Run the server

```bash
python manage.py runserver
```

Open in your browser: **http://127.0.0.1:8000**

To stop the server press `Ctrl + C`.

## Administration and private configuration

Create an administrator locally with `python manage.py createsuperuser`.
Keep administrator credentials and deployment secrets private; do not put
real credentials or `.env` files in source control. Configure production
settings directly on the hosting server.
## Useful commands

```bash
python manage.py seed_demo                # add demo audiobooks
python manage.py cleanup_media            # delete unused local media/cache files
python manage.py migrate_to_cloudinary    # optional: move legacy local files to Cloudinary
python manage.py check                    # verify everything is OK
```

## Notes

- `.env.example` documents the main settings.
- If you copy this project, you can delete `db.sqlite3` and local `media/` to
  start fresh.
- Never leave a development server running in production; use a proper WSGI/
  ASGI server and set `DEBUG=false`.
- Cloudinary usage statistics can lag by about a day on the storage dashboard.

### Running on Android (Termux)

Android shared storage (`/sdcard`) doesn't allow the symlinks a venv needs,
so create the venv outside the project folder and point at it directly:

```bash
python3 -m venv ~/amber-venv
~/amber-venv/bin/pip install -r requirements.txt
~/amber-venv/bin/python manage.py migrate
~/amber-venv/bin/python manage.py seed_demo
~/amber-venv/bin/python manage.py runserver
```

Everything else (models, templates, uploads) is already platform-safe —
`fcntl`-based locking is auto-skipped on Windows and on filesystems
that don't support it.
