"""Codex enemy portrait choices sourced from Frozen Trail UIEnemyMetaData."""

ARC_ENEMIES = (
    ("BULLCRAB", "Leaper (Bullcrab)", "T_UI_ARC_Bison.png"),
    ("CHONK", "Bastion (Chonk)", "T_UI_ARC_Chonk.png"),
    ("CHONK_MORTAR", "Bombardier", "T_UI_Bombardier.png"),
    ("COURIER", "ARC Reclaimer", "T_UI_ARC_Courier.png"),
    ("ELITE_DRONE_FLAMETHROWER", "Firefly", "T_UI_ARC_Firefly.png"),
    ("ELITE_DRONE_HORNET", "Hornet", "T_UI_ARC_Hornet.png"),
    ("TURRET", "Turret", "T_UI_ARC_Turret.png"),
    ("FIREBALL", "Fireball", "T_UI_ARC_Fireball.png"),
    ("HEAVY_DRONE_LASER", "Vaporizer", "T_UI_ARC_LaserRocketeer.png"),
    ("HEAVY_DRONE_MISSILE", "Rocketeer", "T_UI_ARC_Rocketeer.png"),
    ("KEBAB", "Hydra", "T_UI_ARC_Kebab.png"),
    ("WASP", "Wasp", "T_UI_ARC_Wasp.png"),
    ("MEERKAT", "Skulker", "T_UI_ARC_Meerkat.png"),
    ("PEPPERMINT", "Queen", "T_UI_ARC_Baron.png"),
    ("MONOLITH", "ARC Turbine", "T_UI_ARC_Monolith.png"),
    ("PINGER", "Spotter", "T_UI_ARC_Spotter.png"),
    ("POP", "Pop", "T_UI_ARC_Pop.png"),
    ("BOOM", "Comet", "T_UI_ARC_Comet.png"),
    ("ROLLBOT", "ARC Surveyor", "T_UI_ARC_Rollbot.png"),
    ("SHREDDER", "Shredder", "T_UI_ARC_Shredder.png"),
    ("SNIPER", "Sentinel", "T_UI_ARC_Sniper.png"),
    ("SNITCH", "Snitch", "T_UI_ARC_Snitch.png"),
    ("TARRAGON", "Bully", "T_UI_ARC_Tarragon.png"),
    ("TICK", "Tick", "T_UI_ARC_Tick.png"),
)

_BY_KEY = {row[0]: row for row in ARC_ENEMIES}


def by_key(key):
    return _BY_KEY.get(str(key or ""))


def enum_items(_self=None, _context=None):
    items=[]
    for key,label,_image in ARC_ENEMIES:
        codename=key.title().replace('_',' ')
        # Keep the already-humanized codenames for the entries with legacy
        # labels; otherwise show the catalog's stable codename in parentheses.
        if '(' not in label:
            label=f'{label} ({codename})'
        items.append((key,label,f'Codex ARC portrait: {label}'))
    return items
