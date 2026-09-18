"""Best-effort child-process hook; primary bootstraps also install explicitly."""
import os
if os.getenv("NEXUS_OFFLINE_TEST_ROOT"):
    from offline_test_bootstrap.network_guard import install
    install()
