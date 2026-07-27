# OpenClass + Apache Answer

This sidecar keeps forum data and upgrades separate from OpenClass while using OpenClass accounts for login.

## Start the service

Configure the `OPENCLASS_COMMUNITY_*` variables documented in the repository `.env.example`, then run:

```bash
docker compose --env-file ../../.env -f deploy/answer/docker-compose.yml up -d --build
```

Open `http://127.0.0.1:9080` and complete Answer's first-run site and administrator setup.

The production sidecar remains bound to loopback. To expose it on the main
OpenClass origin without taking over unrelated root routes, build with
`OPENCLASS_ANSWER_BASE_PATH=/community`, include `nginx-locations.conf` inside
the existing HTTPS server block, and set `OPENCLASS_COMMUNITY_PUBLIC_URL` plus
`OPENCLASS_ANSWER_SITE_URL` to `https://open-classes.com/community`. The Answer
API and uploaded files keep their native `/answer/` and `/uploads/` paths.
The supplied snippet sends the exact `/community` request to the OpenClass Web
upstream: it is the identity-aware entry that sends registered users through
SSO. `/community/` and below go to Answer, where the forum is served. An
anonymous visitor is redirected from the entry to that trailing-slash mount.
The image build defaults to a 1536 MB Node.js heap; constrained builders can
override it with `OPENCLASS_ANSWER_NODE_OPTIONS`.

The image applies `openclass-theme.css` through Answer's supported custom CSS
surface after each database upgrade. The theme keeps Answer's forum behavior and
responsive layout while matching the warm OpenClass visual system. Set
`OPENCLASS_ANSWER_THEME_ENABLED=false` to keep Answer's stock appearance.
The entrypoint also places an explicit theme stylesheet link in Answer's
server-rendered document head so OAuth login and authenticated navigation keep
the same appearance as the public community page.
For same-origin deployments, the document head also includes a small session
bridge. If OpenClass is still authenticated while Answer has no valid session,
the bridge returns through the OpenClass `/community` entry and completes SSO
automatically. A short per-tab cooldown prevents redirect loops when the
connector is unavailable.
The theme also adds a top-left link back to OpenClass. Set `OPENCLASS_HOME_URL`
to the public `/home` URL in deployed environments; it defaults to
`http://127.0.0.1:3000/home` for local development.
The community favicon reuses the OpenClass favicon at the origin inferred from
that URL. Set `OPENCLASS_FAVICON_URL` only when the favicon is served elsewhere.

For unattended first-run setup, pass `OPENCLASS_ANSWER_AUTO_INSTALL=true`,
`OPENCLASS_ANSWER_BOOTSTRAP_ADMIN_EMAIL`, and a temporary
`OPENCLASS_ANSWER_BOOTSTRAP_ADMIN_PASSWORD` to the first `docker compose up`.
Answer administrator usernames must use its lowercase username format; the
default service account username is `openclassadmin`.
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

Set `OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID` and
`OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET` before starting the container. The
entrypoint enables `basic_connector`, disables Answer email/password login, and
configures the following values automatically:

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

The authorize, token, and user-info URLs default to the OpenClass origin derived
from `OPENCLASS_HOME_URL`. Deployments with different internal routing can
override them with `OPENCLASS_COMMUNITY_OAUTH_AUTHORIZE_URL`,
`OPENCLASS_COMMUNITY_OAUTH_TOKEN_URL`, and
`OPENCLASS_COMMUNITY_OAUTH_USERINFO_URL`.

Set Answer's site URL to the same origin used by `OPENCLASS_COMMUNITY_PUBLIC_URL`. Its generated callback must exactly equal `OPENCLASS_COMMUNITY_OAUTH_REDIRECT_URI`.

External registration remains enabled so a first OpenClass login can create the
matching Answer account. Answer email registration and password login remain
disabled, leaving OpenClass as the only account entry point.

After a successful login test, restart OpenClass API and Web services so the integration readiness check is refreshed.

## Ownership and licensing

OpenClass owns course-to-community links and identity handoff. Answer owns questions, answers, tags, votes, moderation, notifications, search, and reputation. The image contains Apache Answer and the official `connector-basic` plugin under Apache License 2.0; retain their bundled `LICENSE` and `NOTICE` files when redistributing the image.
