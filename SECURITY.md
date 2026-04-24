# Security Policy

## Supported Versions

Only the latest commit on `main` is actively maintained.

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public GitHub issue. Instead, report it privately via [GitHub's private vulnerability reporting](https://github.com/McCavity/jeelink-davis/security/advisories/new).

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

You can expect an acknowledgement within a few days. This is a small personal project — response times may vary.

## Scope

This project runs on a private local network (Raspberry Pi). The web dashboard has no authentication by default and is intended to be protected by network perimeter controls (firewall, VPN, or reverse proxy with authentication). **Do not expose the dashboard directly to the internet without adding authentication.**

Credentials (InfluxDB token, MQTT password) should always be stored in environment variables or `/etc/davis-weather.env`, never in `config.toml` which may be world-readable.
