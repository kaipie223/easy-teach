# Remote PostgreSQL Development

The development database is PostgreSQL on the application server. The local
worktree connects through an SSH tunnel; PostgreSQL does not need to be
exposed directly to the public network.

## Start the tunnel

Keep this command running in a separate terminal:

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -L 15432:127.0.0.1:5432 -i "$env:USERPROFILE\.ssh\id_ed25519" ubuntu@<server-host>
```

The local `.env` file is ignored by Git and should contain the development
connection string:

```env
DATABASE_URL=postgresql+psycopg://easy_teach_dev_app:<password>@127.0.0.1:15432/easy_teach_shared
```

## Apply the schema

Run migrations from the repository root after the tunnel is available:

```powershell
uv run alembic upgrade head
uv run alembic check
```

The shared development database is `easy_teach_shared`. The previous
development database `easy_teach_dev` is kept separate, and the legacy server
database `easy_teach` is a different application; neither should be used by
this worktree.

## Run the application

```powershell
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

The backend health endpoint reports `engine: postgresql` when the tunnel and
database configuration are active.
