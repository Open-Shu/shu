// Layout primitives shared by AdminLayout / UserLayout and by components
// that need to align with their dimensions (e.g. a Snackbar centering
// relative to the visible content column rather than the raw viewport).
//
// Kept as raw numbers (not theme.spacing()) because TopBar's leftOffset
// prop interpolates the value into a CSS string (`${leftOffset}px`) — a
// theme.spacing() return value would produce "56pxpx" and silently break.
export const DRAWER_WIDTH = 280;

// Offset that re-centers a viewport-centered overlay (e.g. Snackbar) onto
// the visible content column when the desktop drawer is open. Half of
// DRAWER_WIDTH: shifting an element by this amount counteracts the drawer
// pushing the visible content right.
export const DRAWER_CONTENT_CENTER_OFFSET = DRAWER_WIDTH / 2;

// Width reserved on mobile for the hamburger toggle before the drawer
// opens as a temporary overlay. TopBar consumes this via its leftOffset.
export const MOBILE_HAMBURGER_WIDTH = 56;

// Horizontal gap between the right edge of the persistent desktop drawer
// and the start of the TopBar's section-title content area.
export const TOPBAR_CONTENT_GAP = 16;
