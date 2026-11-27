"""Constants for the Extron integration."""

DOMAIN = "extron"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_PASSWORD = "password"
CONF_READ_ONLY = "read_only"
CONF_INPUTS = "inputs"
CONF_OUTPUTS = "outputs"
CONF_MODEL_NAME = "model_name"
CONF_UNIT_NAME = "unit_name"
CONF_KIND = "kind"
CONF_AUDIO = "audio"

KIND_IN1508 = "in1508"

CONF_INPUT_NAMES = "input_names"
CONF_AUDIO_INPUT_NAMES = "audio_input_names"
CONF_OUTPUT_NAMES = "output_names"
CONF_AUDIO_OUTPUT_NAMES = "audio_output_names"
CONF_REFRESH_SECONDS = "refresh_seconds"
CONF_SHOW_ADVANCED = "show_advanced"

DEFAULT_PORT = 23
DEFAULT_REFRESH = 60
MIN_REFRESH = 5
MAX_REFRESH = 1800

SOURCE_NONE = "None"
SOURCE_MIXED = "Mixed"

# How long a second press has to follow the first before a saved preset is overwritten or cleared.
CONFIRM_SECONDS = 10

LOCK_OPTIONS = {
    0: "Off",
    1: "Mode 1 (all front-panel changes locked)",
    2: "Mode 2 (advanced functions locked)",
}
GROUP_OPTIONS = {0: "None", 1: "Group 1", 2: "Group 2", 3: "Group 3", 4: "Group 4"}

# The unit's daylight saving rules (Esc n CX).
DST_OPTIONS = {0: "Off", 1: "North America", 2: "Europe", 3: "Brazil"}
