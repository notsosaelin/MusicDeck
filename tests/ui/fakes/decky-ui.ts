/** @decky/ui without Steam: the navigation calls the tested modules make, doing nothing. */
export const Navigation = {
  Navigate: () => {},
  NavigateBack: () => {},
  CloseSideMenus: () => {},
  OpenQuickAccessMenu: () => {},
  NavigateToExternalWeb: () => {},
};
export const findModuleExport = () => undefined;
/** No window open: nothing to read where the main window is */
export const Router = { WindowStore: undefined };
