"""Engine plugins. Each plugin knows how to detect, extract, rebuild and install one game engine."""
from .system4.plugin import System4Engine

ENGINES = [System4Engine]


def detect_engine(game_dir):
    for cls in ENGINES:
        if cls.detect(game_dir):
            return cls
    return None


def get_engine(name):
    for cls in ENGINES:
        if cls.name == name:
            return cls
    raise KeyError(name)
