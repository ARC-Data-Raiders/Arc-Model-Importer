# Character shading 2.19.20

Correct packed skin normal reconstruction and gate scalp/facial detail with their alpha masks. Skin response groups are owned per material so existing roughness and subsurface settings survive repair. Eyes now use exact head MIs with authored iris radius, pupil scale, centered UV scaling, tint and limbus controls.

Hair cards use signed UV-space tangents, authored opacity/dither parameters and palette variation, including explicit zero values. Native v10 COL0 RGBA masks drive secondary colors when present; unrelated vertex paint is preserved. UE depth-offset atlases no longer produce glass transmission.

Verified with329 integrated checks across18 heads and31 hair/facial meshes, independent eye mask comparison and controlled skin/eye renders. ZIP install, overwrite, disable/re-enable and restart checks use isolated Z: profiles. Live scene repair preserves788 objects, geometry, UVs, shape keys and placement. Reports and safety copy: Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21920. Saved repaired scene: Z:/Datamining/Arc Raiders/Blends/AllAccessories-2.19.20.blend.

Unreal eye refraction and hair scattering remain approximations. Non-BC5 tangent reconstruction follows the sampled Revamped recipe rather than independently verified native shader decoding.
