# Nail123 Backend

FastAPI backend for NailMuse.

## Vercel

Deploy this repository as a Vercel Python/FastAPI project. Vercel uses `api/index.py`
as the serverless entrypoint and `requirements.txt` as the deployment dependency list.

Required environment variables should be configured in Vercel Project Settings, not
committed to GitHub. Copy the values from the local `backend/.env` file into Vercel.

## Health Check

After deployment, open:

```text
https://<your-backend-domain>/api/health
```

Expected response:

```json
{"ok": true}
```

## Notes

- `backend/.env`, `backend/.meituan_session.json`, and `xhs/data/xhs_storage_state.json`
  are intentionally ignored.
- `models/` and `services/` are kept in the GitHub repository for source completeness,
  but excluded from the Vercel serverless bundle through `.vercelignore`.
