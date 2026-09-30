# ADLV Media Streamer desktop app

This is a native Python window around the existing ADLV frontend. It loads the
same frontend pages from the
running FastAPI server, so the desktop app has the same visual design and uses
the same authentication, media, progress, admin, and watch-together APIs.

## Run

Start the backend as usual, then from the repository root run:

```powershell
python -m pip install -r frontend/desktop_app/requirements.txt
python frontend/desktop_app/main.py --url http://127.0.0.1:8000
```

For desktop diagnostics, run the Python app directly. This does not require
rebuilding the executable; diagnostics are enabled automatically and WebView,
URL, bridge, and fullscreen events are printed to the terminal:

```powershell
python frontend/desktop_app/main.py
```

The backend URL can also be provided with `ADLV_BACKEND_URL`. The launcher
checks these sources in order: `--url`, environment variables
(`ADLV_BACKEND_URL`, `BACKEND_URL`, `API_URL`, `PREVIEW_API_URL`), then the
same URL keys in `backend/.env` near the project or packaged executable, then
a URL-only `backend.env` beside the executable, and finally
`http://127.0.0.1:8000`. Do not copy the full `backend/.env` beside a packaged
app because it contains secrets.

For example:

```powershell
$env:ADLV_BACKEND_URL = "https://your-streaming-host.example"
python frontend/desktop_app/main.py
```

If PyWebView/WebView2 is unavailable, the same frontend can be opened in the
system browser with:

```powershell
python frontend/desktop_app/main.py --browser-fallback --url http://127.0.0.1:8000
```

The desktop shell does not duplicate or proxy credentials; session storage and
requests remain handled by the frontend and the existing backend.

## App icon on Windows

PyWebView's Windows EdgeChromium renderer gets its application icon from the
packaged executable. Build a Windows executable with the existing project
favicon:

```powershell
python -m pip install pyinstaller
$buildStamp = Get-Date -Format "yyyyMMdd-HHmmss"
pyinstaller --noconfirm --clean `
  --workpath "build/ADLV Media Streamer-$buildStamp" `
  --distpath "dist/ADLV Media Streamer-$buildStamp" `
  --windowed --name "ADLV Media Streamer" `
  --icon frontend/favicon.ico frontend/desktop_app/main.py
```

The executable will be created under
`dist/ADLV Media Streamer-<timestamp>/ADLV Media Streamer/`. The runtime icon
option is also enabled for platforms where PyWebView supports setting the
window icon directly.

The player fullscreen button uses the native PyWebView window fullscreen API
when running in the desktop app. In a normal browser it continues using the
standard browser fullscreen API.

## Login persistence

The existing frontend saves the access token, refresh token, and user session
in `localStorage`. The desktop shell uses a persistent WebView profile at
`%LOCALAPPDATA%\\ADLV Media Streamer\\webview-profile` on Windows, so the saved
session remains available after restarting the app. The frontend automatically
uses the access token for API requests and attempts a refresh when the access
token expires.

The shell explicitly disables PyWebView private mode; private mode would clear
local storage when the app exits.

Use the app's **Log Out** button to clear the saved session. Removing the
profile directory also signs the desktop shell out, but is not normally needed.
