# Firebase Rules and recovery emulator tests

Install Node 22, Java 21, and the repository's Python development dependencies.
From this directory, run `npm ci --ignore-scripts` and `npm test`.

The suite uses local Firestore and Authentication on ports 8080 and 9099.
Firebase CLI starts the emulators for demo projects. Only synthetic records
are created. Rules tests run unauthenticated, verified, unverified, and app
admin clients. The recovery test compares snapshots across demo projects and
verifies fixture sign-in. A separate case exercises the worker's lease and
write preconditions using the emulator's actual REST API.
