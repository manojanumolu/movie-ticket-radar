# Firestore rules tests

`firestore.rules` run in the real Firebase emulator — no production project,
no credentials (`demo-` projects never reach Google).

    cd tests/firestore_rules
    npm install
    npm test

Needs Node 18+ and Java 11+ (the emulator is a jar). The Python suite
(`pytest`) does not run these; `tests/test_security_hardening.py` checks the
rules file states the same limits as `monitor/policy.py`.
