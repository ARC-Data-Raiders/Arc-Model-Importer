# Steam folder selection (2.19.48)

The Steam Install picker resolves Arc Raiders' `PioneerGame/Content/Paks` folder from supported install ancestors, including Steam's own root. For a Steam root, it checks `steamapps/libraryfolders.vdf` so secondary Steam libraries work without recursively scanning all installed games. The wait cursor uses `context.window.cursor_set`, which is the Blender API; `WindowManager` has no `cursor_set` method.
