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
