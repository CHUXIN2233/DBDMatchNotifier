# Security

Please do not include `wechat_state.json`, logs, screenshots, or captured
network observations in bug reports. These files can contain session data or
personal identifiers.

If a credential is accidentally committed, stop publishing immediately,
revoke or re-authenticate the affected WeChat ClawBot session, then remove the
credential from the complete Git history before making the repository public.

This project observes DBD process UDP endpoint metadata from outside the game.
It does not inject DLLs, hook functions, modify game files, or write to game
memory.
