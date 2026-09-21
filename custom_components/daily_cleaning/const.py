"""Constants for Daily Cleaning."""

from datetime import time
from typing import Final

DOMAIN: Final = "daily_cleaning"
VERSION: Final = "0.1.1"
PLATFORMS: Final = ["switch", "binary_sensor"]

CONF_ROOMS: Final = "rooms"
CONF_VACUUMS: Final = "vacuums"
CONF_AREA_ID: Final = "area_id"
CONF_SEGMENT_ID: Final = "segment_id"
CONF_SEGMENT_NAME: Final = "segment_name"
CONF_SEGMENT_GROUP: Final = "segment_group"
CONF_VACUUM_ENTITY_ID: Final = "vacuum_entity_id"
CONF_ROBOROCK_CONFIG_ENTRY_ID: Final = "roborock_config_entry_id"
CONF_ROBOROCK_DEVICE_ID: Final = "roborock_device_id"
CONF_ROBOROCK_ENTITY_UNIQUE_ID: Final = "roborock_entity_unique_id"

ATTR_AREA_ID: Final = "area_id"
ATTR_ROBOROCK_SEGMENT_ID: Final = "roborock_segment_id"
ATTR_ROBOROCK_SEGMENT_NAME: Final = "roborock_segment_name"
ATTR_ROBOROCK_CONFIG_ENTRY_ID: Final = "roborock_config_entry_id"
ATTR_ROBOROCK_DEVICE_ID: Final = "roborock_device_id"
ATTR_ROBOROCK_ENTITY_UNIQUE_ID: Final = "roborock_entity_unique_id"
ATTR_VACUUM_ENTITY_ID: Final = "vacuum_entity_id"
ATTR_LAST_CLEANED: Final = "last_cleaned"

RESET_TIME: Final = time(hour=3)
STORAGE_KEY: Final = f"{DOMAIN}.state"
STORAGE_VERSION: Final = 1
