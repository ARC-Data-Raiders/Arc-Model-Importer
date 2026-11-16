"""Codex enemy portrait choices sourced from Frozen Trail UIEnemyMetaData."""

ARC_ENEMIES = (
    ("BULLCRAB", "Leaper (Bullcrab)", "T_UI_ARC_Bison.avif"),
    ("CHONK", "Bastion (Chonk)", "T_UI_ARC_Chonk.avif"),
    ("CHONK_MORTAR", "Bombardier", "T_UI_Bombardier.avif"),
    ("COURIER", "ARC Reclaimer", "T_UI_ARC_Courier.avif"),
    ("ELITE_DRONE_FLAMETHROWER", "Firefly", "T_UI_ARC_Firefly.avif"),
    ("ELITE_DRONE_HORNET", "Hornet", "T_UI_ARC_Hornet.avif"),
    ("TURRET", "Turret", "T_UI_ARC_Turret.avif"),
    ("FIREBALL", "Fireball", "T_UI_ARC_Fireball.avif"),
    ("HEAVY_DRONE_LASER", "Vaporizer", "T_UI_ARC_LaserRocketeer.avif"),
    ("HEAVY_DRONE_MISSILE", "Rocketeer", "T_UI_ARC_Rocketeer.avif"),
    ("KEBAB", "Hydra", "T_UI_ARC_Kebab.avif"),
    ("WASP", "Wasp", "T_UI_ARC_Wasp.avif"),
    ("MEERKAT", "Skulker", "T_UI_ARC_Meerkat.avif"),
    ("PEPPERMINT", "Queen", "T_UI_ARC_Baron.avif"),
    ("MONOLITH", "ARC Turbine", "T_UI_ARC_Monolith.avif"),
    ("CATNIP", "Emperor", "T_UI_ARC_Emperor.avif"),
    ("PINGER", "Spotter", "T_UI_ARC_Spotter.avif"),
    ("POP", "Pop", "T_UI_ARC_Pop.avif"),
    ("BOOM", "Comet", "T_UI_ARC_Comet.avif"),
    ("ROLLBOT", "ARC Surveyor", "T_UI_ARC_Rollbot.avif"),
    ("SHREDDER", "Shredder", "T_UI_ARC_Shredder.avif"),
    ("SNIPER", "Sentinel", "T_UI_ARC_Sniper.avif"),
    ("SNITCH", "Snitch", "T_UI_ARC_Snitch.avif"),
    ("TARRAGON", "Bully", "T_UI_ARC_Tarragon.avif"),
    ("TICK", "Tick", "T_UI_ARC_Tick.avif"),
)

_BY_KEY = {row[0]: row for row in ARC_ENEMIES}

# Skeletal mesh packages per ARC, relative to Content/Pioneer (pak index, game 2.3.0).
ARC_MESHES = {
    "BULLCRAB": ("Enemies/BullCrab/Art/SK_BullCrab_01",),
    "CHONK": ("Enemies/Chonk/Art/SK_Chonk_01_A",),
    "CHONK_MORTAR": ("Enemies/ChonkMortar/Art/SK_ChonkMortar_01",),
    "COURIER": ("Enemies/Courier/Art/SK_Courier_01_A",),
    "ELITE_DRONE_FLAMETHROWER": ("Enemies/EliteDrone/Flamethrower/Art/SK_EliteDrone_Flamethrower_01_A",),
    "ELITE_DRONE_HORNET": ("Enemies/EliteDrone/Hornet/Art/SK_LightDroneElite_01_A",),
    "TURRET": ("Enemies/Turret/TurretEnemy/Art/SK/SK_Turret_01_Body_A",),
    "FIREBALL": ("Enemies/FireBall/Art/SK/SK_FireBall_01",),
    "HEAVY_DRONE_LASER": ("Enemies/HeavyDrone/HeavyDroneLaser/Art/SK_HeavyDrone_Laser_01",),
    "HEAVY_DRONE_MISSILE": ("Enemies/HeavyDrone/HeavyDroneMissile/Art/SK_HeavyDrone_Missile_01",),
    "KEBAB": ("Enemies/Kebab/Gatling/Art/SK_Kebab_01_Base", "Enemies/Kebab/Gatling/Art/SK_KebabGatling_01_B"),
    "WASP": ("Enemies/LightDrone/Wasp/Art/SK_LightDrone_01_A",),
    "MEERKAT": ("Enemies/Meerkat/Meerkat/Art/SK_Meerkat_02_A",),
    "PEPPERMINT": ("Enemies/MintType/Peppermint/Art/SK_Peppermint_01",),
    "MONOLITH": ("Enemies/Monolith/Monolith/Art/SK_Monolith_01_A",),
    "CATNIP": ("Enemies/Catnip/Blockout/SK_Catnip_05_A",),
    "PINGER": ("Enemies/Pinger/Art/SK_Pinger_01",),
    "POP": ("Enemies/Pop/Pop/Art/SK_Pop_01",),
    "BOOM": ("Enemies/RollBot/Boom/Art/SK_Boom_01_B",),
    "ROLLBOT": ("Enemies/RollBot/Rollbot_Normal_Version/Art/SK_RollBot_01",),
    "SHREDDER": ("Enemies/Runner/Shredder/Art/SK_Runner_01",),
    "SNIPER": ("Enemies/Sniper/Art/SK_Sniper_01_A",),
    "SNITCH": ("Enemies/SnitchBot/Shared/Art/SK_ScoutDrone_01_A",),
    "TARRAGON": ("Enemies/Tarragon/Art/SK_Tarragon_01",),
    "TICK": ("Enemies/Tick/Shared/Art/SK_Tick_02_A",),
}


def mesh_rels(key):
    return ARC_MESHES.get(str(key or ""), ())


def by_key(key):
    return _BY_KEY.get(str(key or ""))


_ENUM_ITEMS=None


def display_label(key):
    """Clean enemy name for a catalog key: 'Leaper (Bullcrab)' -> 'Leaper'."""
    row=by_key(key)
    if not row:
        return ''
    return row[1].split('(',1)[0].strip() or row[1]


def enum_items(_self=None, _context=None):
    # Blender keeps pointers into the returned strings: build once and keep alive.
    global _ENUM_ITEMS
    if _ENUM_ITEMS is None:
        items=[('NONE','Choose an Enemy...','Pick an enemy to import')]
        for key,_label,_image in ARC_ENEMIES:
            name=display_label(key)
            items.append((key,name,f'{name}. Code name: {key}'))
        _ENUM_ITEMS=items
    return _ENUM_ITEMS
