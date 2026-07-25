# OpenClass + Apache Answer

This sidecar keeps forum data and upgrades separate from OpenClass while using OpenClass accounts for login.

## Start the service

Configure the `OPENCLASS_COMMUNITY_*` variables documented in the repository `.env.example`, then run:

```bash
docker compose --env-file ../../.env -f deploy/answer/docker-compose.yml up -d --build
```

Open `http://127.0.0.1:9080` and complete Answer's first-run site and administrator setup.

The image applies `openclass-theme.css` through Answer's supported custom CSS
surface after each database upgrade. The theme keeps Answer's forum behavior and
responsive layout while matching the warm OpenClass visual system. Set
`OPENCLASS_ANSWER_THEME_ENABLED=false` to keep Answer's stock appearance.

For unattended first-run setup, pass `OPENCLASS_ANSWER_AUTO_INSTALL=true`,
`OPENCLASS_ANSWER_BOOTSTRAP_ADMIN_EMAIL`, and a temporary
`OPENCLASS_ANSWER_BOOTSTRAP_ADMIN_PASSWORD` to the first `docker compose up`.
The bootstrap administrator is a service account: its email must never be reused
by an OpenClass user. Answer matches an incoming external identity by email, so an
email collision prevents the user's first SSO account from being created. The
default `answer-admin@openclass.local` keeps these identities separate. Use
`OPENCLASS_ANSWER_CONTACT_EMAIL` for a public contact address instead.

Recreate the container without the temporary password after installation; Answer
keeps its database in the `answer-data` volume.
If the default package registries are not reachable, set
`OPENCLASS_ANSWER_GOPROXY` and `OPENCLASS_ANSWER_NPM_REGISTRY` without editing
the Dockerfile.

## Configure the OAuth2 Basic connector

In Answer administration, enable `basic_connector` and configure:

| Field | Value |
| --- | --- |
| Name | `OpenClass` |
| Client ID | `OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID` |
| Client secret | `OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET` |
| Authorize URL | `https://your-openclass-origin/api/auth/community/authorize` |
| Token URL | `https://your-openclass-origin/api/auth/community/token` |
| User JSON URL | `https://your-openclass-origin/api/auth/community/userinfo` |
| User ID JSON path | `id` |
| User display name JSON path | `name` |
| User username JSON path | `username` |
| User email JSON path | `email` |
| User avatar JSON path | `avatar_url` |
| Check email verified | Off |

Set Answer's site URL to the same origin used by `OPENCLASS_COMMUNITY_PUBLIC_URL`. Its generated callback must exactly equal `OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI`.

Keep external registration enabled so a first OpenClass login can create the
matching Answer account, but disable Answer's email registration and password
login. This leaves OpenClass as the only account entry point.

After a successful login test, restart OpenClass API and Web services so the integration readiness check is refreshed.

## Ownership and licensing

OpenClass owns course-to-community links and identity handoff. Answer owns questions, answers, tags, votes, moderation, notifications, search, and reputation. The image contains Apache Answer and the official `connector-basic` plugin under Apache License 2.0; retain their bundled `LICENSE` and `NOTICE` files when redistributing the image.
