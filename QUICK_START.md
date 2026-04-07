# Quick start (5 minutes)

## Prerequisites
- ✅ Python 3.9+
- ✅ PostgreSQL installed and running
- ✅ H1B Excel file(s) downloaded

## Five steps

### 1. Create the database
```bash
psql -U postgres
# Enter your password, then run:
CREATE DATABASE h1b_checker;
\q
```

### 2. Edit `.env`
Set your database password in `.env`:
```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/h1b_checker
```

### 3. Install dependencies
```bash
cd h1b-checker
pip install -r requirements.txt
```

### 4. Import data
Put Excel files in `data/`, then:
```bash
python process_data.py
```

### 5. Start the API
```bash
python main.py
# or
uvicorn main:app --reload
```

## Try the API

In your browser:
- **API docs**: http://localhost:8000/docs
- **Check Google**: http://localhost:8000/check?company=Google
- **Search Amazon**: http://localhost:8000/search?q=amazon&limit=5
- **Stats** (if implemented): http://localhost:8000/stats

## Quick troubleshooting

| Issue | What to try |
|------|-------------|
| PostgreSQL connection fails | Check `.env` password; test with `psql -U postgres` |
| No Excel files found | Put `.xlsx` files under `data/` |
| Missing packages | Run `pip install -r requirements.txt` |
| Port 8000 in use | `uvicorn main:app --port 8001` |

## Layout

```
h1b-checker/
├── main.py                  ← API server
├── process_data.py          ← Data import
├── models.py                ← DB models
├── database.py              ← DB config
├── requirements.txt         ← Dependencies
├── .env                     ← Env (set password)
├── data/                    ← Put Excel files here
├── SETUP_GUIDE.md           ← Detailed setup guide
├── README.md                ← Full documentation
└── QUICK_START.md           ← This file
```

---

**Need more detail?** See `SETUP_GUIDE.md`.
