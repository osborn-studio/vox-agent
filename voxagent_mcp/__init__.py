"""VOXAGENT MCP tool server package.

NOTE ON NAMING: this package is deliberately named `voxagent_mcp`, NOT `mcp`
as the original build plan specified. The repo root precedes site-packages
on `sys.path` for any code run from within this repository (the normal case
for this project's own code/tests), so a local package literally named `mcp`
would permanently shadow the upstream Model Context Protocol Python SDK
(`pip install mcp`) — `import mcp` would always resolve to the local package
instead of the SDK, no matter how the SDK is installed. That would make
`serve()` (see `voxagent_server.py`) dead on arrival even after installing
the real SDK. Renaming this package to `voxagent_mcp` removes the collision
entirely, so `voxagent_server.serve()` can do a plain, ordinary `import mcp`
of the real SDK.
"""
