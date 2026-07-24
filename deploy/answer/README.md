# OpenClass + Apache Answer

This sidecar keeps forum data and upgrades separate from OpenClass while using OpenClass accounts for login.

## Start the service

Configure the `OPENCLASS_COMMUNITY_*` variables documented in the repository `.env.example`, then run:

```bash
docker compose --env-file ../../.env -f deploy/answer/docker-compose.yml up -d --build
```

Open `http://127.0.0.1:9080` and complete Answer's first-run site and administrator setup. Keep `OPENCLASS_COMMUNITY_PROVIDER=native` until the connector below is enabled.

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

After a successful login test, set `OPENCLASS_COMMUNITY_PROVIDER=answer` and restart OpenClass API and Web services.

## Ownership and licensing

OpenClass owns course-to-community links and identity handoff. Answer owns questions, answers, tags, votes, moderation, notifications, search, and reputation. The image contains Apache Answer and the official `connector-basic` plugin under Apache License 2.0; retain their bundled `LICENSE` and `NOTICE` files when redistributing the image.
