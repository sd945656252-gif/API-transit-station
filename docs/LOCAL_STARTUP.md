# Existing local installation startup

Use this after the Docker containers have already been installed:

    powershell -NoProfile -ExecutionPolicy Bypass -File tools/start-existing-local.ps1 -InstallAutoStart

The script registers a Windows user-login startup shortcut. It waits for
Docker Desktop, then PostgreSQL and Redis health, and only then starts the exact
current application container. It does not build images, pull a different
version, recreate the database, or start a whole Compose group. It is safe to
rerun against a healthy installation. Logs remain in the ignored local
.managed-updater/startup.log. The installed containers must already exist.

Docker restart policies remain enabled. Windows login startup is separate from
Codex; exiting Codex does not stop these services. A reboot without user login
has not been configured as a Windows service by this installer.

After a managed update has succeeded and the new container is healthy, the
updater saves the old container's private recovery descriptor to
/state/retired-containers/ and removes only its stopped container metadata.
Images and all data volumes remain. These descriptors contain credentials:
they are restricted files in the private updater state volume, must not be
published, and are for operator recovery rather than a one-click rollback API.
Unfinished updates retain their original recovery container. A failed archive
leaves the old container intact and records a retirement error.

This prevents old containers, which retain the same Compose service labels,
from being started alongside the new version through Docker Desktop.
