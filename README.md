# H1B Checker — H1B LCA data API

A FastAPI + PostgreSQL backend for querying U.S. Department of Labor H1B LCA disclosure data.

## Project layout

```
h1b-checker/
├── process_data.py      ← Import script (read Excel, clean, load PostgreSQL)
├── main.py              ← FastAPI app and routes
├── models.py            ← SQLAlchemy models
├── database.py          ← DB connection
├── requirements.txt     ← Python dependencies
├── .env                 ← Environment variables
├── data/                ← Excel files
└── README.md            ← This file
```

---

## Quick start

### Requirements
- **Python 3.9+**
- **PostgreSQL 12+** (installed and running)
- **pip**

### Step 1: Install dependencies

```bash
cd h1b-checker
pip install -r requirements.txt
```

### Step 2: Create the PostgreSQL database

#### Option A: Command line

```bash
psql -U postgres

# In psql:
CREATE DATABASE h1b_checker;
\q
```

#### Option B: GUI (e.g. pgAdmin)
1. Open pgAdmin  
2. Create a new database named `h1b_checker`

### Step 3: Configure `.env`

Edit `.env` with your connection string:

```env
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/h1b_checker
```

**Replace:**
- `postgres` — PostgreSQL username  
- `your_password` — PostgreSQL password  
- `localhost` — host (often `localhost`)  
- `5432` — port (default 5432)  
- `h1b_checker` — database name  

### Step 4: Prepare Excel files

1. Download H1B LCA data (Excel) from the [U.S. Department of Labor](https://www.dol.gov/agencies/eta/foreign-labor/performance).  
2. Place `.xlsx` files under `./data/`.

```bash
ls -la data/
# Example:
# h1b_data_2023.xlsx
# h1b_data_2024.xlsx
```

### Step 5: Import data

```bash
python process_data.py
```

**Example output (aligned with the English script):**

```
============================================================
🚀 H1B data processing script
============================================================
📂 Found 2 xlsx file(s):
...
✅ All steps completed.
============================================================
```

### Step 6: Run the API

```bash
python main.py
```

Or with auto-reload:

```bash
uvicorn main:app --reload
```

**Example:**

```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Press CTRL+C to quit
```

---

## API endpoints

### 1. Check one company

**Request:**
```bash
curl "http://localhost:8000/check?company=Google"
```

**Response:**
```json
{
  "found": true,
  "employer_name": "GOOGLE LLC",
  "h1b_count": 8420,
  "sponsors_h1b": true
}
```

### 2. Search companies (fuzzy)

**Request:**
```bash
curl "http://localhost:8000/search?q=amazon&limit=5"
```

**Response:**
```json
{
  "results": [
    {
      "employer_name": "AMAZON.COM INC",
      "h1b_count": 7850
    },
    {
      "employer_name": "AMAZON CORPORATE LLC",
      "h1b_count": 2300
    }
  ],
  "total": 2
}
```

### 3. Stats

**Request:**
```bash
curl "http://localhost:8000/stats"
```

If not implemented, this route may return 404 until you add it.

### 4. Health

**Request:**
```bash
curl "http://localhost:8000/health"
```

**Response:**
```json
{
  "status": "healthy",
  "service": "H1B Checker API"
}
```

---

## Interactive API docs

After the server starts:

- **Swagger UI**: http://localhost:8000/docs  
- **ReDoc**: http://localhost:8000/redoc  

---

## Database schema

```sql
CREATE TABLE employers (
    id SERIAL PRIMARY KEY,
    employer_name TEXT UNIQUE NOT NULL,
    h1b_count INTEGER,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Columns:**
- `id` — primary key  
- `employer_name` — unique company name  
- `h1b_count` — certified H1B count (aggregated)  
- `last_updated` — last update time  

---

## Data flow

```
Excel files
    ↓
[process_data.py]
    ↓
Clean & aggregate
    ↓
PostgreSQL
    ↓
[FastAPI server]
    ↓
[API routes] → clients
```

---

## FAQ

### Q1: `DATABASE_URL not found`
**Fix:** Put `.env` in the project root and set `DATABASE_URL` correctly.

### Q2: PostgreSQL connection timeout
**Fix:**
1. Confirm PostgreSQL is running: `psql -U postgres -c "SELECT 1"`  
2. Check host, port, user, and password in `.env`

### Q3: Excel read errors
**Fix:**
1. Ensure valid `.xlsx` (open in Excel if unsure)  
2. Check permissions: `ls -la data/`  
3. Test pandas: `python -c "import pandas as pd; df = pd.read_excel('data/file.xlsx'); print(df.shape)"`

### Q4: Re-import from scratch
**Fix:**
```bash
psql -U postgres -d h1b_checker -c "DROP TABLE IF EXISTS employers;"
python process_data.py
```

---

## Performance notes

- **Import:** ~30–60 seconds per million rows (hardware-dependent)  
- **Queries:** typically milliseconds (indexed names)  
- **Suggested:** PostgreSQL 12+ and 4GB+ RAM  

---

## Production hints

1. **Secrets:** use a secrets manager; never commit real passwords  
2. **CORS:** configure if browsers call from another origin  
3. **Auth:** add API keys or OAuth if needed  
4. **Logging:** centralize (e.g. ELK)  
5. **Monitoring:** use APM where appropriate  

---

## Support checklist

1. Read the error message  
2. Verify `.env`  
3. Confirm PostgreSQL is running  
4. Re-read the FAQ above  

---

## License

MIT License

---

Happy building.
