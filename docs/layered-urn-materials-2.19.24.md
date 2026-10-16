# Layered Urn material corrections — 2.19.24

Urn Terracotta was missing its orange/red mottling and ceramic medium-normal stage, while base normals were incorrectly scaled by BaseRoughnessTiling. The fixes follow current game2.1 material/shader data and the sampled ARC Texturer Revamped1.7 where it reconstructs the native stages. Decal construction is unchanged.

## Native evidence
MI_Urn_Urn_Terracotta derives from MI_Character_Layered_4. SM5 resource4D173095A6A3A14CFD3AA8D6B1EF73928A29B91E, pixel0811_D03D19E79F81633B7D38D92341E865C822CDECF3, lines238–249 proves BaseNormal sampling at UV1*Tiling*DetailTiling, independently of BaseRoughnessTiling. Urn zone1 therefore uses11.256163 rather than2.251233; zone2 uses12.413624 rather than3.05003.

Pattern lines582–645 sample the direct weighted PatternID into the array, including valid slice0. UV is UV1*Tiling*PatternTiling, without DetailTiling. The pattern RGB is linear blend weights. Raw RGB first mixes by PatternMask.alpha, then authored PatternColorA/B/C mix by swatch alpha×pattern.R/G/B. A sequential PatternMaskRGB/ColorMaskRGB fold gates the result. White, black and pure-red swatches are valid when their alpha is active. Pattern processing follows ColorTex and precedes crease/edge overlays. Urn zone1 PatternID0, PatternTiling1.7253401 and active A/B/C swatches provide its missing mottling.

Medium-normal lines389–440 use the OCM green channel, four cardinal BlurOffset taps and a centre tap at BlurMipLevel. Neighbours each weigh3/16, centre1/4. ridge=1-min(2*abs(blurG-MediumMaskValue),1); factor=saturate((ridge-(1-MediumMaskSoftness))/MediumMaskSoftness)*MediumNormalStrength. Strength is not clamped to1: Urn uses1.5 on ceramic and7.5 on zones2/3. Medium normal is (decodedXY*factor,1+(decodedZ-1)*factor), then RNM-combined with the existing Base→Crease→Edge result. Revamped omits this stage, so its omission was not adopted.

## Implementation and validation
materials/layered_medium.py reconstructs the OCM mip pyramid in memory, samples the native five taps and implements mask/strength/RNM. Generated mip images are packed into the blend; no temporary texture files are written to C:. The mip cache includes source modification metadata. Patterns use exact pattern-array files and cannot accidentally resolve a Colors_LAYER0 file. Material diagnostics report active pattern/medium zones.

20 independent pattern-helper checks, including five actual emission bakes, pass.33 medium-node checks plus7 scalar-oracle checks pass: unclamped7.5, negative strengthened Z, BOX mip levels, periodic bilinear five-tap blur including fractional LOD, selected/excluded zones and RNM.30 existing layered backpack meshes rebuild with identical geometry, UVs, material-face indices, placements, shape-key values and all existing decal settings. Before/after controlled renders were inspected against the direct UI icon. Install/overwrite/disable/re-enable/restart pass.

Live update affects only Backpack_Urn (SM_Urn_Urn), preserves788 scene objects and sun settings/linking, and saves Z:/Datamining/Arc Raiders/Blends/AllAccessories-2.19.24.blend without overwriting the original. Concurrent weapon/UI work is excluded. Evidence, reports, shader disassembly, renders and ZIP: Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21924.

## Limits
Blender cannot request an explicit image SampleLevel in this graph. BOX-reconstructed mips approximate the game's stored mip filtering/compression; the mask/strength/order formulas themselves follow native arithmetic. This is not a claim of pixel-identical game lighting/BSDF/weather or implemented HueVariation. Existing Rabbit’s Foot hair-scattering/PDO limits remain unchanged.
