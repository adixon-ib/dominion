# NIOS boot and network timing
NIOS_REBOOT_TIMEOUT = 300     # seconds for NIOS to reboot after network change
NIOS_WAPI_READY_TIMEOUT = 360 # seconds to poll WAPI before giving up
NIOS_WAPI_POLL_INTERVAL = 5   # seconds between WAPI poll attempts
NIOS_SSH_READY_DELAY = 30     # seconds after console setup before SSH is usable
FRESH_INSTALL_SSH_TIMEOUT = 60  # seconds to wait for SSH after grid_deploy
FRESH_INSTALL_SSH_INTERVAL = 5  # seconds between SSH probes
REBOOT_SYSTEM_TIMEOUT = 30    # seconds for a reboot_system poweroff/poweron call
ADDKEYS_TIMEOUT = 20          # seconds before giving up on addkeys
ADDKEYS_RETRY_TIMEOUT = 180   # seconds to retry addkeys + SSH after enabling via console
ADDKEYS_RETRY_INTERVAL = 30   # seconds between addkeys retries (SSH rate limit: 10 conns/60s)
ADDKEYS_QUICK_RETRY_TIMEOUT = 1  # one addkeys + SSH attempt on the cheap path, then power-cycle
ADDKEYS_QUICK_WAPI_TIMEOUT = 60  # seconds to wait for WAPI before the cheap addkeys-only attempt

# NIOS default credentials
NIOS_CONSOLE_USER = "admin"
NIOS_CONSOLE_PASSWORD = "infoblox"

# WAPI
WAPI_VERSION = "v2.12"
TOKEN_BUNDLE_ENABLE_TIMEOUT = 900  # seconds for ipc:bundle migration to finish
TOKEN_BUNDLE_POLL_INTERVAL = 5     # seconds between ipc:bundle status polls
