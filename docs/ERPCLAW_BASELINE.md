# ERPClaw baseline verification

Verified 12 July 2026 against core commit
`4ae975868ba4de8a651905e2d87b6107ac0e88de`.

## Successful smoke path

- Initialized a clean isolated ERPClaw home.
- Created 213 tables, 608 indexes and registered 22 skills.
- Created a Thailand company using THB.
- Created a Thai customer and Thai catalog item.
- Created a draft sales order for 2 cases at THB 420, total THB 840.
- ERPClaw returned `status: ok` through every step.

This verifies the action boundary needed by the future controlled adapter. It
does not validate Thai accounting or tax compliance.

## Test status

- The full ERPClaw Python test suite was not run because the pinned repository
  does not bundle pytest and the prototype smoke path required no extra package.
- ERPClaw Web production build completed successfully and includes the new
  `/order-intake` route.
- ERPClaw Web's existing suite reports 42 of 43 tests passing. The one failure
  is an upstream `stores.test.ts` expectation that treats the exported Svelte
  writable store as a plain record; it is unrelated to the new route.
- The new Order Intake API suite passes 6 of 6 tests, including its live local
  HTTP flow.
