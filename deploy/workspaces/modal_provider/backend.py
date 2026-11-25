"""Modal implementation for the durable workspace service."""
import os
import threading
import uuid

import modal
from modal._utils.async_utils import synchronizer
from modal.client import _Client
from modal_proto import api_pb2

from errors import Failure
from service import serve
from .filesystem import file_operation, runner_image


@synchronizer.create_blocking
async def delete_volume_id(volume_id):
    # SDK 1.6.4's name-based delete performs another lookup. Use the same RPC
    # with the already verified ID so a replacement can never be deleted.
    client = await _Client.from_env()
    try:
        await client._stub.VolumeDelete(api_pb2.VolumeDeleteRequest(volume_id=volume_id))
    except modal.exception.NotFoundError:
        pass


class ModalBackend:
    single_mount = False

    def __init__(self, owner):
        self.owner = owner
        self.environment = os.environ["MODAL_ENVIRONMENT"]
        self.app_name = os.environ["MODAL_APP_NAME"]
        self.app = modal.App.lookup(self.app_name, environment_name=self.environment)
        self.identity = f"modal:{self.environment}:{self.app.app_id}"
        self.image = runner_image()
        self.timeout = int(os.getenv("MODAL_SANDBOX_SECONDS", "3600"))
        if self.timeout <= 0:
            raise ValueError("MODAL_SANDBOX_SECONDS must be positive")
        # Reload affects the whole sandbox. A bounded lock table also permits
        # unrelated sandboxes to execute file operations concurrently.
        self.mount_locks = [threading.Lock() for _ in range(64)]

    def volume_name(self, data):
        return "ow-data-" + uuid.uuid5(uuid.UUID(self.owner), data).hex

    def volume(self, data):
        return modal.Volume.from_name(self.volume_name(data), environment_name=self.environment)

    def claim(self, data, pinned):
        volume = self.volume(data)
        try:
            volume.hydrate()
        except modal.exception.NotFoundError:
            if pinned:
                raise Failure("workspace_data_missing", 409) from None
            modal.Volume.objects.create(self.volume_name(data), version=2, allow_existing=True,
                                        environment_name=self.environment)
            volume = self.volume(data)
            volume.hydrate()
        if pinned and volume.object_id != pinned:
            raise Failure("workspace_identity_changed", 409)
        return volume.object_id

    def sandbox(self, workspace, pinned=None):
        try:
            sb = modal.Sandbox.from_name(self.app_name, workspace, environment_name=self.environment)
        except modal.exception.NotFoundError:
            return None
        if (pinned and sb.object_id != pinned) or sb.get_tags().get("registration") != self.owner:
            raise Failure("workspace_identity_changed", 409)
        return sb

    def has_compute(self, workspace, uid):
        return self.sandbox(workspace, uid) is not None

    def compute(self, workspace, data, profile, previous_uid, data_uid):
        sb = self.sandbox(workspace, previous_uid)
        tags = {"registration": self.owner, "volume": data_uid, "profile": profile}
        if sb is None:
            sb = modal.Sandbox.create(
                "sleep", "infinity", app=self.app, name=workspace, tags=tags,
                image=self.image, volumes={"/data": modal.Volume.from_id(data_uid)},
                env={"WORKSPACE_ID": workspace}, timeout=self.timeout,
                cpu=0.25 if profile == "small" else 0.5, memory=256 if profile == "small" else 512,
                block_network=True,
            )
        else:
            observed = sb.get_tags()
            if any(observed.get(k) != v for k, v in tags.items()):
                raise Failure("workspace_identity_changed", 409)
        return sb.object_id

    def ready(self, workspace, uid):
        # Modal exec waits for the sandbox to become ready.
        return True

    def file(self, workspace, physical_id, operation):
        with self.mount_locks[hash(physical_id) % len(self.mount_locks)]:
            return file_operation(modal.Sandbox.from_id(physical_id), workspace, operation)

    def remove(self, kind, name, pinned):
        if kind == "compute":
            sb = self.sandbox(name, pinned)
            if sb is not None:
                sb.terminate(wait=True)
        else:
            volume = self.volume(name)
            try:
                volume.hydrate()
            except modal.exception.NotFoundError:
                return True
            if pinned and volume.object_id != pinned:
                raise Failure("workspace_identity_changed", 409)
            delete_volume_id(volume.object_id)
        return True


if __name__ == "__main__":
    serve(ModalBackend)
