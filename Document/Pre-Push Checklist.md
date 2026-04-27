# Pre-Push Checklist

Complete all applicable checks before pushing to `main`. Render and Vercel deploy automatically on push — mistakes go live immediately.

---

## 1. Database Schema Changes

**Required if you modified any SQLAlchemy model in `StorageHelperDataStorageService/app/models/`.**

```bash
cd StorageHelperDataStorageService
venv\Scripts\activate          # Windows
pip install alembic==1.13.1    # skip if already installed

# Generate migration file
alembic revision --autogenerate -m "short description of change"
```

Then:
1. Open the generated file in `migrations/versions/`
2. Review the `upgrade()` and `downgrade()` functions — confirm they match your intent
3. Watch for false positives (Alembic sometimes generates spurious changes for JSON columns or indexes)
4. Stage and commit the migration file along with your model changes

```bash
git add migrations/versions/<new_file>.py
git add app/models/<changed_model>.py
```

> **If modifying an existing production database** (e.g. Supabase) without redeploying from scratch,
> go to Supabase Dashboard → SQL Editor and run the migration SQL manually first,
> then update `alembic_version` to the new revision ID.

---

## 2. Backend API Changes (DataStorageService / AIOrchestraService)

- [ ] New endpoints are registered in `main.py` or the router
- [ ] New required environment variables are added to Render's environment settings
- [ ] No hardcoded secrets, API keys, or local paths in code

---

## 3. Frontend Changes (WebService)

- [ ] New environment variables are added to Vercel's environment settings
- [ ] No `console.log` left in production-facing code
- [ ] Tested the feature locally before pushing

---

## 4. General

- [ ] No uncommitted `.env` files
- [ ] No debug flags left enabled (e.g. `echo=True` in SQLAlchemy engine)
- [ ] If a new service dependency was added, `requirements.txt` (backend) or `package.json` (frontend) is updated

---

## 5. Rollback Procedure

If a push introduces a bug, roll back in this order (frontend first, DB last):

1. **Vercel** → Dashboard → Deployments → find last good deploy → Redeploy
2. **Render** → Dashboard → service → Deploys → find last good deploy → Rollback
3. **Database** (only if schema changed) → run in Supabase SQL Editor:
   ```bash
   # locally to get the SQL:
   alembic downgrade -1
   ```
   or manually run the `downgrade()` SQL from the migration file.

**Prevention — keep migrations forward-compatible:**
- Only ADD columns/tables, never DROP or RENAME in the same deploy as code changes
- A new nullable column with a default is safe; dropping a column requires a separate deploy after the old code is retired

---

## What Happens After Push

| Service | Trigger | Action |
|---|---|---|
| Render (DataStorageService) | push to `main` | runs `alembic upgrade head` then starts server |
| Render (AIOrchestraService) | push to `main` | restarts with new code |
| Vercel (WebService) | push to `main` | rebuilds and redeploys frontend |
| Supabase | — | **not automatic** — apply schema changes manually or via migration as above |
