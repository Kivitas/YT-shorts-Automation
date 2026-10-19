# Security Policy

## Supported Versions

AutoShorts receives active security updates on the main branch:

| Version | Supported |
|---|---|
| 3.1.x | :white_check_mark: |
| < 3.1 | :x: |

## Reporting a Vulnerability

If you discover a security vulnerability or potential credential leak within AutoShorts, please do not file a public issue.

Instead, please send a report to the repository maintainer:
- **Email**: `stavic78.94@gmail.com`

Please include:
1. Description of the vulnerability.
2. Steps or proof-of-concept script to reproduce the issue.
3. Impact assessment.

We strive to acknowledge receipt of vulnerability reports within 48 hours and provide remediation in a timely manner.

## Security Best Practices for Operators

1. **Credential Safety**:
   - Keep `.env`, `data/client_secret.json`, and `data/token.json` strictly private and verify they remain in `.gitignore`.
   - Never commit API keys (`GEMINI_API_KEY`, `OPENROUTER_API_KEY`).
2. **Local Isolation**:
   - The web dashboard binds to `127.0.0.1:8765` by default. Do not expose this port to public networks or untrusted LANs without authentication.
3. **Path Safety**:
   - All cleanup operations use path-guarded deletion (`assert_safe_to_delete`) and `send2trash` to prevent accidental data loss.
