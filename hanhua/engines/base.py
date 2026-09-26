"""Engine plugin interface."""


class Engine:
    name = 'base'
    description = ''

    def __init__(self, ws, cfg):
        self.ws = ws
        self.cfg = cfg

    @classmethod
    def detect(cls, game_dir):
        raise NotImplementedError

    def scan(self, game_dir):
        """Return a dict describing the game files (for the orchestrator)."""
        raise NotImplementedError

    def backup(self, game_dir):
        raise NotImplementedError

    def extract(self):
        """Return (units, scenes). unit: dict(key, kind, seq, scene, speaker, source, skip, auto)."""
        raise NotImplementedError

    def build(self, translations):
        """Produce patched files in ws/build. translations: {key: text}."""
        raise NotImplementedError

    def validate(self, translations):
        """Return list of problems (empty = ok)."""
        raise NotImplementedError

    def install(self, game_dir):
        raise NotImplementedError

    def uninstall(self, game_dir):
        raise NotImplementedError

    def game_running(self):
        return False
