# Shader controls

## Fur

Select a fur mesh or its parent outfit. Open Asset Modifications, then Fur.

Length Scale multiplies the authored shell offset. Its default preserves the imported length. Zero removes the shell offset without editing the source vertices or shape keys. The modifier remains before armature deformation. Viewport and Render control its visibility.

Root Color, Tip Color, Color Tint and Roughness Offset are material controls. An edited value survives a material rebuild. An unedited value follows the newly selected authored material or colorway. Roughness Offset defaults to zero.

Current compact PAK material exports and older Parameters-based exports are supported. Texture references are normalized from ObjectPath and AssetPathName records before loading.

## Lightbulbs

The bulb builder keeps the authored color, intensity, shell mask and off state. A nonzero BumpOffset adds a bounded tangent-space view shift. Texture alpha supplies its height, with a centered reference plane.

A Pulsating switch or positive PulseAmount enables a timeline-driven sinusoidal pulse. Pulse Amount, Pulse Speed and Pulse Phase are labeled shader values. Speed is cycles per second. Phase is radians. The time driver accounts for both frame rate and fps_base. Static lights do not receive a time driver.

The parallax and pulse are Blender approximations. They are not a reconstruction of native noise flicker or a claim of matched game appearance.

## Glass

Window panes preserve separately authored inside and outside opacity and dirt strength. Zero opacity, roughness, specular and dirt strength are valid values. The builders do not impose a minimum visible opacity.

Window and translucent glass use Principled transmission and IOR, optional authored Normal or NormalMap inputs, and dirt-dependent roughness. Transmission defaults to one and IOR defaults to 1.45. Their transport is a Blender approximation, not native cubemap or refraction parity. The opaque glass family remains opaque.

## Prop integration

This update includes the available prop-thread runtime work, including its unfinished conditional surface and decal corrections. Newer importer cleanup, graph layout, Cubic albedo sampling, per-user logging and lazy exporter startup are retained.

The separate atlas-normal reconstruction remains research. It is not integrated because the native vertex binding, View inputs and renderer qualification remain unresolved. Import and graph checks do not certify complete game fidelity.
