# H1B backend setup (from scratch)

## Overview

You already have the project files. Follow these steps to run everything locally.

```
h1b-checker/
├── process_data.py      ← Data pipeline (run this first)
├── main.py              ← FastAPI app (run second)
├── models.py            ← DB models
├── database.py          ← DB config
├── requirements.txt     ← Dependencies
├── .env                 ← Edit this
└── data/                ← Put Excel files here
```

---

## Step 1: Install PostgreSQL (if needed)

### Windows
1. Download [PostgreSQL 14+](https://www.postgresql.org/download/windows/)  
2. Run the installer  
3. Save the password you set (important)  
4. Keep default port 5432  

### Mac (Homebrew)
```bash
brew install postgresql@14
brew services start postgresql@14
```

### Linux (Ubuntu/Debian)
```bash
sudo apt-get update
sudo apt-get install postgresql postgresql-contrib
sudo systemctl start postgresql
```

### Verify PostgreSQL
```bash
psql --version
# Example: psql (PostgreSQL) 14.5
```

---

## Step 2: Create the database

### Option A: Command line (recommended)

```bash
psql -U postgres

# Enter the password you set at install:
Password for user postgres:

# In psql:
CREATE DATABASE h1b_checker;
\q
```

### Option B: pgAdmin
1. Open pgAdmin  
2. Connect to the local server  
3. Right-click → Create → Database  
4. Name: `h1b_checker`  

---

## Step 3: Configure `.env`

Edit `h1b-checker/.env` for your PostgreSQL settings:

```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/h1b_checker
```

**Meaning:**
- `postgres` — DB user (often `postgres`)  
- `password` — password you set  
- `localhost` — host (local = `localhost`)  
- `5432` — default port (usually unchanged)  
- `h1b_checker` — database name (must match Step 2)  

**Example:**
```env
# User postgres, password mypassword
DATABASE_URL=postgresql://postgres:mypassword@localhost:5432/h1b_checker
```

---

## Step 4: Install Python dependencies

```bash
cd h1b-checker
pip install -r requirements.txt
```

**Typical output:**
```
Collecting fastapi==0.104.1
...
Successfully installed fastapi-0.104.1 uvicorn-0.24.0 sqlalchemy-2.0.23 ...
```

---

## Step 5: Prepare Excel data

### Download H1B data

1. Open [US Department of Labor — Foreign Labor](https://www.dol.gov/agencies/eta/foreign-labor/performance)  
2. Download H1B LCA Disclosure Data (Excel)  
3. Filenames often look like `h1b_disclosure_data_2024.xlsx`  

### Copy into `data/`

```bash
cp ~/Downloads/h1b_disclosure_data_2024.xlsx ./data/
ls -la data/
# Should list your .xlsx file
```

---

## Step 6: Import data

The import script will:

1. Read all `.xlsx` files under `data/`  
2. Clean (trim, uppercase names)  
3. Keep certified cases  
4. Aggregate by employer name  
5. Write to PostgreSQL  

```bash
python process_data.py
```

**Example output (illustrative; actual script messages are in English):**

```
============================================================
🚀 H1B data processing script
============================================================
...
✅ All steps completed.
============================================================
```

### Common errors

#### Error 1: No Excel files
```
⚠️  No .xlsx files found in 'data'
```
**Fix:** Put `.xlsx` files under `data/`.

#### Error 2: `DATABASE_URL not found`
**Fix:** Ensure `.env` exists and contains `DATABASE_URL=...`.

#### Error 3: PostgreSQL connection failed
```
psycopg2.OperationalError: could not connect to server
```
**Fix:**
```bash
psql -U postgres -c "SELECT 1"
# If not running:
# Windows: start PostgreSQL service
# Mac: brew services start postgresql@14
# Linux: sudo systemctl start postgresql
```

#### Error 4: Missing columns
```
⚠️  Missing columns: {'VISA_CLASS', 'BEGIN_DATE'}
```
**Fix:** Column names differ from the script. Adjust `REQUIRED_COLUMNS` in `process_data.py` to match your file.

---

## Step 7: Start FastAPI

```bash
python main.py
```

Or with reload:

```bash
uvicorn main:app --reload
```

**Typical output:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
INFO:     Press CTRL+C to quit
```

**Server is up.**

---

## Step 8: Test the API

### Browser

- **Swagger UI**: http://localhost:8000/docs  
- **ReDoc**: http://localhost:8000/redoc  

### Swagger

1. Open http://localhost:8000/docs  
2. Open `/check`  
3. Click **Try it out**  
4. Set `company=Google`  
5. Click **Execute**  

**Example response:**
```json
{
  "found": true,
  "employer_name": "GOOGLE LLC",
  "h1b_count": 8420,
  "sponsors_h1b": true
}
```

### curl

```bash
curl "http://localhost:8000/check?company=Google"
curl "http://localhost:8000/search?q=amazon&limit=5"
curl "http://localhost:8000/stats"
curl "http://localhost:8000/health"
```

### Python

```python
import requests

r = requests.get("http://localhost:8000/check?company=Google")
print(r.json())

r = requests.get("http://localhost:8000/search?q=amazon&limit=5")
print(r.json())

r = requests.get("http://localhost:8000/stats")
print(r.json())
```

---

## How the pipeline works

```
Your Excel files
    ↓
[process_data.py reads]
    ↓
Clean (trim, uppercase)
    ↓
Filter (Certified only)
    ↓
Aggregate by employer name
    ↓
PostgreSQL
    ↓
[FastAPI starts]
    ↓
[API handles requests]
    ↓
[Query PostgreSQL]
    ↓
[Return JSON]
    ↓
[Your client]
```

---

## What each file does

| File | Role | When to run |
|------|------|-------------|
| `requirements.txt` | Dependency list | `pip install -r requirements.txt` |
| `.env` | DB URL and secrets | Edit once |
| `models.py` | Table definitions | Imported by app (no standalone run) |
| `database.py` | Engine and sessions | Imported by app |
| `process_data.py` | Excel → DB | `python process_data.py` |
| `main.py` | FastAPI app | `python main.py` |

---

## Re-importing data

To reload data (e.g. replace old rows):

```bash
# Option 1: drop table and re-import
psql -U postgres -d h1b_checker -c "DROP TABLE IF EXISTS employers;"
python process_data.py
```

Or run import again — `process_data.py` uses upsert, so repeated runs update existing employers.

```bash
python process_data.py
```

---

## Debugging

### Inspect the database

```bash
psql -U postgres -d h1b_checker
\dt
SELECT COUNT(*) FROM employers;
SELECT * FROM employers LIMIT 5;
SELECT * FROM employers WHERE employer_name LIKE '%GOOGLE%';
\q
```

### FastAPI logs

Each request appears in the console, for example:

```
INFO:     127.0.0.1:54321 - "GET /check?company=Google HTTP/1.1" 200 OK
```

### Python version

```bash
python --version
# Use 3.9 or newer
```

---

## Deploying to a server

After local testing, you can deploy (AWS, GCP, etc.):

1. Install Python and PostgreSQL on the server  
2. Upload the project  
3. Set `.env` with the server DB URL  
4. `pip install -r requirements.txt`  
5. `python process_data.py`  
6. Run with gunicorn + Uvicorn workers in production  

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

---

## Short FAQ

**Q: How long does import take?**  
A: Often 30–60 seconds per million rows, depending on hardware.

**Q: Can I run import multiple times?**  
A: Yes. Upsert updates existing employers and inserts new ones.

**Q: Excel columns don’t match?**  
A: Edit `REQUIRED_COLUMNS` in `process_data.py`, or rename columns in Excel.

**Q: How do I call the API from a frontend?**  
A: Use `fetch` or `axios`:

```javascript
fetch('http://localhost:8000/check?company=Google')
  .then(res => res.json())
  .then(data => console.log(data))
```

**Q: Expose the API on the internet?**  
A: By default it listens locally. For remote access: bind `0.0.0.0`, open firewall rules, and in production use a reverse proxy (e.g. Nginx) with TLS.

---

## Still stuck?

1. Read the error message carefully  
2. Double-check `.env`  
3. Confirm PostgreSQL: `psql -U postgres -c "SELECT 1"`  
4. Re-read this guide  

Good luck with your setup.
