# Head preview control height and crop padding - 2.19.30

The eyelash checkbox belongs to the head's left control column. Head previews now use all four control rows for their wide-layout height cap. Narrow layouts place the checkbox before the preview, together with the other head controls.

Appearance thumbnails now retain their tightly cropped rectangular pixel bounds, instead of re-padding the art into a square canvas with a 2% border. Source PNGs and scene references remain unchanged.

Remaining limitation: Blender template_icon reserves a square, adds internal preview padding, and fits rectangular artwork into that square. Landscape previews therefore retain vertical letterboxing. This update removes addon-generated padding but does not eliminate that built-in space. A native template_preview texture experiment was rejected: Blender uses the area as its render-job owner, so multiple previews compete for one result and earlier images can disappear. No texture-preview cache or experimental drawing code is shipped.

Private Blender 5.1.2 UI screenshots verified simultaneous head and briefcase previews in both branches, tight alpha bounds, and the complete head control column. Evidence is under Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/21930/. The packaged ZIP must pass the repository install gate before sync.
