#!/usr/bin/env python3
"""
Auto-increments patch version in dashboard/app.py before every commit.
Run via: python bump_version.py
Or wire into pre-commit hook.
"""
import re, sys, os

APP = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")

with open(APP) as f:
    content = f.read()

# Find current version
m = re.search(r'Engine v(\d+)\.(\d+)\.(\d+)', content)
if not m:
    print("ERROR: Could not find version string in app.py")
    sys.exit(1)

major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
new_patch = patch + 1
old_ver = f"v{major}.{minor}.{patch}"
new_ver = f"v{major}.{minor}.{new_patch}"

content = content.replace(
    f"**Engine {old_ver}**",
    f"**Engine {new_ver}**"
)

with open(APP, "w") as f:
    f.write(content)

print(f"Version bumped: {old_ver} → {new_ver}")
