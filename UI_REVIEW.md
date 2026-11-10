# UI review

This is a compact GTK tool for people switching between GeForce NOW and its macro picker. The design uses low energy, steady rhythm, and brief motion. It keeps the original dark panel, uses Pretendard for both Korean and English, and reserves the accent for the current tab, focus, and primary actions. Icons preserve room at the smallest width while the active tab names the current view. Empty and unavailable controls use plain text so the user can see what is missing.

| Check | Evidence |
| --- | --- |
| Navigation and keyboard | Five native GTK tabs, accessible names, active tab text, a visible focus outline, and Ctrl+1 through Ctrl+5 exercised in GTK tests. |
| Compact layout and localization | All five tabs rendered offscreen at 360px in English and Korean. No horizontal growth in the compact snapshots. |
| Contrast | Theme foreground over background is 15.46:1, muted text over background is 7.34:1, and muted text over selected rows is 4.70:1. |
| Empty and error states | Fresh library and blacklist rendered without personal data. Visual Workflow controls reflect whether an editable workflow or an active run exists. Credential and blacklist tests exercise their GTK controls. |
| Restraint | Existing GTK icons, Pretendard, tab underline, and layout provide hierarchy. No promotional claims or new illustration assets. |
| Verification limits | Real game sign-in, controller hardware, live VPN operations, and a live Hyprland session are outside the offscreen checks. |
