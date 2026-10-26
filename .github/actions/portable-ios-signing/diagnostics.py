"""Source-owned lifecycle labels only; never inspect a diagnostic payload."""

from contextlib import contextmanager
from enum import Enum
import os


class Stage(Enum):
    UNKNOWN = "unknown"
    CONFIGURATION = "configuration"
    SIGNING_LOCK = "signing-lock"
    PRIVATE_INPUTS = "private-inputs"
    API_KEY = "api-key-validation"
    CERTIFICATE = "certificate-validation"
    XCODE_IDENTITY = "xcode-identity"
    API_AUTHENTICATION = "api-authentication"
    CAPABILITIES = "declared-capabilities"
    REGISTERED_CERTIFICATE = "registered-certificate-lookup"
    BUNDLE_LOOKUP = "bundle-lookup"
    BUNDLE_CREATE = "bundle-create-attempt"
    CAPABILITY_LOOKUP = "capability-lookup"
    CAPABILITY_ENABLE = "capability-enable-attempt"
    PROFILE_LOOKUP = "profile-lookup"
    PROFILE_CREATE = "profile-create-attempt"
    PROFILE_VALIDATION = "profile-validation"
    PROFILE_INSTALL = "profile-installation"
    KEYCHAIN = "keychain-setup"
    ARCHIVE = "archive"
    ARCHIVE_VALIDATION = "archive-validation"
    EXPORT = "export"
    IPA_VALIDATION = "ipa-validation"
    IPA_OUTPUT = "ipa-output"
    TESTFLIGHT = "testflight-upload-attempt"
    CLEANUP = "cleanup"
    COMPLETED = "completed"


class CleanupStage(Enum):
    UNKNOWN = "unknown"
    NOT_STARTED = "not-started"
    SEARCH_LIST = "search-list"
    KEYCHAIN = "keychain"
    PROFILES = "profiles"
    PRIVATE_DIRECTORY = "private-directory"
    COMPLETED = "completed"


class Diagnostics:
    def __init__(self):
        self.current = Stage.UNKNOWN
        self.failure = None
        self.cleanup_failure = None
        self.cleanup = CleanupStage.NOT_STARTED

    def begin(self, stage):
        if not isinstance(stage, Stage):
            raise TypeError()
        self.current = stage

    def failed(self):
        if self.failure is None:
            self.failure = Stage.CLEANUP if self.cleanup_failure else self.current

    @contextmanager
    def cleaning(self, stage):
        if not isinstance(stage, CleanupStage):
            raise TypeError()
        self.cleanup = stage
        try:
            yield
        except BaseException:
            if self.cleanup_failure is None:
                self.cleanup_failure = stage
            raise

    def cleaned(self):
        # Called only after all applicable finally blocks and directory removal.
        self.cleanup = CleanupStage.COMPLETED

    def values(self):
        stage = self.failure or self.current
        cleanup = self.cleanup_failure or self.cleanup
        return (stage.value if isinstance(stage, Stage) else Stage.UNKNOWN.value,
                cleanup.value if isinstance(cleanup, CleanupStage) else CleanupStage.UNKNOWN.value)

    def publish(self, env):
        """Best effort diagnostics must never replace the signing exit status."""
        try:
            stage, cleanup = self.values()
            descriptor = os.open(env["GITHUB_OUTPUT"], os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(f"stage={stage}\ncleanup-stage={cleanup}\n")
        except Exception:
            try:
                print("::warning::Portable iOS diagnostic stage: output-transport")
            except Exception:
                pass
