from enum import Enum


class ResolverState(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class PdfSource(str, Enum):
    PENDING = "pending"
    S3_MIRROR = "s3_mirror"
    REMOTE = "remote"
    MANUAL = "manual"


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"


class ParseStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    COMPLETED = "completed"
    FAILED = "failed"
