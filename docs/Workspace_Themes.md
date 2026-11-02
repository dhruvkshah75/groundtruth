# Workspace themes

The workspace defaults to dark. The sun or moon button in the top right switches between dark and light, and the browser remembers the choice. The initial HTML applies the saved preference before the app mounts to avoid a light flash.

Both themes retain the existing workspace structure, typography, chat alignment, inspector tiers, and graph interactions. Colors come from shared CSS tokens: neutral charcoal surfaces from the supplied interface screenshot, with muted teal (#70AFA6) primary accents and soft mint (#91BDB3) graph selection accents in dark mode, white/lilac surfaces in light mode. Revision and error colors remain distinct. The graph updates its background, nodes, and edges in place when the theme changes, preserving its camera and selection.

On compact screens, navigation remains a dismissible drawer, touch controls have larger targets, inspector cards use the available width, and graph actions wrap rather than overflowing. The composer accounts for phone safe areas. Reduced-motion preferences continue to apply.

This theme extension supersedes the light-only guidance in the original DESIGN.md scan. The original Figma community reference remains the composition reference; connector access to that file was unavailable for this refinement, so no new Figma frame or design asset was imported.

The graph scope selector uses a translucent tray and a softly highlighted glass lens that slides between Active beliefs and Belief history. Floating graph camera controls share the glass surface treatment in dark mode. Keyboard focus remains visible, and the sliding transition is disabled for reduced-motion preferences.

Shared glass tokens also style the composer, selected workspace/chat navigation, New chat button, chat menus and confirmations, graph action buttons, camera controls, and graph dialogs in both themes. Light mode uses white/lilac translucency; dark mode uses neutral charcoal translucency. Content panels and graph labels keep solid readable surfaces. Blur is confined to floating controls and overlays, with an opaque gradient fallback when backdrop filtering is unavailable.
