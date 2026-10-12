# Inventory AI web client

This folder contains the React application built with Vite. For full-stack prerequisites and configuration, start with the repository [developer setup](../docs/DEVELOPER_SETUP.md).

## Local development

Install dependencies and start the API first as described in the developer setup. Then, from this directory:

```powershell
npm ci
$env:VITE_API_URL = "http://127.0.0.1:8000"
npm run dev -- --host 127.0.0.1
```

Open the local URL printed by Vite. The API URL must match the backend host and port, and the current CORS configuration allows local Vite origins on port 5173.

## Checks

```powershell
npm run lint
npm run build
```

Do not commit `node_modules/`, `dist/`, local environment files, or credentials.
