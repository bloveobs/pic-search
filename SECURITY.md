# Security Policy

## Supported versions

Only the latest commit on `main` is supported. Fixes are not backported to older versions.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability**, or use this link:

https://github.com/bloveobs/pic-search/security/advisories/new

Please include:

- what the problem is and which file or route it affects
- steps to reproduce it
- what an attacker could do with it

## What to expect

pic-search is a personal project maintained in spare time. Reports are handled on a best-effort basis with no guaranteed response time. I will acknowledge a report when I can, fix confirmed problems on `main`, and credit the reporter in the fix unless asked not to.

## Scope

In scope: the code in this repository — the Flask web app (`app.py`), query parsing (`query_parser.py`), the indexing and search code (`app/`), the shell scripts and the `Containerfile`.

Out of scope:

- vulnerabilities in third-party dependencies (Flask, Ollama, Podman, Caddy, open_clip, insightface and so on) — please report those to the respective projects
- problems that come from not following the README's deployment guidance, such as exposing Flask's port 5000 directly instead of through the reverse proxy, publishing Ollama's port to the network, or using a weak or committed `SECRET_KEY`
- anything a user on the `ALLOWED_EMAILS` list can do inside the Pictures directory, which the README documents as intended behaviour
