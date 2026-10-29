from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage


class PrivateEvidenceStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=Path(settings.BASE_DIR) / 'private-evidence', base_url=None)

    def _open(self, name, mode='rb'):
        try:
            return super()._open(name, mode)
        except FileNotFoundError:
            return default_storage.open(name, mode)

    def url(self, name):
        raise ValueError('Private evidence files do not have public URLs.')
