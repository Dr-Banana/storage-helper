# StorageHelperWebService

## Overview

`StorageHelperWebService` is the user-facing API and web layer of the StorageHelper system.  
It exposes HTTP endpoints (and optionally a web UI) that clients use to interact with the AI file management assistant.

This service focuses on **request/response handling, authentication, and basic validation**.  
It does not contain complex AI or storage logic itself; instead, it delegates to the AI Orchestration and Data Storage services.

---

## Responsibilities

- **Public API Gateway**
  - Provide REST/GraphQL endpoints for:
    - Uploading cabinet/drawer/box photos.
    - Uploading document photos and optional text descriptions.
    - Asking “where is my X document?” or “where should I put this?”.
    - Viewing search results and recommended locations.
  - Handle request validation, rate limiting, and basic error responses.

- **Integration with AI Orchestration**
  - Forward high-level user actions to `StorageHelperAIOrchestraService`.
  - Translate orchestration responses into clean JSON responses or web views.
  - Pass user feedback (e.g., “this result was wrong”) back to the AI service.

- **Basic Data Access**
  - For simple, non-AI operations (e.g., list all locations, view a location photo),
    call `StorageHelperDataStorageService` directly.
  - Avoid duplicating business logic that already exists in AI/Data services.

- **(Optional) Web Frontend Hosting**
  - Serve static frontend assets for the StorageHelper web app (if not deployed elsewhere).
  - Act as the backend for the browser UI (SPA, SSR app, etc.).

---

## Boundaries with Other Services

- **Calls `StorageHelperAIOrchestraService`**
  - For anything that requires understanding, ranking, recommendation, or orchestration.

- **Calls `StorageHelperDataStorageService`**
  - For basic reads/writes that do not need AI processing.

The Web Service should remain **thin**, focusing on API contracts, security, and UX-oriented behavior.

---

## Implementation Notes (TBD)

- Web framework and language (e.g., Node.js/Express/NestJS, Python/FastAPI, etc.).
- Authentication/authorization strategy (e.g., JWT, session, OAuth).
- CORS policy for the front-end.
