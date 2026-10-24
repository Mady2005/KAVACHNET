# Security module

Implement security inspection and event creation in this package. The service must satisfy `SecurityDetectionService.inspect(payload) -> KavachEvent` from `app.services.interfaces` and emit events with `source_module=SECURITY`.

Keep policy recommendations separate from executing actions such as blocking or quarantining. Add module-specific schemas and tests here when the module is implemented; coordinate shared API, authentication, and persistence changes before editing shared files.
