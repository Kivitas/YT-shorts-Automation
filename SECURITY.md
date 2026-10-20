# Security Policy

## Supported Versions

AutoShorts receives active security updates on the main branch:

| Version | Supported | Status |
|---|---|---|
| 1.0.x | :white_check_mark: | Current Production Release |
| < 1.0 | :x: | End of Life / Unsupported |

---

## Reporting a Vulnerability

If you discover a security vulnerability or potential credential leak within AutoShorts, please do **not** file a public issue.

Instead, please send a responsible disclosure report to the project maintainer:
- **Email**: `stavic78.94@gmail.com`

Please include:
1. Clear description of the vulnerability.
2. Steps or proof-of-concept script to reproduce the issue.
3. Impact assessment and suggested remediation.

We strive to acknowledge receipt of vulnerability reports within 48 hours and provide remediation in a timely manner.

---

## Security Architecture & Best Practices

AutoShorts is designed for strict local execution with defence-in-depth security principles:

### 1. Atomic Secret Management (`settings_manager.py`)
- API keys, local model paths, and environment settings are managed atomically via `autoshorts.core.settings_manager`.
- Writes to `.env` and `config/config.yaml` use atomic temporary file replacement (`atomic_write`) to prevent file corruption, half-written state, or race conditions during concurrent accesses.
- Settings endpoints mask secret values (`mask_secret()`) showing only the last 4 characters (`••••••••XXXX`) so that plain text credentials are never returned over the network or exposed in frontend DOMs.

### 2. Automatic Secret Redaction (`logging_setup.py`)
- Any secret introduced at runtime—whether loaded from `.env`, uploaded via the settings modal, or obtained through OAuth—is immediately registered with `autoshorts.core.logging_setup.register_secret()`.
- The logging formatter intercepts and redacts all known secrets from console output, SSE log streams (`/api/logs/stream`), and disk logs (`logs/autoshorts.log`).

### 3. Local-Only Sensitive Data Storage
The following files and directories contain sensitive credentials or browser sessions and are strictly excluded from version control via `.gitignore`:
- **`.env`**: Contains sensitive API keys (`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `YOUTUBE_API_KEY`).
- **`data/client_secret.json`**: Google Cloud OAuth 2.0 client credentials.
- **`data/token.json`**: Google OAuth 2.0 refresh and access tokens.
- **`data/edge_profile/`**: Microsoft Edge persistent browser user profile (containing authenticated YouTube Studio cookies and session storage).

These files remain exclusively on your local machine and are never transmitted to third-party endpoints.

### 4. Untracked `.bat` Scripts & SmartScreen Protection
- Batch files (`*.bat`) are deliberately excluded from git tracking (`*.bat` in `.gitignore`).
- Windows SmartScreen and corporate antivirus software routinely flag batch files downloaded from external git repositories as untrusted or malicious.
- To provide a secure and transparent experience, operators create their own local `start.bat` following the documented, auditable instructions in [`bat.md`](bat.md).

### 5. Local Isolation & Network Binding
- The local web dashboard binds strictly to `127.0.0.1:8765` by default.
- Never expose this port to public networks or untrusted local networks without placing an authenticated reverse proxy in front of it.

### 6. Path-Guarded Safety Deletions
- All automatic cleanup operations use path-guarded verification (`assert_safe_to_delete`) in `autoshorts.cleanup.safety_cleaner`.
- Deletions are strictly prohibited on core source trees, configuration roots, and parent directories (`assets/`, `config/`, `data/`, `src/`, `inbox/`).
- Deletions are executed via `send2trash` (sending files to the OS Recycle Bin/Trash) rather than permanent unrecoverable removal.
