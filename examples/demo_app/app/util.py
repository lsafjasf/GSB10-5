from vulnpkg.core import parse
from condpkg.deep import run

ENABLE_LEGACY = False


def process(data):
    result = parse(data)
    if ENABLE_LEGACY:
        run(data)
    return result
