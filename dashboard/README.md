# Evidence Pipeline dashboard

React/Vite remains the reference UI. No model weights or source-specific scraper code belong in this package.

```sh
npm ci
npm test
npm run build
npm run dev
```

Run these commands from `dashboard/` so its client-only npm configuration is used. The pinned Neon SDK has optional server-framework peers; `.npmrc` avoids installing unrelated framework stacks. The build still checks the installed SDK's TypeScript contract.

## Authentication modes

With `VITE_NEON_AUTH_URL` unset, the original self-hosted administrator-token flow stays available. The token lives only in memory; refresh or disconnect clears it.

For managed public accounts, set the actual branch Auth URL as `VITE_NEON_AUTH_URL` at build time. Optionally set `VITE_API_BASE_URL` to the HTTPS API origin; leave it empty for a same-origin API. Only local development permits HTTP. See `.env.example` for public configuration names. Never expose database credentials or a platform administrator token through a `VITE_*` variable.

Enable Neon Managed Better Auth, register the frontend origin in its trusted domains, and configure the backend's Neon issuer/JWKS and allowed frontend origins. An Auth URL alone does not provision the service. Login, signup, email verification codes, session restoration and sign-out use the official SDK. Requests and downloads call the SDK token getter each time; the application does not save JWTs or passwords in browser storage. Managed sign-out ends the session, but an already-issued JWT may remain valid until expiry.

Managed mode never falls back to an administrator token if its configuration or login fails. Workspace and operator status come from authenticated `/v1/me`, not a browser-selected workspace ID. The backend remains responsible for authorization.

## Public-beta controls

- Request approval for an exact public hostname before creating a live HTML/JSON source. No credentials or wildcard-domain access are offered.
- Live source defaults are bounded to 10 pages, 1,000 records, a 1 MB capture limit and a 60-second acquisition deadline.
- Operation checks run only in the foreground, every 15 seconds, for at most two minutes. After that use the explicit status button. Other views refresh on demand.
- Managed accounts, including operators, cannot expand storage or rotate administrator tokens through this UI. Operators may review domain requests with an audited reason.
- Review published records against retained evidence; keep human and AI provenance distinct. Snapshots are training exports, not independent certification datasets by default.
- Register and inspect model metadata within your own workspace using remote HTTPS checkpoint references and training/calibration/evaluation hashes. No weights are fetched, no versions are automatically activated, and no score is represented as a certified classification.

The component tests mock Managed Auth. Real email delivery, trusted-origin behavior and deployment headers must be verified against the configured remote service before opening signup.
