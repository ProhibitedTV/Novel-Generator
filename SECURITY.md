# Security Policy

## Supported Versions

This project is pre-1.0, so security fixes are targeted at the latest release on `main`.

## Reporting a Vulnerability

Please do not open public GitHub issues for security-sensitive reports.

- Email: `admin@prohibited.tv`
- Include: affected version or commit, reproduction steps, impact, and any mitigation ideas

We will acknowledge reports as quickly as possible and coordinate a fix and disclosure timeline with you.

## Deployment Guidance

- Keep the app behind a reverse proxy with HTTPS and authentication if it is reachable outside a trusted network.
- Treat generated manuscripts as sensitive data and back up the SQLite database and artifact volume.
- Do not expose Ollama or the app to the public internet without additional controls.
