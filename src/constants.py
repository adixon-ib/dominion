# NIOS boot and network timing
NIOS_REBOOT_TIMEOUT = 300     # seconds for NIOS to reboot after network change
NIOS_WAPI_READY_TIMEOUT = 360 # seconds to poll WAPI before giving up
NIOS_WAPI_POLL_INTERVAL = 5   # seconds between WAPI poll attempts
NIOS_SSH_READY_DELAY = 30     # seconds after console setup before SSH is usable
ADDKEYS_TIMEOUT = 20          # seconds before giving up on addkeys

# NIOS default credentials
NIOS_CONSOLE_USER = "admin"
NIOS_CONSOLE_PASSWORD = "infoblox"

# WAPI
WAPI_VERSION = "v2.12"
TOKEN_BUNDLE_ENABLE_TIMEOUT = 900  # seconds for ipc:bundle migration to finish
TOKEN_BUNDLE_WAPI_VERSION = "v2.15"  # first WAPI version with ipc:bundle
TOKEN_BUNDLE_APPLY_TIMEOUT = 300  # seconds for ipc:bundle apply (calculator restart)

# run_py_tests: one run does a factory reset afterwards, about 5 minutes
PY_TESTS_TIMEOUT = 1800  # seconds for one run_py_tests invocation
ADMIN_HTTPD_WAIT_TIMEOUT = 660  # seconds for the pgrep wait loop (120 x 5s) to give up
