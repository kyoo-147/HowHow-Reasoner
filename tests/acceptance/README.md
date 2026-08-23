# Live web acceptance

From the repository root, run the API on `127.0.0.1:8000` with a temporary
project root and the Vite cockpit on `127.0.0.1:4173`, then open
`http://127.0.0.1:4173/` in a disposable browser context. Verify ownership with
`netstat -ano` and PowerShell `Get-CimInstance Win32_Process` before teardown.

The focused automated browser boundary checks are:

```bash
uv run pytest tests/acceptance/test_browser_boundary.py
```

Acceptance evidence belongs in the gitignored `.firstmate/evidence/` tree.
Terminate only the verified API and Vite PIDs in a guarded `finally` block; do
not stop shared services.
