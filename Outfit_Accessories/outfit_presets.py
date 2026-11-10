"""Per-outfit card presets for DA_OI visibility, expressed as game option tags.

Keys are outfit folder names under Items/Characters/Skins/Outfit. Each value
maps a game setting tag to the option tag the card shows. Mesh names are never
used here; outfit_visibility.preset_option_paths resolves the tags to option files.

Keys are cache folder names (RAGS uses the Expeditioner folder). Only parts the
card shows differently from the import state belong here (hide or show).
"""

PRESET_OPTION_TAGS = {
    "Expeditioner": {
        "Character.Setting.MeshToggle2": "Character.Option.On",
        "Character.Setting.MeshToggle3": "Character.Option.Off",
    },
    "GWRanger": {
        "Character.Setting.MeshToggle2": "Character.Option.Off",
    },
    "Banshee": {
        "Character.Setting.MeshToggle2": "Character.Option.Off",
    },
    "Drafty": {
        "Character.Setting.MeshToggle1": "Character.Option.Off",
    },
    "Driftcoat": {
        # Card shows the Basic colourway; imported blends carry stale mixed colourway bindings.
        "Character.Setting.Color": "Character.Option.Basic",
    },
    "Moonball": {
        "Character.Setting.Color": "Character.Option.White",
    },
    "Nurse": {
        "Character.Setting.Mask": "Character.Option.Off",
    },
    "Welder": {
        "Character.Setting.MeshToggle1": "Character.Option.On",
    },
}
