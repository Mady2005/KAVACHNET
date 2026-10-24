# Module implementation packages

Each module owner should implement their capability in its own package below. Keep shared API schemas, authentication, database models, and cross-module interfaces in their existing shared locations; coordinate changes to those files with the other owners.

All module events must use `app.schemas.events.KavachEvent` and pass through the shared event persistence path. Implement the service protocol in `app.services.interfaces` for the module. Do not add external side effects to the central decision engine; it returns a policy recommendation for an authorized module to handle.

See `docs/INTEGRATION_CONTRACTS.md` for the current schemas, role boundaries, ownership, and future API contracts.
