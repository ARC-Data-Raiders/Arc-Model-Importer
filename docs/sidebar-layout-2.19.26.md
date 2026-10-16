# Responsive outfit sidebar — 2.19.26

The Outfit Selector keeps its search and short result list above a responsive control area. On sidebars at least 430 UI-scaled pixels wide, Browse for Outfit Selector, Load Outfit and Load All Colorways occupy the left half and the current outfit preview is centered in the right half. The icon scale follows that column width. On narrower sidebars the same controls and preview stack vertically, with the preview using the full width.

Mask Debug now follows Outfit Selector and precedes Body. Import Reference and Merge Selected Armatures are adjacent, with Merge Selected Armatures directly below Import Reference; the Rig wrapper is removed. Offset Asset, Inherit Shape Key and Pose lead Settings. Group by Asset Type and Group by Outfit Slot share a half-width row.

This changes panel drawing and labels only. Operator IDs and scene properties remain the same, so saved files and button actions retain their current behavior. Package 2.19.26 contains only the version metadata and outfit sidebar changes relative to 2.19.25. Concurrent weapon-browser work remains separate.
